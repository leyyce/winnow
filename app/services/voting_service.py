"""
Voting service — DB-backed multi-vote governance orchestrator.

Replaces the Sprint 2.6 in-memory ``_submissions`` dict with real
SQLAlchemy async queries.  All writes within ``cast_vote`` — the vote
INSERT, the optional status UPDATE, and the optional webhook outbox INSERT —
are performed in the *same* ``AsyncSession`` transaction so they commit or
roll back atomically (ADR-DB-006 / Rule 11).

``SELECT … FOR UPDATE`` on the Submission row prevents two concurrent votes
from racing to finalize the same submission (ADR-DB-004).  SQLite (used in
tests) silently ignores ``FOR UPDATE``, which is safe because SQLite
serialises writes at the table level.

References
----------
* Architecture: docs/architecture/03_api_contracts.md §9
* Database:     docs/architecture/05_database_design.md §6 step 9
* Rule 9:       Services never import fastapi / never raise HTTPException
* Rule 11:      Event-driven state transitions via Transactional Outbox
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AlreadyFinalizedError,
    DuplicateVoteError,
    NotEligibleError,
    SubmissionNotFoundError,
)
from app.models.scoring_result import ScoringResult
from app.models.submission import Submission, SubmissionStatus
from app.models.submission_vote import SubmissionVote
from app.registry.manager import registry
from app.schemas.results import RequiredValidations
from app.schemas.voting import VoteRequest, VoteResponse, VoteTally
from app.services import webhook_service

logger = logging.getLogger(__name__)


async def cast_vote(
    submission_id: UUID,
    request: VoteRequest,
    db: AsyncSession,
) -> VoteResponse:
    """
    Record a reviewer's vote and evaluate governance thresholds.

    Steps
    -----
    1. SELECT submission WITH FOR UPDATE (SubmissionNotFoundError if missing).
    2. Check submission is still ``pending_review`` (AlreadyFinalizedError if not).
    3. Load ScoringResult → deserialise RequiredValidations.
    4. Check reviewer eligibility via governance policy (NotEligibleError if ineligible).
    5. Check for duplicate vote via DB UNIQUE index (DuplicateVoteError if exists).
    6. INSERT SubmissionVote + flush.
    7. Load all votes → compute weighted tally.
    8. If threshold met: UPDATE submission.status + INSERT WebhookOutbox (same tx).
    9. Return VoteResponse.

    All DB writes in steps 6–8 share the same ``AsyncSession`` and commit
    atomically when the ``get_db`` dependency finalises the transaction.

    Raises
    ------
    SubmissionNotFoundError, AlreadyFinalizedError, NotEligibleError,
    DuplicateVoteError
    """
    # Step 1 — load submission with row-level lock (no-op on SQLite)
    stmt = (
        select(Submission)
        .where(Submission.submission_id == submission_id)
        .with_for_update()
    )
    submission = (await db.execute(stmt)).scalar_one_or_none()
    if submission is None:
        raise SubmissionNotFoundError(submission_id)

    # Step 2 — must still be pending
    if submission.status != SubmissionStatus.PENDING_REVIEW:
        raise AlreadyFinalizedError(submission_id, submission.status)

    # Step 3 — load governance snapshot from scoring_results
    sr_stmt = select(ScoringResult).where(ScoringResult.submission_id == submission_id)
    sr_row = (await db.execute(sr_stmt)).scalar_one()
    required = RequiredValidations.model_validate(sr_row.required_validations)

    # Step 4 — check reviewer eligibility
    config = registry.get_config(submission.project_id)
    is_eligible = config.governance_policy.is_eligible_reviewer(
        submission_score=sr_row.confidence_score,
        submission_requirements=required,
        reviewer_trust=request.user_trust_level,
        reviewer_role=request.user_role,
    )
    if not is_eligible:
        reasons: list[str] = []
        if request.user_trust_level < required.required_min_trust:
            reasons.append(
                f"trust_level {request.user_trust_level} < required {required.required_min_trust}"
            )
        if required.role_weights.get(request.user_role, 0) <= 0:
            reasons.append(
                f"role '{request.user_role}' has no weight in this tier's role_weights"
            )
        raise NotEligibleError("; ".join(reasons) or "does not meet requirements")

    # Step 5 — duplicate-vote check (DB UNIQUE constraint is the final guard)
    dup_stmt = select(SubmissionVote).where(
        SubmissionVote.submission_id == submission_id,
        SubmissionVote.user_id == request.user_id,
    )
    existing_vote = (await db.execute(dup_stmt)).scalar_one_or_none()
    if existing_vote is not None:
        raise DuplicateVoteError(submission_id, request.user_id)

    # Step 6 — persist the vote
    vote_row = SubmissionVote(
        submission_id=submission_id,
        user_id=request.user_id,
        vote=request.vote,
        user_trust_level=request.user_trust_level,
        user_role=request.user_role,
        note=request.note,
    )
    db.add(vote_row)
    await db.flush()

    logger.info(
        "Vote recorded",
        extra={
            "submission_id": str(submission_id),
            "user_id": str(request.user_id),
            "vote": request.vote,
        },
    )

    # Step 7 — load all votes and compute tally
    all_votes_stmt = select(SubmissionVote).where(
        SubmissionVote.submission_id == submission_id
    )
    all_votes = (await db.execute(all_votes_stmt)).scalars().all()
    tally = _compute_tally(all_votes)

    # Step 8 — evaluate threshold
    threshold_result = _evaluate_threshold(required, all_votes)

    if threshold_result is not None:
        submission.status = threshold_result
        await db.flush()

        logger.info(
            "Threshold met — auto-finalizing",
            extra={
                "submission_id": str(submission_id),
                "final_status": threshold_result,
                "approve_count": tally.approve,
                "reject_count": tally.reject,
            },
        )

        # Queue outbox entry within the same transaction (Transactional Outbox)
        await webhook_service.queue_finalization_webhook(
            submission_id=submission_id,
            project_id=submission.project_id,
            final_status=threshold_result,
            confidence_score=sr_row.confidence_score,
            user_context=submission.user_context,
            tally=tally,
            db=db,
        )

        return VoteResponse(
            submission_id=submission_id,
            vote_registered=True,
            current_votes=tally,
            threshold_met=True,
            final_status=threshold_result,
            message=(
                f"Threshold met. Submission finalized as '{threshold_result}'. "
                "Webhook notification queued."
            ),
        )

    # Step 9 — threshold not yet met
    approve_score = _compute_weighted_score(required, all_votes, "approve")
    reject_score = _compute_weighted_score(required, all_votes, "reject")
    remaining = required.threshold_score - max(approve_score, reject_score)
    return VoteResponse(
        submission_id=submission_id,
        vote_registered=True,
        current_votes=tally,
        threshold_met=False,
        final_status=None,
        message=f"Vote recorded. {remaining} more eligible vote(s) needed.",
    )


# ── Internal helpers ──────────────────────────────────────────────────────────
# These helpers work on SubmissionVote ORM rows (same field names as the old
# StoredVote dataclass: vote, user_trust_level, user_role, user_id).

def _compute_tally(votes: list[SubmissionVote]) -> VoteTally:
    """Count approve and reject votes (all votes, regardless of eligibility)."""
    approve = sum(1 for v in votes if v.vote == "approve")
    reject = sum(1 for v in votes if v.vote == "reject")
    return VoteTally(approve=approve, reject=reject)


def _compute_weighted_score(
    required: RequiredValidations,
    votes: list[SubmissionVote],
    decision: str,
) -> int:
    """
    Sum the role-weights of all eligible votes matching ``decision``.

    A vote is eligible when:
    - ``vote == decision``
    - ``user_trust_level >= required.required_min_trust``
    - ``role_weights.get(user_role, 0) > 0``

    All weights come from ``RequiredValidations`` (snapshotted from the
    project config at submission time) — no magic numbers here (Rule 3).
    """
    return sum(
        required.role_weights.get(v.user_role, 0)
        for v in votes
        if v.vote == decision
        and v.user_trust_level >= required.required_min_trust
        and required.role_weights.get(v.user_role, 0) > 0
    )


def _evaluate_threshold(
    required: RequiredValidations,
    votes: list[SubmissionVote],
) -> str | None:
    """
    Evaluate whether accumulated role-weight meets the governance threshold.

    Returns ``"approved"``, ``"rejected"``, or ``None`` if no threshold met.
    Approval is evaluated first — if both thresholds are met simultaneously
    (edge case with large role-weights), approval wins.
    """
    if _compute_weighted_score(required, votes, "approve") >= required.threshold_score:
        return SubmissionStatus.APPROVED
    if _compute_weighted_score(required, votes, "reject") >= required.threshold_score:
        return SubmissionStatus.REJECTED
    return None
