"""
API v1 router — aggregates all v1 endpoint routers under /api/v1.

All endpoint modules expose a ``router`` object that is included here.
The single ``v1_router`` is then included in ``app/main.py``.

References
----------
* Architecture: docs/architecture/01_project_structure.md
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import finalization, results, submissions, tasks

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(submissions.router)
v1_router.include_router(finalization.router)
v1_router.include_router(tasks.router)
v1_router.include_router(results.router)
