"""
TreeProjectBuilder — ProjectBuilder implementation for the 'tree-app' project.

This module is the single authoritative "Composer" that wires together:
  - Stage 1 payload schema  (app.schemas.projects.trees)
  - Stage 2/4-input scoring rules  (app.scoring.projects.trees.*)
  - Stage 4-output trust advisor  (app.scoring.common.trust_advisor)
  - Governance policy  (app.governance.projects.trees)

Sprint 5 upgrade: GovernanceTier now uses ``role_configs`` / ``default_config``
/ ``blocked_roles`` instead of the flat ``role_weights`` + ``required_min_trust``
pattern.  This enables per-role trust minimums and absolute role blocking.

ALL numeric parameters (weights, thresholds, trust scales, penalty values)
are defined here and injected into rule/policy constructors.
No magic numbers live inside rule or policy implementations (Rule 3).

References
----------
* Rule 3: Configuration is King
* Rule 7: Iterative Implementation
"""
from __future__ import annotations

from app.governance.base import GovernanceTier, GovernancePolicy
from app.registry.base import ProjectBuilder
from app.registry.manager import ProjectRegistryEntry
from app.schemas.projects.trees import TreePayload
from app.schemas.results import RoleConfig, ThresholdConfig
from app.scoring.base import ScoringRule
from app.scoring.common.trust_advisor import TrustAdvisor, TrustAdvisorConfig
from app.scoring.common.trust_level import TrustLevelRule
from app.scoring.pipeline import ScoringPipeline
from app.scoring.projects.trees.comment_factor import CommentFactorRule
from app.scoring.projects.trees.distance_factor import DistanceFactorRule
from app.scoring.projects.trees.height_factor import HeightFactorRule
from app.scoring.projects.trees.plausibility_factor import PlausibilityFactorRule


class TreeProjectBuilder(ProjectBuilder):
    """
    Composes the full ProjectRegistryEntry for the 'tree-app' project.

    Add a new project by creating a sibling module with its own
    ``ProjectBuilder`` subclass — this module never needs to change.
    """

    @property
    def project_id(self) -> str:
        return "tree-app"

    def build(self) -> ProjectRegistryEntry:
        # ── Scoring weights ──────────────────────────────────────────────────
        w_height = 0.20
        w_distance = 0.20
        w_trust = 0.25
        w_comment = 0.05
        w_plausibility = 0.30

        # ── Trust-level scale ────────────────────────────────────────────────
        trust_mid = 50
        trust_max = 100

        rules: list[ScoringRule] = [
            HeightFactorRule(weight=w_height, h_max=72.0),
            DistanceFactorRule(
                weight=w_distance,
                measured_score=1.0,
                estimated_score=0.4,
            ),
            TrustLevelRule(
                weight=w_trust,
                trust_level_mid=trust_mid,
                trust_level_max=trust_max,
            ),
            CommentFactorRule(
                weight=w_comment,
                measurement_penalty=0.6,
                photo_penalty_per_photo=0.2,
            ),
            PlausibilityFactorRule(
                weight=w_plausibility,
                alpha_height=0.4,
                alpha_inclination=0.3,
                alpha_trunk_diameter=0.3,
            ),
        ]

        # ── Advisory thresholds for client-side routing ──────────────────────
        thresholds = ThresholdConfig(auto_approve_min=80, manual_review_min=20)

        # ── Stage 4-output: Trust Advisor ────────────────────────────────────
        trust_advisor = TrustAdvisor(
            TrustAdvisorConfig(
                reward_per_approval=1,
                penalty_per_rejection=3,
                streak_bonus=2,
                streak_threshold=3,
                max_trust=trust_max,
            )
        )

        # ── Governance tiers ─────────────────────────────────────────────────
        # Sprint 5: role_configs / default_config / blocked_roles model.
        #
        # Tier semantics (illustrative examples per Rule 2 — not hardcoded):
        #   peer_review:     score >= 80 — 1 vote from any eligible reviewer
        #   community_review: score >= 50 — 2 citizen votes OR 1 expert vote
        #   expert_review:   score  < 50 — only experts with high trust
        #
        # blocked_roles prevents 'guest' and 'banned' users from voting in any tier.

        # Shared blocked roles for all tiers
        BLOCKED = ["guest", "banned"]

        governance_policy = GovernancePolicy(
            tiers=[
                GovernanceTier(
                    confidence_threshold=75.0,
                    review_tier="peer_review",
                    vote_threshold=1,
                    role_configs={
                        "expert": RoleConfig(weight=1, min_trust=0),
                        "citizen": RoleConfig(weight=1, min_trust=30),
                    },
                    default_config=RoleConfig(weight=1, min_trust=30),
                    blocked_roles=BLOCKED,
                ),
                GovernanceTier(
                    confidence_threshold=50.0,
                    review_tier="community_review",
                    vote_threshold=2,
                    role_configs={
                        "expert": RoleConfig(weight=2, min_trust=0),
                        "citizen": RoleConfig(weight=1, min_trust=50),
                    },
                    default_config=RoleConfig(weight=1, min_trust=50),
                    blocked_roles=BLOCKED,
                ),
                GovernanceTier(
                    confidence_threshold=0.0,
                    review_tier="expert_review",
                    vote_threshold=3,
                    role_configs={
                        "expert": RoleConfig(weight=3, min_trust=75),
                    },
                    # default_config=None,
                    blocked_roles=BLOCKED,
                ),
            ]
        )

        return ProjectRegistryEntry(
            payload_schema=TreePayload,
            pipeline=ScoringPipeline(rules),
            thresholds=thresholds,
            trust_advisor=trust_advisor,
            governance_policy=governance_policy,
            valid_entity_types=["tree"],
            webhook_url="http://localhost:8080/api/winnow/webhook",
        )
