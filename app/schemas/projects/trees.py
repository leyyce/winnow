"""
TreePayload — Stage 1 validation schema for tree-measurement submissions.

Enforces structural completeness, correct types, and physical feasibility bounds
(e.g. height > 0, inclination ∈ [0°, 90°]) via Pydantic Field constraints.
Project-specific *scoring* parameters (e.g. maximum plausible height for Hₙ,
species-distribution statistics for Pₙ) are NOT hardcoded here; they are either
supplied by the client inline or live in the project's registry configuration.

Follows the Envelope Pattern: this schema is resolved by the registry at runtime
and applied to the raw `payload` dict before any scoring occurs (Stage 1).
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TreePhotoPayload(BaseModel):
    """A single photo attached to the measurement."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(
        min_length=1,
        description="Relative or absolute path/URL identifying the stored photo.",
    )
    note: str | None = Field(
        default=None,
        description="Optional free-text note attached to this photo.",
    )


class TreeMeasurementPayload(BaseModel):
    """Core measurement values collected by the submitter."""

    model_config = ConfigDict(frozen=True)

    height: float = Field(
        gt=0.0,
        description="Measured tree height in metres (must be positive).",
    )
    inclination: int = Field(
        ge=0,
        le=90,
        description="Trunk inclination in degrees from vertical (0°–90°).",
    )
    trunk_diameter: int = Field(
        gt=0,
        description="Trunk diameter at breast height (DBH) in centimetres.",
    )
    note: str | None = Field(
        default=None,
        description="Optional free-text comment about the measurement.",
    )


class SpeciesStats(BaseModel):
    """
    Historical species statistics forwarded by the client for Pₙ scoring.

    Winnow is stateless with respect to species data — the client (Laravel)
    owns species records and supplies the relevant μ/σ values per submission.
    When std values are zero (insufficient historical data), the plausibility
    rule treats the deviation as zero (no penalty applied).
    """

    model_config = ConfigDict(frozen=True)

    mean_height: float = Field(gt=0.0, description="Mean height (m) for this species.")
    std_height: float = Field(ge=0.0, description="Std deviation of height (m) for this species.")
    mean_inclination: float = Field(ge=0.0, le=90.0, description="Mean inclination (°) for this species.")
    std_inclination: float = Field(ge=0.0, description="Std deviation of inclination (°) for this species.")
    mean_trunk_diameter: float = Field(gt=0.0, description="Mean DBH (cm) for this species.")
    std_trunk_diameter: float = Field(ge=0.0, description="Std deviation of DBH (cm) for this species.")


class TreePayload(BaseModel):
    """
    Full payload for a tree-measurement submission.

    Stage 1 validation enforces structural correctness and physical feasibility
    only. Statistical plausibility (Pₙ) is evaluated during Stage 2 scoring.

    Note: ``tree_id`` (entity identity) has been removed from this payload.
    It is now carried as ``entity_id`` in ``SubmissionMetadata``, which is the
    canonical first-class location for all identity-triplet fields.
    """

    model_config = ConfigDict(frozen=True)

    species_id: UUID = Field(
        description="UUID of the tree species in the client system.",
    )
    measurement: TreeMeasurementPayload = Field(
        description="Core measurement data collected by the submitter.",
    )
    photos: list[TreePhotoPayload] = Field(
        min_length=2,
        description="At least two photos are required per measurement (side view + 45° angle).",
    )
    step_length_measured: bool = Field(
        description=(
            "True if the step length / distance to tree was physically measured; "
            "False if estimated. Used by the Aₙ distance factor."
        ),
    )
    species_stats: SpeciesStats = Field(
        description=(
            "Historical species statistics (μ, σ) for height, inclination, and DBH. "
            "Supplied by the client; used by the Pₙ plausibility factor."
        ),
    )

    @model_validator(mode="after")
    def photos_have_unique_paths(self) -> TreePayload:
        """Guard against duplicate photo paths within one submission."""
        paths = [p.path for p in self.photos]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate photo paths are not allowed within a single submission.")
        return self
