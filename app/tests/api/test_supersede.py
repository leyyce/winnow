"""
API integration tests for withdraw, override, and auto-supersede lifecycle.

Covered endpoints
-----------------
* PATCH /api/v1/submissions/{id}/withdraw  — user-initiated withdrawal
* PATCH /api/v1/submissions/{id}/override  — admin override (power-vote)
* POST  /api/v1/submissions                — auto-supersede + 409 conflict

References
----------
* API contract: docs/architecture/03_api_contracts.md §3
* Issue:        Refinement Sprint — Immutable Audit-Log & Governance Authority
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.tests.conftest import _ctx, _payload


# ── Helpers ───────────────────────────────────────────────────────────────────

def _valid_envelope(
    *,
    project_id: str = "tree-app",
    entity_id: str | None = None,
    measurement_id: str | None = None,
    submission_id: str | None = None,
    trust_level: int = 50,
) -> dict:
    """Return a valid SubmissionEnvelope dict with configurable identity triplet."""
    ctx = _ctx(trust_level=trust_level)
    return {
        "metadata": {
            "project_id": project_id,
            "submission_id": submission_id or str(uuid4()),
            "entity_type": "tree",
            "entity_id": entity_id or str(uuid4()),
            "measurement_id": measurement_id or str(uuid4()),
            "submitted_at": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
        },
        "user_context": {
            "user_id": str(ctx.user_id),
            "username": ctx.username,
            "role": ctx.role,
            "trust_level": ctx.trust_level,
            "account_created_at": ctx.account_created_at.isoformat(),
        },
        "payload": _payload().model_dump(mode="json"),
    }


async def _submit(client: AsyncClient, **kwargs) -> dict:
    """Submit an envelope and return the parsed response body (asserts 201)."""
    response = await client.post("/api/v1/submissions", json=_valid_envelope(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()


# ── PATCH /submissions/{id}/withdraw ─────────────────────────────────────────

async def test_withdraw_pending_returns_200(async_client: AsyncClient) -> None:
    """Withdrawing a pending_review submission returns 200 with status 'voided'."""
    body = await _submit(async_client)
    sid = body["submission_id"]

    # Only pending_review submissions can be withdrawn; skip if auto-approved/rejected
    if body["status"] != "pending_review":
        pytest.skip("Submission was auto-finalized by threshold — cannot test withdraw")

    response = await async_client.patch(f"/api/v1/submissions/{sid}/withdraw")

    assert response.status_code == 200
    assert response.json()["status"] == "voided"


async def test_withdraw_unknown_submission_returns_404(async_client: AsyncClient) -> None:
    """Withdrawing a non-existent submission UUID returns 404."""
    response = await async_client.patch(f"/api/v1/submissions/{uuid4()}/withdraw")

    assert response.status_code == 404


async def test_withdraw_already_voided_returns_409(async_client: AsyncClient) -> None:
    """Withdrawing an already-voided submission returns 409 (AlreadyFinalized)."""
    body = await _submit(async_client)
    sid = body["submission_id"]

    if body["status"] != "pending_review":
        pytest.skip("Submission was auto-finalized — cannot test double-withdraw")

    # First withdrawal succeeds
    r1 = await async_client.patch(f"/api/v1/submissions/{sid}/withdraw")
    assert r1.status_code == 200

    # Second withdrawal must fail
    r2 = await async_client.patch(f"/api/v1/submissions/{sid}/withdraw")
    assert r2.status_code == 409


async def test_withdraw_response_is_rfc7807_on_not_found(async_client: AsyncClient) -> None:
    """404 on withdraw must be RFC 7807 ProblemDetail."""
    response = await async_client.patch(f"/api/v1/submissions/{uuid4()}/withdraw")

    body = response.json()
    assert response.status_code == 404
    assert "type" in body
    assert "title" in body
    assert "status" in body


# ── PATCH /submissions/{id}/override ─────────────────────────────────────────

async def test_override_approve_returns_200_approved(async_client: AsyncClient) -> None:
    """Admin override with vote='approve' forces status to 'approved'."""
    body = await _submit(async_client)
    sid = body["submission_id"]

    if body["status"] != "pending_review":
        pytest.skip("Submission was auto-finalized — cannot test override")

    override_request = {
        "user_id": str(uuid4()),
        "vote": "approve",
        "is_override": True,
        "user_trust_level": 99,
        "user_role": "admin",
    }
    response = await async_client.patch(
        f"/api/v1/submissions/{sid}/override", json=override_request
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


async def test_override_reject_returns_200_rejected(async_client: AsyncClient) -> None:
    """Admin override with vote='reject' forces status to 'rejected'."""
    body = await _submit(async_client)
    sid = body["submission_id"]

    if body["status"] != "pending_review":
        pytest.skip("Submission was auto-finalized — cannot test override")

    override_request = {
        "user_id": str(uuid4()),
        "vote": "reject",
        "is_override": True,
        "user_trust_level": 99,
        "user_role": "admin",
    }
    response = await async_client.patch(
        f"/api/v1/submissions/{sid}/override", json=override_request
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


async def test_override_unknown_submission_returns_404(async_client: AsyncClient) -> None:
    """Override on a non-existent submission returns 404."""
    override_request = {
        "user_id": str(uuid4()),
        "vote": "approve",
        "is_override": True,
        "user_trust_level": 99,
        "user_role": "admin",
    }
    response = await async_client.patch(
        f"/api/v1/submissions/{uuid4()}/override", json=override_request
    )

    assert response.status_code == 404


async def test_override_already_finalized_succeeds(async_client: AsyncClient) -> None:
    """Admin override on an already-finalized submission must succeed (200).

    Per the Post-Sprint 5 spec: admins can cast override votes regardless of
    the current terminal status.  A second override (e.g. voiding an approved
    submission) must be accepted, not rejected with 409.
    """
    body = await _submit(async_client)
    sid = body["submission_id"]

    if body["status"] != "pending_review":
        pytest.skip("Submission was auto-finalized — cannot test double-override")

    # First override: pending_review → approved
    r1 = await async_client.patch(
        f"/api/v1/submissions/{sid}/override",
        json={
            "user_id": str(uuid4()),
            "vote": "approve",
            "is_override": True,
            "user_trust_level": 99,
            "user_role": "admin",
        },
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "approved"

    # Second override: approved → voided (admin can always override)
    r2 = await async_client.patch(
        f"/api/v1/submissions/{sid}/override",
        json={
            "user_id": str(uuid4()),
            "vote": "voided",
            "is_override": True,
            "user_trust_level": 99,
            "user_role": "admin",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "voided"


# ── Auto-supersede via POST /submissions ─────────────────────────────────────

async def test_auto_supersede_pending_submission(async_client: AsyncClient) -> None:
    """
    Submitting a new envelope for the same (project, entity, measurement)
    triplet auto-supersedes the prior pending_review submission.
    """
    shared_entity_id = str(uuid4())
    shared_measurement_id = str(uuid4())

    first = await _submit(
        async_client,
        entity_id=shared_entity_id,
        measurement_id=shared_measurement_id,
    )
    if first["status"] != "pending_review":
        pytest.skip("First submission was auto-finalized — cannot test auto-supersede")

    first_sid = first["submission_id"]

    # Same triplet — should auto-supersede the first
    second_response = await async_client.post(
        "/api/v1/submissions",
        json=_valid_envelope(
            entity_id=shared_entity_id,
            measurement_id=shared_measurement_id,
        ),
    )
    assert second_response.status_code == 201

    # Sprint 5: old chain is NEVER modified — it stays pending_review.
    # Supersession is tracked by the new chain's backward pointer (supersedes FK).
    get_first = await async_client.get(f"/api/v1/results/{first_sid}")
    assert get_first.status_code == 200
    assert get_first.json()["status"] == "pending_review"

    # New submission carries supersede_reason='edited' confirming the cross-chain link
    second_body = second_response.json()
    assert second_body["supersede_reason"] == "edited"


async def test_auto_supersede_terminal_returns_409(async_client: AsyncClient) -> None:
    """
    Submitting for the same triplet when the prior submission is already in a
    terminal state must return 409 Conflict.
    """
    shared_entity_id = str(uuid4())
    shared_measurement_id = str(uuid4())

    first = await _submit(
        async_client,
        entity_id=shared_entity_id,
        measurement_id=shared_measurement_id,
    )
    first_sid = first["submission_id"]

    if first["status"] != "pending_review":
        pytest.skip("First submission was auto-finalized — cannot test 409 via override")

    # Force terminal state via admin override
    override_request = {
        "user_id": str(uuid4()),
        "vote": "approve",
        "is_override": True,
        "user_trust_level": 99,
        "user_role": "admin",
    }
    r = await async_client.patch(
        f"/api/v1/submissions/{first_sid}/override", json=override_request
    )
    assert r.status_code == 200

    # Now a new submission for the same triplet must return 409
    conflict_response = await async_client.post(
        "/api/v1/submissions",
        json=_valid_envelope(
            entity_id=shared_entity_id,
            measurement_id=shared_measurement_id,
        ),
    )
    assert conflict_response.status_code == 409
    body = conflict_response.json()
    assert "type" in body
    assert body["status"] == 409


async def test_invalid_entity_type_returns_422_with_field(async_client: AsyncClient) -> None:
    """Unknown entity_type must return 422 with the failing field in RFC 7807 errors."""
    envelope = _valid_envelope()
    envelope["metadata"]["entity_type"] = "unknown_entity"

    response = await async_client.post("/api/v1/submissions", json=envelope)

    assert response.status_code == 422
    body = response.json()
    assert "errors" in body
    assert any("entity_type" in e["field"] for e in body["errors"])


async def test_old_supersede_route_returns_404(async_client: AsyncClient) -> None:
    """The old PATCH /supersede route must no longer exist (404)."""
    response = await async_client.patch(
        f"/api/v1/submissions/{uuid4()}/supersede",
        json={"status": "superseded", "superseded_by": str(uuid4())},
    )
    assert response.status_code == 404
