"""
ScoringPipeline — aggregates weighted RuleResult scores into a Confidence Score.

The pipeline is project-agnostic: it iterates over whatever ScoringRule
instances the registry supplies and accumulates their weighted contributions.

    CS = 100 × Σ (rule.score × rule.weight)

Weight contract
---------------
For a non-empty pipeline the weights of all rules must sum to exactly 1.0
(within floating-point tolerance).  A misconfigured pipeline raises
``ValueError`` at construction time, preventing a silent overflow of the
Confidence Score beyond the [0, 100] schema constraint.
"""
from __future__ import annotations

import math
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
    Raises ``ValueError`` if the supplied rules are non-empty and their weights
    do not sum to 1.0 (tolerance: 1 × 10⁻⁶).
    """

    _WEIGHT_TOLERANCE = 1e-6

    def __init__(self, rules: list[ScoringRule]) -> None:
        if not rules:
            raise ValueError("A ScoringPipeline requires at least one ScoringRule.")
        for rule in rules:
            if not (0.0 <= rule.weight <= 1.0):
                raise ValueError(
                    f"Rule '{rule.name}' weight {rule.weight!r} is out of bounds "
                    f"[0.0, 1.0]"
                )

        total_weight = sum(rule.weight for rule in rules)
        if not math.isclose(total_weight, 1.0, abs_tol=self._WEIGHT_TOLERANCE):
            raise ValueError(
                f"ScoringPipeline rule weights must sum to 1.0, "
                f"got {total_weight:.8f} "
                f"(rules: {[r.name for r in rules]})"
            )
        self._rules = rules

    @property
    def rules(self) -> tuple[ScoringRule, ...]:
        """Read-only ordered view of the pipeline's rules."""
        return tuple(self._rules)

    def run(self, payload: BaseModel, context: UserContext) -> PipelineResult:
        """Execute all rules and return the aggregated PipelineResult."""
        breakdown: list[RuleResult] = []
        total: float = 0.0

        for rule in self._rules:
            result = rule.evaluate(payload, context)
            breakdown.append(result)
            total += result.score * rule.weight * 100.0

        return PipelineResult(total_score=round(total, 4), breakdown=breakdown)
