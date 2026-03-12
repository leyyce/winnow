"""
TreeProjectBuilder — ProjectBuilder implementation for the 'tree-app' project.

This module is the single authoritative "Composer" that wires together:
  - Stage 1 payload schema  (app.schemas.projects.trees)
  - Stage 2/4-input scoring rules  (app.scoring.projects.trees.*)
  - Stage 4-output trust advisor  (app.scoring.common.trust_advisor)
  - Governance policy  (app.governance.projects.trees)

ALL numeric parameters (weights, thresholds, trust scales, penalty values)
are defined here and injected into rule/policy constructors.
No magic numbers live inside rule or policy implementations (Rule 3).
"""
from __future__ import annotations

from app.governance.projects.trees import GovernanceTier, TreeGovernancePolicy
from app.registry.base import ProjectBuilder
from app.registry.manager import ProjectRegistryEntry
from app.schemas.projects.trees import TreePayload
from app.schemas.results import ThresholdConfig
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
        # Example weights from the design document; tune based on empirical data.
        W_HEIGHT = 0.20
        W_DISTANCE = 0.20
        W_TRUST = 0.25
        W_COMMENT = 0.05
        W_PLAUSIBILITY = 0.30

        # ── Trust-level scale ────────────────────────────────────────────────
        TRUST_MID = 50   # TL at which Tₙ = 0.5 (user considered trustworthy from here)
        TRUST_MAX = 100  # TL at which Tₙ = 1.0

        rules: list[ScoringRule] = [
            HeightFactorRule(
                weight=W_HEIGHT,
                h_max=72.0,              # maximum plausible tree height (m)
            ),
            DistanceFactorRule(
                weight=W_DISTANCE,
                measured_score=1.0,      # full score when step length was physically measured
                estimated_score=0.4,     # reduced score when step length was estimated
            ),
            TrustLevelRule(
                weight=W_TRUST,
                trust_level_mid=TRUST_MID,
                trust_level_max=TRUST_MAX,
            ),
            CommentFactorRule(
                weight=W_COMMENT,
                measurement_penalty=0.6,      # penalty when a measurement note is present
                photo_penalty_per_photo=0.2,  # penalty per photo note present
            ),
            PlausibilityFactorRule(
                weight=W_PLAUSIBILITY,
                alpha_height=0.4,
                alpha_inclination=0.3,
                alpha_trunk_diameter=0.3,
            ),
        ]

        # ── Advisory thresholds for client-side routing ──────────────────────
        thresholds = ThresholdConfig(auto_approve_min=80, manual_review_min=50)

        # ── Stage 4-output: Trust Advisor ────────────────────────────────────
        trust_advisor = TrustAdvisor(
            TrustAdvisorConfig(
                reward_per_approval=1,
                penalty_per_rejection=3,
                streak_bonus=2,
                streak_threshold=5,
                min_trust=0,
                max_trust=TRUST_MAX,
            )
        )

        # ── Governance tiers (sorted automatically, highest threshold first) ─
        governance_policy = TreeGovernancePolicy(
            tiers=[
                GovernanceTier(
                    score_threshold=80.0,
                    review_tier="peer_review",
                    min_validators=1,
                    required_min_trust=30,
                    required_role=None,
                ),
                GovernanceTier(
                    score_threshold=50.0,
                    review_tier="community_review",
                    min_validators=2,
                    required_min_trust=50,
                    required_role=None,
                ),
                GovernanceTier(
                    score_threshold=0.0,
                    review_tier="expert_review",
                    min_validators=1,
                    required_min_trust=75,
                    required_role="expert",
                ),
            ]
        )

        return ProjectRegistryEntry(
            payload_schema=TreePayload,
            pipeline=ScoringPipeline(rules),
            thresholds=thresholds,
            trust_advisor=trust_advisor,
            governance_policy=governance_policy,
        )
