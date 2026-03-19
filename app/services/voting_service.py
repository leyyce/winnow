"""
Voting service — append-only governance orchestrator.

Sprint 5 (Lifecycle Ledger) rewrite
-------------------------------------
* **Append-only votes**: no UNIQUE constraint on (submission_id, user_id).
  When a reviewer changes their mind they INSERT a new row.  The "active"
  vote per reviewer is always the latest row by ``created_at`` (Mandate 1).
* **Latest-wins tally**: weighted tally is computed by resolving each
  reviewer's active vote then summing weights from the ``role_configs``
  snapshot stored in ``scoring_snapshots.required_validations`` JSONB.
* **FOR UPDATE locking**: the latest ``status_ledger`` row is locked before
  any tally computation to prevent concurrent finalization races (Mandate 3).
* **role_configs governance**: ``GovernancePolicy.get_vote_weight()`` handles
  blocked_roles / role_configs / default_config / trust floor evaluation.
* **Trust delta**: computed via the shared ``_resolve_lineage_sum`` CTE from
  ``scoring_service`` — cross-chain aware.
* Every ``status_ledger`` INSERT enqueues a ``webhook_outbox`` row in the
  same transaction (Rule 11).

Admin Override (Power-Vote Pattern)
------------------------------------
When ``request.is_override=True``:
* Eligibility check is bypassed.
* The submission is immediately forced into the specified terminal state.
* ``supersede_reason='admin_overwrite'`` is recorded.

References
----------
* Rule 9:  Services never import fastapi / never raise HTTPException
* Rule 10: Immutable Submissions & Append-Only State
* Rule 11: Event-driven state transitions via Transactional Outbox
"""
from __future__ import annotations

import logging
from uuid import UUID, uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AlreadyFinalizedError,
    NotEligibleError,
    SubmissionNotFoundError,
)
from app.models.scoring_snapshot import ScoringSnapshot
from app.models.status_ledger import (
    TERMINAL_STATES,
    StatusLedger,
    StatusLedgerStatus,
    SupersedeReason,
)
from app.models.submission import Submission
from app.models.submission_vote import SubmissionVote
from app.registry.manager import registry
from app.schemas.results import RequiredValidations, ScoringResultResponse
from app.schemas.voting import VoteRequest, VoteResponse, VoteTally
from app.services import webhook_service
from app.services.scoring_service import (
    _build_response,
    _enqueue_webhook,
    _latest_ledger_entry,
    _latest_scoring_snapshot,
    _resolve_lineage_sum,
    _target_trust,
)

logger = logging.getLogger(__name__)


