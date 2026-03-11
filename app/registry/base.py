"""
ProjectBuilder — abstract base class for project configuration providers.

Every project that wants to register with Winnow must implement this interface.
This follows the Open/Closed Principle: the registry is open for extension
(add a new ProjectBuilder subclass) but closed for modification (the registry
manager itself never changes when a new project is onboarded).

Usage::

    class MyProjectBuilder(ProjectBuilder):
        @property
        def project_id(self) -> str:
            return "my-project"

        def build(self) -> ProjectRegistryEntry:
            ...  # wire together schemas, rules, governance
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.registry.manager import ProjectRegistryEntry


class ProjectBuilder(ABC):
    """
    Abstract provider that constructs a fully assembled ProjectRegistryEntry
    for a single project and declares the project_id it registers under.

    Concrete implementations live in ``app/registry/projects/``.
    """

    @property
    @abstractmethod
    def project_id(self) -> str:
        """Unique identifier for this project (e.g. ``'tree-app'``)."""

    @abstractmethod
    def build(self) -> ProjectRegistryEntry:
        """
        Assemble and return the complete ProjectRegistryEntry.

        This is the single authoritative source for all project-specific
        numeric parameters (weights, thresholds, trust scales, etc.).
        No magic numbers may appear anywhere else (Rule 3: Configuration is King).
        """
