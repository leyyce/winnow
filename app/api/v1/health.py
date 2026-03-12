"""
Health check endpoint.

Mounted at two paths simultaneously:
  - GET /api/v1/health   — versioned path for API consumers (canonical)
  - GET /health          — permanent infrastructure alias for Docker HEALTHCHECK,
                           load balancers, and Kubernetes liveness probes

The ``registry_loaded`` flag lets monitoring systems distinguish between a
process that is up but has not yet bootstrapped vs. one that is fully
operational.

References
----------
* Architecture: docs/architecture/01_project_structure.md
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.registry.manager import registry

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness / readiness response payload."""

    status: Literal["ok"] = "ok"
    registry_loaded: bool


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness and readiness check",
    description=(
        "Returns 200 when the process is running. "
        "``registry_loaded: true`` indicates that at least one project has been "
        "successfully bootstrapped and the application is ready to score submissions."
    ),
)
async def health() -> HealthResponse:
    """Liveness check. Returns 200 when the process is running."""
    return HealthResponse(
        status="ok",
        registry_loaded=len(registry.registered_projects) > 0,
    )
