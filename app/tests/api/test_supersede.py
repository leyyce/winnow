"""
API integration tests for the Winnow supersede endpoint.

Covered endpoint
----------------
* PATCH /api/v1/submissions/{id}/supersede

Tests verify
------------
- Happy path: existing pending_review submission → 200 SupersedeResponse
- Unknown submission_id → 404 RFC 7807
- Already-finalized submission → 409 RFC 7807
- Schema enforcement: ONLY ``status="superseded"`` accepted
- ``superseded_by`` is a required UUID field
- Missing or malformed fields produce RFC 7807 422 responses with field names
- The old PATCH /final-status route is no longer registered

References
----------
* API contract: docs/architecture/03_api_contracts.md §3b
* Sprint 3:     supersede is now fully DB-backed (no longer a 501 stub)
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


def _supersede_body(
    *,
    status: str = "superseded",
    superseded_by: str | None = None,
) -> dict:
    """Return a serialisable dict representing a valid SupersedeRequest."""
    body: dict = {"status": status}
    body["superseded_by"] = superseded_by or str(uuid4())
    return body


async def _submit_and_get_id(client: AsyncClient) -> str:
    """Submit a valid envelope and return the submission_id string."""
    response = await client.post("/api/v1/submissions", json=_valid_envelope())
    assert response.status_code == 201
    return response.json()["submission_id"]


# ── Happy path — DB-backed (Sprint 3) ────────────────────────────────────────

async def test_supersede_valid_request_returns_200(async_client: AsyncClient) -> None:
    """
    Valid supersede on an existing ``pending_review`` submission → 200.

    Sprint 3: the endpoint is fully implemented against the DB.
    The submission must exist first (POST /submissions), then PATCH supersede.
    """
    sid = await _submit_and_get_id(async_client)
    replacement_id = str(uuid4())

    response = await async_client.patch(
        f"/api/v1/submissions/{sid}/supersede",
        json=_supersede_body(superseded_by=replacement_id),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "superseded"
    assert body["submission_id"] == sid
    assert body["superseded_by"] == replacement_id
    assert "updated_at" in body


async def test_supersede_response_shape(async_client: AsyncClient) -> None:
    """200 response must contain all required SupersedeResponse fields."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.patch(
        f"/api/v1/submissions/{sid}/supersede",
        json=_supersede_body(),
    )
    body = response.json()
    assert response.status_code == 200
    assert "submission_id" in body
    assert "status" in body
    assert "superseded_by" in body
    assert "updated_at" in body


# ── 404 for unknown submission_id ─────────────────────────────────────────────

async def test_supersede_unknown_id_returns_404(async_client: AsyncClient) -> None:
    """
    Supersede with an unknown ``submission_id`` → 404 RFC 7807 response.
    The submission does not exist in DB so ``SubmissionNotFoundError`` is raised.
    """
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json=_supersede_body(),
    )
    assert response.status_code == 404


async def test_supersede_unknown_id_rfc7807_body(async_client: AsyncClient) -> None:
    """404 body for unknown submission must be a well-formed RFC 7807 ProblemDetail."""
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json=_supersede_body(),
    )
    body = response.json()
    assert body["type"].endswith("/errors/submission-not-found")
    assert body["title"] == "Submission Not Found"
    assert body["status"] == 404
    assert "instance" in body
    assert fake_id in body["detail"]


# ── 409 for already-finalized submission ──────────────────────────────────────

async def test_supersede_already_superseded_returns_409(
    async_client: AsyncClient,
) -> None:
    """Superseding an already-superseded submission → 409 AlreadyFinalized."""
    sid = await _submit_and_get_id(async_client)
    # First supersede — succeeds
    await async_client.patch(
        f"/api/v1/submissions/{sid}/supersede",
        json=_supersede_body(),
    )
    # Second supersede — must fail with 409
    response = await async_client.patch(
        f"/api/v1/submissions/{sid}/supersede",
        json=_supersede_body(),
    )
    assert response.status_code == 409
    body = response.json()
    assert body["type"].endswith("/errors/already-finalized")


# ── Schema enforcement: only "superseded" is accepted ────────────────────────

async def test_supersede_status_approved_returns_422(async_client: AsyncClient) -> None:
    """
    ``status="approved"`` must be rejected with 422.
    Approved/rejected transitions are governed by the vote-threshold engine.
    """
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json=_supersede_body(status="approved"),
    )
    assert response.status_code == 422


async def test_supersede_status_rejected_returns_422(async_client: AsyncClient) -> None:
    """``status="rejected"`` must be rejected with 422 for the same reason."""
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json=_supersede_body(status="rejected"),
    )
    assert response.status_code == 422


async def test_supersede_status_pending_review_returns_422(
    async_client: AsyncClient,
) -> None:
    """No lifecycle status other than 'superseded' is valid on this endpoint."""
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json=_supersede_body(status="pending_review"),
    )
    assert response.status_code == 422


async def test_supersede_invalid_status_mentions_field_in_errors(
    async_client: AsyncClient,
) -> None:
    """422 for wrong status value must name 'status' in the RFC 7807 errors list."""
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json=_supersede_body(status="approved"),
    )
    body = response.json()
    assert response.status_code == 422
    fields = [e["field"] for e in body.get("errors", [])]
    assert any("status" in f for f in fields)


# ── Missing / malformed fields ────────────────────────────────────────────────

async def test_supersede_missing_superseded_by_returns_422(
    async_client: AsyncClient,
) -> None:
    """``superseded_by`` is required — omitting it must return 422."""
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json={"status": "superseded"},
    )
    assert response.status_code == 422


async def test_supersede_missing_superseded_by_mentions_field(
    async_client: AsyncClient,
) -> None:
    """422 for missing superseded_by must name that field in the RFC 7807 errors."""
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json={"status": "superseded"},
    )
    body = response.json()
    assert response.status_code == 422
    fields = [e["field"] for e in body.get("errors", [])]
    assert any("superseded_by" in f for f in fields)


async def test_supersede_invalid_uuid_for_superseded_by_returns_422(
    async_client: AsyncClient,
) -> None:
    """A non-UUID string for ``superseded_by`` must fail Pydantic UUID validation."""
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json=_supersede_body(superseded_by="not-a-uuid"),
    )
    assert response.status_code == 422


async def test_supersede_invalid_uuid_mentions_field(async_client: AsyncClient) -> None:
    """422 for invalid superseded_by UUID must name that field in errors."""
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json=_supersede_body(superseded_by="not-a-uuid"),
    )
    body = response.json()
    assert response.status_code == 422
    fields = [e["field"] for e in body.get("errors", [])]
    assert any("superseded_by" in f for f in fields)


async def test_supersede_empty_body_returns_422(async_client: AsyncClient) -> None:
    """Completely empty body must fail validation with 422."""
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json={},
    )
    assert response.status_code == 422


# ── Old route is gone ─────────────────────────────────────────────────────────

async def test_old_final_status_route_returns_404(async_client: AsyncClient) -> None:
    """
    The old PATCH /submissions/{id}/final-status endpoint must no longer exist.
    Replaced by PATCH /submissions/{id}/supersede + vote-threshold auto-finalization.
    """
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/final-status",
        json={"final_status": "approved"},
    )
    assert response.status_code == 404
