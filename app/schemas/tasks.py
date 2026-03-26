"""
Task list schemas for the governance review queue.

These models define the shape of data returned by the
GET /api/v1/tasks/available endpoint, where eligible reviewers retrieve
pending submissions they are authorised to validate.

References
----------
* API contract: docs/architecture/03_api_contracts.md §6
"""
from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from app.schemas.results import RequiredValidations
from app.schemas.voting import ActiveVoteItem


class TaskItem(BaseModel):
    """
    A single pending submission item in the reviewer task queue.

    Contains enough context for the client to render the task card and
    determine routing without a separate round-trip to the scoring endpoint.

    ``review_tiers`` contains ALL governance tiers whose ``score_threshold``
    the submission's confidence score meets.  Multiple tiers may apply;
    any one of them constitutes a valid pathway to finalization.
    """

    submission_id: UUID = Field(
        description="UUID of the pending submission.",
    )
    project_id: str = Field(
        min_length=1,
        description="Project this submission belongs to.",
    )
    entity_type: str = Field(
        min_length=1,
        description="Entity type within the project, e.g. 'tree'.",
    )
    confidence_score: float = Field(
        ge=0.0,
        le=100.0,
        description="Confidence Score computed at submission time.",
    )
    review_tiers: list[RequiredValidations] = Field(
        description=(
            "All applicable governance tiers for this submission.  "
            "Each tier is an independent pathway to finalization."
        ),
    )
    active_votes: list[ActiveVoteItem] = Field(
        default_factory=list,
        serialization_alias="votes",
        description="Latest resolved vote per reviewer (append-only latest-wins).",
    )
    submitted_at: AwareDatetime = Field(
        description="ISO-8601 timestamp of when the submission was originally created.",
    )


class TaskListResponse(BaseModel):
    """
    Paginated list of review tasks available to the requesting reviewer.

    Winnow is the Governance Authority: it filters tasks based on the
    reviewer's trust level and role, returning only those the reviewer is
    eligible to process.
    """

    tasks: list[TaskItem] = Field(
        description="Page of task items eligible for review by the requesting user.",
    )
    total: int = Field(
        ge=0,
        description="Total number of eligible tasks across all pages.",
    )
    page: int = Field(
        ge=1,
        description="Current page number (1-based).",
    )
    per_page: int = Field(
        ge=1,
        description="Maximum number of tasks per page.",
    )
