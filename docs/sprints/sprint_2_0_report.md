# Sprint 2 Retrospective Report
**Date Range:** 2026-03-12
**Theme:** API Layer, Scoring Service, RFC 7807 Error Hardening & Docker DX

---

## Goal

Expose the Winnow scoring and governance engine over HTTP via a FastAPI REST API, introduce a dedicated service layer, establish the RFC 7807 Problem Details error contract, and harden the `ThresholdConfig` routing model to eliminate boundary ambiguity.

---

## Commits in This Sprint

| Hash | Date | Message |
|---|---|---|
| `d0aa50c` | 2026-03-12 | Initial implementation of API and scoring service |
| `9519cd4` | 2026-03-12 | refactor(api): harden api layer, domain exceptions, and threshold routing |

---

## Architectural Milestones

### 1. Initial API & Service Layer (`d0aa50c`)
- Introduced the FastAPI application entry point (`app/main.py`) with lifespan management for registry bootstrap and structured JSON logging.
- Created `app/api/v1/router.py` as the versioned API router aggregating all resource endpoints.
- Implemented `app/api/v1/submissions.py` — the primary `POST /api/v1/submissions` endpoint that orchestrates Stage 1 validation → Stage 2 scoring → Stage 3 governance in a single synchronous request/response cycle.
- Implemented `app/api/v1/results.py` — `GET /api/v1/results/{submission_id}` to retrieve stored scoring results.
- Introduced `app/services/scoring_service.py` to encapsulate the pipeline dispatch logic, decoupling the API layer from direct scoring concerns.
- Added `app/services/submission_service.py` to manage submission persistence and retrieval.
- Introduced `app/api/deps.py` for FastAPI dependency injection (registry access, DB sessions).
- Added `app/db/session.py` and SQLAlchemy model base (`app/models/base.py`) for database integration groundwork.
- Introduced `app/models/submission.py` as the ORM model for persisting submissions and their scoring results.

### 2. RFC 7807 Problem Details & Domain Exception Hierarchy (`9519cd4`)
- Implemented a pure **domain exception hierarchy** in `app/core/exceptions.py` (`WinnowException`, `ProjectNotFoundError`, `ValidationError`, `ScoringError`, `NotImplementedError`) — completely decoupling the service layer from FastAPI's `HTTPException` (Rule 9).
- Built `app/api/errors.py` as the **sole RFC 7807 translation layer**: global exception handlers map each domain exception to a structured Problem Details response (`type`, `title`, `status`, `detail`, `instance`) with correct HTTP status codes (400, 422, 501).
- Confirmed Rule 9 compliance: services raise only domain exceptions; the API layer handles all HTTP mapping.

### 3. ThresholdConfig Refactor — 3-Float → 2-Integer System (`9519cd4`)
- Replaced the original 3-float threshold system (`approve_threshold`, `review_threshold`, `reject_threshold`) with a mathematically sound **2-integer system** (`auto_approve_min`, `manual_review_min`) expressed as integer score boundaries.
- This eliminated routing gaps and boundary overlap ambiguities that could cause a submission to fall into no routing bucket or multiple buckets simultaneously.
- Updated `GovernanceAuthority` and all downstream tests to use the new threshold model.

### 4. Structured Logging & Health Endpoint (`9519cd4`)
- Replaced a custom JSON logger with the standard `python-json-logger` library, correcting initialisation order within the application lifespan context.
- Introduced `app/api/v1/health.py` with a Pydantic `HealthResponse` model and dual routing (`/health` for infrastructure probes, `/api/v1/health` for API clients).

### 5. Docker Developer Experience Improvements (`9519cd4`)
- Moved the container virtual environment from the project directory to `/opt/venv`, using a named Docker volume (`dev_venv`) to eliminate stale anonymous volume conflicts during iterative development.
- Unified the Python base image version via `ARG PYTHON_VERSION=3.14` across all multi-stage Docker build targets.

### 6. Performance Fix — O(N²) Weight Lookup (`9519cd4`)
- Identified and fixed an O(N²) performance bottleneck in `scoring_service` where role weights were looked up via repeated list scans; replaced with a pre-built dictionary for O(1) access.

### 7. Architecture & Test Updates (`9519cd4`)
- Updated `03_api_contracts.md` to formally document the RFC 7807 error format, the domain exception hierarchy, and the revised threshold terminology.
- Expanded the test suite to cover the new exception flow paths, threshold boundary correctness, and health endpoint responses.

---

## Files Introduced / Modified

| File | Status |
|---|---|
| `app/main.py` | Created |
| `app/core/exceptions.py` | Created |
| `app/core/config.py` | Created |
| `app/core/logging.py` | Created |
| `app/api/deps.py` | Created |
| `app/api/errors.py` | Created |
| `app/api/v1/router.py` | Created |
| `app/api/v1/health.py` | Created |
| `app/api/v1/submissions.py` | Created |
| `app/api/v1/results.py` | Created |
| `app/services/scoring_service.py` | Created |
| `app/services/submission_service.py` | Created |
| `app/models/base.py` | Created |
| `app/models/submission.py` | Created |
| `app/db/session.py` | Created |
| `app/db/migrations/alembic.ini` | Created |
| `app/db/migrations/env.py` | Created |
| `app/tests/api/test_submissions.py` | Created |
| `app/tests/test_schemas.py` | Created |
| `docs/architecture/03_api_contracts.md` | Updated |
| `Dockerfile` | Updated |
| `compose.dev.yaml` | Updated |

---

## Tests Status

All tests passed by end of `9519cd4`, covering:
- `POST /api/v1/submissions` happy path and Stage 1 validation failures.
- RFC 7807 error shape assertions (type, title, status, detail fields present).
- `ThresholdConfig` 2-integer boundary routing correctness.
- Health endpoint response at both routes.
- Domain exception → HTTP status code mapping.

---

## Architectural Rules Applied

| Rule | How Applied |
|---|---|
| Rule 1 | `03_api_contracts.md` updated before/alongside API code. |
| Rule 7 | API layer built only after the core framework (Sprint 1) was stable. |
| Rule 9 | Services raise only domain exceptions; `errors.py` owns all HTTP mapping. |
| Rule 10 | Submissions designed as append-only from the outset; no UPDATE paths. |
| Rule 13 | RFC 7807 error field assertions added; threshold boundary edge cases tested. |
