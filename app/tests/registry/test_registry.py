"""
Tests for the registry layer:
  - bootstrap() auto-discovery and idempotency
  - ProjectBuilder ABC contract
  - TreeProjectBuilder.build() completeness
  - _Registry.load() / .register() / .get_config() / .registered_projects
"""
from __future__ import annotations

import pytest

from app.registry.base import ProjectBuilder
from app.registry.manager import ProjectRegistryEntry, Registry, registry
from app.core.exceptions import ProjectNotFoundError
from app.registry.projects.trees import TreeProjectBuilder
from app.schemas.projects.trees import TreePayload
from app.scoring.common.trust_advisor import TrustAdvisor
from app.scoring.pipeline import ScoringPipeline
from app.governance.projects.trees import TreeGovernancePolicy


# ── Bootstrap ──────────────────────────────────────────────────────────────────

class TestBootstrap:
    def test_tree_app_registered_after_bootstrap(self):
        assert "tree-app" in registry.registered_projects

    def test_bootstrap_is_idempotent(self):
        # Calling bootstrap() a second time must not raise and must leave
        # tree-app still correctly registered.
        from app.bootstrap import bootstrap
        bootstrap()
        bootstrap()
        assert "tree-app" in registry.registered_projects

    def test_bootstrap_discovers_without_manual_import(self):
        # The registry must be populated purely by scanning app.registry.projects —
        # no explicit TreeProjectBuilder import in bootstrap.py itself.
        import app.bootstrap as _bs
        import inspect
        source = inspect.getsource(_bs)
        assert "TreeProjectBuilder" not in source

    def test_only_concrete_builders_loaded(self):
        # ProjectBuilder ABC itself must never appear as a registered project.
        for project_id in registry.registered_projects:
            entry = registry.get_config(project_id)
            assert isinstance(entry, ProjectRegistryEntry)


# ── ProjectBuilder ABC ─────────────────────────────────────────────────────────

class TestProjectBuilderABC:
    def test_cannot_instantiate_abstract_base(self):
        with pytest.raises(TypeError):
            ProjectBuilder()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_project_id(self):
        class MissingId(ProjectBuilder):
            def build(self) -> ProjectRegistryEntry:  # type: ignore[override]
                ...

        with pytest.raises(TypeError):
            MissingId()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_build(self):
        class MissingBuild(ProjectBuilder):
            @property
            def project_id(self) -> str:
                return "missing-build"

        with pytest.raises(TypeError):
            MissingBuild()  # type: ignore[abstract]

    def test_fully_concrete_subclass_can_be_instantiated(self):
        class DummyBuilder(ProjectBuilder):
            @property
            def project_id(self) -> str:
                return "dummy"

            def build(self) -> ProjectRegistryEntry:
                return registry.get_config("tree-app")  # borrow a valid entry

        builder = DummyBuilder()
        assert builder.project_id == "dummy"


# ── TreeProjectBuilder ─────────────────────────────────────────────────────────

class TestTreeProjectBuilder:
    def setup_method(self):
        self.builder = TreeProjectBuilder()

    def test_project_id_is_tree_app(self):
        assert self.builder.project_id == "tree-app"

    def test_build_returns_registry_entry(self):
        entry = self.builder.build()
        assert isinstance(entry, ProjectRegistryEntry)

    def test_payload_schema_is_tree_payload(self):
        entry = self.builder.build()
        assert entry.payload_schema is TreePayload

    def test_pipeline_is_scoring_pipeline(self):
        entry = self.builder.build()
        assert isinstance(entry.pipeline, ScoringPipeline)

    def test_pipeline_has_five_rules(self):
        entry = self.builder.build()
        assert len(entry.pipeline.rules) == 5

    def test_pipeline_weights_sum_to_one(self):
        entry = self.builder.build()
        total = sum(r.weight for r in entry.pipeline.rules)
        assert total == pytest.approx(1.0)

    def test_trust_advisor_is_trust_advisor(self):
        entry = self.builder.build()
        assert isinstance(entry.trust_advisor, TrustAdvisor)

    def test_governance_policy_is_tree_policy(self):
        entry = self.builder.build()
        assert isinstance(entry.governance_policy, TreeGovernancePolicy)

    def test_thresholds_auto_approve_above_manual_review(self):
        entry = self.builder.build()
        assert entry.thresholds.auto_approve_min >= entry.thresholds.manual_review_min

    def test_thresholds_within_valid_range(self):
        entry = self.builder.build()
        for value in (entry.thresholds.auto_approve_min, entry.thresholds.manual_review_min):
            assert 0 <= value <= 100

    def test_thresholds_are_integers(self):
        entry = self.builder.build()
        assert isinstance(entry.thresholds.auto_approve_min, int)
        assert isinstance(entry.thresholds.manual_review_min, int)

    def test_build_is_deterministic(self):
        # Two calls must produce entries with identical configuration.
        entry_a = self.builder.build()
        entry_b = self.builder.build()
        assert entry_a.payload_schema is entry_b.payload_schema
        assert entry_a.thresholds.auto_approve_min == entry_b.thresholds.auto_approve_min
        assert entry_a.thresholds.manual_review_min == entry_b.thresholds.manual_review_min
        rule_names_a = {r.name for r in entry_a.pipeline.rules}
        rule_names_b = {r.name for r in entry_b.pipeline.rules}
        assert rule_names_a == rule_names_b


