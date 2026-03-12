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

    def test_load_overwrites_existing_entry(self):
        self.reg.load(TreeProjectBuilder())
        first_entry = self.reg.get_config("tree-app")
        self.reg.load(TreeProjectBuilder())
        second_entry = self.reg.get_config("tree-app")
        # Both are valid entries; overwrite must not raise.
        assert isinstance(second_entry, ProjectRegistryEntry)
        assert first_entry is not second_entry  # new object built each time

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
