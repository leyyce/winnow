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
When ``is_override=True``:
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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AlreadyFinalizedError,
    NotEligibleError,
    SubmissionNotFoundError,
)
from app.governance.base import GovernancePolicy
from app.models.status_ledger import (
    StatusLedger,
    StatusLedgerStatus,
    SupersedeReason,
)
from app.models.submission import Submission
from app.models.submission_vote import SubmissionVote
from app.registry.manager import registry
from app.schemas.results import RequiredValidations
from app.schemas.voting import VoteRequest, VoteResponse, VoteTally
from app.services.scoring_service import (
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
            weight = GovernancePolicy.get_vote_weight(
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
    is_override: bool = False,
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
    # Admin overrides may target any status (including terminal states).
    # Normal votes are only permitted when the submission is pending_review.
    if not is_override:
        if current_ledger is None or current_ledger.status != StatusLedgerStatus.PENDING_REVIEW:
            status = getattr(current_ledger, "status", "unknown")
            raise AlreadyFinalizedError(submission_id, status)

    # Load latest scoring snapshot for governance snapshot
    snapshot = await _latest_scoring_snapshot(submission_id, db)
    # required_validations is stored as a JSON array of tier dicts
    all_requirements: list[RequiredValidations] = [
        RequiredValidations.model_validate(r) for r in snapshot.required_validations
    ]

    config = registry.get_config(submission.project_id)

    # Step 3–4 — eligibility check (skipped for admin override)
    # Reviewer is eligible if they qualify in at least one applicable tier.
    if not is_override:
        eligible_in_any = False
        for req in all_requirements:
            try:
                GovernancePolicy.get_vote_weight(
                    req,
                    request.user_role,
                    request.user_trust_level,
                )
                eligible_in_any = True
                break
            except NotEligibleError:
                continue
        if not eligible_in_any:
            raise NotEligibleError(
                f"Role '{request.user_role}' with trust {request.user_trust_level} "
                "is not eligible to vote on this submission under any applicable tier."
            )

    # Validate override vote value
    if is_override and request.vote not in ("approve", "reject", "voided"):
        raise NotEligibleError("Override vote must be 'approve', 'reject', or 'voided'.")

    # Step 5 — INSERT vote (append-only, no duplicate check)
    vote_row = SubmissionVote(
        id=uuid4(),
        submission_id=submission_id,
        user_id=request.user_id,
        vote=request.vote,
        is_override=is_override,
        user_trust_level=request.user_trust_level,
        user_role=request.user_role,
        note=request.note,
    )
    db.add(vote_row)
    await db.flush()

    # ── Admin Override — forced terminal state ────────────────────────────────
    if is_override:
        forced_status = _vote_to_status(request.vote)
        # current_ledger may be None for a missing submission with no ledger entries;
        # guard defensively — SubmissionNotFoundError was already raised above.
        supersedes_id = current_ledger.id if current_ledger is not None else None
        lineage_sum = await _resolve_lineage_sum(supersedes_id, db)
        target = _target_trust(forced_status, config)
        trust_delta = target - lineage_sum

        new_ledger = StatusLedger(
            id=uuid4(),
            submission_id=submission_id,
            scoring_snapshot_id=snapshot.id,
            status=forced_status,
            trust_delta=trust_delta,
            supersedes=supersedes_id,
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

    # Step 6 — recompute latest-wins tally across ALL applicable tiers.
    # The submission finalizes when ANY tier's threshold is met.
    active_votes = await _latest_vote_per_user(submission_id, db)

    threshold_met = False
    final_status: str | None = None
    winning_approve = 0
    winning_reject = 0

    for req in all_requirements:
        approve_weight, reject_weight = _compute_tally(active_votes, req)
        if approve_weight >= req.threshold_score or reject_weight >= req.threshold_score:
            threshold_met = True
            winning_approve = approve_weight
            winning_reject = reject_weight
            final_status = (
                StatusLedgerStatus.APPROVED
                if approve_weight >= req.threshold_score
                else StatusLedgerStatus.REJECTED
            )
            break  # first (most-restrictive) tier that meets threshold wins

    # Use first tier's tally for response display when no threshold met
    if not threshold_met:
        winning_approve, winning_reject = _compute_tally(
            active_votes, all_requirements[0]
        )

    tally = VoteTally(approve=winning_approve, reject=winning_reject)

    # Step 7 — finalize if threshold met
    if threshold_met:
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
                "approve_weight": winning_approve,
                "reject_weight": winning_reject,
                "trust_delta": trust_delta,
            },
        )

    message = (
        f"Vote registered. Approve weight: {winning_approve}, "
        f"Reject weight: {winning_reject}."
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
