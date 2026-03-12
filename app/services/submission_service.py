"""
Submission service — thin coordinator that delegates to the scoring service.

This module exists as a named boundary in the service layer so that future
additions (e.g. idempotency checks, pre-submission rate-limiting) have a
clear home without modifying the scoring service directly.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
"""

from __future__ import annotations

from app.schemas.envelope import SubmissionEnvelope
from app.schemas.results import ScoringResultResponse
from app.services import scoring_service


async def submit(envelope: SubmissionEnvelope) -> ScoringResultResponse:
    """
    Accept a submission envelope and delegate orchestration to the scoring service.

    Future work: idempotency check (return existing result if submission_id
    already exists in DB) would be inserted here before delegating.
    """
    # TODO: idempotency check — if submission_id already exists, return cached result
    return await scoring_service.process_submission(envelope)
