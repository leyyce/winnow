"""
CommentFactorRule (Kₙ) — applies a penalty when the submitter adds comments
that signal measurement uncertainty.

    P_sum = I_mess × measurement_penalty + n_mand × photo_penalty_per_photo
    Kₙ    = max(0, 1 − P_sum)

where:
    I_mess  ∈ {0, 1}  — 1 if a comment is attached to the measurement note
    n_mand  ∈ ℕ       — number of mandatory-photo notes present

Penalty magnitudes are project-specific and injected at construction time.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.envelope import UserContext
from app.schemas.projects.trees import TreePayload
from app.scoring.base import RuleResult, ScoringRule


class CommentFactorRule(ScoringRule):
    """
    Reduces the score when submitter-supplied comments indicate uncertainty.
    All penalty values are injected from project registry configuration.
    """

    def __init__(
        self,
        *,
        weight: float,
        measurement_penalty: float,
        photo_penalty_per_photo: float,
    ) -> None:
        if not (0.0 <= measurement_penalty <= 1.0):
            raise ValueError("measurement_penalty must be in [0, 1]")
        if not (0.0 <= photo_penalty_per_photo <= 1.0):
            raise ValueError("photo_penalty_per_photo must be in [0, 1]")
        self._weight = weight
        self._measurement_penalty = measurement_penalty
        self._photo_penalty_per_photo = photo_penalty_per_photo

    @property
    def name(self) -> str:
        return "comment_factor"

    @property
    def weight(self) -> float:
        return self._weight

    def evaluate(self, payload: BaseModel, context: UserContext) -> RuleResult:
        assert isinstance(payload, TreePayload), f"Expected TreePayload, got {type(payload)}"

        i_mess = 1 if payload.measurement.note else 0
        n_mand = sum(1 for p in payload.photos if p.note)

        p_sum = i_mess * self._measurement_penalty + n_mand * self._photo_penalty_per_photo
        score = max(0.0, 1.0 - p_sum)

        return RuleResult(
            rule_name=self.name,
            score=score,
            details=(
                f"measurement_note={bool(i_mess)}, photo_notes={n_mand}, "
                f"P_sum={p_sum:.3f} → Kₙ={score:.4f}"
            ),
        )
