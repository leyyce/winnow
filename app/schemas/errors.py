"""
RFC 7807 Problem Details schema for all Winnow error responses.

Every non-2xx response from the Winnow API is serialised as a ``ProblemDetail``
object so clients have a consistent, machine-readable error shape to parse.

References
----------
* RFC 7807: https://www.rfc-editor.org/rfc/rfc7807
* API contract: docs/architecture/03_api_contracts.md §4
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FieldError(BaseModel):
    """Per-field validation error detail embedded inside a ``ProblemDetail``."""

    field: str = Field(
        min_length=1,
        description="Dot-separated field path, e.g. 'payload.measurement.height'.",
    )
    message: str = Field(
        min_length=1,
        description="Human-readable description of why the value was rejected.",
    )
    type: str = Field(
        min_length=1,
        description="Machine-readable error category, e.g. 'value_error', 'missing'.",
    )


class ProblemDetail(BaseModel):
    """
    RFC 7807 Problem Details object returned for all error responses.

    ``type`` is a URI reference that identifies the problem type.
    ``instance`` is a URI reference that identifies the specific occurrence.
    ``errors`` carries per-field details when a validation error produces
    multiple addressable failures.
    """

    type: str = Field(
        min_length=1,
        description="URI identifying the problem type, e.g. '/errors/validation-error'.",
    )
    title: str = Field(
        min_length=1,
        description="Short, human-readable summary of the problem type.",
    )
    status: int = Field(
        ge=100,
        le=599,
        description="HTTP status code.",
    )
    detail: str = Field(
        min_length=1,
        description="Human-readable explanation specific to this occurrence.",
    )
    instance: str = Field(
        default="",
        description="URI reference identifying the specific occurrence of the problem.",
    )
    errors: list[FieldError] = Field(
        default_factory=list,
        description="Per-field error details; populated for validation failures.",
    )
