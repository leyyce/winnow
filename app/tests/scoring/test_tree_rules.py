"""
Tests for tree-project scoring components:
  - HeightFactorRule (Hₙ)
  - DistanceFactorRule (Aₙ)
  - CommentFactorRule (Kₙ)
  - PlausibilityFactorRule (Pₙ)
  - TreePayload Stage 1 validation
  - TreeGovernancePolicy
  - ScoringPipeline integration
"""
from __future__ import annotations

import pytest

from app.governance.projects.trees import GovernanceTier, TreeGovernancePolicy
from app.schemas.projects.trees import SpeciesStats, TreePhotoPayload
from app.schemas.results import RequiredValidations
from app.registry.manager import registry
from app.scoring.pipeline import ScoringPipeline
from app.scoring.projects.trees.comment_factor import CommentFactorRule
from app.scoring.projects.trees.distance_factor import DistanceFactorRule
from app.scoring.projects.trees.height_factor import HeightFactorRule
from app.scoring.projects.trees.plausibility_factor import PlausibilityFactorRule
from app.tests.conftest import _ctx, _default_stats, _payload


# ── TreePayload Stage 1 Validation ────────────────────────────────────────────

class TestTreePayloadValidation:
    def test_valid_payload_parses(self):
        p = _payload()
        assert p.measurement.height == 20.0

    def test_requires_at_least_two_photos(self):
        with pytest.raises(Exception):
            _payload(photos=[TreePhotoPayload(path="only_one.jpg")])

    def test_duplicate_photo_paths_rejected(self):
        with pytest.raises(ValueError, match="Duplicate photo paths"):
            _payload(photos=[
                TreePhotoPayload(path="same.jpg"),
                TreePhotoPayload(path="same.jpg"),
            ])

    def test_zero_height_rejected(self):
        with pytest.raises(Exception):
            _payload(height=0.0)

    def test_negative_height_rejected(self):
        with pytest.raises(Exception):
            _payload(height=-1.0)

    def test_inclination_above_90_rejected(self):
        with pytest.raises(Exception):
            _payload(inclination=91)

    def test_inclination_at_90_accepted(self):
        p = _payload(inclination=90)
        assert p.measurement.inclination == 90

    def test_zero_trunk_diameter_rejected(self):
        with pytest.raises(Exception):
            _payload(trunk_diameter=0)

    def test_three_photos_accepted(self):
        photos = [
            TreePhotoPayload(path="a.jpg"),
            TreePhotoPayload(path="b.jpg"),
            TreePhotoPayload(path="c.jpg"),
        ]
        p = _payload(photos=photos)
        assert len(p.photos) == 3

    def test_optional_measurement_note_accepted(self):
        p = _payload(note="looks uncertain")
        assert p.measurement.note == "looks uncertain"

    def test_optional_photo_note_accepted(self):
        photos = [
            TreePhotoPayload(path="a.jpg", note="blurry"),
            TreePhotoPayload(path="b.jpg"),
        ]
        p = _payload(photos=photos)
        assert p.photos[0].note == "blurry"


# ── HeightFactorRule (Hₙ) ────────────────────────────────────────────────────

class TestHeightFactorRule:
    def setup_method(self):
        self.rule = HeightFactorRule(weight=0.2, h_max=72.0)

    def test_at_h_max_gives_one(self):
        assert self.rule.evaluate(_payload(height=72.0), _ctx()).score == pytest.approx(1.0)

    def test_above_h_max_clamped_to_one(self):
        assert self.rule.evaluate(_payload(height=100.0), _ctx()).score == pytest.approx(1.0)

    def test_half_h_max_gives_half(self):
        assert self.rule.evaluate(_payload(height=36.0), _ctx()).score == pytest.approx(0.5)

    def test_low_height_proportional(self):
        assert self.rule.evaluate(_payload(height=7.2), _ctx()).score == pytest.approx(0.1)

    def test_rule_name(self):
        assert self.rule.name == "height_factor"

    def test_weight(self):
        assert self.rule.weight == 0.2

    def test_details_populated(self):
        result = self.rule.evaluate(_payload(height=36.0), _ctx())
        assert result.details is not None

    def test_invalid_h_max_raises(self):
        with pytest.raises(ValueError):
            HeightFactorRule(weight=0.2, h_max=0.0)

    def test_negative_h_max_raises(self):
        with pytest.raises(ValueError):
            HeightFactorRule(weight=0.2, h_max=-10.0)


