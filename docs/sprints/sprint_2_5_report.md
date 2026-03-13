# Sprint 2.5 Retrospective Report
**Date Range:** 2026-03-13
**Theme:** Database Design Documentation & Schema Formalisation

---

## Goal

Before implementing any ORM models or Alembic migrations, produce a comprehensive, authoritative database design document (`05_database_design.md`) that captures the full relational schema, indexing strategy, ADRs, and migration plan — serving as the Single Source of Truth for all future persistence work.

---

## Commits in This Sprint

| Hash | Date | Message |
|---|---|---|
| `abf66b3` | 2026-03-13 | Created database design doc |

---

## Architectural Milestones

### 1. `05_database_design.md` — Authoritative Persistence Contract
- Created `docs/architecture/05_database_design.md` as the complete reference for Winnow's database layer, covering every table, column, type, constraint, index, and relationship.
- Documented the **core Winnow tables** owned exclusively by the framework (Rule 5 — Domain Ownership):
  - `submissions` — point-in-time snapshots of inbound envelopes; immutable after insert (Rule 10).
  - `scoring_results` — one-to-one with `submissions`; stores pipeline output, rule breakdown, and final status.
  - `project_configs` — registry of active projects and their `ProjectConfig` JSON blobs.
  - `outbox_events` — transactional outbox table for guaranteed async webhook delivery (Rule 11).
  - `votes` — multi-vote tracking table for the governance voting workflow (previewed for Sprint 2.6).
- Explicitly confirmed that **no domain entity tables** (trees, users, measurements) are created inside Winnow, maintaining strict domain boundary separation.

### 2. ER Diagram & Relationship Formalisation
- Documented the full Entity-Relationship structure: `submissions` ←1:1→ `scoring_results`, `submissions` ←N:1→ `project_configs`, `submissions` ←1:N→ `votes`, `submissions` ←1:N→ `outbox_events`.
- Specified foreign key constraints, cascade behaviours, and nullable columns.

### 3. Indexing Strategy
- Defined index strategy per table: composite and single-column indices on high-cardinality lookup columns (`project_id`, `submitter_id`, `status`, `created_at`).
- Documented rationale for each index in terms of expected query patterns (submission lookups by project, outbox polling, vote aggregation).

### 4. Architectural Decision Records (ADRs)
- Documented all significant persistence design decisions inline as ADR blocks within `05_database_design.md` (Rule 12):
  - **ADR-DB-01:** Use of PostgreSQL `JSONB` for the `payload` column to accommodate dynamic, project-specific domain data without schema migrations per project.
  - **ADR-DB-02:** `scoring_results` stored in a separate table (not embedded in `submissions`) to support independent querying and future result versioning.
  - **ADR-DB-03:** Outbox pattern chosen over direct webhook dispatch to guarantee delivery under failure conditions (Rule 11).
  - **ADR-DB-04:** `votes` as a separate table (not a JSONB blob) to enable aggregation queries and idempotency enforcement at the database level.

### 5. Idempotency, Race Conditions & Supersede Handling
- Documented idempotency implementation: unique constraint on `(project_id, submitter_id, payload_hash)` for duplicate submission detection.
- Described race condition mitigations for concurrent vote inserts (unique constraint on `(submission_id, voter_id)`).
- Formally defined the `superseded` state transition: old submissions transition to `superseded` when a new submission from the same submitter supersedes them; the payload itself is never mutated (Rule 10).

### 6. Alembic Migration Plan
- Outlined the planned Alembic migration sequence: `0001_create_project_configs` → `0002_create_submissions` → `0003_create_scoring_results` → `0004_create_outbox_events` → `0005_create_votes`.
- Confirmed all migrations are additive-only (no destructive `ALTER` or `DROP` in initial versions).

### 7. SQLAlchemy Model Design Notes
- Specified the mapping between database columns and SQLAlchemy ORM model attributes.
- Noted use of `mapped_column` (SQLAlchemy 2.x style) and `Mapped[]` type annotations for all models.

---

## Files Introduced / Modified

| File | Status |
|---|---|
| `docs/architecture/05_database_design.md` | Created |

---

## Tests Status

No new executable tests in this sprint — this was a documentation-only sprint. The existing test suite (from Sprints 1 & 2) continued to pass without modification.

---

## Architectural Rules Applied

| Rule | How Applied |
|---|---|
| Rule 1 | Database schema fully designed and documented before any ORM/migration code. |
| Rule 5 | Explicitly confirmed no domain entity tables in Winnow's schema. |
| Rule 7 | Database layer documented as a standalone deliverable before implementation begins. |
| Rule 8 | `05_database_design.md` is the single source of truth; `03_api_contracts.md` cross-references it rather than duplicating schema details. |
| Rule 10 | Immutability of `submissions` payload enforced at schema level (no UPDATE path). |
| Rule 11 | Outbox pattern and `outbox_events` table specified as the async webhook delivery mechanism. |
| Rule 12 | All design decisions recorded as explicit ADR blocks inside the document. |
| Rule 15 | This sprint report documents the goal, changes, and files for the documentation milestone. |
