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

    def load(self, builder: "ProjectBuilder") -> None:
        """
        Build and register a project using the provided ``ProjectBuilder``.
        Calling ``load`` a second time with the same ``project_id`` overwrites
        the previous entry (idempotent re-registration is safe during tests).
        """
        self._entries[builder.project_id] = builder.build()

    def register(self, project_id: str, entry: ProjectRegistryEntry) -> None:
        """Low-level registration — prefer ``load(builder)`` for new projects."""
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
