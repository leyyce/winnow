# Sprint 3 — Phase 2: Database Implementation Report

**Date:** 2026-03-13
**Status:** Complete ✓
**Tests:** 326 passed, 0 failed

---

## Goal

Implement the full persistence layer as designed in `docs/architecture/05_database_design.md`.
Replace every in-memory data structure (dictionaries, lists) in the service layer with real
SQLAlchemy 2.0 async database operations.  Ensure all 320+ existing tests continue to pass
seamlessly against the new persistence layer.

---

## Changes Made

### 1. Blueprint Amendment

- **`docs/architecture/05_database_design.md` §6** — Added Step 13 (collision guard for
  `allow_overwrite=False` in `Registry.register()` / `Registry.load()`), making the
  implementation plan complete with 13 ordered steps.

### 2. Dependencies & Configuration

- **`pyproject.toml`** — Added `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `greenlet`
  to production deps; `aiosqlite`, `anyio`, `pytest-asyncio` to dev deps.
- **`app/core/config.py`** — Rewrote to build `DATABASE_URL` as a `@computed_field` from
  individual `POSTGRES_*` env vars.  No hardcoded DSNs anywhere in the codebase.
- **`.env.example`** — Added `POSTGRES_HOST/PORT/DB/USER/PASSWORD` variables aligned with
  the `db` service names in `compose.yaml` and `compose.dev.yaml`.

### 3. ORM Models (`app/models/`)

| File | Description |
|---|---|
| `base.py` | `Base` (DeclarativeBase + naming convention), `UUIDPrimaryKeyMixin`, `TimestampMixin` |
| `submission.py` | `Submission` table — natural UUID PK, `submission_type`, JSONB columns, self-referential `superseded_by` FK, CHECK constraint |
| `scoring_result.py` | `ScoringResult` — 1:1 with Submission, JSONB breakdown/required_validations/thresholds |
| `submission_vote.py` | `SubmissionVote` — composite UNIQUE(submission_id, user_id) duplicate-vote guard |
| `webhook_outbox.py` | `WebhookOutbox` — Transactional Outbox with retry fields and composite index on (status, next_retry_at) |
| `__init__.py` | Imports all models to register them with the SQLAlchemy mapper registry |

### 4. Async Session (`app/db/session.py`)

- `create_async_engine` with `pool_pre_ping=True`, `pool_size=5`, `max_overflow=10`.
- `async_sessionmaker` with `expire_on_commit=False` (prevents MissingGreenlet errors).
- `get_db()` FastAPI dependency — commits on success, rolls back on exception.

### 5. Alembic Setup (`app/db/migrations/`)

- `alembic.ini` — no hardcoded credentials; `DATABASE_URL` injected at runtime.
- `env.py` — async online mode via `asyncio.run()` + `connection.run_sync(do_run_migrations)`.
- `script.py.mako` — standard Alembic template.
- `versions/20260313_0000_a1b2c3d4e5f6_initial_schema.py` — hand-authored initial migration
  covering all 4 tables, CHECK constraints (not emitted by autogenerate), all indexes, and
  the self-referential FK via `use_alter=True`.

### 6. Collision Guard (`app/registry/manager.py`)

- `Registry.load(builder, *, allow_overwrite=False)` and
  `Registry.register(project_id, entry, *, allow_overwrite=False)` — raise `ValueError` on
  duplicate `project_id` by default.
- `bootstrap()` catches `ValueError` and logs a warning, keeping bootstrap idempotent
  (safe to call multiple times in tests or during hot-reload).

### 7. Service Layer — All Stubs Eradicated

| Service | Stub removed | Replaced with |
|---|---|---|
| `scoring_service.py` | `# TODO: persist` comments | `db.add()` + `await db.flush()` for Submission + ScoringResult; idempotency via `db.get()`; full `supersede_submission()`; new `get_submission_result()` |
| `submission_service.py` | `voting_service.register_submission()` call | Thin delegate to `scoring_service.process_submission(envelope, db)` |
| `voting_service.py` | `_submissions: dict`, `StoredVote`, `SubmissionRecord` | `SELECT … FOR UPDATE` on Submission; INSERT SubmissionVote; atomic status UPDATE + outbox INSERT |
| `webhook_service.py` | `_outbox: dict`, `OutboxEntry` | DB-backed INSERT into `webhook_outbox`; `get_pending_entries(db)`, `attempt_delivery(id, db)` |
| `governance_service.py` | `pending_submissions: list = []` | JOIN query on Submission + ScoringResult WHERE status='pending_review' |

