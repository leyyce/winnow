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

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.governance.projects.trees import GovernanceTier, TreeGovernancePolicy
from app.schemas.envelope import UserContext
from app.schemas.projects.trees import (
    SpeciesStats,
    TreeMeasurementPayload,
    TreePayload,
    TreePhotoPayload,
)
from app.schemas.results import RequiredValidations
from app.registry.manager import registry
from app.scoring.pipeline import ScoringPipeline
from app.scoring.projects.trees.comment_factor import CommentFactorRule
from app.scoring.projects.trees.distance_factor import DistanceFactorRule
from app.scoring.projects.trees.height_factor import HeightFactorRule
from app.scoring.projects.trees.plausibility_factor import PlausibilityFactorRule


# ── Shared helpers ────────────────────────────────────────────────────────────

def _ctx(trust_level: int = 50) -> UserContext:
    return UserContext(
        user_id=uuid4(),
        username="tester",
        role="citizen",
        trust_level=trust_level,
        total_submissions=5,
        account_created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )


def _default_stats() -> SpeciesStats:
    return SpeciesStats(
        mean_height=20.0, std_height=5.0,
        mean_inclination=5.0, std_inclination=2.0,
        mean_trunk_diameter=30.0, std_trunk_diameter=10.0,
    )


def _payload(
    height: float = 20.0,
    inclination: int = 5,
    trunk_diameter: int = 30,
    note: str | None = None,
    step_length_measured: bool = True,
    photos: list[TreePhotoPayload] | None = None,
    species_stats: SpeciesStats | None = None,
) -> TreePayload:
    if photos is None:
        photos = [TreePhotoPayload(path="a.jpg"), TreePhotoPayload(path="b.jpg")]
    return TreePayload(
        tree_id=uuid4(),
        species_id=uuid4(),
        measurement=TreeMeasurementPayload(
            height=height,
            inclination=inclination,
            trunk_diameter=trunk_diameter,
            note=note,
        ),
        photos=photos,
        step_length_measured=step_length_measured,
        species_stats=species_stats or _default_stats(),
    )


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

class TestTreeGovernancePolicy:
    def setup_method(self):
        self.policy = TreeGovernancePolicy(tiers=[
            GovernanceTier(score_threshold=80.0, review_tier="peer_review",
                           min_validators=1, required_min_trust=30, required_role=None),
            GovernanceTier(score_threshold=50.0, review_tier="community_review",
                           min_validators=2, required_min_trust=50, required_role=None),
            GovernanceTier(score_threshold=0.0, review_tier="expert_review",
                           min_validators=1, required_min_trust=75, required_role="expert"),
        ])

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

    def test_tiers_sorted_automatically(self):
        # Pass tiers in wrong order — policy must sort them
        policy = TreeGovernancePolicy(tiers=[
            GovernanceTier(score_threshold=0.0, review_tier="expert_review",
                           min_validators=1, required_min_trust=75, required_role="expert"),
            GovernanceTier(score_threshold=80.0, review_tier="peer_review",
                           min_validators=1, required_min_trust=30, required_role=None),
        ])
        assert policy.determine_requirements(90.0, _ctx()).review_tier == "peer_review"

    def test_empty_tiers_raises(self):
        with pytest.raises(ValueError):
            TreeGovernancePolicy(tiers=[])

    def test_eligible_reviewer_passes_trust(self):
        req = RequiredValidations(min_validators=1, required_min_trust=30,
                                  required_role=None, review_tier="peer_review")
        assert self.policy.is_eligible_reviewer(85.0, req, reviewer_trust=30, reviewer_role="citizen")

    def test_ineligible_reviewer_fails_trust(self):
        req = RequiredValidations(min_validators=1, required_min_trust=30,
                                  required_role=None, review_tier="peer_review")
        assert not self.policy.is_eligible_reviewer(85.0, req, reviewer_trust=29, reviewer_role="citizen")

    def test_ineligible_reviewer_fails_role(self):
        req = RequiredValidations(min_validators=1, required_min_trust=75,
                                  required_role="expert", review_tier="expert_review")
        assert not self.policy.is_eligible_reviewer(20.0, req, reviewer_trust=80, reviewer_role="citizen")

    def test_eligible_reviewer_matches_required_role(self):
        req = RequiredValidations(min_validators=1, required_min_trust=75,
                                  required_role="expert", review_tier="expert_review")
        assert self.policy.is_eligible_reviewer(20.0, req, reviewer_trust=80, reviewer_role="expert")


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