# ── DistanceFactorRule (Aₙ) ──────────────────────────────────────────────────

class TestDistanceFactorRule:
    def setup_method(self):
        self.rule = DistanceFactorRule(weight=0.2, measured_score=1.0, estimated_score=0.4)

    def test_measured_gives_measured_score(self):
        result = self.rule.evaluate(_payload(step_length_measured=True), _ctx())
        assert result.score == pytest.approx(1.0)

    def test_estimated_gives_estimated_score(self):
        result = self.rule.evaluate(_payload(step_length_measured=False), _ctx())
        assert result.score == pytest.approx(0.4)

    def test_rule_name(self):
        assert self.rule.name == "distance_factor"

    def test_weight(self):
        assert self.rule.weight == 0.2

    def test_details_populated(self):
        result = self.rule.evaluate(_payload(step_length_measured=True), _ctx())
        assert result.details is not None

    def test_measured_score_above_one_raises(self):
        with pytest.raises(ValueError, match="measured_score"):
            DistanceFactorRule(weight=0.2, measured_score=1.5, estimated_score=0.4)

    def test_estimated_score_negative_raises(self):
        with pytest.raises(ValueError, match="estimated_score"):
            DistanceFactorRule(weight=0.2, measured_score=1.0, estimated_score=-0.1)


# ── CommentFactorRule (Kₙ) ───────────────────────────────────────────────────

class TestCommentFactorRule:
    def setup_method(self):
        self.rule = CommentFactorRule(
            weight=0.05,
            measurement_penalty=0.6,
            photo_penalty_per_photo=0.2,
        )

    def test_no_comments_gives_one(self):
        assert self.rule.evaluate(_payload(note=None), _ctx()).score == pytest.approx(1.0)

    def test_measurement_note_applies_penalty(self):
        assert self.rule.evaluate(_payload(note="uncertain"), _ctx()).score == pytest.approx(0.4)

    def test_one_photo_note_applies_penalty(self):
        photos = [TreePhotoPayload(path="a.jpg", note="blurry"), TreePhotoPayload(path="b.jpg")]
        assert self.rule.evaluate(_payload(photos=photos), _ctx()).score == pytest.approx(0.8)

    def test_two_photo_notes_accumulate(self):
        photos = [TreePhotoPayload(path="a.jpg", note="n1"), TreePhotoPayload(path="b.jpg", note="n2")]
        assert self.rule.evaluate(_payload(photos=photos), _ctx()).score == pytest.approx(0.6)

    def test_measurement_and_photo_penalties_accumulate(self):
        photos = [TreePhotoPayload(path="a.jpg", note="n1"), TreePhotoPayload(path="b.jpg")]
        result = self.rule.evaluate(_payload(note="uncertain", photos=photos), _ctx())
        assert result.score == pytest.approx(max(0.0, 1.0 - 0.6 - 0.2))

    def test_score_floored_at_zero(self):
        photos = [TreePhotoPayload(path=f"{i}.jpg", note="n") for i in range(5)]
        result = self.rule.evaluate(_payload(note="bad", photos=photos), _ctx())
        assert result.score == pytest.approx(0.0)

    def test_rule_name(self):
        assert self.rule.name == "comment_factor"

    def test_invalid_measurement_penalty_raises(self):
        with pytest.raises(ValueError, match="measurement_penalty"):
            CommentFactorRule(weight=0.05, measurement_penalty=1.5, photo_penalty_per_photo=0.2)


# ── PlausibilityFactorRule (Pₙ) ──────────────────────────────────────────────

