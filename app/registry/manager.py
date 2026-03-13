"""
Registry manager — container and lookup for all registered projects.

``ProjectRegistryEntry`` is a fully assembled configuration bundle for one
project. ``Registry`` is the singleton that stores and resolves entries by
project_id.

Design notes
------------
* The registry itself is **value-agnostic**: it stores whatever a
  ``ProjectBuilder`` hands it and never inspects numeric parameters.
* Projects are loaded at application startup via ``app/bootstrap.py``.
* Configuration source: code-based for the prototype (see
  02_architecture_patterns.md §3). A DB-backed approach can be added later
  without changing this module — only the builders change.

Architectural Trade-off — Pragmatic Domain Imports (W2)
--------------------------------------------------------
``ProjectRegistryEntry`` imports concrete types from the scoring and governance
layers (``ScoringPipeline``, ``TrustAdvisor``, ``GovernancePolicy``).
Architecturally the registry should be fully domain-agnostic; however,
removing these imports would require replacing the typed dataclass fields with
``Any``, destroying IDE type-hinting and auto-complete for every service and
test that consumes ``ProjectRegistryEntry``.

Decision: accept this pragmatic trade-off.
* Only **abstract base types / concrete infrastructure classes** are imported —
  never project-specific rule implementations.
* The registry never inspects or calls any domain logic; it is a typed
  container only.
* This decision is documented here and in
  ``docs/architecture/02_architecture_patterns.md`` §3.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Type

from pydantic import BaseModel

from app.core.exceptions import ProjectNotFoundError
from app.governance.base import GovernancePolicy
from app.schemas.results import ThresholdConfig
from app.scoring.common.trust_advisor import TrustAdvisor
from app.scoring.pipeline import ScoringPipeline

if TYPE_CHECKING:
    from app.registry.base import ProjectBuilder


@dataclass
class ProjectRegistryEntry:
    """
    Fully assembled configuration for a registered project.
    Resolved by the registry on every scoring service call.
    """

    payload_schema: Type[BaseModel]      # Stage 1 validation schema class
    pipeline: ScoringPipeline            # Stage 2 + 4-input scoring pipeline
    thresholds: ThresholdConfig          # advisory score thresholds for client routing
    trust_advisor: TrustAdvisor          # Stage 4-output trust adjustment advisor
    governance_policy: GovernancePolicy  # review tiers + reviewer eligibility


class Registry:
    """
    Project registry singleton. Projects are loaded at startup by the bootstrap
    module. The registry is deliberately ignorant of any project-specific logic.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ProjectRegistryEntry] = {}

    def load(self, builder: "ProjectBuilder", *, allow_overwrite: bool = False) -> None:
        """
        Build and register a project using the provided ``ProjectBuilder``.

        Parameters
        ----------
        builder:
            A ``ProjectBuilder`` whose ``build()`` method assembles the full
            ``ProjectRegistryEntry``.
        allow_overwrite:
            If ``False`` (default) and a project with the same ``project_id``
            is already registered, raises ``ValueError`` to prevent silent
            double-registration at bootstrap time (§2.1 collision guard).
            Set to ``True`` in test fixtures that re-register the same project
            across multiple test cases for idempotency.
        """
        self.register(builder.project_id, builder.build(), allow_overwrite=allow_overwrite)

    def register(
        self,
        project_id: str,
        entry: ProjectRegistryEntry,
        *,
        allow_overwrite: bool = False,
    ) -> None:
        """
        Low-level registration — prefer ``load(builder)`` for new projects.

        Parameters
        ----------
        project_id:
            Unique string identifier for the project (matches the value in
            ``UNIQUE(project_id)`` on the ``project_configs`` DB table).
        entry:
            Fully assembled ``ProjectRegistryEntry`` to store.
        allow_overwrite:
            Controls collision behaviour.  ``False`` by default so that
            production bootstrap calls fail loudly on duplicate IDs rather
            than silently clobbering an existing registration.  Pass
            ``allow_overwrite=True`` in test fixtures that need idempotent
            re-registration.  See docs/architecture/05_database_design.md §2.1.
        """
        if not allow_overwrite and project_id in self._entries:
            raise ValueError(
                f"Project {project_id!r} is already registered. "
                "Pass allow_overwrite=True to replace it intentionally "
                "(e.g. in test fixtures), or check for duplicate bootstrap calls."
            )
        self._entries[project_id] = entry

    def get_config(self, project_id: str) -> ProjectRegistryEntry:
        """
        Return the ``ProjectRegistryEntry`` for the given project_id.
        Raises ``ProjectNotFoundError`` with a descriptive message if not registered.
        """
        try:
            return self._entries[project_id]
        except KeyError:
            raise ProjectNotFoundError(project_id)

    @property
    def registered_projects(self) -> list[str]:
        """Return a list of all registered project identifiers."""
        return list(self._entries)


# ── Module-level singleton — consumed by services via dependency injection ───
registry = Registry()
