"""
PlausibilityFactorRule (Pₙ) — scores how well the submitted measurements
align with the species' historically observed distribution.

For each core measurement x ∈ {height (h), inclination (i), trunk_diameter (d)}:

    D_x = |x − μ_x| / σ_x        (normalised z-score deviation)
    Pₙ  = max(0, 1 − (α_h·D_h + α_i·D_i + α_d·D_d))

When σ_x = 0 (insufficient historical data), D_x is treated as 0 so no
penalty is applied for that dimension.

μ_x and σ_x are supplied by the client in the payload (SpeciesStats), since
Laravel owns species data (Rule 5: Domain Ownership). Sensitivity parameters
α_h, α_i, α_d are project-specific and injected at construction time.
"""
from __future__ import annotations

from app.schemas.envelope import UserContext
from app.schemas.projects.trees import TreePayload
from app.scoring.base import RuleResult, ScoringRule


class PlausibilityFactorRule(ScoringRule[TreePayload]):
    """
    Computes the plausibility score for a tree measurement relative to the
    species' historical distribution. Sensitivity parameters are injected
    from project registry configuration.
    """

    def __init__(
        self,
        *,
        weight: float,
        alpha_height: float,
        alpha_inclination: float,
        alpha_trunk_diameter: float,
    ) -> None:
        self._weight = weight
        self._alpha_h = alpha_height
        self._alpha_i = alpha_inclination
        self._alpha_d = alpha_trunk_diameter

    @property
    def name(self) -> str:
        return "plausibility_factor"

    @property
    def weight(self) -> float:
        return self._weight

    @property
    def payload_type(self) -> type[TreePayload]:
        return TreePayload

    @staticmethod
    def _normalised_deviation(value: float, mean: float, std: float) -> float:
        """Return |value − mean| / std, or 0.0 when std ≤ 0 (insufficient data)."""
        if std <= 0.0:
            return 0.0
        return abs(value - mean) / std

    def _evaluate(self, payload: TreePayload, context: UserContext) -> RuleResult:
        m = payload.measurement
        s = payload.species_stats
        d_h = self._normalised_deviation(m.height, s.mean_height, s.std_height)
        d_i = self._normalised_deviation(float(m.inclination), s.mean_inclination, s.std_inclination)
        d_d = self._normalised_deviation(float(m.trunk_diameter), s.mean_trunk_diameter, s.std_trunk_diameter)
        score = max(0.0, 1.0 - (self._alpha_h * d_h + self._alpha_i * d_i + self._alpha_d * d_d))
        return RuleResult(
            rule_name=self.name,
            score=score,
            details=f"D_h={d_h:.3f}, D_i={d_i:.3f}, D_d={d_d:.3f} → Pₙ={score:.4f}",
        )
