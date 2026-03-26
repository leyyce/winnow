"""
Governance service — DB-backed review task queue.

Sprint 5 (Lifecycle Ledger) changes
-------------------------------------
* Queries ``status_ledger`` (latest entry per submission) instead of the
  removed ``scoring_results`` table.
* Uses ``scoring_snapshots`` for confidence_score and required_validations.
* Eligibility evaluated via ``governance_policy.get_vote_weight()`` which
  implements the new role_configs / default_config / blocked_roles model.

Sprint 6 (Post-Sprint refinements)
------------------------------------
* ``required_validations`` in ``scoring_snapshots`` is now a JSON array of
  tier dicts.  All tiers are parsed into ``list[RequiredValidations]`` and
  exposed as ``review_tiers`` in ``TaskItem``.
* Reviewer eligibility: eligible if they qualify in at least one tier.
* ``active_votes`` list populated from the latest-wins vote resolution.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3
* API contract: docs/architecture/03_api_contracts.md §6
* Database:     docs/architecture/05_database_design.md §6 step 9 (task list)
* Rule 9:       Services never import fastapi / never raise HTTPException
"""
from __future__ import annotations

import logging
from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotEligibleError
from app.governance.base import GovernancePolicy
from app.models.scoring_snapshot import ScoringSnapshot
from app.models.status_ledger import StatusLedger, StatusLedgerStatus
from app.models.submission import Submission
from app.schemas.results import RequiredValidations
from app.schemas.tasks import TaskItem, TaskListResponse
from app.services.scoring_service import _get_active_votes

logger = logging.getLogger(__name__)


async def get_available_tasks(
    project_id: str,
    user_trust: int,
    user_role: str,
    db: AsyncSession,
    page: int = 1,
    per_page: int = 20,
) -> TaskListResponse:
    """
    Return the paginated list of pending submissions the reviewer may process.

    Steps
    -----
    1. Resolve project config (ProjectNotFoundError → 422 at API layer).
    2. Query all ``pending_review`` submissions for ``project_id`` by joining
       to the latest ``status_ledger`` entry per submission.
    3. Join to ``scoring_snapshots`` for confidence_score and governance data.
    4. Filter via ``governance_policy.get_vote_weight()`` for eligibility
       across all applicable tiers.
    5. Populate ``active_votes`` for each eligible task.
    6. Paginate and return ``TaskListResponse``.
    """

    # Subquery: latest status_ledger created_at per submission
    latest_ledger_subq = (
        select(
            StatusLedger.submission_id,
            func.max(StatusLedger.created_at).label("max_created_at"),
        )
        .group_by(StatusLedger.submission_id)
        .subquery()
    )

    # Subquery: latest scoring_snapshot created_at per submission
    latest_snapshot_subq = (
        select(
            ScoringSnapshot.submission_id,
            func.max(ScoringSnapshot.created_at).label("max_created_at"),
        )
        .group_by(ScoringSnapshot.submission_id)
        .subquery()
    )

    stmt = (
        select(Submission, StatusLedger, ScoringSnapshot)
        .join(
            StatusLedger,
            StatusLedger.submission_id == Submission.submission_id,
        )
        .join(
            latest_ledger_subq,
            (latest_ledger_subq.c.submission_id == StatusLedger.submission_id)
            & (latest_ledger_subq.c.max_created_at == StatusLedger.created_at),
        )
        .join(
            ScoringSnapshot,
            ScoringSnapshot.submission_id == Submission.submission_id,
        )
        .join(
            latest_snapshot_subq,
            (latest_snapshot_subq.c.submission_id == ScoringSnapshot.submission_id)
            & (latest_snapshot_subq.c.max_created_at == ScoringSnapshot.created_at),
        )
        .where(
            Submission.project_id == project_id,
            StatusLedger.status == StatusLedgerStatus.PENDING_REVIEW,
        )
        .order_by(Submission.created_at.asc())
    )

    rows = (await db.execute(stmt)).all()

    # Filter by reviewer eligibility — eligible in at least one applicable tier
    eligible: list[TaskItem] = []
    for submission, ledger_row, snapshot_row in rows:
        # Parse all tiers from the JSON array snapshot
        all_tiers: list[RequiredValidations] = [
            RequiredValidations.model_validate(r)
            for r in snapshot_row.required_validations
        ]

        # Check eligibility across tiers
        eligible_in_any = False
        for tier in all_tiers:
            try:
                GovernancePolicy.get_vote_weight(tier, user_role, user_trust)
                eligible_in_any = True
                break
            except NotEligibleError:
                continue

        if not eligible_in_any:
            continue

        submitted_at = submission.created_at
        if submitted_at.tzinfo is None:
            submitted_at = submitted_at.replace(tzinfo=timezone.utc)

        active_votes = await _get_active_votes(submission.submission_id, db)

        eligible.append(
            TaskItem(
                submission_id=submission.submission_id,
                project_id=submission.project_id,
                entity_type=submission.entity_type,
                confidence_score=snapshot_row.confidence_score,
                review_tiers=all_tiers,
                active_votes=active_votes,
                submitted_at=submitted_at,
            )
        )

    total = len(eligible)
    start = (page - 1) * per_page
    page_items = eligible[start : start + per_page]

    logger.info(
        "Task list requested",
        extra={
            "project_id": project_id,
            "user_trust": user_trust,
            "user_role": user_role,
            "eligible_total": total,
            "page": page,
        },
    )

    return TaskListResponse(
        tasks=page_items,
        total=total,
        page=page,
        per_page=per_page,
    )
