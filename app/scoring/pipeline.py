"""
ScoringPipeline — aggregates weighted RuleResult scores into a Confidence Score.

The pipeline is project-agnostic: it iterates over whatever ScoringRule
instances the registry supplies and accumulates their weighted contributions.

    CS = 100 × Σ (rule.score × rule.weight)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from app.schemas.envelope import UserContext
from app.scoring.base import RuleResult, ScoringRule


@dataclass
class PipelineResult:
    """Aggregated output produced by a single ScoringPipeline run."""

    total_score: float                      # Confidence Score ∈ [0.0, 100.0]
    breakdown: list[RuleResult] = field(default_factory=list)


class ScoringPipeline:
    """
    Iterates over an ordered list of ScoringRule instances, calls evaluate() on
    each, and accumulates weighted contributions into a final Confidence Score.

    Weights are project-specific and injected via the registry at construction.
    """

    def __init__(self, rules: list[ScoringRule]) -> None:
        self._rules = rules

    def run(self, payload: BaseModel, context: UserContext) -> PipelineResult:
        """Execute all rules and return the aggregated PipelineResult."""
        breakdown: list[RuleResult] = []
        total: float = 0.0

        for rule in self._rules:
            result = rule.evaluate(payload, context)
            breakdown.append(result)
            total += result.score * rule.weight * 100.0

        return PipelineResult(total_score=round(total, 4), breakdown=breakdown)
