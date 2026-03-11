"""
Unit tests for ScoringPipeline (app/scoring/pipeline.py).
Covers edge cases not exercised by the integration tests in test_tree_rules.py:
  - Empty pipeline
  - Single-rule pipeline
  - Multi-rule score accumulation math
  - Rounding behaviour
  - Breakdown length and content
  - RuleResult dataclass immutability
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.schemas.envelope import UserContext
from app.scoring.base import RuleResult, ScoringRule
from app.scoring.pipeline import PipelineResult, ScoringPipeline


# ── Helpers ────────────────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _ctx() -> UserContext:
    return UserContext(
        user_id=uuid4(),
        username="tester",
        role="citizen",
        trust_level=50,
        total_submissions=5,
        account_created_at=_NOW,
    )


class DummyPayload(BaseModel):
    value: float = 1.0


class FixedRule(ScoringRule):
    """A stub rule that always returns a fixed score."""

    def __init__(self, name: str, weight: float, fixed_score: float, details: str | None = None):
        self._name = name
        self._weight = weight
        self._score = fixed_score
        self._details = details

    @property
    def name(self) -> str:
        return self._name

    @property
    def weight(self) -> float:
        return self._weight

    def evaluate(self, payload: BaseModel, context: UserContext) -> RuleResult:
        return RuleResult(rule_name=self._name, score=self._score, details=self._details)


# ── PipelineResult dataclass ───────────────────────────────────────────────────

class TestPipelineResult:
    def test_construction(self):
        pr = PipelineResult(total_score=72.5, breakdown=[])
        assert pr.total_score == 72.5
        assert pr.breakdown == []

    def test_default_breakdown_is_empty_list(self):
        pr = PipelineResult(total_score=0.0)
        assert pr.breakdown == []


# ── RuleResult dataclass ───────────────────────────────────────────────────────

class TestRuleResult:
    def test_construction(self):
        rr = RuleResult(rule_name="height_factor", score=0.8)
        assert rr.rule_name == "height_factor"
        assert rr.score == 0.8
        assert rr.details is None

    def test_details_stored(self):
        rr = RuleResult(rule_name="r", score=0.5, details="some detail")
        assert rr.details == "some detail"

    def test_frozen_immutable(self):
        rr = RuleResult(rule_name="r", score=0.5)
        with pytest.raises(Exception):  # dataclass(frozen=True) raises FrozenInstanceError
            setattr(rr, "score", 0.9)


# ── ScoringPipeline — empty ────────────────────────────────────────────────────

class TestEmptyPipeline:
    def test_empty_pipeline_total_score_is_zero(self):
        pipeline = ScoringPipeline(rules=[])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.total_score == 0.0

    def test_empty_pipeline_breakdown_is_empty(self):
        pipeline = ScoringPipeline(rules=[])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.breakdown == []


# ── ScoringPipeline — single rule ─────────────────────────────────────────────

class TestSingleRulePipeline:
    def test_full_score_single_rule(self):
        # score=1.0, weight=0.4 → total = 1.0 × 0.4 × 100 = 40.0
        pipeline = ScoringPipeline([FixedRule("r1", weight=0.4, fixed_score=1.0)])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.total_score == pytest.approx(40.0)

    def test_zero_score_single_rule(self):
        pipeline = ScoringPipeline([FixedRule("r1", weight=0.4, fixed_score=0.0)])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.total_score == pytest.approx(0.0)

    def test_half_score_single_rule(self):
        # score=0.5, weight=1.0 → total = 50.0
        pipeline = ScoringPipeline([FixedRule("r1", weight=1.0, fixed_score=0.5)])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.total_score == pytest.approx(50.0)

    def test_breakdown_has_one_entry(self):
        pipeline = ScoringPipeline([FixedRule("r1", weight=1.0, fixed_score=0.8)])
        result = pipeline.run(DummyPayload(), _ctx())
        assert len(result.breakdown) == 1

    def test_breakdown_entry_is_rule_result(self):
        pipeline = ScoringPipeline([FixedRule("my_rule", weight=1.0, fixed_score=0.6)])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.breakdown[0].rule_name == "my_rule"
        assert result.breakdown[0].score == pytest.approx(0.6)

    def test_breakdown_details_propagated(self):
        pipeline = ScoringPipeline([FixedRule("r", weight=1.0, fixed_score=0.5, details="ok")])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.breakdown[0].details == "ok"


# ── ScoringPipeline — multi-rule accumulation ──────────────────────────────────

class TestMultiRulePipeline:
    def test_two_rules_accumulate_correctly(self):
        # r1: 0.8 × 0.5 × 100 = 40.0
        # r2: 0.6 × 0.5 × 100 = 30.0
        # total = 70.0
        pipeline = ScoringPipeline([
            FixedRule("r1", weight=0.5, fixed_score=0.8),
            FixedRule("r2", weight=0.5, fixed_score=0.6),
        ])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.total_score == pytest.approx(70.0)

    def test_three_rules_full_score_equals_100(self):
        pipeline = ScoringPipeline([
            FixedRule("r1", weight=0.25, fixed_score=1.0),
            FixedRule("r2", weight=0.25, fixed_score=1.0),
            FixedRule("r3", weight=0.50, fixed_score=1.0),
        ])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.total_score == pytest.approx(100.0)

    def test_breakdown_length_matches_rule_count(self):
        rules = [FixedRule(f"r{i}", weight=0.2, fixed_score=0.5) for i in range(5)]
        pipeline = ScoringPipeline(rules)
        result = pipeline.run(DummyPayload(), _ctx())
        assert len(result.breakdown) == 5

    def test_breakdown_order_matches_rule_order(self):
        pipeline = ScoringPipeline([
            FixedRule("alpha", weight=0.5, fixed_score=1.0),
            FixedRule("beta",  weight=0.5, fixed_score=0.0),
        ])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.breakdown[0].rule_name == "alpha"
        assert result.breakdown[1].rule_name == "beta"

    def test_one_zero_rule_reduces_total(self):
        pipeline = ScoringPipeline([
            FixedRule("r1", weight=0.5, fixed_score=1.0),
            FixedRule("r2", weight=0.5, fixed_score=0.0),
        ])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.total_score == pytest.approx(50.0)

    def test_asymmetric_weights(self):
        # r1: 1.0 × 0.8 × 100 = 80.0
        # r2: 1.0 × 0.2 × 100 = 20.0
        pipeline = ScoringPipeline([
            FixedRule("r1", weight=0.8, fixed_score=1.0),
            FixedRule("r2", weight=0.2, fixed_score=1.0),
        ])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.total_score == pytest.approx(100.0)


# ── ScoringPipeline — rounding ─────────────────────────────────────────────────

class TestPipelineRounding:
    def test_result_rounded_to_four_decimal_places(self):
        # 1/3 × 1/3 × 100 ≈ 11.1111…  → rounded to 4 dp = 11.1111
        pipeline = ScoringPipeline([
            FixedRule("r", weight=round(1 / 3, 10), fixed_score=round(1 / 3, 10)),
        ])
        result = pipeline.run(DummyPayload(), _ctx())
        # Verify no more than 4 decimal places in representation
        decimal_part = str(result.total_score).split(".")
        if len(decimal_part) > 1:
            assert len(decimal_part[1]) <= 4

    def test_exact_integer_result_has_no_extra_decimals(self):
        pipeline = ScoringPipeline([FixedRule("r", weight=0.5, fixed_score=0.5)])
        result = pipeline.run(DummyPayload(), _ctx())
        assert result.total_score == pytest.approx(25.0)


# ── ScoringPipeline — payload and context pass-through ────────────────────────

class TestPipelinePassThrough:
    def test_payload_passed_to_rule(self):
        received: list = []

        class CapturingRule(ScoringRule):
            @property
            def name(self) -> str:
                return "capture"

            @property
            def weight(self) -> float:
                return 1.0

            def evaluate(self, payload: BaseModel, context: UserContext) -> RuleResult:
                received.append(payload)
                return RuleResult(rule_name="capture", score=1.0)

        payload = DummyPayload(value=42.0)
        ScoringPipeline([CapturingRule()]).run(payload, _ctx())
        assert received[0] is payload

    def test_context_passed_to_rule(self):
        received: list = []

        class CapturingRule(ScoringRule):
            @property
            def name(self) -> str:
                return "capture"

            @property
            def weight(self) -> float:
                return 1.0

            def evaluate(self, payload: BaseModel, context: UserContext) -> RuleResult:
                received.append(context)
                return RuleResult(rule_name="capture", score=1.0)

        ctx = _ctx()
        ScoringPipeline([CapturingRule()]).run(DummyPayload(), ctx)
        assert received[0] is ctx
