"""
Scoring service — orchestrates the Lifecycle Ledger submission flow.

Sprint 5 (Lifecycle Ledger) full rewrite
-----------------------------------------
* ``submissions`` + ``submission_user_snapshots`` + ``scoring_snapshots`` +
  ``status_ledger`` replace the old ``submissions`` + ``scoring_results`` pair.
* Trust delta is computed using a ``WITH RECURSIVE`` CTE that traverses the
  full ``supersedes`` lineage — O(chain depth), single round-trip (Mandate 4).
* Every ``status_ledger`` INSERT is immediately followed by a
  ``webhook_outbox`` INSERT in the same transaction (Rule 11).
* Auto-supersede uses ``SELECT … FOR UPDATE`` to prevent race conditions
  (Mandate 3 / ADR-DB-004).
* ``total_submissions`` is calculated at creation time via a correlated
  subquery that counts active chains for the submitting user.

Case mapping
------------
A: Initial submission            → single ledger entry, supersedes=NULL
B: User edit (triplet collision)  → Chain B's first entry supersedes last(A)
C: User withdrawal                → ``withdraw_submission`` appends voided entry
D: Auto-finalization              → status='approved'/'rejected' on first entry

References
----------
* Database design: docs/architecture/05_database_design.md
* Rule 9:  Services never import fastapi / never raise HTTPException
* Rule 10: Immutable Submissions & Append-Only State
* Rule 11: Event-driven state transitions via Transactional Outbox
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4


def _ensure_tz(dt: datetime | None) -> datetime:
    """Attach UTC timezone to naive datetimes returned by SQLite."""
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    InvalidEntityTypeError,
    SubmissionNotFoundError,
)
from app.models.scoring_snapshot import ScoringSnapshot
from app.models.status_ledger import (
    TERMINAL_STATES,
    StatusLedger,
    StatusLedgerStatus,
    SupersedeReason,
)
from app.models.submission import Submission
from app.models.submission_user_snapshot import SubmissionUserSnapshot
from app.models.submission_vote import SubmissionVote
from app.registry.manager import registry
from app.schemas.envelope import SubmissionEnvelope
from app.schemas.results import RequiredValidations, RuleBreakdown, ScoringResultResponse
from app.schemas.voting import ActiveVoteItem
from app.services import webhook_service

logger = logging.getLogger(__name__)


# ── Event type mapping ────────────────────────────────────────────────────────

_EVENT_TYPE_MAP: dict[tuple[str, str | None], str] = {
    (StatusLedgerStatus.PENDING_REVIEW, None): "submission.created",
    (StatusLedgerStatus.APPROVED, SupersedeReason.AUTO_APPROVE): "submission.auto_approved",
    (StatusLedgerStatus.REJECTED, SupersedeReason.AUTO_REJECT): "submission.auto_rejected",
    (StatusLedgerStatus.VOIDED, SupersedeReason.EDITED): "submission.superseded",
    (StatusLedgerStatus.VOIDED, SupersedeReason.DELETED): "submission.withdrawn",
    (StatusLedgerStatus.APPROVED, SupersedeReason.VOTING_CONCLUDED): "submission.approved",
    (StatusLedgerStatus.REJECTED, SupersedeReason.VOTING_CONCLUDED): "submission.rejected",
    (StatusLedgerStatus.APPROVED, SupersedeReason.ADMIN_OVERWRITE): "submission.admin_overridden",
    (StatusLedgerStatus.REJECTED, SupersedeReason.ADMIN_OVERWRITE): "submission.admin_overridden",
    (StatusLedgerStatus.VOIDED, SupersedeReason.ADMIN_OVERWRITE): "submission.admin_overridden",
}


def _event_type(status: str, reason: str | None) -> str:
    return _EVENT_TYPE_MAP.get((status, reason), f"submission.{status}")


# ── Trust target values (from TrustAdvisor config) ────────────────────────────

def _target_trust(status: str, config) -> int:
    """
    Return the target absolute trust level for a given terminal status.
    Reads from the project's TrustAdvisorConfig — no hardcoded values (Rule 3).
    For pending_review and voided the target is always 0.
    """
    if status == StatusLedgerStatus.APPROVED:
        return config.trust_advisor._config.reward_per_approval
    if status == StatusLedgerStatus.REJECTED:
        return -config.trust_advisor._config.penalty_per_rejection
    return 0


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _latest_ledger_entry(
    submission_id: UUID,
    db: AsyncSession,
    *,
    for_update: bool = False,
) -> StatusLedger | None:
    """Return the most recent status_ledger row for a submission."""
    stmt = (
        select(StatusLedger)
        .where(StatusLedger.submission_id == submission_id)
        .order_by(StatusLedger.created_at.desc())
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _latest_scoring_snapshot(
    submission_id: UUID,
    db: AsyncSession,
) -> ScoringSnapshot | None:
    """Return the most recent scoring_snapshot row for a submission."""
    stmt = (
        select(ScoringSnapshot)
        .where(ScoringSnapshot.submission_id == submission_id)
        .order_by(ScoringSnapshot.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _resolve_lineage_sum(
    supersedes_id: UUID | None,
    db: AsyncSession,
) -> int:
    """
    Compute the SUM of trust_deltas across the full supersedes lineage using
    a WITH RECURSIVE CTE — O(chain depth), single DB round-trip (Mandate 4).

    Returns 0 if supersedes_id is None (initial entry in a fresh chain).

    Works on both PostgreSQL and SQLite 3.8+ (aiosqlite).
    """
    if supersedes_id is None:
        return 0

    # Base case: the entry being superseded
    base_stmt = select(
        StatusLedger.id,
        StatusLedger.trust_delta,
        StatusLedger.supersedes,
    ).where(StatusLedger.id == supersedes_id)
    lineage_cte = base_stmt.cte("lineage", recursive=True)

    # Recursive step: walk backwards through the chain
    recursive_stmt = select(
        StatusLedger.id,
        StatusLedger.trust_delta,
        StatusLedger.supersedes,
    ).join(lineage_cte, StatusLedger.id == lineage_cte.c.supersedes)

    lineage_cte = lineage_cte.union_all(recursive_stmt)

    sum_stmt = select(func.coalesce(func.sum(lineage_cte.c.trust_delta), 0))
    result = await db.execute(sum_stmt)
    return result.scalar() or 0


async def _find_triplet_match(
    project_id: str,
    entity_id: UUID,
    measurement_id: UUID,
    db: AsyncSession,
) -> Submission | None:
    """Find an existing submission matching the identity triplet."""
    stmt = select(Submission).where(
        Submission.project_id == project_id,
        Submission.entity_id == entity_id,
        Submission.measurement_id == measurement_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _count_active_submissions(
    user_id: UUID,
    project_id: str,
    db: AsyncSession,
) -> int:
    """
    Count active submission chains for the user in this project.

    A chain is active if its latest status_ledger entry is NOT voided with
    supersede_reason in ('edited', 'deleted').  Calculated once at creation
    time and stored as a static snapshot integer.
    """
    # Correlated subquery: find the latest ledger entry per submission
    latest_ledger = (
        select(StatusLedger.status, StatusLedger.supersede_reason)
        .where(StatusLedger.submission_id == Submission.submission_id)
        .order_by(StatusLedger.created_at.desc())
        .limit(1)
        .correlate(Submission)
        .scalar_subquery()
    )

    # We need status and supersede_reason separately — use two correlated subqueries
    latest_status = (
        select(StatusLedger.status)
        .where(StatusLedger.submission_id == Submission.submission_id)
        .order_by(StatusLedger.created_at.desc())
        .limit(1)
        .correlate(Submission)
        .scalar_subquery()
    )
    latest_reason = (
        select(StatusLedger.supersede_reason)
        .where(StatusLedger.submission_id == Submission.submission_id)
        .order_by(StatusLedger.created_at.desc())
        .limit(1)
        .correlate(Submission)
        .scalar_subquery()
    )

    stmt = select(func.count()).select_from(Submission).where(
        Submission.user_id == user_id,
        Submission.project_id == project_id,
        # Exclude voided chains caused by user action (edit or delete)
        ~(
            (latest_status == StatusLedgerStatus.VOIDED)
            & (latest_reason.in_(["edited", "deleted"]))
        ),
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def _get_active_votes(
    submission_id: UUID,
    db: AsyncSession,
) -> list[ActiveVoteItem]:
    """
    Return the latest-wins resolved vote list for all reviewers on a submission.

    Uses a subquery to select the most recent ``created_at`` per user, then
    joins back to get the full row — identical to the VotingService tally
    helper but returns ``ActiveVoteItem`` schema objects for API responses.
    """
    from datetime import timezone

    max_per_user = (
        select(
            SubmissionVote.user_id,
            func.max(SubmissionVote.created_at).label("max_created"),
        )
        .where(SubmissionVote.submission_id == submission_id)
        .group_by(SubmissionVote.user_id)
        .subquery()
    )
    stmt = select(SubmissionVote).join(
        max_per_user,
        (SubmissionVote.user_id == max_per_user.c.user_id)
        & (SubmissionVote.created_at == max_per_user.c.max_created)
        & (SubmissionVote.submission_id == submission_id),
    )
    rows = (await db.execute(stmt)).scalars().all()
    items = []
    for v in rows:
        created = v.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        items.append(
            ActiveVoteItem(
                user_id=v.user_id,
                user_role=v.user_role,
                vote=v.vote,
                is_override=v.is_override,
                note=v.note,
                created_at=created,
            )
        )
    return items


async def _enqueue_webhook(
    *,
    ledger_entry: StatusLedger,
    submission: Submission,
    snapshot: ScoringSnapshot,
    webhook_url: str | None,
    db: AsyncSession,
) -> None:
    """Enqueue a webhook event for a status_ledger INSERT."""
    et = _event_type(ledger_entry.status, ledger_entry.supersede_reason)
    occurred_at = _ensure_tz(ledger_entry.created_at)
    await webhook_service.enqueue_ledger_event(
        ledger_entry_id=ledger_entry.id,
        submission_id=submission.submission_id,
        project_id=submission.project_id,
        entity_id=submission.entity_id,
        entity_type=submission.entity_type,
        new_status=ledger_entry.status,
        supersede_reason=ledger_entry.supersede_reason,
        trust_delta=ledger_entry.trust_delta,
        confidence_score=snapshot.confidence_score,
        occurred_at=occurred_at,
        event_type=et,
        webhook_url=webhook_url,
        db=db,
    )


def _build_response(
    submission: Submission,
    snapshot: ScoringSnapshot,
    ledger_entry: StatusLedger,
    active_votes: list[ActiveVoteItem] | None = None,
) -> ScoringResultResponse:
    """Assemble a ScoringResultResponse from ORM objects."""
    from app.schemas.results import ThresholdConfig

    breakdown = [RuleBreakdown.model_validate(r) for r in snapshot.breakdown]
    # required_validations is stored as a JSON array of tier dicts
    required_list = [
        RequiredValidations.model_validate(r) for r in snapshot.required_validations
    ]
    thresholds = ThresholdConfig.model_validate(snapshot.thresholds)

    return ScoringResultResponse(
        submission_id=submission.submission_id,
        project_id=submission.project_id,
        entity_type=submission.entity_type,
        entity_id=submission.entity_id,
        measurement_id=submission.measurement_id,
        status=ledger_entry.status,
        supersede_reason=ledger_entry.supersede_reason,
        trust_delta=ledger_entry.trust_delta,
        confidence_score=snapshot.confidence_score,
        breakdown=breakdown,
        required_validations=required_list,
        thresholds=thresholds,
        active_votes=active_votes or [],
        ledger_entry_id=ledger_entry.id,
        created_at=_ensure_tz(submission.created_at),
    )


# ── Public API ────────────────────────────────────────────────────────────────

async def process_submission(
    envelope: SubmissionEnvelope,
    db: AsyncSession,
    requesting_user_id: UUID | None = None,
) -> ScoringResultResponse:
    """
    Orchestrate Stage 1 validation → scoring → Lifecycle Ledger persistence.

    Idempotent: if ``submission_id`` already exists the stored result is
    returned without re-scoring (ADR-DB-003 / Rule 10).

    Steps
    -----
    1. Resolve project config; validate entity_type.
    2. Idempotency check.
    3. Stage 1: validate raw payload.
    4. Stage 2+4: run scoring pipeline.
    5. Determine governance requirements (RequiredValidations snapshot).
    6. Count active submissions for the user snapshot.
    7. Auto-supersede: detect triplet collision with FOR UPDATE lock.
    8. Determine initial status from thresholds.
    9. Compute trust_delta via WITH RECURSIVE CTE lineage sum.
    10. INSERT submissions, submission_user_snapshots, scoring_snapshots.
    11. INSERT status_ledger + enqueue webhook.
    12. Return ScoringResultResponse.

    Raises
    ------
    InvalidEntityTypeError  → 422
    ConflictError           → 409 (triplet matches a terminal chain)
    pydantic.ValidationError → 422
    """
    project_id = envelope.metadata.project_id
    submission_id = envelope.metadata.submission_id
    entity_type = envelope.metadata.entity_type
    entity_id = envelope.metadata.entity_id
    measurement_id = envelope.metadata.measurement_id
    user_ctx = envelope.user_context

    # Step 1 — resolve project config + entity_type gate
    config = registry.get_config(project_id)
    if entity_type not in config.valid_entity_types:
        raise InvalidEntityTypeError(entity_type, project_id, config.valid_entity_types)

    # Step 2 — idempotency: return stored result if already processed
    existing = await db.get(Submission, submission_id)
    if existing is not None:
        snapshot = await _latest_scoring_snapshot(submission_id, db)
        ledger = await _latest_ledger_entry(submission_id, db)
        active_votes = await _get_active_votes(submission_id, db)
        logger.info("Idempotent re-submission — returning stored result",
                    extra={"submission_id": str(submission_id)})
        return _build_response(existing, snapshot, ledger, active_votes)

    # Step 3 — Stage 1: validate payload
    validated_payload = config.payload_schema.model_validate(envelope.payload)

    # Step 4 — Stage 2+4: run scoring pipeline
    pipeline_result = config.pipeline.run(validated_payload, user_ctx)

    # Step 5 — governance requirements (full snapshot for VotingService)
    required = config.governance_policy.determine_requirements(
        pipeline_result.total_score, user_ctx
    )
    weight_map = {rule.name: rule.weight for rule in config.pipeline.rules}
    breakdown = [
        RuleBreakdown(
            rule=r.rule_name,
            weight=(w := weight_map.get(r.rule_name, 0.0)),
            score=r.score,
            weighted_score=r.score * w * 100.0,
            details=r.details,
        )
        for r in pipeline_result.breakdown
    ]

    # Step 6 — count active submissions for user snapshot
    total_submissions = await _count_active_submissions(user_ctx.user_id, project_id, db)

    # Step 7 — auto-supersede: detect triplet collision
    prev_submission = await _find_triplet_match(project_id, entity_id, measurement_id, db)
    supersedes_ledger_id: UUID | None = None

    if prev_submission is not None:
        # Lock the latest ledger entry of the old chain (FOR UPDATE)
        prev_ledger = await _latest_ledger_entry(
            prev_submission.submission_id, db, for_update=True
        )
        if prev_ledger is not None:
            if prev_ledger.status in TERMINAL_STATES:
                raise ConflictError(prev_submission.submission_id, prev_ledger.status)
            # Active edit — Chain B's first entry will supersede this entry
            supersedes_ledger_id = prev_ledger.id

    # Step 8 — determine initial status from thresholds
    thresholds = config.thresholds
    if pipeline_result.total_score >= thresholds.auto_approve_min:
        initial_status = StatusLedgerStatus.APPROVED
        supersede_reason: str | None = SupersedeReason.AUTO_APPROVE
    elif pipeline_result.total_score < thresholds.manual_review_min:
        initial_status = StatusLedgerStatus.REJECTED
        supersede_reason = SupersedeReason.AUTO_REJECT
    else:
        initial_status = StatusLedgerStatus.PENDING_REVIEW
        supersede_reason = None

    # Cross-chain edit (Case B): the backward pointer always carries reason='edited',
    # regardless of whether the new submission is auto-finalized or pending.
    if supersedes_ledger_id is not None:
        supersede_reason = SupersedeReason.EDITED

    # Step 9 — trust_delta via WITH RECURSIVE lineage CTE
    lineage_sum = await _resolve_lineage_sum(supersedes_ledger_id, db)
    target = _target_trust(initial_status, config)
    trust_delta = target - lineage_sum

    # Step 10 — INSERT submissions + user snapshot + scoring snapshot
    submission = Submission(
        submission_id=submission_id,
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        measurement_id=measurement_id,
        user_id=user_ctx.user_id,
        raw_payload=validated_payload.model_dump(mode="json"),
    )
    db.add(submission)
    await db.flush()  # get submission_id into session

    user_snapshot = SubmissionUserSnapshot(
        submission_id=submission_id,
        user_id=user_ctx.user_id,
        role=user_ctx.role,
        trust_level=user_ctx.trust_level,
        username=user_ctx.username,
        total_submissions=total_submissions,
        user_account_created_at=user_ctx.account_created_at,
        user_account_updated_at=(
            user_ctx.account_updated_at or user_ctx.account_created_at
        ),
        custom_data=user_ctx.custom_data,
    )
    db.add(user_snapshot)

    snapshot = ScoringSnapshot(
        id=uuid4(),
        submission_id=submission_id,
        confidence_score=pipeline_result.total_score,
        breakdown=[r.model_dump(mode="json") for r in breakdown],
        required_validations=[r.model_dump(mode="json") for r in required],
        thresholds=thresholds.model_dump(mode="json"),
    )
    db.add(snapshot)
    await db.flush()  # get snapshot.id for ledger FK

    # Step 11 — INSERT status_ledger + enqueue webhook
    ledger_entry = StatusLedger(
        id=uuid4(),
        submission_id=submission_id,
        scoring_snapshot_id=snapshot.id,
        status=initial_status,
        trust_delta=trust_delta,
        supersedes=supersedes_ledger_id,
        supersede_reason=supersede_reason,
    )
    db.add(ledger_entry)
    await db.flush()

    await _enqueue_webhook(
        ledger_entry=ledger_entry,
        submission=submission,
        snapshot=snapshot,
        webhook_url=config.webhook_url,
        db=db,
    )

    logger.info(
        "Submission processed",
        extra={
            "submission_id": str(submission_id),
            "project_id": project_id,
            "status": initial_status,
            "confidence_score": pipeline_result.total_score,
            "trust_delta": trust_delta,
        },
    )

    return _build_response(submission, snapshot, ledger_entry)


async def withdraw_submission(
    submission_id: UUID,
    db: AsyncSession,
) -> ScoringResultResponse:
    """
    Withdraw a pending submission (Case C).

    Appends a new ``status_ledger`` row with status='voided' and
    supersede_reason='deleted'.  Only permitted when current status is
    'pending_review'.  Returns 409 for terminal submissions.

    Raises
    ------
    SubmissionNotFoundError → 404
    ConflictError           → 409 (already terminal)
    """
    submission = await db.get(Submission, submission_id)
    if submission is None:
        raise SubmissionNotFoundError(submission_id)

    # Lock the latest ledger entry (FOR UPDATE — race condition prevention)
    current_ledger = await _latest_ledger_entry(submission_id, db, for_update=True)
    if current_ledger is None or current_ledger.status != StatusLedgerStatus.PENDING_REVIEW:
        raise ConflictError(submission_id, getattr(current_ledger, "status", "unknown"))

    snapshot = await _latest_scoring_snapshot(submission_id, db)
    config = registry.get_config(submission.project_id)

    # No trust was awarded for pending — lineage sum equals 0 for voided
    lineage_sum = await _resolve_lineage_sum(current_ledger.id, db)
    target = _target_trust(StatusLedgerStatus.VOIDED, config)
    trust_delta = target - lineage_sum

    ledger_entry = StatusLedger(
        id=uuid4(),
        submission_id=submission_id,
        scoring_snapshot_id=snapshot.id,
        status=StatusLedgerStatus.VOIDED,
        trust_delta=trust_delta,
        supersedes=current_ledger.id,
        supersede_reason=SupersedeReason.DELETED,
    )
    db.add(ledger_entry)
    await db.flush()

    await _enqueue_webhook(
        ledger_entry=ledger_entry,
        submission=submission,
        snapshot=snapshot,
        webhook_url=config.webhook_url,
        db=db,
    )

    logger.info("Submission withdrawn", extra={"submission_id": str(submission_id)})
    return _build_response(submission, snapshot, ledger_entry)


async def get_submission_result(
    submission_id: UUID,
    db: AsyncSession,
    requesting_user_id: UUID | None = None,
) -> ScoringResultResponse:
    """
    Retrieve the current result for a submission.

    Raises SubmissionNotFoundError if not found.
    """
    submission = await db.get(Submission, submission_id)
    if submission is None:
        raise SubmissionNotFoundError(submission_id)

    snapshot = await _latest_scoring_snapshot(submission_id, db)
    ledger = await _latest_ledger_entry(submission_id, db)
    active_votes = await _get_active_votes(submission_id, db)

    return _build_response(submission, snapshot, ledger, active_votes)
