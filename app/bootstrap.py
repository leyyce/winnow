"""
Application bootstrap — initializes the registry and loads all active projects.

Call ``bootstrap()`` once at application startup (e.g. from ``app/main.py``
or a FastAPI ``lifespan`` handler). It is intentionally idempotent: calling it
multiple times (e.g. during tests) is safe because ``_Registry.load`` simply
overwrites the existing entry for a given project_id.

Adding a new project
--------------------
1. Create ``app/registry/projects/<your_project>.py`` with a ``ProjectBuilder``
   subclass.
2. Import and instantiate it here, then pass it to ``registry.load()``.
   No other file needs to change (Open/Closed Principle).
"""
from __future__ import annotations

from app.registry.manager import registry
from app.registry.projects.trees import TreeProjectBuilder


def bootstrap() -> None:
    """Load all active project configurations into the registry."""
    registry.load(TreeProjectBuilder())


# ── Auto-bootstrap on import so that tests and services work without
#    explicitly calling bootstrap().  Production code should call it
#    explicitly inside the FastAPI lifespan handler for clarity.
bootstrap()
