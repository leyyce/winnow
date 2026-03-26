"""
Voting request and response schemas for the Governance Engine.

These models define the shape of data exchanged during the
POST /api/v1/submissions/{id}/votes lifecycle step, where individual
reviewers submit votes to Winnow. Winnow tracks votes, enforces
duplicate-vote prevention, and evaluates accumulated votes against the
submission's ``required_validations``.

References
----------
* API contract: docs/architecture/03_api_contracts.md §9
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class ActiveVoteItem(BaseModel):
    """
    A single resolved (latest-wins) vote for a submission.

    Returned in the ``active_votes`` list on submission and task responses
    so clients can display who voted what without a separate round-trip.
    """

    user_id: UUID = Field(description="Reviewer's stable identifier.")
    user_role: str = Field(description="Role at vote time.")
    vote: str = Field(description="Active vote value: 'approve', 'reject', or 'voided'.")
    is_override: bool = Field(description="True if this was an admin power-vote override.")
    note: str | None = Field(default=None, description="Optional comment accompanying the vote.")
    created_at: AwareDatetime = Field(description="Timestamp of the vote row (latest wins).")


class VoteRequest(BaseModel):
    """
    Body sent by the client when a reviewer casts a vote on a submission.

    The ``user_trust_level`` and ``user_role`` are snapshots at vote time
    (Data on the Wire pattern). They are used for eligibility checks and
    threshold evaluation.
    """

    user_id: UUID = Field(
        description="Stable reviewer identifier from the client system.",
    )
    vote: Literal["approve", "reject", "voided"] = Field(
        description=(
            "The reviewer's decision. Normal votes: 'approve' or 'reject'. "
            "Admin override votes (is_override=True) may additionally use 'voided'."
        ),
    )
    user_trust_level: int = Field(
        ge=0,
        description="Reviewer's current trust level as known by the client. Scale is project-specific.",
    )
    user_role: str = Field(
        min_length=1,
        description="Reviewer's role in the client system (e.g. 'citizen', 'expert').",
    )
    note: str | None = Field(
        default=None,
        description="Optional human-readable comment accompanying the vote.",
    )


class VoteTally(BaseModel):
    """Current vote counts for a submission, broken down by decision."""

    approve: int = Field(
        ge=0,
        description="Number of 'approve' votes cast so far.",
    )
    reject: int = Field(
        ge=0,
        description="Number of 'reject' votes cast so far.",
    )


class VoteResponse(BaseModel):
    """
    Response returned after a vote is successfully recorded.

    If the vote triggers threshold evaluation and the submission is
    auto-finalized, ``threshold_met`` is True and ``final_status`` is set.
    Otherwise, ``threshold_met`` is False and ``final_status`` is None.
    """

    submission_id: UUID = Field(
        description="Echo of the submission UUID that was voted on.",
    )
    vote_registered: bool = Field(
        description="Whether the vote was successfully recorded.",
    )
    current_votes: VoteTally = Field(
        description="Current tally of all votes on this submission.",
    )
    threshold_met: bool = Field(
        description="Whether the governance threshold was met by this vote.",
    )
    final_status: str | None = Field(
        default=None,
        description="Final status if threshold was met; None otherwise.",
    )
    message: str = Field(
        description="Human-readable summary of the vote outcome.",
    )
