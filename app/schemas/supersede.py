"""
Supersede request and response schemas.

These models define the shape of data exchanged during the PATCH
/api/v1/submissions/{id}/supersede lifecycle step, where the client
signals that an old submission has been replaced by a corrected one.

Key constraint: this endpoint ONLY accepts ``status="superseded"``.
Approved/rejected transitions are handled automatically by the Governance
Engine vote-threshold logic (see voting_service.py). This design enforces
a clean separation between automated governance finalization and explicit
client-driven supersede actions.

References
----------
* API contract:    docs/architecture/03_api_contracts.md §3b
* Immutable submissions: docs/architecture/02_architecture_patterns.md §4
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class SupersedeRequest(BaseModel):
    """
    Body sent by the client when marking a submission as superseded.

    A submission is superseded when the submitter corrects their domain data
    in the client system (e.g., fixes a tree measurement). The client sends a
    brand-new submission (with a new UUID) and then calls PATCH …/supersede to
    explicitly retire the old one, preserving a complete audit trail.

    ``status`` is a fixed Literal — the validator rejects any other value,
    preventing accidental mis-use of this endpoint to approve or reject.
    """

    status: Literal["superseded"] = Field(
        description=(
            "Must be exactly 'superseded'. Any other value is rejected with 422. "
            "Use the voting endpoint to trigger approved/rejected transitions."
        ),
    )
    superseded_by: UUID = Field(
        description=(
            "UUID of the newer submission that replaces this one. "
            "Must be a valid UUID v4 as issued by the client system."
        ),
    )


class SupersedeResponse(BaseModel):
    """
    Confirmation returned after PATCH /api/v1/submissions/{id}/supersede.

    Echoes the submission that was retired and the ID of its replacement
    so the client can update its local state atomically.
    """

    submission_id: UUID = Field(
        description="The submission that has been marked as superseded.",
    )
    status: Literal["superseded"] = Field(
        description="Confirmed lifecycle state — always 'superseded' on this response.",
    )
    superseded_by: UUID = Field(
        description="UUID of the replacement submission supplied in the request.",
    )
    updated_at: AwareDatetime = Field(
        description="ISO-8601 timestamp (UTC) of when Winnow processed the supersede request.",
    )
