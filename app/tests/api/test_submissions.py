"""
API integration tests for the Winnow submission endpoints.

Tests use an ``httpx.AsyncClient`` wired directly to the FastAPI app (no real
HTTP server). The ``async_client`` fixture is defined in conftest.py.

Covered endpoints
-----------------
* GET  /health
* POST /api/v1/submissions  (happy path, bad envelope, unknown project,
                             RFC 7807 field assertions, idempotency,
                             required_validations shape, boundary payloads)
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.tests.conftest import _ctx, _payload


# ── Helpers ───────────────────────────────────────────────────────────────────

def _valid_envelope(project_id: str = "tree-app", trust_level: int = 50) -> dict:
    """Return a serialisable dict representing a valid SubmissionEnvelope."""
    ctx = _ctx(trust_level=trust_level)
    payload = _payload()
    return {
        "metadata": {
            "project_id": project_id,
            "submission_id": str(uuid4()),
            "entity_type": "tree_measurement",
            "entity_id": str(uuid4()),
            "measurement_id": str(uuid4()),
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
    assert body["status"] == "pending_review"
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
    assert body["type"].endswith("/errors/unknown-project")


# ── RFC 7807 field-level assertions ───────────────────────────────────────────

async def test_post_submission_missing_metadata_422_mentions_field(
    async_client: AsyncClient,
) -> None:
    """422 detail for missing metadata fields must name the offending field."""
    response = await async_client.post("/api/v1/submissions", json={})
    body = response.json()

    assert response.status_code == 422
    # RFC 7807 ProblemDetail must carry type/title/status
    assert body["type"].endswith("/errors/validation-error")
    assert body["status"] == 422
    # The 'errors' list must name at least one concrete field path
    fields = [e["field"] for e in body.get("errors", [])]
    assert any("metadata" in f or "user_context" in f for f in fields)


async def test_post_submission_missing_user_context_422_mentions_field(
    async_client: AsyncClient,
) -> None:
    """422 when user_context is absent must name that field in the detail."""
    envelope = _valid_envelope()
    del envelope["user_context"]
    response = await async_client.post("/api/v1/submissions", json=envelope)
    body = response.json()

    assert response.status_code == 422
    fields = [e["field"] for e in body.get("errors", [])]
    assert any("user_context" in f for f in fields)


async def test_post_submission_negative_trust_level_422_mentions_field(
    async_client: AsyncClient,
) -> None:
    """Negative trust_level in user_context → 422 mentioning 'trust_level'."""
    envelope = _valid_envelope()
    envelope["user_context"]["trust_level"] = -1
    response = await async_client.post("/api/v1/submissions", json=envelope)
    body = response.json()

    assert response.status_code == 422
    fields = [e["field"] for e in body.get("errors", [])]
    assert any("trust_level" in f for f in fields)


async def test_post_submission_empty_project_id_422_mentions_field(
    async_client: AsyncClient,
) -> None:
    """Empty project_id → 422 mentioning 'project_id'."""
    envelope = _valid_envelope()
    envelope["metadata"]["project_id"] = ""
    response = await async_client.post("/api/v1/submissions", json=envelope)
    body = response.json()

    assert response.status_code == 422
    fields = [e["field"] for e in body.get("errors", [])]
    assert any("project_id" in f for f in fields)


# ── Idempotency — same UUID submitted twice ───────────────────────────────────

async def test_post_submission_same_uuid_twice_both_succeed(
    async_client: AsyncClient,
) -> None:
    """
    Submitting the same submission_id twice must not crash.

    In the stub (no-DB) phase both calls process independently and return 201.
    This test documents and guards the idempotency contract: once the DB layer
    arrives, the second call must return the cached result, not a 500.
    """
    envelope = _valid_envelope()
    r1 = await async_client.post("/api/v1/submissions", json=envelope)
    r2 = await async_client.post("/api/v1/submissions", json=envelope)

    # Both must succeed — no 5xx
    assert r1.status_code == 201
    assert r2.status_code in (200, 201)
    # Both must echo the same submission_id
    assert r1.json()["submission_id"] == r2.json()["submission_id"]


# ── required_validations shape (role-weights pattern) ─────────────────────────

async def test_post_submission_required_validations_has_threshold_score(
    async_client: AsyncClient,
) -> None:
    """required_validations in 201 body must contain threshold_score >= 1."""
    response = await async_client.post("/api/v1/submissions", json=_valid_envelope())
    rv = response.json()["required_validations"]

    assert "threshold_score" in rv
    assert rv["threshold_score"] >= 1


async def test_post_submission_required_validations_has_role_configs(
    async_client: AsyncClient,
) -> None:
    """required_validations must contain role_configs, default_config, blocked_roles (Sprint 5)."""
    response = await async_client.post("/api/v1/submissions", json=_valid_envelope())
    rv = response.json()["required_validations"]

    assert "role_configs" in rv
    assert "default_config" in rv
    assert "blocked_roles" in rv
    assert isinstance(rv["role_configs"], dict)
    assert isinstance(rv["blocked_roles"], list)
    for role, cfg in rv["role_configs"].items():
        assert isinstance(role, str)
        assert "weight" in cfg and "min_trust" in cfg


async def test_post_submission_required_validations_no_old_fields(
    async_client: AsyncClient,
) -> None:
    """required_validations must NOT contain the old min_validators or required_role fields."""
    response = await async_client.post("/api/v1/submissions", json=_valid_envelope())
    rv = response.json()["required_validations"]

    assert "min_validators" not in rv
    assert "required_role" not in rv


# ── Boundary payload values ───────────────────────────────────────────────────

async def test_post_submission_very_large_height_returns_201(
    async_client: AsyncClient,
) -> None:
    """Height well above h_max is schema-valid; pipeline clamps score to [0, 100]."""
    envelope = _valid_envelope()
    envelope["payload"]["measurement"]["height"] = 9999.0
    response = await async_client.post("/api/v1/submissions", json=envelope)

    assert response.status_code == 201
    assert 0.0 <= response.json()["confidence_score"] <= 100.0


async def test_post_submission_inclination_at_90_returns_201(
    async_client: AsyncClient,
) -> None:
    """Inclination exactly at the schema boundary (90°) must succeed."""
    envelope = _valid_envelope()
    envelope["payload"]["measurement"]["inclination"] = 90
    response = await async_client.post("/api/v1/submissions", json=envelope)

    assert response.status_code == 201


async def test_post_submission_inclination_above_90_returns_422(
    async_client: AsyncClient,
) -> None:
    """Inclination 91° violates the schema constraint → 422."""
    envelope = _valid_envelope()
    envelope["payload"]["measurement"]["inclination"] = 91
    response = await async_client.post("/api/v1/submissions", json=envelope)

    assert response.status_code == 422


async def test_post_submission_negative_height_returns_422(
    async_client: AsyncClient,
) -> None:
    """Negative height is physically impossible → 422."""
    envelope = _valid_envelope()
    envelope["payload"]["measurement"]["height"] = -1.0
    response = await async_client.post("/api/v1/submissions", json=envelope)

    assert response.status_code == 422


async def test_post_submission_zero_trunk_diameter_returns_422(
    async_client: AsyncClient,
) -> None:
    """Zero trunk diameter violates the ge=1 constraint → 422."""
    envelope = _valid_envelope()
    envelope["payload"]["measurement"]["trunk_diameter"] = 0
    response = await async_client.post("/api/v1/submissions", json=envelope)

    assert response.status_code == 422
