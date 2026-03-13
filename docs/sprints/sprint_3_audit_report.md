# Sprint 3 — Audit & Realignment Report
**Date:** 2026-03-13
**Focus:** Schema realignment (`confidence_score` relocation), documentation of rationale and trade-offs, infrastructure catch-up audit.

---

## Goal

Three interconnected tasks were executed in this sprint:

1. **Goal 1 — Schema Realignment:** Move `confidence_score` from the `submissions` table to the `scoring_results` table, aligning the schema with the submission immutability principle.
2. **Goal 2 — Architectural Documentation:** Document the rationale, performance trade-off, and future mitigation strategy for the relocation in `05_database_design.md`.
3. **Goal 3 — Infrastructure Catch-up:** Audit all documentation and ensure consistency with three manually applied infrastructure changes (`ALEMBIC_CONFIG`, `dev_venv` removal, PostgreSQL port exposure).

---

## Changes Made

### Goal 1: Schema Realignment — `confidence_score`

#### Problem
`confidence_score` was stored as a column on the `submissions` table. This violates the core immutability principle: `submissions` is a point-in-time INPUT snapshot (the received envelope). `confidence_score` is a computed OUTPUT of the scoring pipeline.

#### Files Modified

| File | Change |
|---|---|
| `app/models/submission.py` | Removed `confidence_score: Mapped[float]` column entirely. |
| `app/models/scoring_result.py` | Added `confidence_score: Mapped[float]` column with `CHECK (0 <= confidence_score <= 100)` constraint via `__table_args__`. Added `CheckConstraint` import. |
| `app/db/migrations/versions/20260313_0000_a1b2c3d4e5f6_initial_schema.py` | Removed `sa.Column("confidence_score", ...)` from the `submissions` block. Added it to the `scoring_results` block along with the `CheckConstraint` and a new `ix_scoring_results_confidence_score` B-Tree index. |
| `app/services/scoring_service.py` | Removed `confidence_score=pipeline_result.total_score` from `Submission(...)` constructor; added it to `ScoringResult(...)` constructor. Fixed `_build_response_from_orm` to read `sr_row.confidence_score` instead of `submission.confidence_score`. |
| `app/services/governance_service.py` | Updated `get_available_tasks` — both the `is_eligible_reviewer()` call and the `TaskItem(...)` constructor now use `sr_row.confidence_score` (the loop already unpacked `(submission, sr_row)` tuples). |
| `app/services/voting_service.py` | Updated `vote_on_submission` — the `is_eligible_reviewer()` call and the `queue_finalization_webhook()` call now use `sr_row.confidence_score` (the service already loaded `sr_row` via a separate `SELECT`). |

#### Why No Test Changes Were Needed
All tests construct submissions via `POST /submissions` and read scores from the `ScoringResultResponse` body — they never assert on `Submission.confidence_score` directly. The service layer change is transparent to the test layer.

---

### Goal 2: Documentation — `05_database_design.md`

| Section | Change |
|---|---|
| **Header `Last updated`** | Updated to reflect Sprint 3 Phase 2 audit and ADR-DB-008. |
| **ADR-DB-008 (new)** | Added after ADR-DB-007. Documents: (1) decision and rationale (immutability, re-scoring path, separation of concerns); (2) explicit performance trade-off (JOIN required for combined filters); (3) future mitigation options (covering index, materialised view, intentional denormalisation as a last resort). |
| **§4.1 Alembic Configuration** | Added two new bullet points: `script_location` is now project-root-relative (`app/db/migrations`), and the `ALEMBIC_CONFIG` env var is required for CLI invocation. |
| **§4.4 Development Environment Notes (new)** | Documents the two `compose.dev.yaml` changes with rationale: `dev_venv` volume removed (image-sourced deps) and PostgreSQL port `5432:5432` exposed (local DB access). |
| **§7 Summary table** | Added three new rows cross-referencing ADR-DB-008, §4.1, and §4.4. Updated closing statement. |

---

### Goal 3: Infrastructure Catch-up Audit

The following infrastructure changes were applied manually and are now fully reflected in documentation and configuration:

| Change | Location | Status |
|---|---|---|
| `ALEMBIC_CONFIG=app/db/migrations/alembic.ini` env var | `.env.example` (already present in diff), `05_database_design.md §4.1` | ✅ Documented |
| `script_location = app/db/migrations` (project-root-relative) | `alembic.ini` (already updated in diff), `05_database_design.md §4.1` | ✅ Documented |
| `dev_venv` named volume commented out in `compose.dev.yaml` | `05_database_design.md §4.4` | ✅ Documented |
| PostgreSQL port `5432:5432` exposed in `compose.dev.yaml` | `05_database_design.md §4.4` | ✅ Documented |

---

## Test Results

```
326 passed in 1.42s
```

All 326 existing tests pass without modification. No new tests were required — the `confidence_score` relocation is internal to the service layer and the existing test suite covers all affected code paths through integration tests.

---

## Architectural Invariants Restored

| Invariant | Before | After |
|---|---|---|
| `submissions` is a pure INPUT snapshot | ❌ Violated — `confidence_score` (a computed output) was stored here | ✅ Restored — `submissions` contains only envelope data |
| `scoring_results` holds all pipeline outputs | ❌ Incomplete — `confidence_score` was split off to `submissions` | ✅ Complete — all outputs (`score`, `breakdown`, `required_validations`, `thresholds`) in one table |
| Future re-scoring supported by schema | ❌ Blocked — re-scoring would require UPDATE on `submissions` (immutability violation) | ✅ Enabled — `scoring_results` can hold multiple rows per submission in Phase 2 |
| Architecture and code fully synchronised | ❌ Gap — infra changes undocumented | ✅ Closed — ADR-DB-008, §4.1, §4.4 all added |