### 8. API Layer

- **`app/api/deps.py`** — Re-exports `get_db` from `app/db/session` as the canonical dependency.
- **`submissions.py`, `voting.py`, `supersede.py`, `tasks.py`** — All endpoints now inject
  `db: AsyncSession = Depends(get_db)` and pass it to the corresponding service function.
- **`results.py`** — Upgraded from 501 stub to real `scoring_service.get_submission_result()`.

### 9. Test Suite Migration

- **`conftest.py`** — Function-scoped async SQLite engine (`create_async_engine("sqlite+aiosqlite:///:memory:")`),
  `db_session` fixture with rollback isolation, `async_client` overrides `get_db` with the
  test session (same session shared between HTTP calls and direct service calls within a test).
- **`test_scoring_service.py`** — All 15 tests updated with `db_session: AsyncSession` parameter;
  new `test_process_submission_idempotent_same_id_returns_stored` test added.
- **`test_voting.py`** — `_clean_stores` fixture removed; `test_vote_finalization_creates_outbox_entry`
  updated to use `await webhook_service.get_pending_entries(db_session)`.
- **`test_supersede.py`** — Replaced two 501-stub tests with DB-backed happy-path (200) and
  404-not-found tests; added 409 already-finalized test.
- **`test_registry.py`** — Two tests updated to use `allow_overwrite=True`; two new collision-guard
  tests added (`test_load_raises_on_duplicate_without_allow_overwrite`,
  `test_register_raises_on_duplicate_without_allow_overwrite`).

---

## Key Architectural Decisions

| Decision | Rationale |
|---|---|
| Function-scoped SQLite engine per test | Eliminates all shared state; no savepoint complexity; fastest reliable isolation pattern |
| `expire_on_commit=False` in session factory | Prevents `MissingGreenlet` / lazy-load errors after commit in async context |
| `with_for_update()` in vote + supersede queries | PostgreSQL row-level lock prevents concurrent race to finalize; SQLite silently ignores it |
| Datetime timezone normalisation in `_build_response_from_orm` | SQLite returns naive datetimes; PostgreSQL returns aware ones; normalise to UTC at read time |
| `bootstrap()` catches `ValueError` from collision guard | Keeps bootstrap idempotent; production double-registration is logged, not fatal |

---

## Files Touched

**New files:** `app/models/base.py`, `app/models/submission.py`, `app/models/scoring_result.py`,
`app/models/submission_vote.py`, `app/models/webhook_outbox.py`, `app/models/__init__.py`,
`app/db/session.py`, `app/db/migrations/alembic.ini`, `app/db/migrations/env.py`,
`app/db/migrations/script.py.mako`,
`app/db/migrations/versions/20260313_0000_a1b2c3d4e5f6_initial_schema.py`,
`docs/sprints/sprint_3_phase2_report.md`

**Modified:** `pyproject.toml`, `app/core/config.py`, `.env.example`,
`app/registry/manager.py`, `app/bootstrap.py`,
`app/services/scoring_service.py`, `app/services/submission_service.py`,
`app/services/voting_service.py`, `app/services/webhook_service.py`,
`app/services/governance_service.py`,
`app/api/deps.py`, `app/api/v1/submissions.py`, `app/api/v1/voting.py`,
`app/api/v1/supersede.py`, `app/api/v1/tasks.py`, `app/api/v1/results.py`,
`app/tests/conftest.py`, `app/tests/services/test_scoring_service.py`,
`app/tests/api/test_voting.py`, `app/tests/api/test_supersede.py`,
`app/tests/registry/test_registry.py`,
`docs/architecture/05_database_design.md`

---

## Tests Passed

```
326 passed in 1.33s
```

All pre-existing tests continue to pass.  6 new tests added (idempotency, collision guard ×2,
supersede happy-path, supersede 404, supersede 409).
