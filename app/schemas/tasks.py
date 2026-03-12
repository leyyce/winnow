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

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.results import RequiredValidations


class TaskItem(BaseModel):
    """
    A single pending submission item in the reviewer task queue.

    Contains enough context for the client to render the task card and
    determine routing without a separate round-trip to the scoring endpoint.
    """

    submission_id: UUID = Field(
        description="UUID of the pending submission.",
    )
    project_id: str = Field(
        min_length=1,
        description="Project this submission belongs to.",
    )
    submission_type: str = Field(
        min_length=1,
        description="Submission variant within the project, e.g. 'tree_measurement'.",
    )
    confidence_score: float = Field(
        ge=0.0,
        le=100.0,
        description="Confidence Score computed at submission time.",
    )
    review_tier: str = Field(
        min_length=1,
        description="Review tier label, e.g. 'peer_review', 'expert_review'.",
    )
    required_validations: RequiredValidations = Field(
        description="Governance Target State — review requirements for this submission.",
    )
    submitted_at: datetime = Field(
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
