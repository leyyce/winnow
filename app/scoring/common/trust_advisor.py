"""
TrustAdvisor — Stage 4 output component (Trust Evaluation & Advisory).

Computes a per-submission trust_adjustment delta after the client sends a
finalization signal with the ground-truth outcome. The advisor uses Winnow's
own submission history to derive user reliability metrics; no user table in
Winnow is required (Rule 5: Domain Ownership).

All reward/penalty magnitudes and trust-scale bounds are project-specific and
injected at construction time via TrustAdvisorConfig (Rule 3: Configuration
is King).
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TrustAdvisorConfig:
    """
    Project-specific configuration for the Trust Advisor.
    All values are set from the registry — never hardcoded here.
    """

    reward_per_approval: int
    penalty_per_rejection: int      # stored as positive int; applied as negative delta
    streak_bonus: int               # extra delta awarded when consecutive_approvals ≥ streak_threshold
    streak_threshold: int           # number of consecutive approvals needed to trigger the bonus
    min_trust: int                  # project-configured lower bound (returned to client for clamping)
    max_trust: int                  # project-configured upper bound (returned to client for clamping)


@dataclass(frozen=True)
class UserSubmissionStats:
    """User reliability metrics derived from Winnow's own submissions table."""

    total_finalized: int
    total_approved: int
    total_rejected: int
    consecutive_approvals: int      # current approval streak length


@dataclass(frozen=True)
class TrustAdjustment:
    """
    Trust adjustment recommendation returned to the client after finalization.

    The client applies the delta atomically:
        new_level = CLAMP(trust_level + recommended_delta, project_min_trust, project_max_trust)

    Winnow advises; the client decides whether to apply the recommendation.
    """

    user_id: UUID
    recommended_delta: int          # positive = reward, negative = penalty
    reason: str
    current_trust_level: int        # as received on the wire at submission time
    project_min_trust: int
    project_max_trust: int


class TrustAdvisor:
    """
    Computes trust_adjustment deltas based on ground-truth finalization outcomes.

    Reward/penalty rules and trust-scale bounds are project-specific and injected
    via TrustAdvisorConfig. The advisor never writes to any user table.
    """

    def __init__(self, config: TrustAdvisorConfig) -> None:
        self._config = config

    def compute_adjustment(
        self,
        user_id: UUID,
        current_trust_level: int,
        final_status: str,
        user_history: UserSubmissionStats,
    ) -> TrustAdjustment:
        """
        Derive a trust delta from the finalization outcome and user history.

        Args:
            user_id: Stable user identifier from the client.
            current_trust_level: Trust level received on the wire at submission time.
            final_status: Ground-truth outcome — 'approved' or 'rejected'.
            user_history: Reliability metrics from Winnow's submissions table.

        Returns:
            A TrustAdjustment recommendation (Winnow advises, client decides).
        """
        cfg = self._config

        if final_status == "approved":
            delta = cfg.reward_per_approval
            reason = "Submission approved"
            if user_history.consecutive_approvals >= cfg.streak_threshold:
                delta += cfg.streak_bonus
                reason = (
                    f"{user_history.consecutive_approvals} consecutive approvals "
                    f"(streak bonus of +{cfg.streak_bonus} included)"
                )
        elif final_status == "rejected":
            delta = -cfg.penalty_per_rejection
            reason = "Submission rejected"
        else:
            raise ValueError(
                f"Unknown final_status '{final_status}'; "
                f"expected 'approved' or 'rejected'"
            )

        return TrustAdjustment(
            user_id=user_id,
            recommended_delta=delta,
            reason=reason,
            current_trust_level=current_trust_level,
            project_min_trust=cfg.min_trust,
            project_max_trust=cfg.max_trust,
        )