class TestPlausibilityFactorRule:
    def setup_method(self):
        self.rule = PlausibilityFactorRule(
            weight=0.3,
            alpha_height=0.4,
            alpha_inclination=0.3,
            alpha_trunk_diameter=0.3,
        )

    def test_perfect_match_gives_one(self):
        stats = SpeciesStats(
            mean_height=20.0, std_height=5.0,
            mean_inclination=5.0, std_inclination=2.0,
            mean_trunk_diameter=30.0, std_trunk_diameter=10.0,
        )
        result = self.rule.evaluate(_payload(height=20.0, inclination=5, trunk_diameter=30, species_stats=stats), _ctx())
        assert result.score == pytest.approx(1.0)

    def test_large_deviation_gives_zero(self):
        stats = SpeciesStats(
            mean_height=20.0, std_height=1.0,
            mean_inclination=5.0, std_inclination=1.0,
            mean_trunk_diameter=30.0, std_trunk_diameter=1.0,
        )
        result = self.rule.evaluate(_payload(height=40.0, species_stats=stats), _ctx())
        assert result.score == pytest.approx(0.0)

    def test_zero_std_treated_as_no_deviation(self):
        stats = SpeciesStats(
            mean_height=20.0, std_height=0.0,
            mean_inclination=5.0, std_inclination=0.0,
            mean_trunk_diameter=30.0, std_trunk_diameter=0.0,
        )
        result = self.rule.evaluate(_payload(height=999.0, species_stats=stats), _ctx())
        assert result.score == pytest.approx(1.0)

    def test_score_never_negative(self):
        stats = SpeciesStats(
            mean_height=1.0, std_height=0.1,
            mean_inclination=1.0, std_inclination=0.1,
            mean_trunk_diameter=1.0, std_trunk_diameter=0.1,
        )
        result = self.rule.evaluate(_payload(height=100.0, inclination=90, trunk_diameter=500, species_stats=stats), _ctx())
        assert result.score >= 0.0

    def test_rule_name(self):
        assert self.rule.name == "plausibility_factor"

    def test_details_populated(self):
        result = self.rule.evaluate(_payload(), _ctx())
        assert result.details is not None
        assert "D_h" in result.details


# ── TreeGovernancePolicy ──────────────────────────────────────────────────────

from app.core.exceptions import NotEligibleError
from app.schemas.results import RoleConfig


# Shared helper: build a RequiredValidations using Sprint 5 role_configs model.
def _req(
    threshold_score: int = 1,
    role_configs: dict | None = None,
    default_config: dict | None = None,
    blocked_roles: list | None = None,
    review_tier: str = "peer_review",
) -> RequiredValidations:
    return RequiredValidations(
        threshold_score=threshold_score,
        role_configs=role_configs or {
            "citizen": {"weight": 1, "min_trust": 30},
            "expert": {"weight": 1, "min_trust": 0},
        },
        default_config=default_config or {"weight": 1, "min_trust": 30},
        blocked_roles=blocked_roles or [],
        review_tier=review_tier,
    )


