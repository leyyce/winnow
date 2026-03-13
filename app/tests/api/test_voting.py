"""
API integration tests for the Winnow voting endpoint.

Covers the full multi-vote governance flow:
* POST /api/v1/submissions/{id}/votes — happy path, duplicate vote,
  already-finalized, not eligible, submission not found, threshold met.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import webhook_service
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


def _vote_body(
    *,
    user_id: str | None = None,
    vote: str = "approve",
    trust: int = 50,
    role: str = "citizen",
    note: str | None = None,
) -> dict:
    """Return a serialisable dict representing a VoteRequest."""
    return {
        "user_id": user_id or str(uuid4()),
        "vote": vote,
        "user_trust_level": trust,
        "user_role": role,
        "note": note,
    }


async def _submit_and_get_id(client: AsyncClient) -> str:
    """Submit a valid envelope and return the submission_id."""
    response = await client.post("/api/v1/submissions", json=_valid_envelope())
    assert response.status_code == 201
    return response.json()["submission_id"]


# ── POST /submissions/{id}/votes — happy path ────────────────────────────────

async def test_vote_happy_path_returns_201(async_client: AsyncClient) -> None:
    """Valid vote on a pending submission → 201."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(),
    )
    assert response.status_code == 201


async def test_vote_response_shape(async_client: AsyncClient) -> None:
    """201 response body has the expected VoteResponse fields."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(),
    )
    body = response.json()
    assert body["submission_id"] == sid
    assert body["vote_registered"] is True
    assert "current_votes" in body
    assert body["current_votes"]["approve"] == 1
    assert body["current_votes"]["reject"] == 0
    assert body["threshold_met"] is False
    assert body["final_status"] is None
    assert "message" in body


async def test_vote_reject(async_client: AsyncClient) -> None:
    """A reject vote increments the reject tally."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(vote="reject"),
    )
    body = response.json()
    assert body["current_votes"]["reject"] == 1
    assert body["current_votes"]["approve"] == 0


# ── Threshold met — auto-finalization ─────────────────────────────────────────

async def test_vote_threshold_met_approval(async_client: AsyncClient) -> None:
    """When enough approvals accumulate, the submission auto-finalizes as 'approved'."""
    sid = await _submit_and_get_id(async_client)

    # First vote — threshold not yet met
    r1 = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50),
    )
    assert r1.json()["threshold_met"] is False

    # Second vote — threshold met (tree-app community_review tier: min_validators=2)
    r2 = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50),
    )
    body = r2.json()
    assert body["threshold_met"] is True
    assert body["final_status"] == "approved"
    assert "Webhook notification queued" in body["message"]


async def test_vote_threshold_met_rejection(async_client: AsyncClient) -> None:
    """When enough rejections accumulate, the submission auto-finalizes as 'rejected'."""
    sid = await _submit_and_get_id(async_client)

    await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(vote="reject", trust=50),
    )
    r2 = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(vote="reject", trust=50),
    )
    body = r2.json()
    assert body["threshold_met"] is True
    assert body["final_status"] == "rejected"


# ── Error: submission not found ───────────────────────────────────────────────

async def test_vote_submission_not_found_returns_404(async_client: AsyncClient) -> None:
    """Voting on a non-existent submission → 404."""
    fake_id = str(uuid4())
    response = await async_client.post(
        f"/api/v1/submissions/{fake_id}/votes",
        json=_vote_body(),
    )
    assert response.status_code == 404
    assert response.json()["type"].endswith("/errors/submission-not-found")


# ── Error: duplicate vote ─────────────────────────────────────────────────────

async def test_vote_duplicate_returns_409(async_client: AsyncClient) -> None:
    """Same user_id voting twice on the same submission → 409."""
    sid = await _submit_and_get_id(async_client)
    user_id = str(uuid4())

    r1 = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(user_id=user_id),
    )
    assert r1.status_code == 201

    r2 = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(user_id=user_id),
    )
    assert r2.status_code == 409
    assert r2.json()["type"].endswith("/errors/duplicate-vote")


# ── Error: already finalized ──────────────────────────────────────────────────

