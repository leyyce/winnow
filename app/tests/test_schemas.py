"""
Tests for core Pydantic schemas:
  - SubmissionMetadata, UserContext, SubmissionEnvelope  (envelope.py)
  - RuleBreakdown, ThresholdConfig, RequiredValidations, ScoringResultResponse  (results.py)
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.envelope import SubmissionEnvelope, SubmissionMetadata, UserContext
from app.schemas.results import (
    RequiredValidations,
    RuleBreakdown,
    ScoringResultResponse,
    ThresholdConfig,
)

# ── Shared helpers ─────────────────────────────────────────────────────────────

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _metadata(**overrides) -> dict:
    base = dict(
        project_id="tree-app",
        submission_id=uuid4(),
        entity_type="tree_measurement",
        entity_id=uuid4(),
        measurement_id=uuid4(),
        submitted_at=_NOW,
    )
    base.update(overrides)
    return base


def _user_context(**overrides) -> dict:
    base = dict(
        user_id=uuid4(),
        username="alice",
        role="citizen",
        trust_level=30,
        total_submissions=5,
        account_created_at=_NOW,
    )
    base.update(overrides)
    return base


def _thresholds(**overrides) -> dict:
    base = dict(auto_approve_min=80, manual_review_min=50)
    base.update(overrides)
    return base


def _required_validations(**overrides) -> dict:
    base = dict(
        threshold_score=2,
        role_configs={"citizen": {"weight": 1, "min_trust": 10}, "expert": {"weight": 2, "min_trust": 0}},
        default_config={"weight": 1, "min_trust": 25},
        blocked_roles=["guest", "banned"],
        review_tier="peer_review",
    )
    base.update(overrides)
    return base


# ── SubmissionMetadata ─────────────────────────────────────────────────────────

class TestSubmissionMetadata:
    def test_valid_construction(self):
        m = SubmissionMetadata.model_validate(_metadata())
        assert m.project_id == "tree-app"
        assert m.client_version is None

    def test_client_version_optional(self):
        m = SubmissionMetadata.model_validate(_metadata(client_version="1.2.3"))
        assert m.client_version == "1.2.3"

    def test_empty_project_id_rejected(self):
        with pytest.raises(ValidationError, match="project_id"):
            SubmissionMetadata.model_validate(_metadata(project_id=""))

    def test_empty_entity_type_rejected(self):
        with pytest.raises(ValidationError, match="entity_type"):
            SubmissionMetadata.model_validate(_metadata(entity_type=""))

    def test_missing_submission_id_rejected(self):
        data = _metadata()
        del data["submission_id"]
        with pytest.raises(ValidationError):
            SubmissionMetadata.model_validate(data)

    def test_missing_submitted_at_rejected(self):
        data = _metadata()
        del data["submitted_at"]
        with pytest.raises(ValidationError):
            SubmissionMetadata.model_validate(data)


# ── UserContext ────────────────────────────────────────────────────────────────

class TestUserContext:
    def test_valid_construction(self):
        ctx = UserContext.model_validate(_user_context())
        assert ctx.username == "alice"
        assert ctx.trust_level == 30

    def test_zero_trust_allowed(self):
        ctx = UserContext.model_validate(_user_context(trust_level=0))
        assert ctx.trust_level == 0

    def test_negative_trust_rejected(self):
        with pytest.raises(ValidationError, match="trust_level"):
            UserContext.model_validate(_user_context(trust_level=-1))

    def test_large_trust_allowed(self):
        # Trust scale is project-specific — no upper bound on schema level
        ctx = UserContext.model_validate(_user_context(trust_level=500))
        assert ctx.trust_level == 500

    def test_empty_username_rejected(self):
        with pytest.raises(ValidationError, match="username"):
            UserContext.model_validate(_user_context(username=""))

    def test_empty_role_rejected(self):
        with pytest.raises(ValidationError, match="role"):
            UserContext.model_validate(_user_context(role=""))

    def test_any_role_string_accepted(self):
        # role is str — no Literal constraint (project-generic)
        ctx = UserContext.model_validate(_user_context(role="super_custom_role"))
        assert ctx.role == "super_custom_role"

    def test_zero_total_submissions_allowed(self):
        ctx = UserContext.model_validate(_user_context(total_submissions=0))
        assert ctx.total_submissions == 0

    def test_negative_total_submissions_rejected(self):
        with pytest.raises(ValidationError, match="total_submissions"):
            UserContext.model_validate(_user_context(total_submissions=-1))

    def test_missing_account_created_at_rejected(self):
        data = _user_context()
        del data["account_created_at"]
        with pytest.raises(ValidationError):
            UserContext.model_validate(data)


# ── SubmissionEnvelope ─────────────────────────────────────────────────────────

class TestSubmissionEnvelope:
    def test_valid_construction(self):
        env = SubmissionEnvelope.model_validate(dict(
            metadata=_metadata(),
            user_context=_user_context(),
            payload={"tree_id": str(uuid4()), "height": 15.0},
        ))
        assert env.metadata.project_id == "tree-app"
        assert env.payload["height"] == 15.0

    def test_payload_accepts_arbitrary_dict(self):
        env = SubmissionEnvelope.model_validate(dict(
            metadata=_metadata(),
            user_context=_user_context(),
            payload={"anything": True, "nested": {"a": 1}},
        ))
        assert env.payload["anything"] is True

    def test_empty_payload_accepted(self):
        env = SubmissionEnvelope.model_validate(dict(
            metadata=_metadata(),
            user_context=_user_context(),
            payload={},
        ))
        assert env.payload == {}

    def test_missing_metadata_rejected(self):
        with pytest.raises(ValidationError):
            SubmissionEnvelope.model_validate(dict(
                user_context=_user_context(),
                payload={},
            ))

    def test_missing_user_context_rejected(self):
        with pytest.raises(ValidationError):
            SubmissionEnvelope.model_validate(dict(
                metadata=_metadata(),
                payload={},
            ))


# ── RuleBreakdown ──────────────────────────────────────────────────────────────

class TestRuleBreakdown:
    def test_valid_construction(self):
        rb = RuleBreakdown.model_validate(dict(
            rule="height_factor", weight=0.3, score=0.8, weighted_score=24.0,
        ))
        assert rb.rule == "height_factor"
        assert rb.details is None

    def test_details_optional(self):
        rb = RuleBreakdown.model_validate(dict(
            rule="height_factor", weight=0.3, score=0.8, weighted_score=24.0,
            details="within expected range",
        ))
        assert rb.details == "within expected range"

    def test_empty_rule_name_rejected(self):
        with pytest.raises(ValidationError, match="rule"):
            RuleBreakdown.model_validate(dict(rule="", weight=0.3, score=0.8, weighted_score=24.0))

    def test_weight_above_one_rejected(self):
        with pytest.raises(ValidationError, match="weight"):
            RuleBreakdown.model_validate(dict(rule="r", weight=1.1, score=0.5, weighted_score=5.0))

    def test_weight_below_zero_rejected(self):
        with pytest.raises(ValidationError, match="weight"):
            RuleBreakdown.model_validate(dict(rule="r", weight=-0.1, score=0.5, weighted_score=5.0))

    def test_score_above_one_rejected(self):
        with pytest.raises(ValidationError, match="score"):
            RuleBreakdown.model_validate(dict(rule="r", weight=0.5, score=1.1, weighted_score=5.0))

    def test_score_below_zero_rejected(self):
        with pytest.raises(ValidationError, match="score"):
            RuleBreakdown.model_validate(dict(rule="r", weight=0.5, score=-0.1, weighted_score=5.0))

    def test_weighted_score_zero_allowed(self):
        rb = RuleBreakdown.model_validate(dict(rule="r", weight=0.5, score=0.0, weighted_score=0.0))
        assert rb.weighted_score == 0.0

    def test_weighted_score_below_zero_rejected(self):
        with pytest.raises(ValidationError, match="weighted_score"):
            RuleBreakdown.model_validate(dict(rule="r", weight=0.5, score=0.5, weighted_score=-1.0))


# ── ThresholdConfig ────────────────────────────────────────────────────────────

class TestThresholdConfig:
    def test_valid_construction(self):
        tc = ThresholdConfig.model_validate(_thresholds())
        assert tc.auto_approve_min == 80
        assert tc.manual_review_min == 50

    def test_auto_approve_min_above_100_rejected(self):
        with pytest.raises(ValidationError, match="auto_approve_min"):
            ThresholdConfig.model_validate(_thresholds(auto_approve_min=101))

    def test_manual_review_min_below_zero_rejected(self):
        with pytest.raises(ValidationError, match="manual_review_min"):
            ThresholdConfig.model_validate(_thresholds(manual_review_min=-1))

    def test_boundary_values_accepted(self):
        tc = ThresholdConfig.model_validate(dict(auto_approve_min=100, manual_review_min=0))
        assert tc.auto_approve_min == 100
        assert tc.manual_review_min == 0

    def test_auto_approve_below_manual_review_raises(self):
        with pytest.raises(ValidationError, match="'auto_approve_min'"):
            ThresholdConfig.model_validate(dict(auto_approve_min=40, manual_review_min=50))

    def test_equal_thresholds_accepted(self):
        # auto_approve_min == manual_review_min collapses the review band to zero width;
        # degenerate but logically valid — all submissions either auto-approve or auto-reject.
        tc = ThresholdConfig.model_validate(dict(auto_approve_min=50, manual_review_min=50))
        assert tc.auto_approve_min == tc.manual_review_min == 50

    def test_integer_types_enforced(self):
        # Floats that are whole numbers are coerced to int by Pydantic
        tc = ThresholdConfig.model_validate(dict(auto_approve_min=80.0, manual_review_min=50.0))
        assert isinstance(tc.auto_approve_min, int)
        assert isinstance(tc.manual_review_min, int)

    def test_no_old_three_field_shape(self):
        # The old approve/review/reject shape must not exist — only 2 boundaries are returned
        tc = ThresholdConfig.model_validate(_thresholds())
        assert not hasattr(tc, "approve")
        assert not hasattr(tc, "review")
        assert not hasattr(tc, "reject")


# ── RequiredValidations ────────────────────────────────────────────────────────

class TestRequiredValidations:
    def test_valid_construction(self):
        rv = RequiredValidations.model_validate(_required_validations())
        assert rv.threshold_score == 2
        assert rv.review_tier == "peer_review"
        assert "citizen" in rv.role_configs
        assert "expert" in rv.role_configs
        assert rv.default_config.weight == 1
        assert rv.default_config.min_trust == 25
        assert rv.blocked_roles == ["guest", "banned"]

    def test_role_configs_expert_only(self):
        # Expert-only tier: only expert in role_configs
        rv = RequiredValidations.model_validate(
            _required_validations(role_configs={"expert": {"weight": 3, "min_trust": 0}})
        )
        assert "expert" in rv.role_configs
        assert "citizen" not in rv.role_configs
        assert rv.role_configs["expert"].weight == 3

    def test_role_configs_empty_dict_accepted(self):
        # Empty role_configs — all roles fall back to default_config
        rv = RequiredValidations.model_validate(
            _required_validations(role_configs={})
        )
        assert rv.role_configs == {}

    def test_threshold_score_zero_rejected(self):
        with pytest.raises(ValidationError, match="threshold_score"):
            RequiredValidations.model_validate(_required_validations(threshold_score=0))

    def test_threshold_score_negative_rejected(self):
        with pytest.raises(ValidationError, match="threshold_score"):
            RequiredValidations.model_validate(_required_validations(threshold_score=-1))

    def test_threshold_score_one_accepted(self):
        rv = RequiredValidations.model_validate(_required_validations(threshold_score=1))
        assert rv.threshold_score == 1

    def test_default_config_min_trust_zero_allowed(self):
        rv = RequiredValidations.model_validate(
            _required_validations(default_config={"weight": 1, "min_trust": 0})
        )
        assert rv.default_config.min_trust == 0

    def test_default_config_negative_min_trust_rejected(self):
        with pytest.raises(ValidationError, match="min_trust"):
            RequiredValidations.model_validate(
                _required_validations(default_config={"weight": 1, "min_trust": -1})
            )

    def test_empty_review_tier_rejected(self):
        with pytest.raises(ValidationError, match="review_tier"):
            RequiredValidations.model_validate(_required_validations(review_tier=""))

    def test_large_trust_accepted(self):
        rv = RequiredValidations.model_validate(
            _required_validations(default_config={"weight": 1, "min_trust": 500})
        )
        assert rv.default_config.min_trust == 500

    def test_old_min_validators_field_does_not_exist(self):
        rv = RequiredValidations.model_validate(_required_validations())
        assert not hasattr(rv, "min_validators")

    def test_old_required_role_field_does_not_exist(self):
        rv = RequiredValidations.model_validate(_required_validations())
        assert not hasattr(rv, "required_role")
        assert not hasattr(rv, "role_weights")
        assert not hasattr(rv, "required_min_trust")


# ── ScoringResultResponse ──────────────────────────────────────────────────────

class TestScoringResultResponse:
    def _make(self, **overrides) -> dict:
        base = dict(
            submission_id=uuid4(),
            project_id="tree-app",
            entity_type="tree_measurement",
            entity_id=uuid4(),
            measurement_id=uuid4(),
            status="pending_review",
            confidence_score=72.5,
            breakdown=[],
            required_validations=[_required_validations()],
            thresholds=_thresholds(),
            ledger_entry_id=uuid4(),
            created_at=_NOW,
        )
        base.update(overrides)
        return base

    def test_valid_pending_review(self):
        resp = ScoringResultResponse.model_validate(self._make())
        assert resp.status == "pending_review"

    def test_valid_approved_status(self):
        resp = ScoringResultResponse.model_validate(self._make(status="approved"))
        assert resp.status == "approved"

    def test_valid_rejected_status(self):
        resp = ScoringResultResponse.model_validate(self._make(status="rejected"))
        assert resp.status == "rejected"

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError, match="status"):
            ScoringResultResponse.model_validate(self._make(status="unknown"))

    def test_confidence_score_above_100_rejected(self):
        with pytest.raises(ValidationError, match="confidence_score"):
            ScoringResultResponse.model_validate(self._make(confidence_score=100.1))

    def test_confidence_score_below_zero_rejected(self):
        with pytest.raises(ValidationError, match="confidence_score"):
            ScoringResultResponse.model_validate(self._make(confidence_score=-0.1))

    def test_confidence_score_boundary_values_accepted(self):
        for score in (0.0, 100.0):
            resp = ScoringResultResponse.model_validate(self._make(confidence_score=score))
            assert resp.confidence_score == score

    def test_breakdown_with_rule_entries(self):
        entry = dict(rule="height_factor", weight=0.25, score=0.8, weighted_score=20.0)
        resp = ScoringResultResponse.model_validate(self._make(breakdown=[entry]))
        assert len(resp.breakdown) == 1
        assert resp.breakdown[0].rule == "height_factor"

    def test_empty_project_id_rejected(self):
        with pytest.raises(ValidationError, match="project_id"):
            ScoringResultResponse.model_validate(self._make(project_id=""))
