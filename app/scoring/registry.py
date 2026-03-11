"""
DEPRECATED — this module is a backward-compatibility shim only.

The registry has moved to ``app/registry/``.
Import from there directly:

    from app.registry.manager import ProjectRegistryEntry, registry
    from app.registry.base import ProjectBuilder

This shim will be removed in a future cleanup pass.
"""
from app.registry.manager import ProjectRegistryEntry, registry  # noqa: F401
from app.registry.base import ProjectBuilder  # noqa: F401

# Trigger bootstrap so the registry is populated when this shim is imported.
import app.bootstrap  # noqa: F401