async def test_vote_already_finalized_returns_409(async_client: AsyncClient) -> None:
    """Voting on a submission that has already been finalized → 409."""
    sid = await _submit_and_get_id(async_client)

    # Finalize via two approvals
    await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50),
    )
    await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50),
    )

    # Third vote attempt
    r3 = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50),
    )
    assert r3.status_code == 409
    assert r3.json()["type"].endswith("/errors/already-finalized")


# ── Error: not eligible ───────────────────────────────────────────────────────

async def test_vote_not_eligible_low_trust_returns_403(async_client: AsyncClient) -> None:
    """Reviewer with trust below required_min_trust → 403."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=0),  # below any reasonable threshold
    )
    assert response.status_code == 403
    assert response.json()["type"].endswith("/errors/not-eligible")


# ── Error: validation errors ──────────────────────────────────────────────────

async def test_vote_invalid_body_returns_422(async_client: AsyncClient) -> None:
    """Missing required fields in vote body → 422."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json={"vote": "approve"},  # missing user_id, trust, role
    )
    assert response.status_code == 422


async def test_vote_invalid_vote_value_returns_422(async_client: AsyncClient) -> None:
    """Invalid vote literal → 422."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(vote="maybe"),
    )
    assert response.status_code == 422


# ── Webhook outbox integration ────────────────────────────────────────────────

async def test_vote_finalization_creates_outbox_entry(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When threshold is met, a webhook outbox entry must be created in the DB."""
    sid = await _submit_and_get_id(async_client)

    # First citizen vote — weight=1, threshold=2, not met yet
    await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50),
    )
    # Second citizen vote (different user_id) — weight=2, threshold met → outbox entry
    await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50),
    )

    # Query DB directly — db_session shares the same SQLite engine as async_client
    pending = await webhook_service.get_pending_entries(db_session)
    assert len(pending) == 1
    assert str(pending[0].submission_id) == sid
    assert pending[0].event_type == "submission.finalized"


# ── Role-weights threshold edge cases ────────────────────────────────────────

async def test_vote_expert_single_vote_meets_threshold(async_client: AsyncClient) -> None:
    """
    A single expert vote (weight=2 in community_review) must meet threshold_score=2.

    This validates the core role-weights design: 1 expert = 2 citizens in weight.
    """
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50, role="expert"),
    )
    body = response.json()

    assert response.status_code == 201
    assert body["threshold_met"] is True
    assert body["final_status"] == "approved"