class TestTreeGovernancePolicy:
    def setup_method(self):
        # Sprint 5 GovernanceTier uses role_configs / default_config / blocked_roles.
        # peer_review:     score >= 80 — weight 1 for citizen (min_trust 30) or expert
        # community_review: score >= 50 — expert weight 2, citizen weight 1 (min_trust 50)
        # expert_review:   score >= 0  — only expert (weight 3, min_trust 75)
        BLOCKED = ["guest", "banned"]
        self.policy = TreeGovernancePolicy(tiers=[
            GovernanceTier(
                score_threshold=80.0, review_tier="peer_review",
                threshold_score=1,
                role_configs={
                    "citizen": RoleConfig(weight=1, min_trust=30),
                    "expert": RoleConfig(weight=1, min_trust=0),
                },
                default_config=RoleConfig(weight=1, min_trust=30),
                blocked_roles=BLOCKED,
            ),
            GovernanceTier(
                score_threshold=50.0, review_tier="community_review",
                threshold_score=2,
                role_configs={
                    "citizen": RoleConfig(weight=1, min_trust=50),
                    "expert": RoleConfig(weight=2, min_trust=0),
                },
                default_config=RoleConfig(weight=1, min_trust=50),
                blocked_roles=BLOCKED,
            ),
            GovernanceTier(
                score_threshold=0.0, review_tier="expert_review",
                threshold_score=3,
                role_configs={
                    "expert": RoleConfig(weight=3, min_trust=75),
                },
                default_config=RoleConfig(weight=0, min_trust=9999),
                blocked_roles=BLOCKED,
            ),
        ])

    # ── Tier routing ──────────────────────────────────────────────────────────

    def test_high_score_maps_to_peer_review(self):
        req = self.policy.determine_requirements(85.0, _ctx())
        assert req.review_tier == "peer_review"

    def test_mid_score_maps_to_community_review(self):
        req = self.policy.determine_requirements(65.0, _ctx())
        assert req.review_tier == "community_review"

    def test_low_score_maps_to_expert_review(self):
        req = self.policy.determine_requirements(20.0, _ctx())
        assert req.review_tier == "expert_review"

    def test_at_threshold_matches_tier(self):
        req = self.policy.determine_requirements(80.0, _ctx())
        assert req.review_tier == "peer_review"

    def test_score_just_below_threshold_falls_to_lower_tier(self):
        # 79.9 is just below peer_review threshold → community_review
        req = self.policy.determine_requirements(79.9, _ctx())
        assert req.review_tier == "community_review"

    def test_score_exactly_zero_maps_to_lowest_tier(self):
        req = self.policy.determine_requirements(0.0, _ctx())
        assert req.review_tier == "expert_review"

    def test_tiers_sorted_automatically(self):
        # Pass tiers in wrong order — policy must sort them descending
        policy = TreeGovernancePolicy(tiers=[
            GovernanceTier(
                score_threshold=0.0, review_tier="expert_review",
                threshold_score=3,
                role_configs={"expert": RoleConfig(weight=3, min_trust=75)},
                default_config=RoleConfig(weight=0, min_trust=9999),
            ),
            GovernanceTier(
                score_threshold=80.0, review_tier="peer_review",
                threshold_score=1,
                role_configs={"citizen": RoleConfig(weight=1, min_trust=30)},
                default_config=RoleConfig(weight=1, min_trust=30),
            ),
        ])
        assert policy.determine_requirements(90.0, _ctx()).review_tier == "peer_review"

    def test_empty_tiers_raises(self):
        with pytest.raises(ValueError):
            TreeGovernancePolicy(tiers=[])

    # ── RequiredValidations fields on result ──────────────────────────────────

    def test_determine_requirements_returns_threshold_score(self):
        req = self.policy.determine_requirements(85.0, _ctx())
        assert req.threshold_score == 1

    def test_determine_requirements_returns_role_configs(self):
        req = self.policy.determine_requirements(85.0, _ctx())
        assert "citizen" in req.role_configs
        assert "expert" in req.role_configs
        assert req.role_configs["citizen"].weight == 1
        assert req.role_configs["expert"].weight == 1

    def test_community_review_has_correct_threshold_and_weights(self):
        req = self.policy.determine_requirements(65.0, _ctx())
        assert req.threshold_score == 2
        assert req.role_configs["citizen"].weight == 1
        assert req.role_configs["expert"].weight == 2

    def test_expert_review_citizen_not_in_role_configs(self):
        req = self.policy.determine_requirements(20.0, _ctx())
        # citizen absent from role_configs in expert_review; default_config has weight=0
        assert "citizen" not in req.role_configs

    # ── Reviewer eligibility via get_vote_weight ──────────────────────────────

    def test_eligible_reviewer_passes_trust_and_weight(self):
        # citizen weight=1, min_trust=30 → eligible at trust=30
        req = _req(role_configs={"citizen": {"weight": 1, "min_trust": 30},
                                  "expert": {"weight": 1, "min_trust": 0}})
        weight = self.policy.get_vote_weight(req, "citizen", 30)
        assert weight == 1

    def test_ineligible_reviewer_fails_trust(self):
        req = _req(role_configs={"citizen": {"weight": 1, "min_trust": 30},
                                  "expert": {"weight": 1, "min_trust": 0}})
        with pytest.raises(NotEligibleError):
            self.policy.get_vote_weight(req, "citizen", 29)

    def test_ineligible_reviewer_role_not_in_weights(self):
        # expert_review: citizen absent → falls to default_config weight=0 → ineligible
        req = _req(threshold_score=3,
                   role_configs={"expert": {"weight": 3, "min_trust": 75}},
                   default_config={"weight": 0, "min_trust": 9999},
                   review_tier="expert_review")
        with pytest.raises(NotEligibleError):
            self.policy.get_vote_weight(req, "citizen", 80)

    def test_ineligible_reviewer_role_has_zero_weight(self):
        # Explicitly zero weight → ineligible even with sufficient trust
        req = _req(role_configs={"citizen": {"weight": 0, "min_trust": 0},
                                  "expert": {"weight": 3, "min_trust": 0}})
        with pytest.raises(NotEligibleError):
            self.policy.get_vote_weight(req, "citizen", 80)

    def test_eligible_expert_reviewer_in_expert_review(self):
        req = _req(threshold_score=3,
                   role_configs={"expert": {"weight": 3, "min_trust": 75}},
                   default_config={"weight": 0, "min_trust": 9999},
                   review_tier="expert_review")
        weight = self.policy.get_vote_weight(req, "expert", 75)
        assert weight == 3

    def test_ineligible_reviewer_exactly_one_below_min_trust(self):
        # Boundary: trust=49 < min_trust=50 → ineligible
        req = _req(role_configs={"citizen": {"weight": 1, "min_trust": 50},
                                  "expert": {"weight": 2, "min_trust": 0}})
        with pytest.raises(NotEligibleError):
            self.policy.get_vote_weight(req, "citizen", 49)

    def test_eligible_reviewer_exactly_at_min_trust(self):
        # Boundary: trust=50 == min_trust=50 → eligible
        req = _req(role_configs={"citizen": {"weight": 1, "min_trust": 50},
                                  "expert": {"weight": 2, "min_trust": 0}})
        weight = self.policy.get_vote_weight(req, "citizen", 50)
        assert weight == 1

    def test_unknown_role_falls_to_default_config(self):
        # 'moderator' not in role_configs → falls back to default_config
        req = _req(role_configs={"citizen": {"weight": 1, "min_trust": 0},
                                  "expert": {"weight": 2, "min_trust": 0}},
                   default_config={"weight": 1, "min_trust": 25})
        weight = self.policy.get_vote_weight(req, "moderator", 25)
        assert weight == 1

    def test_blocked_role_raises_regardless_of_trust(self):
        req = _req(blocked_roles=["guest", "banned"])
        with pytest.raises(NotEligibleError):
            self.policy.get_vote_weight(req, "guest", 9999)

    def test_blocked_role_takes_precedence_over_role_configs(self):
        # Even if 'guest' is in role_configs, blocked_roles wins
        req = _req(role_configs={"guest": {"weight": 5, "min_trust": 0}},
                   blocked_roles=["guest"])
        with pytest.raises(NotEligibleError):
            self.policy.get_vote_weight(req, "guest", 100)


