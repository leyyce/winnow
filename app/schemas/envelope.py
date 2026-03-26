"""
Envelope schema — stable outer structure for all Winnow submission requests.

Implements the Envelope Pattern: every POST /api/v1/submissions request wraps
project-specific domain data inside a strictly-typed, project-agnostic outer
structure. The `payload` field is intentionally kept as `dict[str, Any]` so the
envelope remains decoupled from any single project's data shape; project-specific
Stage 1 validation is deferred to the registry-resolved Pydantic schema.

Refinement Sprint changes:
* `submission_type` renamed to `entity_type` — aligns with domain terminology.
* `entity_id` and `measurement_id` added to `SubmissionMetadata` — these are the
  identity triplet fields that, together with `project_id`, uniquely identify and
  version a domain measurement.  They were previously embedded in project-specific
  payloads (e.g. `tree_id` in `TreePayload`) which violated DRY and made it
  impossible to enforce the triplet contract at the envelope level.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class SubmissionMetadata(BaseModel):
    """Stable envelope header present on every request regardless of project."""

    project_id: str = Field(
        min_length=1,
        description="Registered project identifier, e.g. 'tree-app'.",
    )
    submission_id: UUID = Field(
        description="Client-generated UUID — used for idempotency checks.",
    )
    entity_type: str = Field(
        min_length=1,
        description=(
            "Submission variant within the project, e.g. 'tree'. "
            "Must match one of the project's configured valid_entity_types."
        ),
    )
    entity_id: UUID = Field(
        description=(
            "UUID of the domain entity in the client system "
            "(e.g. the tree being measured). Part of the identity triplet."
        ),
    )
    measurement_id: UUID = Field(
        description=(
            "UUID of the specific measurement event in the client system. "
            "Part of the identity triplet."
        ),
    )
    submitted_at: AwareDatetime = Field(
        description="ISO-8601 timestamp (with timezone) of when the client built the envelope.",
    )
    client_version: str | None = Field(
        default=None,
        description="Semver string of the calling client. Used to detect outdated integrations.",
    )


class UserContext(BaseModel):
    """
    User metadata sent on every request ('Data on the Wire' pattern).

    Because Laravel and Winnow maintain separate databases, the current user
    state is embedded in every request rather than fetched from a shared source.
    The trust_level here serves as the Tₙ scoring input (Stage 4 input).
    """

    user_id: UUID = Field(description="Stable user identifier from the client system.")
    username: str = Field(
        min_length=1,
        description="Human-readable username; stored for audit purposes.",
    )
    role: str = Field(
        min_length=1,
        description="User role in the client application. Governs task eligibility. Project-specific.",
    )
    trust_level: int = Field(
        ge=0,
        description="Current trust score as known by the client at request time. Scale is project-specific.",
    )
    account_created_at: AwareDatetime = Field(
        description="ISO-8601 timestamp of account creation; changes only once.",
    )
    account_updated_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "ISO-8601 timestamp of the last account update.  Optional — when "
            "omitted the scoring service falls back to account_created_at."
        ),
    )
    custom_data: dict | None = Field(
        default=None,
        description="Project-specific user metadata forwarded to the user snapshot.",
    )


class SubmissionEnvelope(BaseModel):
    """
    Top-level request body for POST /api/v1/submissions.

    The envelope separates three concerns:
    - `metadata`     — routing, idempotency, and entity identity (always validated here).
    - `user_context` — user snapshot for scoring and governance (always validated here).
    - `payload`      — raw domain data; accepted as a generic dict and validated
                       separately by the project-specific Pydantic schema (Stage 1).
    """

    metadata: SubmissionMetadata
    user_context: UserContext
    payload: dict[str, Any] = Field(
        description=(
            "Project-specific domain data. Accepted as raw JSON at the envelope level; "
            "validated against the registry-resolved PayloadSchema during Stage 1."
        ),
    )
