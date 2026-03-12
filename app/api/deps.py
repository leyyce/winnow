"""
FastAPI dependency providers for the Winnow API layer.

All dependencies are pure functions that return infrastructure singletons.
For the prototype the registry is a module-level singleton — no async DB
session is needed at this stage.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
"""

from __future__ import annotations

from app.registry.manager import _Registry, registry


def get_registry() -> _Registry:
    """Return the global project registry singleton."""
    return registry