async def _latest_vote_per_user(
    submission_id: UUID,
    db: AsyncSession,
) -> list[SubmissionVote]:
    """
    Return the latest vote row per (submission_id, user_id) pair.

    Uses a subquery to select the most recent ``created_at`` per user,
    then joins back to get the full row.  This implements the
    "latest-row-wins" pattern for append-only voting (Mandate 1).
    """
    # Subquery: max created_at per user for this submission
    max_per_user = (
        select(
            SubmissionVote.user_id,
            func.max(SubmissionVote.created_at).label("max_created"),
        )
        .where(SubmissionVote.submission_id == submission_id)
        .group_by(SubmissionVote.user_id)
        .subquery()
    )
    # Join to get the full row matching the latest timestamp
    stmt = select(SubmissionVote).join(
        max_per_user,
        (SubmissionVote.user_id == max_per_user.c.user_id)
        & (SubmissionVote.created_at == max_per_user.c.max_created)
        & (SubmissionVote.submission_id == submission_id),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _compute_tally(
    active_votes: list[SubmissionVote],
    requirements: RequiredValidations,
    governance_policy,
) -> tuple[int, int]:
    """
    Compute (approve_weight, reject_weight) from the list of active votes.

    Each vote's weight is resolved via ``governance_policy.get_vote_weight``.
    Ineligible votes (blocked role / insufficient trust) contribute 0 weight.
    """
    approve_weight = 0
    reject_weight = 0

    for vote in active_votes:
        try:
            weight = governance_policy.get_vote_weight(
                requirements,
                vote.user_role,
                vote.user_trust_level,
            )
        except NotEligibleError:
            continue  # ignore ineligible votes in tally

        if vote.vote == "approve":
            approve_weight += weight
        elif vote.vote == "reject":
            reject_weight += weight
        # 'voided' votes (admin override) don't contribute to tally

    return approve_weight, reject_weight


async def cast_vote(
    submission_id: UUID,
    request: VoteRequest,
    db: AsyncSession,
) -> VoteResponse:
    """
    Record a reviewer's vote and evaluate governance thresholds.

    For **normal votes** (``is_override=False``):
    1. Load submission (SubmissionNotFoundError if missing).
    2. Load latest status_ledger WITH FOR UPDATE; reject if not pending_review.
    3. Resolve RequiredValidations from latest scoring snapshot.
    4. Check reviewer eligibility via ``governance_policy.get_vote_weight``.
    5. INSERT SubmissionVote.
    6. Recompute latest-wins tally across ALL active votes.
    7. If threshold met: INSERT new status_ledger + enqueue webhook.
    8. Return VoteResponse.

    For **admin override votes** (``is_override=True``):
    * Steps 3–4 (eligibility check) are skipped.
    * The terminal state is forced immediately after vote INSERT.
    * ``supersede_reason='admin_overwrite'`` is used.

    Raises
    ------
    SubmissionNotFoundError  → 404
    AlreadyFinalizedError    → 409
    NotEligibleError         → 403
    """
    # Step 1 — load submission
    submission = await db.get(Submission, submission_id)
    if submission is None:
        raise SubmissionNotFoundError(submission_id)

    # Step 2 — lock latest ledger entry (FOR UPDATE)
    current_ledger = await _latest_ledger_entry(submission_id, db, for_update=True)
    if current_ledger is None or current_ledger.status != StatusLedgerStatus.PENDING_REVIEW:
        status = getattr(current_ledger, "status", "unknown")
        raise AlreadyFinalizedError(submission_id, status)

    # Load latest scoring snapshot for governance snapshot
    snapshot = await _latest_scoring_snapshot(submission_id, db)
    requirements = RequiredValidations.model_validate(snapshot.required_validations)

    config = registry.get_config(submission.project_id)
    governance_policy = config.governance_policy

    # Step 3–4 — eligibility check (skipped for admin override)
    if not request.is_override:
        # Raises NotEligibleError if ineligible — propagates to API layer (Rule 9)
        governance_policy.get_vote_weight(
            requirements,
            request.user_role,
            request.user_trust_level,
        )

    # Validate override vote value
    if request.is_override and request.vote not in ("approve", "reject", "voided"):
        raise NotEligibleError("Override vote must be 'approve', 'reject', or 'voided'.")

    # Step 5 — INSERT vote (append-only, no duplicate check)
    vote_row = SubmissionVote(
        id=uuid4(),
        submission_id=submission_id,
        user_id=request.user_id,
        vote=request.vote,
        is_override=request.is_override,
        user_trust_level=request.user_trust_level,
        user_role=request.user_role,
        note=request.note,
    )
    db.add(vote_row)
    await db.flush()

    # ── Admin Override — forced terminal state ────────────────────────────────
    if request.is_override:
        forced_status = _vote_to_status(request.vote)
        lineage_sum = await _resolve_lineage_sum(current_ledger.id, db)
        target = _target_trust(forced_status, config)
        trust_delta = target - lineage_sum

        new_ledger = StatusLedger(
            id=uuid4(),
            submission_id=submission_id,
            scoring_snapshot_id=snapshot.id,
            status=forced_status,
            trust_delta=trust_delta,
            supersedes=current_ledger.id,
            supersede_reason=SupersedeReason.ADMIN_OVERWRITE,
        )
        db.add(new_ledger)
        await db.flush()

        await _enqueue_webhook(
            ledger_entry=new_ledger,
            submission=submission,
            snapshot=snapshot,
            webhook_url=config.webhook_url,
            db=db,
        )

        logger.info(
            "Admin override applied",
            extra={
                "submission_id": str(submission_id),
                "forced_status": forced_status,
                "trust_delta": trust_delta,
            },
        )

        return VoteResponse(
            submission_id=submission_id,
            vote_registered=True,
            current_votes=VoteTally(approve=0, reject=0),
            threshold_met=True,
            final_status=forced_status,
            message=f"Admin override: submission forced to '{forced_status}'.",
        )

    # ── Normal vote — tally and threshold evaluation ──────────────────────────

    # Step 6 — recompute latest-wins tally
    active_votes = await _latest_vote_per_user(submission_id, db)
    approve_weight, reject_weight = _compute_tally(
        active_votes, requirements, governance_policy
    )
    threshold = requirements.threshold_score

    tally = VoteTally(approve=approve_weight, reject=reject_weight)
    threshold_met = approve_weight >= threshold or reject_weight >= threshold
    final_status: str | None = None

    # Step 7 — finalize if threshold met
    if threshold_met:
        final_status = (
            StatusLedgerStatus.APPROVED
            if approve_weight >= threshold
            else StatusLedgerStatus.REJECTED
        )
        lineage_sum = await _resolve_lineage_sum(current_ledger.id, db)
        target = _target_trust(final_status, config)
        trust_delta = target - lineage_sum

        new_ledger = StatusLedger(
            id=uuid4(),
            submission_id=submission_id,
            scoring_snapshot_id=snapshot.id,
            status=final_status,
            trust_delta=trust_delta,
            supersedes=current_ledger.id,
            supersede_reason=SupersedeReason.VOTING_CONCLUDED,
        )
        db.add(new_ledger)
        await db.flush()

        await _enqueue_webhook(
            ledger_entry=new_ledger,
            submission=submission,
            snapshot=snapshot,
            webhook_url=config.webhook_url,
            db=db,
        )

        logger.info(
            "Submission finalized via voting",
            extra={
                "submission_id": str(submission_id),
                "final_status": final_status,
                "approve_weight": approve_weight,
                "reject_weight": reject_weight,
                "trust_delta": trust_delta,
            },
        )

    message = (
        f"Vote registered. Approve weight: {approve_weight}, "
        f"Reject weight: {reject_weight}, Threshold: {threshold}."
    )
    if threshold_met:
        message += f" Submission {final_status}."

    return VoteResponse(
        submission_id=submission_id,
        vote_registered=True,
        current_votes=tally,
        threshold_met=threshold_met,
        final_status=final_status,
        message=message,
    )


def _vote_to_status(vote: str) -> str:
    """Map a vote value to a StatusLedgerStatus."""
    mapping = {
        "approve": StatusLedgerStatus.APPROVED,
        "reject": StatusLedgerStatus.REJECTED,
        "voided": StatusLedgerStatus.VOIDED,
    }
    return mapping.get(vote, StatusLedgerStatus.VOIDED)
