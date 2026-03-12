"""
Winnow FastAPI application factory.

The lifespan handler bootstraps the project registry and sets up structured
logging before the first request is served. ``setup_logging`` is called first
so that all bootstrap log records are emitted as valid JSON from startup.
Exception handlers and the v1 router are registered on the application instance.

Health endpoint routing strategy
---------------------------------
The health router is mounted twice:
  - Inside ``v1_router`` → GET /api/v1/health  (canonical versioned path)
  - At application root  → GET /health         (permanent infrastructure alias)
This zero-breaking-change approach lets Docker HEALTHCHECK, load balancers, and
Kubernetes probes continue using /health while API consumers use the versioned path.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.v1.health import router as health_router
from app.api.v1.router import v1_router
from app.bootstrap import bootstrap
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan handler.

    Structured logging is configured first so that bootstrap warnings are
    captured as JSON records, then the project registry is populated via
    fault-tolerant auto-discovery of ProjectBuilders.
    """
    setup_logging()
    bootstrap()
    yield


app = FastAPI(
    title="Winnow",
    description="QA framework for user submitted data in citizen science projects.",
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.include_router(v1_router)
# Permanent infrastructure alias: GET /health → same handler as GET /api/v1/health.
# Docker HEALTHCHECK, load balancers, and Kubernetes probes target this path.
app.include_router(health_router)
