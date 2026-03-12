"""
Tests for common scoring components:
  - TrustLevelRule (Tₙ)
  - TrustAdvisor + TrustAdvisorConfig + UserSubmissionStats
  - ScoringRegistry lookup
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.schemas.projects.trees import TreePayload
from app.scoring.common.trust_advisor import (
    TrustAdvisor,
    TrustAdvisorConfig,
)
from app.scoring.common.trust_level import TrustLevelRule
from app.registry.manager import registry
from app.core.exceptions import ProjectNotFoundError
from app.tests.conftest import _ctx, _payload, _user_stats


# ── TrustLevelRule ────────────────────────────────────────────────────────────

class TestTrustLevelRule:
    def setup_method(self):
        self.rule = TrustLevelRule(weight=0.25, trust_level_mid=50, trust_level_max=100)

    def test_zero_trust_gives_zero_score(self):
        result = self.rule.evaluate(_payload(), _ctx(0))
        assert result.score == 0.0

    def test_mid_trust_gives_half_score(self):
        result = self.rule.evaluate(_payload(), _ctx(50))
        assert result.score == pytest.approx(0.5)

    def test_max_trust_gives_full_score(self):
        result = self.rule.evaluate(_payload(), _ctx(100))
        assert result.score == pytest.approx(1.0)

    def test_trust_above_max_clamped_to_one(self):
        result = self.rule.evaluate(_payload(), _ctx(999))
        assert result.score == pytest.approx(1.0)

    def test_quarter_trust_gives_quarter_score(self):
        result = self.rule.evaluate(_payload(), _ctx(25))
        assert result.score == pytest.approx(0.25)

    def test_three_quarter_trust_gives_three_quarter_score(self):
        result = self.rule.evaluate(_payload(), _ctx(75))
        assert result.score == pytest.approx(0.75)

    def test_score_always_in_range(self):
        for tl in range(0, 201, 10):
            result = self.rule.evaluate(_payload(), _ctx(tl))
            assert 0.0 <= result.score <= 1.0

    def test_rule_name(self):
        assert self.rule.name == "trust_level"

    def test_weight(self):
        assert self.rule.weight == 0.25

    def test_result_contains_details(self):
        result = self.rule.evaluate(_payload(), _ctx(50))
        assert result.details is not None
        assert "50" in result.details

    def test_invalid_mid_zero_raises(self):
        with pytest.raises(ValueError, match="trust_level_mid"):
            TrustLevelRule(weight=0.25, trust_level_mid=0, trust_level_max=100)

    def test_max_not_greater_than_mid_raises(self):
        with pytest.raises(ValueError, match="trust_level_max"):
            TrustLevelRule(weight=0.25, trust_level_mid=100, trust_level_max=50)

    def test_max_equal_to_mid_raises(self):
        with pytest.raises(ValueError, match="trust_level_max"):
            TrustLevelRule(weight=0.25, trust_level_mid=50, trust_level_max=50)


# ── TrustAdvisor ─────────────────────────────────────────────────────────────

class TestTrustAdvisor:
    def setup_method(self):
        self.config = TrustAdvisorConfig(
            reward_per_approval=1,
            penalty_per_rejection=3,
            streak_bonus=2,
            streak_threshold=5,
            min_trust=0,
            max_trust=100,
        )
        self.advisor = TrustAdvisor(self.config)
        self.user_id = uuid4()

    def test_approval_gives_positive_delta(self):
        result = self.advisor.compute_adjustment(self.user_id, 20, "approved", _user_stats())
        assert result.recommended_delta == 1

    def test_rejection_gives_negative_delta(self):
        result = self.advisor.compute_adjustment(self.user_id, 20, "rejected", _user_stats())
        assert result.recommended_delta == -3

    def test_streak_bonus_applied_at_threshold(self):
        result = self.advisor.compute_adjustment(self.user_id, 20, "approved", _user_stats(consecutive=5))
        assert result.recommended_delta == 1 + 2

    def test_streak_bonus_applied_above_threshold(self):
        result = self.advisor.compute_adjustment(self.user_id, 20, "approved", _user_stats(consecutive=10))
        assert result.recommended_delta == 1 + 2

    def test_streak_bonus_not_applied_below_threshold(self):
        result = self.advisor.compute_adjustment(self.user_id, 20, "approved", _user_stats(consecutive=4))
        assert result.recommended_delta == 1

    def test_project_bounds_returned(self):
        result = self.advisor.compute_adjustment(self.user_id, 20, "approved", _user_stats())
        assert result.project_min_trust == 0
        assert result.project_max_trust == 100

    def test_unknown_status_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown final_status"):
            self.advisor.compute_adjustment(self.user_id, 20, "pending", _user_stats())

    def test_current_trust_level_echoed(self):
        result = self.advisor.compute_adjustment(self.user_id, 42, "approved", _user_stats())
        assert result.current_trust_level == 42

    def test_user_id_echoed(self):
        result = self.advisor.compute_adjustment(self.user_id, 20, "approved", _user_stats())
        assert result.user_id == self.user_id

    def test_reason_contains_streak_info(self):
        result = self.advisor.compute_adjustment(self.user_id, 20, "approved", _user_stats(consecutive=5))
        assert "streak" in result.reason.lower() or "consecutive" in result.reason.lower()


# ── ScoringRegistry ───────────────────────────────────────────────────────────

class TestScoringRegistry:
    def test_tree_app_is_registered(self):
        assert "tree-app" in registry.registered_projects

    def test_get_config_returns_entry(self):
        entry = registry.get_config("tree-app")
        assert entry is not None

    def test_unknown_project_raises_project_not_found_error(self):
        with pytest.raises(ProjectNotFoundError, match="not registered"):
            registry.get_config("nonexistent-project")

    def test_tree_app_has_payload_schema(self):
        entry = registry.get_config("tree-app")
        assert entry.payload_schema is TreePayload

    def test_tree_app_has_pipeline_with_five_rules(self):
        entry = registry.get_config("tree-app")
        assert len(entry.pipeline.rules) == 5

    def test_tree_app_pipeline_rule_names(self):
        entry = registry.get_config("tree-app")
        names = {r.name for r in entry.pipeline.rules}
        assert names == {"height_factor", "distance_factor", "trust_level",
                         "comment_factor", "plausibility_factor"}

    def test_tree_app_weights_sum_to_one(self):
        entry = registry.get_config("tree-app")
        total = sum(r.weight for r in entry.pipeline.rules)
        assert total == pytest.approx(1.0)

    def test_tree_app_thresholds_present(self):
        entry = registry.get_config("tree-app")
        assert entry.thresholds.auto_approve_min >= entry.thresholds.manual_review_min

    def test_tree_app_has_trust_advisor(self):
        entry = registry.get_config("tree-app")
        assert isinstance(entry.trust_advisor, TrustAdvisor)

    def test_tree_app_has_governance_policy(self):
        from app.governance.projects.trees import TreeGovernancePolicy
        entry = registry.get_config("tree-app")
        assert isinstance(entry.governance_policy, TreeGovernancePolicy)
