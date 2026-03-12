"""
HeightFactorRule (Hₙ) — normalises measured tree height against a project-
configured maximum plausible height.

    Hₙ = min(h / h_max, 1.0),  Hₙ ∈ [0, 1]

A higher score indicates the submitted height is closer to the project-
configured maximum, implying reduced relative measurement error (taller trees
have smaller relative measurement uncertainty). h_max is project-specific and
injected at construction time — never hardcoded in this module.
"""
from __future__ import annotations

from app.schemas.envelope import UserContext
from app.schemas.projects.trees import TreePayload
from app.scoring.base import RuleResult, ScoringRule


class HeightFactorRule(ScoringRule[TreePayload]):
    """
    Normalises submitted tree height against the project-configured h_max.
    All parameters are injected from project registry configuration.
    """

    def __init__(self, *, weight: float, h_max: float) -> None:
        if h_max <= 0:
            raise ValueError("h_max must be > 0")
        self._weight = weight
        self._h_max = h_max

    @property
    def name(self) -> str:
        return "height_factor"

    @property
    def weight(self) -> float:
        return self._weight

    @property
    def payload_type(self) -> type[TreePayload]:
        return TreePayload

    def _evaluate(self, payload: TreePayload, context: UserContext) -> RuleResult:
        h = payload.measurement.height
        score = min(h / self._h_max, 1.0)
        return RuleResult(
            rule_name=self.name,
            score=score,
            details=f"h={h}m, h_max={self._h_max}m → Hₙ={score:.4f}",
        )