# ── _Registry ──────────────────────────────────────────────────────────────────

class TestRegistry:
    def setup_method(self):
        # Use a fresh isolated registry for unit tests to avoid side effects.
        self.reg = Registry()

    def test_empty_registry_has_no_projects(self):
        assert self.reg.registered_projects == []

    def test_load_registers_project(self):
        self.reg.load(TreeProjectBuilder())
        assert "tree-app" in self.reg.registered_projects

    def test_get_config_after_load(self):
        self.reg.load(TreeProjectBuilder())
        entry = self.reg.get_config("tree-app")
        assert isinstance(entry, ProjectRegistryEntry)

    def test_get_config_unknown_project_raises_project_not_found_error(self):
        with pytest.raises(ProjectNotFoundError, match="not registered"):
            self.reg.get_config("ghost-project")

    def test_project_not_found_error_carries_project_id(self):
        self.reg.load(TreeProjectBuilder())
        with pytest.raises(ProjectNotFoundError) as exc_info:
            self.reg.get_config("ghost")
        assert exc_info.value.project_id == "ghost"

    def test_register_low_level_method(self):
        entry = TreeProjectBuilder().build()
        self.reg.register("custom-id", entry)
        assert "custom-id" in self.reg.registered_projects
        assert self.reg.get_config("custom-id") is entry

    def test_load_overwrites_existing_entry_with_allow_overwrite(self):
        """allow_overwrite=True must silently replace the existing entry."""
        self.reg.load(TreeProjectBuilder())
        first_entry = self.reg.get_config("tree-app")
        self.reg.load(TreeProjectBuilder(), allow_overwrite=True)
        second_entry = self.reg.get_config("tree-app")
        assert isinstance(second_entry, ProjectRegistryEntry)
        assert first_entry is not second_entry  # new object built each time

    def test_load_raises_on_duplicate_without_allow_overwrite(self):
        """load() without allow_overwrite=True must raise ValueError on duplicate."""
        self.reg.load(TreeProjectBuilder())
        with pytest.raises(ValueError, match="tree-app"):
            self.reg.load(TreeProjectBuilder())

    def test_register_raises_on_duplicate_without_allow_overwrite(self):
        """register() without allow_overwrite=True must raise ValueError on duplicate."""
        entry = TreeProjectBuilder().build()
        self.reg.register("tree-app", entry)
        with pytest.raises(ValueError, match="tree-app"):
            self.reg.register("tree-app", entry)

    def test_registered_projects_returns_list(self):
        self.reg.load(TreeProjectBuilder())
        projects = self.reg.registered_projects
        assert isinstance(projects, list)
        assert "tree-app" in projects

    def test_multiple_projects_can_be_registered(self):
        class BirdBuilder(ProjectBuilder):
            @property
            def project_id(self) -> str:
                return "bird-app"

            def build(self) -> ProjectRegistryEntry:
                # Re-use tree entry for simplicity — only project_id differs.
                return TreeProjectBuilder().build()

        self.reg.load(TreeProjectBuilder())
        self.reg.load(BirdBuilder())
        assert set(self.reg.registered_projects) == {"tree-app", "bird-app"}

    def test_duplicate_load_second_entry_replaces_first(self):
        """
        Loading the same project_id twice with allow_overwrite=True must silently
        overwrite — no error raised.  The second entry is the canonical one.

        This documents the re-registration contract used by test fixtures.
        Production bootstrap() catches ValueError and logs a warning instead.
        """
        self.reg.load(TreeProjectBuilder())
        first = self.reg.get_config("tree-app")

        self.reg.load(TreeProjectBuilder(), allow_overwrite=True)
        second = self.reg.get_config("tree-app")

        # No exception — the overwrite is intentional
        assert isinstance(second, ProjectRegistryEntry)
        # A fresh build produces a distinct object each time
        assert first is not second

    def test_register_with_different_id_than_builder_project_id(self):
        """
        low-level register() lets callers attach an entry under any key — it is
        the caller's responsibility that key and semantic content match.
        The registry does not validate the key against any builder property.
        """
        entry = TreeProjectBuilder().build()
        self.reg.register("alias-for-trees", entry)

        assert "alias-for-trees" in self.reg.registered_projects
        assert self.reg.get_config("alias-for-trees") is entry

    def test_get_config_returns_same_object_as_registered(self):
        """get_config must return the exact same object that was registered (identity)."""
        entry = TreeProjectBuilder().build()
        self.reg.register("my-project", entry)

        assert self.reg.get_config("my-project") is entry

    def test_project_not_found_for_unregistered_id_after_other_registrations(self):
        """ProjectNotFoundError must be raised even when other projects exist."""
        self.reg.load(TreeProjectBuilder())

        with pytest.raises(ProjectNotFoundError, match="unknown-project"):
            self.reg.get_config("unknown-project")

    def test_registered_projects_is_independent_copy(self):
        """Mutating the returned list must not affect the registry's internal state."""
        self.reg.load(TreeProjectBuilder())
        projects = self.reg.registered_projects
        projects.append("injected-fake")

        # Internal state must be unchanged
        assert "injected-fake" not in self.reg.registered_projects