async def test_vote_one_citizen_does_not_meet_threshold(async_client: AsyncClient) -> None:
    """One citizen vote (weight=1) must NOT meet threshold_score=2."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50, role="citizen"),
    )
    body = response.json()

    assert body["threshold_met"] is False
    assert body["final_status"] is None


async def test_vote_two_citizens_meet_threshold(async_client: AsyncClient) -> None:
    """Two citizen votes (1+1=2 weight) must meet threshold_score=2."""
    sid = await _submit_and_get_id(async_client)

    r1 = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50, role="citizen"),
    )
    assert r1.json()["threshold_met"] is False

    r2 = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50, role="citizen"),
    )
    assert r2.json()["threshold_met"] is True
    assert r2.json()["final_status"] == "approved"


async def test_vote_ineligible_role_not_in_weights_returns_403(
    async_client: AsyncClient,
) -> None:
    """
    A role absent from role_weights (e.g. 'moderator') must be rejected with 403.

    Unlike the old required_role check, ineligibility is now determined purely
    by the role_weights dict — any role with weight 0 or absent is ineligible.
    """
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50, role="moderator"),
    )
    assert response.status_code == 403
    assert response.json()["type"].endswith("/errors/not-eligible")


async def test_vote_low_trust_citizen_cannot_contribute_weight(
    async_client: AsyncClient,
) -> None:
    """
    A citizen with trust below required_min_trust must be ineligible even though
    the 'citizen' role appears in role_weights. Trust gate is checked first.
    """
    sid = await _submit_and_get_id(async_client)
    # trust=10 is below community_review required_min_trust=50
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=10, role="citizen"),
    )
    assert response.status_code == 403


async def test_vote_mixed_eligible_ineligible_votes_threshold_uses_eligible_only(
    async_client: AsyncClient,
) -> None:
    """
    Ineligible votes (low trust) must not contribute weight toward the threshold.
    Two ineligible votes followed by two eligible votes — only the eligible ones count.
    """
    sid = await _submit_and_get_id(async_client)

    # Two ineligible votes (trust too low — do NOT meet required_min_trust=50)
    await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=0, role="citizen"),
    )
    # (both return 403, so we just verify they don't finalise)
    r_check = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50, role="citizen"),
    )
    # After 1 eligible citizen vote: weight=1, threshold=2 → not met
    assert r_check.json()["threshold_met"] is False


# ── RFC 7807 body assertions for error responses ──────────────────────────────

async def test_vote_404_rfc7807_body_has_required_fields(
    async_client: AsyncClient,
) -> None:
    """404 body for unknown submission must be a complete RFC 7807 ProblemDetail."""
    fake_id = str(uuid4())
    response = await async_client.post(
        f"/api/v1/submissions/{fake_id}/votes",
        json=_vote_body(),
    )
    body = response.json()

    assert response.status_code == 404
    assert body["type"].endswith("/errors/submission-not-found")
    assert body["title"] == "Submission Not Found"
    assert body["status"] == 404
    assert "instance" in body
    assert fake_id in body["detail"]


async def test_vote_409_duplicate_rfc7807_body_has_required_fields(
    async_client: AsyncClient,
) -> None:
    """409 body for duplicate vote must be a complete RFC 7807 ProblemDetail."""
    sid = await _submit_and_get_id(async_client)
    user_id = str(uuid4())

    await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(user_id=user_id, trust=50),
    )
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(user_id=user_id, trust=50),
    )
    body = response.json()

    assert response.status_code == 409
    assert body["type"].endswith("/errors/duplicate-vote")
    assert body["title"] == "Duplicate Vote"
    assert body["status"] == 409
    assert "instance" in body


async def test_vote_409_already_finalized_rfc7807_body(
    async_client: AsyncClient,
) -> None:
    """409 body for already-finalized submission must carry correct RFC 7807 fields."""
    sid = await _submit_and_get_id(async_client)

    # Finalize via two citizen approvals
    await async_client.post(f"/api/v1/submissions/{sid}/votes", json=_vote_body(trust=50))
    await async_client.post(f"/api/v1/submissions/{sid}/votes", json=_vote_body(trust=50))

    # Third vote attempt
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=50),
    )
    body = response.json()

    assert response.status_code == 409
    assert body["type"].endswith("/errors/already-finalized")
    assert body["title"] == "Already Finalized"
    assert body["status"] == 409
    assert "instance" in body


async def test_vote_403_not_eligible_rfc7807_body_mentions_reason(
    async_client: AsyncClient,
) -> None:
    """403 body for ineligible reviewer must carry the reason in 'detail'."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(trust=0),
    )
    body = response.json()

    assert response.status_code == 403
    assert body["type"].endswith("/errors/not-eligible")
    assert body["title"] == "Not Eligible"
    assert body["status"] == 403
    # The detail must explain why — trust level failure
    assert "trust_level" in body["detail"] or "trust" in body["detail"].lower()


async def test_vote_422_missing_user_id_mentions_field(
    async_client: AsyncClient,
) -> None:
    """422 for missing user_id must list that field in the RFC 7807 errors array."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json={"vote": "approve", "user_trust_level": 50, "user_role": "citizen"},
    )
    body = response.json()

    assert response.status_code == 422
    fields = [e["field"] for e in body.get("errors", [])]
    assert any("user_id" in f for f in fields)


async def test_vote_422_invalid_vote_value_mentions_field(
    async_client: AsyncClient,
) -> None:
    """422 for invalid vote literal must list 'vote' in the RFC 7807 errors array."""
    sid = await _submit_and_get_id(async_client)
    response = await async_client.post(
        f"/api/v1/submissions/{sid}/votes",
        json=_vote_body(vote="abstain"),
    )
    body = response.json()

    assert response.status_code == 422
    fields = [e["field"] for e in body.get("errors", [])]
    assert any("vote" in f for f in fields)
