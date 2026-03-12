"""
DistanceFactorRule (Aₙ) — rewards physically measured step-length / distance
over estimated values.

    Aₙ = measured_score   if step_length_measured is True
    Aₙ = estimated_score  otherwise

Both score values are project-specific and injected at construction time.
The design document notes its example values should be adjusted based on
empirical evidence — they are not hardcoded in this module.
"""
from __future__ import annotations

from app.schemas.envelope import UserContext
from app.schemas.projects.trees import TreePayload
from app.scoring.base import RuleResult, ScoringRule


class DistanceFactorRule(ScoringRule[TreePayload]):
    """
    Awards a higher score when the submitter physically measured the step
    length rather than estimating it. Both scores are injected from config.
    """

    def __init__(
        self,
        *,
        weight: float,
        measured_score: float,
        estimated_score: float,
    ) -> None:
        if not (0.0 <= measured_score <= 1.0):
            raise ValueError("measured_score must be in [0, 1]")
        if not (0.0 <= estimated_score <= 1.0):
            raise ValueError("estimated_score must be in [0, 1]")
        self._weight = weight
        self._measured_score = measured_score
        self._estimated_score = estimated_score

    @property
    def name(self) -> str:
        return "distance_factor"

    @property
    def weight(self) -> float:
        return self._weight

    @property
    def payload_type(self) -> type[TreePayload]:
        return TreePayload

    def _evaluate(self, payload: TreePayload, context: UserContext) -> RuleResult:
        if payload.step_length_measured:
            score = self._measured_score
            detail = f"step length measured → Aₙ={score}"
        else:
            score = self._estimated_score
            detail = f"step length estimated → Aₙ={score}"
        return RuleResult(rule_name=self.name, score=score, details=detail)
