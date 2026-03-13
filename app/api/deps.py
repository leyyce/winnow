"""
FastAPI dependency providers for the Winnow API layer.

Re-exports ``get_db`` from ``app/db/session.py`` as the canonical async
database session dependency.  Tests override this via
``app.dependency_overrides[get_db] = ...`` to inject an in-memory SQLite
session without touching any application code.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
* Session:      app/db/session.py
"""
from __future__ import annotations

from app.db.session import get_db  # re-exported for endpoint imports
from app.registry.manager import Registry, registry


def get_registry() -> Registry:
    """Return the global project registry singleton."""
    return registry


__all__ = ["get_db", "get_registry"]