# ── TreeProjectBuilder governance role-weights ────────────────────────────────

class TestTreeProjectBuilderGovernance:
    """
    Verify that the tree-app registry entry carries the new role-weights governance
    fields on every tier. These tests act as a contract guard: if someone modifies
    the registry builder and accidentally drops threshold_score or role_weights,
    a test failure here surfaces the breakage immediately.
    """

    def setup_method(self):
        self.entry = TreeProjectBuilder().build()
        self.policy = self.entry.governance_policy

    def test_governance_policy_has_tiers(self):
        assert len(self.policy._tiers) >= 1

    def test_all_tiers_have_positive_threshold_score(self):
        for tier in self.policy._tiers:
            assert tier.threshold_score >= 1, (
                f"Tier '{tier.review_tier}' has invalid threshold_score={tier.threshold_score}"
            )

    def test_all_tiers_have_non_empty_role_weights(self):
        for tier in self.policy._tiers:
            assert isinstance(tier.role_weights, dict), (
                f"Tier '{tier.review_tier}' role_weights is not a dict"
            )
            assert len(tier.role_weights) > 0, (
                f"Tier '{tier.review_tier}' role_weights is empty"
            )

    def test_all_role_weights_are_non_negative_integers(self):
        for tier in self.policy._tiers:
            for role, weight in tier.role_weights.items():
                assert isinstance(weight, int), (
                    f"Tier '{tier.review_tier}' role '{role}' weight is not int"
                )
                assert weight >= 0, (
                    f"Tier '{tier.review_tier}' role '{role}' weight is negative"
                )

    def test_all_tiers_have_non_negative_required_min_trust(self):
        for tier in self.policy._tiers:
            assert tier.required_min_trust >= 0

    def test_tiers_sorted_descending_by_score_threshold(self):
        thresholds = [t.score_threshold for t in self.policy._tiers]
        assert thresholds == sorted(thresholds, reverse=True)

    def test_expert_review_tier_excludes_citizen(self):
        """expert_review tier must have citizen weight=0 (or absent) to enforce expert-only."""
        expert_tier = next(
            (t for t in self.policy._tiers if t.review_tier == "expert_review"), None
        )
        assert expert_tier is not None, "expert_review tier not found"
        assert expert_tier.role_weights.get("citizen", 0) == 0

    def test_community_review_tier_expert_weight_exceeds_citizen(self):
        """
        In community_review, expert weight must be > citizen weight to allow
        single-expert finalization when citizens need multiple votes.
        """
        community_tier = next(
            (t for t in self.policy._tiers if t.review_tier == "community_review"), None
        )
        assert community_tier is not None, "community_review tier not found"
        citizen_w = community_tier.role_weights.get("citizen", 0)
        expert_w = community_tier.role_weights.get("expert", 0)
        assert expert_w > citizen_w, (
            f"expert weight ({expert_w}) must exceed citizen weight ({citizen_w}) "
            f"so a single expert can meet the threshold alone"
        )
