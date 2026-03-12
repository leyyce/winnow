"""
Health check endpoint.

GET /health returns a simple liveness signal. The ``registry_loaded`` flag
lets monitoring systems distinguish between a process that is up but has not
yet bootstrapped vs. one that is fully operational.

References
----------
* Architecture: docs/architecture/01_project_structure.md
"""

from __future__ import annotations

from fastapi import APIRouter

from app.registry.manager import registry

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    """Liveness check. Returns 200 when the process is running."""
    return {
        "status": "ok",
        "registry_loaded": len(registry.registered_projects) > 0,
    }