# ── ScoringPipeline integration ───────────────────────────────────────────────

class TestScoringPipelineIntegration:
    def test_all_perfect_scores_give_100(self):
        rules = [
            HeightFactorRule(weight=0.5, h_max=72.0),
            DistanceFactorRule(weight=0.5, measured_score=1.0, estimated_score=0.4),
        ]
        pipeline = ScoringPipeline(rules)
        result = pipeline.run(_payload(height=72.0, step_length_measured=True), _ctx())
        assert result.total_score == pytest.approx(100.0)

    def test_breakdown_has_one_entry_per_rule(self):
        rules = [HeightFactorRule(weight=1.0, h_max=72.0)]
        result = ScoringPipeline(rules).run(_payload(), _ctx())
        assert len(result.breakdown) == 1

    def test_zero_weight_rule_contributes_nothing(self):
        # A zero-weight rule paired with a full-weight rule: only the latter
        # contributes. Pipeline weights still sum to 1.0 (0.0 + 1.0).
        rules = [
            HeightFactorRule(weight=0.0, h_max=72.0),
            DistanceFactorRule(weight=1.0, measured_score=0.5, estimated_score=0.5),
        ]
        result = ScoringPipeline(rules).run(_payload(height=72.0), _ctx())
        # height rule contributes 0, distance rule contributes 0.5 × 1.0 × 100 = 50
        assert result.total_score == pytest.approx(50.0)

    def test_empty_pipeline_gives_zero(self):
        # Empty pipelines are exempt from the weight-sum constraint
        result = ScoringPipeline([]).run(_payload(), _ctx())
        assert result.total_score == pytest.approx(0.0)
        assert result.breakdown == []

    def test_wrong_payload_type_raises_type_error(self):
        from pydantic import BaseModel

        class OtherPayload(BaseModel):
            value: float = 1.0

        rule = HeightFactorRule(weight=1.0, h_max=72.0)
        with pytest.raises(TypeError, match="HeightFactorRule expected TreePayload"):
            rule.evaluate(OtherPayload(), _ctx())

    def test_full_five_rule_pipeline_within_range(self):
        entry = registry.get_config("tree-app")
        result = entry.pipeline.run(_payload(height=36.0, step_length_measured=True), _ctx(trust_level=50))
        assert 0.0 <= result.total_score <= 100.0
        assert len(result.breakdown) == 5
