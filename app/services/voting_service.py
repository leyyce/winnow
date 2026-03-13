"""
Voting service — application-layer orchestrator for the multi-vote governance flow.

Accepts individual reviewer votes, enforces eligibility and duplicate-vote
prevention, evaluates accumulated votes against governance thresholds, and
triggers auto-finalization + webhook queuing when the threshold is met.

All persistence is via in-memory dicts (stubs) until the DB layer is added
in Sprint 3.

References
----------
* Architecture: docs/architecture/03_api_contracts.md §9
* Database design: docs/architecture/05_database_design.md (submission_votes table)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from app.core.exceptions import (
    AlreadyFinalizedError,
    DuplicateVoteError,
    NotEligibleError,
    SubmissionNotFoundError,
)
from app.registry.manager import registry
from app.schemas.results import RequiredValidations, ScoringResultResponse
from app.schemas.voting import VoteRequest, VoteResponse, VoteTally
from app.services import webhook_service

logger = logging.getLogger(__name__)


# ── In-memory vote store (stub until Sprint 3 DB layer) ──────────────────────

@dataclass(frozen=True)
class StoredVote:
    """In-memory representation of a single recorded vote."""

    user_id: UUID
    vote: str           # "approve" or "reject"
    user_trust_level: int
    user_role: str
    note: str | None
    created_at: datetime


@dataclass
class SubmissionRecord:
    """
    In-memory representation of a submission with its scoring result and votes.

    This stub replaces DB queries until Sprint 3. The ``scoring_result`` is
    the full ScoringResultResponse returned at submission time. Votes are
    appended as they arrive.
    """

    scoring_result: ScoringResultResponse
    status: str = "pending_review"
    votes: list[StoredVote] = field(default_factory=list)
    # Set of user_ids who have already voted (fast duplicate check)
    voter_ids: set[UUID] = field(default_factory=set)


# Global in-memory store: submission_id → SubmissionRecord
_submissions: dict[UUID, SubmissionRecord] = {}


# ── Public API ────────────────────────────────────────────────────────────────

def register_submission(result: ScoringResultResponse) -> None:
    """
    Register a scored submission in the in-memory store for vote tracking.

    Called by the submission service after scoring completes. In Sprint 3
    this will be replaced by the DB INSERT in the same transaction.
    """
    _submissions[result.submission_id] = SubmissionRecord(
        scoring_result=result,
        status="pending_review",
    )


def get_submission_record(submission_id: UUID) -> SubmissionRecord | None:
    """Look up a submission record by ID. Returns None if not found."""
    return _submissions.get(submission_id)


def clear_store() -> None:
    """Reset the in-memory store. Used by tests only."""
    _submissions.clear()


async def cast_vote(
    submission_id: UUID,
    request: VoteRequest,
) -> VoteResponse:
    """
    Record a reviewer's vote and evaluate governance thresholds.

    Steps
    -----
    1. Look up submission (SubmissionNotFoundError if missing).
    2. Check submission is still in ``pending_review`` (AlreadyFinalizedError if not).
    3. Check reviewer eligibility via governance policy (NotEligibleError if ineligible).
    4. Check for duplicate vote (DuplicateVoteError if already voted).
    5. Record the vote.
    6. Evaluate threshold — if met, auto-finalize and queue webhook.
    7. Return VoteResponse.

    Raises
    ------
    SubmissionNotFoundError
        If ``submission_id`` is not in the store.
    AlreadyFinalizedError
        If the submission has already been finalized.
    NotEligibleError
        If the reviewer does not meet trust/role requirements.
    DuplicateVoteError
        If the same ``user_id`` has already voted on this submission.
    """
    # Step 1 — look up submission
    record = _submissions.get(submission_id)
    if record is None:
        raise SubmissionNotFoundError(submission_id)

    # Step 2 — check submission is still pending
    if record.status != "pending_review":
        raise AlreadyFinalizedError(submission_id, record.status)

    # Step 3 — check reviewer eligibility
    required = record.scoring_result.required_validations
    config = registry.get_config(record.scoring_result.project_id)
    is_eligible = config.governance_policy.is_eligible_reviewer(
        submission_score=record.scoring_result.confidence_score,
        submission_requirements=required,
        reviewer_trust=request.user_trust_level,
        reviewer_role=request.user_role,
    )
    if not is_eligible:
        reasons = []
        if request.user_trust_level < required.required_min_trust:
            reasons.append(
                f"trust_level {request.user_trust_level} < required {required.required_min_trust}"
            )
        if required.role_weights.get(request.user_role, 0) <= 0:
            reasons.append(
                f"role '{request.user_role}' has no weight in this tier's role_weights"
            )
        raise NotEligibleError("; ".join(reasons) or "does not meet requirements")

    # Step 4 — check for duplicate vote
    if request.user_id in record.voter_ids:
        raise DuplicateVoteError(submission_id, request.user_id)

    # Step 5 — record the vote
    vote = StoredVote(
        user_id=request.user_id,
        vote=request.vote,
        user_trust_level=request.user_trust_level,
        user_role=request.user_role,
        note=request.note,
        created_at=datetime.now(timezone.utc),
    )
    record.votes.append(vote)
    record.voter_ids.add(request.user_id)

    logger.info(
        "Vote recorded",
        extra={
            "submission_id": str(submission_id),
            "user_id": str(request.user_id),
            "vote": request.vote,
        },
    )

    # Step 6 — evaluate threshold using accumulated role-weights
    tally = _compute_tally(record.votes)
    threshold_result = _evaluate_threshold(required, record.votes)

    if threshold_result is not None:
        # Auto-finalize
        record.status = threshold_result
        record.scoring_result = record.scoring_result.model_copy(
            update={"status": threshold_result},
        )

        logger.info(
            "Threshold met — auto-finalizing",
            extra={
                "submission_id": str(submission_id),
                "final_status": threshold_result,
                "approve_count": tally.approve,
                "reject_count": tally.reject,
            },
        )

        # Queue webhook (async, best-effort in stub mode)
        await webhook_service.queue_finalization_webhook(
            submission_id=submission_id,
            project_id=record.scoring_result.project_id,
            final_status=threshold_result,
            confidence_score=record.scoring_result.confidence_score,
            user_context=None,  # will be resolved from DB in Sprint 3
            tally=tally,
        )

        return VoteResponse(
            submission_id=submission_id,
            vote_registered=True,
            current_votes=tally,
            threshold_met=True,
            final_status=threshold_result,
            message=(
                f"Threshold met. Submission finalized as '{threshold_result}'. "
                f"Webhook notification queued."
            ),
        )

    # Step 7 — threshold not met, return current state
    approve_score = _compute_weighted_score(required, record.votes, "approve")
    reject_score = _compute_weighted_score(required, record.votes, "reject")
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

def _compute_tally(votes: list[StoredVote]) -> VoteTally:
    """Count approve and reject votes (all votes, regardless of eligibility)."""
    approve = sum(1 for v in votes if v.vote == "approve")
    reject = sum(1 for v in votes if v.vote == "reject")
    return VoteTally(approve=approve, reject=reject)


def _compute_weighted_score(
    required: RequiredValidations,
    votes: list[StoredVote],
    decision: str,
) -> int:
    """
    Sum the role-weights of all eligible votes matching ``decision``.

    A vote contributes its role-weight when:
    - ``vote == decision``
    - ``user_trust_level >= required.required_min_trust``
    - ``required.role_weights.get(user_role, 0) > 0``  (role has positive weight)

    This replaces the old count-based ``_count_eligible`` helper with a
    weight-accumulation approach, making the service layer value-agnostic
    (Rule 3: Configuration is King — all weights live in the registry).
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
    votes: list[StoredVote],
) -> str | None:
    """
    Evaluate whether accumulated role-weight meets the governance threshold.

    Returns
    -------
    str | None
        ``"approved"`` if approve_weight >= threshold_score,
        ``"rejected"`` if reject_weight >= threshold_score,
        or ``None`` if neither threshold is met.

    Approval is checked first — if both thresholds are met simultaneously
    (unlikely but possible in edge cases), approval wins.
    """
    approve_score = _compute_weighted_score(required, votes, "approve")
    if approve_score >= required.threshold_score:
        return "approved"

    reject_score = _compute_weighted_score(required, votes, "reject")
    if reject_score >= required.threshold_score:
        return "rejected"

    return None
