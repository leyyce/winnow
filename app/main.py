"""
Winnow FastAPI application factory.

The lifespan handler bootstraps the project registry and sets up structured
logging before the first request is served. Exception handlers and the v1
router are registered on the application instance.

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

    Runs bootstrap (fault-tolerant auto-discovery of ProjectBuilders) and
    structured logging setup before yielding to serve requests.
    """
    bootstrap()
    setup_logging()
    yield


app = FastAPI(
    title="Winnow",
    description="QA framework for user submitted data in citizen science projects.",
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.include_router(health_router)
app.include_router(v1_router)
