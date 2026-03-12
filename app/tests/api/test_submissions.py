"""
API integration tests for the Winnow submission endpoints.

Tests use an ``httpx.AsyncClient`` wired directly to the FastAPI app (no real
HTTP server). The ``async_client`` fixture is defined in conftest.py.

Covered endpoints
-----------------
* GET  /health
* POST /api/v1/submissions  (happy path, bad envelope, unknown project)
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.tests.conftest import _ctx, _payload


# ── Helpers ───────────────────────────────────────────────────────────────────

def _valid_envelope(project_id: str = "tree-app") -> dict:
    """Return a serialisable dict representing a valid SubmissionEnvelope."""
    ctx = _ctx(trust_level=50)
    payload = _payload()
    return {
        "metadata": {
            "project_id": project_id,
            "submission_id": str(uuid4()),
            "submission_type": "tree_measurement",
            "submitted_at": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
        },
        "user_context": {
            "user_id": str(ctx.user_id),
            "username": ctx.username,
            "role": ctx.role,
            "trust_level": ctx.trust_level,
            "total_submissions": ctx.total_submissions,
            "account_created_at": ctx.account_created_at.isoformat(),
        },
        "payload": payload.model_dump(mode="json"),
    }


# ── Health ────────────────────────────────────────────────────────────────────

async def test_health_returns_200(async_client: AsyncClient) -> None:
    """GET /health must return 200 with status='ok'."""
    response = await async_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "registry_loaded" in body


async def test_health_registry_loaded_after_bootstrap(async_client: AsyncClient) -> None:
    """registry_loaded must be True after the session-scoped bootstrap fixture runs."""
    response = await async_client.get("/health")

    assert response.json()["registry_loaded"] is True


# ── POST /api/v1/submissions — happy path ─────────────────────────────────────

async def test_post_submission_happy_path_returns_201(async_client: AsyncClient) -> None:
    """Valid envelope → 201 Created with ScoringResultResponse body."""
    response = await async_client.post("/api/v1/submissions", json=_valid_envelope())

    assert response.status_code == 201


async def test_post_submission_response_shape(async_client: AsyncClient) -> None:
    """201 response body must contain the required ScoringResultResponse fields."""
    response = await async_client.post("/api/v1/submissions", json=_valid_envelope())

    body = response.json()
    assert "submission_id" in body
    assert "project_id" in body
    assert body["project_id"] == "tree-app"
    assert body["status"] == "pending_finalization"
    assert "confidence_score" in body
    assert "breakdown" in body
    assert "required_validations" in body
    assert "thresholds" in body
    assert "created_at" in body


async def test_post_submission_confidence_score_in_range(async_client: AsyncClient) -> None:
    """confidence_score in the 201 response must be in [0, 100]."""
    response = await async_client.post("/api/v1/submissions", json=_valid_envelope())

    score = response.json()["confidence_score"]
    assert 0.0 <= score <= 100.0


# ── POST /api/v1/submissions — bad envelope (Stage 1 failure) ─────────────────

async def test_post_submission_missing_fields_returns_422(async_client: AsyncClient) -> None:
    """Incomplete envelope body → 422 with RFC 7807 ProblemDetail."""
    response = await async_client.post("/api/v1/submissions", json={"metadata": {}})

    assert response.status_code == 422


async def test_post_submission_bad_payload_returns_422(async_client: AsyncClient) -> None:
    """Envelope with structurally invalid payload → 422."""
    envelope = _valid_envelope()
    envelope["payload"] = {"totally": "wrong", "tree_id": "bad-uuid"}
    response = await async_client.post("/api/v1/submissions", json=envelope)

    assert response.status_code == 422


async def test_post_submission_validation_error_is_rfc7807(async_client: AsyncClient) -> None:
    """422 response body must be a RFC 7807 ProblemDetail."""
    response = await async_client.post("/api/v1/submissions", json={})

    body = response.json()
    assert "type" in body
    assert "title" in body
    assert "status" in body
    assert body["status"] == 422


# ── POST /api/v1/submissions — unknown project ────────────────────────────────

async def test_post_submission_unknown_project_returns_422(async_client: AsyncClient) -> None:
    """Unknown project_id → 422 with /errors/unknown-project type."""
    envelope = _valid_envelope(project_id="no-such-project")
    response = await async_client.post("/api/v1/submissions", json=envelope)

    assert response.status_code == 422


async def test_post_submission_unknown_project_rfc7807_type(async_client: AsyncClient) -> None:
    """Unknown project 422 body must use the /errors/unknown-project type URI."""
    envelope = _valid_envelope(project_id="no-such-project")
    response = await async_client.post("/api/v1/submissions", json=envelope)

    body = response.json()
    assert body["type"] == "/errors/unknown-project"
