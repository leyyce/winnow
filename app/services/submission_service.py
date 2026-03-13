"""
Submission service — thin coordinator that delegates to the scoring service.

This module exists as a named boundary in the service layer so that future
additions (e.g. pre-submission rate-limiting, quota checks) have a clear
home without modifying the scoring service directly.

The ``db`` session is threaded through from the API endpoint dependency so
that the entire submission lifecycle (score + persist) runs in a single
transaction managed by ``get_db``.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
* Database:     docs/architecture/05_database_design.md §6 step 8
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.envelope import SubmissionEnvelope
from app.schemas.results import ScoringResultResponse
from app.services import scoring_service


async def submit(
    envelope: SubmissionEnvelope,
    db: AsyncSession,
) -> ScoringResultResponse:
    """
    Accept a submission envelope, score it, and persist the result.

    Delegates entirely to ``scoring_service.process_submission`` which:
    - Checks idempotency (returns stored result if submission_id exists)
    - Runs the scoring pipeline
    - Persists Submission + ScoringResult in one atomic flush

    The submission is stored in the DB, so no separate
    ``voting_service.register_submission`` call is needed — the voting
    service queries the DB directly for all subsequent operations.

    Parameters
    ----------
    envelope:
        Validated ``SubmissionEnvelope`` from the API layer.
    db:
        Async SQLAlchemy session injected by the ``get_db`` FastAPI dependency.

    Returns
    -------
    ScoringResultResponse
        Full scoring result with governance requirements.
    """
    return await scoring_service.process_submission(envelope, db)
