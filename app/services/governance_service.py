"""
Governance service — application-layer orchestrator for the review task queue.

Winnow is the Governance Authority: it determines which submissions are eligible
for review by a given user, based on the project's governance policy and the
reviewer's trust level and role. No database persistence is performed yet.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3 (Task Orchestration Pattern)
* API contract: docs/architecture/03_api_contracts.md §6
"""

from __future__ import annotations

import logging

from app.registry.manager import registry
from app.schemas.tasks import TaskListResponse

logger = logging.getLogger(__name__)


async def get_available_tasks(
    project_id: str,
    user_trust: int,
    user_role: str,
    page: int = 1,
    per_page: int = 20,
) -> TaskListResponse:
    """
    Return the paginated list of pending submissions the reviewer is eligible to process.

    Steps
    -----
    1. Resolve project config from registry (ProjectNotFoundError → 422 at API layer).
    2. [STUB] Query pending submissions — returns [] until DB layer is added.
    3. Filter via governance_policy.is_eligible_reviewer() for each submission.
    4. Return paginated TaskListResponse.

    Raises
    ------
    ProjectNotFoundError
        If ``project_id`` is not registered in the registry.
    """
    # Step 1 — resolve project config (raises ProjectNotFoundError on unknown project)
    config = registry.get_config(project_id)

    # Step 2 — [STUB] query pending submissions from DB
    # TODO: query DB for pending submissions in this project
    pending_submissions: list = []

    # Step 3 — filter by reviewer eligibility
    eligible = [
        task for task in pending_submissions
        if config.governance_policy.is_eligible_reviewer(
            submission_score=task.confidence_score,
            submission_requirements=task.required_validations,
            reviewer_trust=user_trust,
            reviewer_role=user_role,
        )
    ]

    # Step 4 — paginate and return
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
        },
    )

    return TaskListResponse(
        tasks=page_items,
        total=total,
        page=page,
        per_page=per_page,
    )
