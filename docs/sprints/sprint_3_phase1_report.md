# Sprint 3 — Phase 1 Report: Blueprint Finalization

**Date:** 2026-03-13
**Goal:** Finalize the database design blueprint before writing any SQLAlchemy or Alembic code. Sync `05_database_design.md` with Sprint 2.6 reality, resolve all open trade-offs, and produce a definitive step-by-step implementation plan.

---

## Changes Made

### File Modified
- `docs/architecture/05_database_design.md` — fully revised (see detail below)

---

## Blueprint Changes in Detail

### 1. Status Enum Corrected (`pending_review` replaces `pending_finalization` as initial state)

The previous document listed `pending_finalization` as the default status for new submissions. The Sprint 2.6 governance engine uses `pending_review` as the initial state (confirmed in `app/services/voting_service.py` and `app/schemas/results.py`). The document now:

- Sets `DEFAULT 'pending_review'` on the `submissions.status` column.
- Defines the `CHECK` constraint as `status IN ('pending_review', 'approved', 'rejected', 'superseded')`.
- Adds a backward-compatibility note explaining that `pending_finalization` is retained only in the Pydantic `ScoringResultResponse` schema for legacy wire compatibility.
- Updates the status lifecycle table and the Mermaid `stateDiagram-v2` to use `pending_review` as the entry state.
- Updates §2.6 and §2.7 stale-submission queries to filter on `pending_review`.

### 2. `superseded_by` Column Added to `submissions`

The column was referenced in ADR-DB-005 consequences but was absent from the table definition and ER diagram. It is now formally documented:

- `superseded_by UUID NULL FK → submissions.id` — self-referential, nullable.
- Added to the ER diagram with the `|o--o|` self-referential relationship.
- Included in the §4.2 migration step for `submissions`.

### 3. `required_validations` JSONB Description Updated

The column description now explicitly documents the Sprint 2.6 role-weights governance pattern:
- `threshold_score` (int) = minimum accumulated role-weight sum to trigger finalization.
- `role_weights` (dict[str, int]) = reviewer role → weight per vote.
- Old `min_validators` / `required_role` fields are explicitly noted as replaced.

### 4. ADR-DB-005 Promoted from "Proposed" to "Accepted"

`superseded` was already fully implemented in Sprint 2.6 (`PATCH /supersede` endpoint, `SupersedeRequest` schema, `SupersedeResponse` schema, `scoring_service.supersede_submission()`). The ADR status was incorrectly left as "Proposed". Now marked **Accepted — implemented in Sprint 2.6**.

### 5. ADR-DB-004 Unified and Clarified

The old document had two separate ADRs for idempotency and finalization locking that overlapped. ADR-DB-004 now covers both contexts (submission insert path and vote/finalization path) with explicit rejection of `INSERT ... ON CONFLICT DO NOTHING + re-SELECT` and Redis cache alternatives.

### 6. §4.2 Migration Plan Expanded from 2 to 4 Tables

The original §4.2 only listed `submissions` and `scoring_results`. It now documents all four Sprint 3 tables in dependency order:
1. `submissions` (with `superseded_by` self-FK and full CHECK constraints)
2. `scoring_results`
3. `submission_votes`
4. `webhook_outbox`

It also documents the `downgrade()` drop order for rollback safety.

### 7. §5 SQLAlchemy Notes Expanded

- Added `§5.4 JSONB Column Typing` — documents use of `sqlalchemy.dialects.postgresql.JSONB` and the Pydantic `.model_dump()` / `model_validate()` contract at the service layer.
- `§5.3 Relationship Mapping` expanded to include all three relationships (`scoring_result`, `votes`, `outbox_events`).
- `§5.2 SubmissionStatus` enum updated to include `PENDING_REVIEW`.

### 8. New §6 — Sprint 3 Step-by-Step Implementation Plan

A 12-step ordered implementation plan added, covering: base mixins → ORM models → DB session → Alembic migration → service layer DB replacement (submission, voting, supersede, webhook outbox poller) → integration tests.

### 9. `webhook_outbox` Table — `payload` Column Description Clarified

Now explicitly references `WebhookEvent` from `app/schemas/webhooks.py` as the serialization source, aligning documentation with the actual implementation.

---

## Trade-Off Summary (All Decisions Final)

| Trade-off | Decision | Rationale |
|---|---|---|
| Global vs per-project tables | **Global `submissions` table** | One ORM model, transparent partitioning path via `PARTITION BY LIST (project_id)` when needed. |
| JSONB vs normalised breakdown | **JSONB** | Always read/written as a unit; structure varies per project; no operational query benefit from normalisation. |
| `SELECT FOR UPDATE` vs `ON CONFLICT` | **`SELECT ... FOR UPDATE`** | Handles both idempotency and finalization locking consistently; distinguishes `200` duplicate from `201` new in one round-trip. |
| `superseded` status | **Accepted & implemented** | Audit-safe, trust-neutral retirement; enforced via dedicated endpoint with `Literal["superseded"]` schema guard. |
| PostgreSQL ENUM vs VARCHAR + CHECK | **VARCHAR + CHECK** | `ALTER TYPE ADD VALUE` cannot run in a transaction; VARCHAR + CHECK is safe to extend in any migration. |
| GIN indexes on JSONB | **Deferred** | No current operational query path needs containment operators; add targeted expression indexes in Phase 2 if analytics demand it. |

---

## Files Touched

| File | Change |
|---|---|
| `docs/architecture/05_database_design.md` | Fully revised — status sync, `superseded_by` column, ADR promotions, 4-table migration plan, implementation plan §6 |

---

## Tests Passed

No code was written in this phase. All existing tests remain unaffected. Sprint 3 Phase 2 (ORM models + Alembic migration) will introduce the first DB-layer tests.
