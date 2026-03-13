"""
API integration tests for the Winnow supersede endpoint.

Covered endpoint
----------------
* PATCH /api/v1/submissions/{id}/supersede

Tests verify:
  - Schema enforcement: ONLY ``status="superseded"`` accepted (not approved/rejected)
  - ``superseded_by`` is a required UUID field
  - Missing or malformed fields produce RFC 7807 422 responses with field names
  - The endpoint returns 501 until the DB layer is added (stub contract)
  - The old PATCH /final-status route is no longer registered
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

def _supersede_body(
    *,
    status: str = "superseded",
    superseded_by: str | None = None,
) -> dict:
    """Return a serialisable dict representing a valid SupersedeRequest."""
    body: dict = {"status": status}
    if superseded_by is not None:
        body["superseded_by"] = superseded_by
    else:
        body["superseded_by"] = str(uuid4())
    return body


# ── Happy path (stub — returns 501 until DB layer) ────────────────────────────

async def test_supersede_valid_request_returns_501(async_client: AsyncClient) -> None:
    """
    Valid supersede request → 501 Not Implemented (stub until Sprint 3 DB layer).

    The schema is valid so the request passes Pydantic validation; the service
    raises NotImplementedYetError which maps to 501.
    """
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json=_supersede_body(),
    )
    assert response.status_code == 501


async def test_supersede_501_body_is_rfc7807(async_client: AsyncClient) -> None:
    """501 response must be a well-formed RFC 7807 ProblemDetail."""
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/supersede",
        json=_supersede_body(),
    )
    body = response.json()

    assert body["type"].endswith("/errors/not-implemented")
    assert body["title"] == "Not Implemented"
    assert body["status"] == 501
    assert "instance" in body


# ── Schema enforcement: only "superseded" is accepted ────────────────────────

async def test_supersede_status_approved_returns_422(async_client: AsyncClient) -> None:
    """
    ``status="approved"`` must be rejected with 422.

    Approved/rejected transitions are governed by the vote-threshold engine,
    NOT by this endpoint. The Literal["superseded"] constraint enforces this.
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


async def test_supersede_status_pending_review_returns_422(async_client: AsyncClient) -> None:
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
        json={"status": "superseded"},  # no superseded_by
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

    It was replaced by PATCH /submissions/{id}/supersede (for superseded status)
    and the vote-threshold auto-finalization for approved/rejected.
    """
    fake_id = str(uuid4())
    response = await async_client.patch(
        f"/api/v1/submissions/{fake_id}/final-status",
        json={"final_status": "approved"},
    )
    assert response.status_code == 404
