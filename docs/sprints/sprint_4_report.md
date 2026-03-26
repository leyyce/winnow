# Sprint 4 — Refinement Sprint: Immutable Audit-Log & Governance Authority

**Date:** 2026-03-17
**Status:** Complete — 323/323 tests passing

---

## Goal

Realign the Winnow architecture to enforce a strict separation between **immutable input data (Submissions)** and **dynamic process states (Scoring Results)**. Implement automatic versioning via an identity triplet, high-performance lookups, threshold-based status transitions, and admin override (Power-Vote Pattern).

---

## Changes Made

### 1. Database & Schema Realignment

**`app/models/submission.py`**
- Removed `status`, `superseded_by`, `SubmissionStatus` enum — state now lives in `scoring_results`
- Renamed `submission_type` → `entity_type`
- Added `entity_id` (UUID) and `measurement_id` (UUID) — form the identity triplet with `project_id`
- Added composite index `ix_submissions_triplet` on `(project_id, entity_id, measurement_id)`

**`app/models/scoring_result.py`**
- Added `status` column with CHECK constraint (pending_review / approved / rejected / superseded / voided)
- Added `superseded_by` FK → `submissions.submission_id`
- Added `override_vote_id` FK → `submission_votes.id`
- Added `reviewed_by`, `review_note`, `trust_adjustment`, `finalized_at` columns
- Removed `UNIQUE` constraint on `submission_id` (now one-to-many)
- Added `ScoringResultStatus` StrEnum
- Added composite index `ix_scoring_results_submission_created` on `(submission_id, created_at)`

**`app/models/submission_vote.py`**
- Added `is_override` boolean column

**`app/db/migrations/versions/20260313_0000_a1b2c3d4e5f6_initial_schema.py`**
- Rewritten in-place to reflect all model changes above

---

### 2. Semantic Alignment & Metadata

**`app/schemas/envelope.py`**
- Renamed `submission_type` → `entity_type` in `SubmissionMetadata`
- Added `entity_id` (UUID) and `measurement_id` (UUID) to `SubmissionMetadata`

**`app/schemas/projects/trees.py`**
- Removed `tree_id` from `TreePayload` — entity identity is now a first-class metadata field

**`app/schemas/results.py`**
- Updated `ScoringResultResponse.status` Literal to include `"voided"`, removed deprecated `"pending_finalization"`

**`app/schemas/tasks.py`**
- Renamed `submission_type` → `entity_type` in `TaskItem`

**`app/schemas/voting.py`**
- Added `is_override: bool` field to `VoteRequest`

**`app/registry/manager.py`**
- Added `valid_entity_types: list[str]` to `ProjectRegistryEntry` with `__post_init__` guard

**`app/registry/projects/trees.py`**
- Added `valid_entity_types=["tree"]` to `TreeProjectBuilder`

**`app/core/exceptions.py`**
- Added `InvalidEntityTypeError` → mapped to HTTP 422 at API layer
- Added `ConflictError` → mapped to HTTP 409 at API layer

---

### 3. Atomic Lifecycle & State Machine

**`app/services/scoring_service.py`** (full rewrite)
- Entity-type gate: rejects unknown `entity_type` with `InvalidEntityTypeError`
- Auto-supersede: detects prior `pending_review` submission matching the identity triplet; appends `superseded` ScoringResult atomically
- Terminal lock: raises `ConflictError` (409) if prior submission is already in a terminal state
- Threshold-based transitions: `score >= auto_approve_min` → `approved`; `score < manual_review_min` → `rejected`; otherwise → `pending_review`
- Webhook queued on every ScoringResult INSERT via `queue_status_transition_webhook`
- `supersede_submission` replaced by `withdraw_submission` (appends `voided` ScoringResult)
- `_build_response_from_orm` now reads status from `ScoringResult`, never from `Submission`

**`app/services/webhook_service.py`**
- Added `queue_status_transition_webhook` for general status-change events

---

### 4. Admin Override & Trust Integrity

**`app/services/voting_service.py`** (full rewrite)
- Reads current status from latest `ScoringResult` (not `submission.status`)
- All finalization transitions INSERT new `ScoringResult` rows (not UPDATE submission)
- `is_override=True`: bypasses eligibility/duplicate checks; forces terminal state immediately; sets `override_vote_id` on new `ScoringResult`; queues webhook

**`app/api/errors.py`**
- Added handler for `InvalidEntityTypeError` → RFC 7807 422 with `metadata.entity_type` field
- Added handler for `ConflictError` → RFC 7807 409

---

### 5. Routes Cleanup

**`app/api/v1/submissions.py`**
- Added `PATCH /submissions/{id}/withdraw` — user withdrawal (pending → voided)
- Added `PATCH /submissions/{id}/override` — admin override (pending → approved/rejected)

**`app/api/v1/supersede.py`** — removed from router (route now returns 404)

**`app/api/v1/router.py`** — removed `supersede.router`

**`app/services/governance_service.py`**
- Updated to query status from latest `ScoringResult` via MAX(created_at) subquery
- Updated `entity_type` field name

---

### 6. Tests

- `app/tests/conftest.py` — removed `tree_id` from `_payload()`
- `app/tests/api/test_submissions.py` — updated `_valid_envelope()` to new metadata fields
- `app/tests/api/test_supersede.py` — fully rewritten to cover: withdraw, override, auto-supersede, 409 conflict, entity-type 422, old supersede route → 404
- `app/tests/api/test_voting.py` — updated `_valid_envelope()`, updated webhook outbox assertion
- `app/tests/services/test_scoring_service.py` — updated `_envelope()`, replaced `tree_id` test with `species_id` test
- `app/tests/test_schemas.py` — updated `_metadata()`, `entity_type` tests, `pending_finalization` → `pending_review`

---

### 7. Documentation

- `docs/architecture/05_database_design.md` — updated with:
  - 100% Immutability Principle, Triplet-Based Versioning, Append-Only State Machine principles
  - New `submissions` table definition (entity triplet, no status columns)
  - New `scoring_results` table definition (one-to-many, status, composite index, status lifecycle)
  - `is_override` in `submission_votes`
  - ADR-DB-009, ADR-DB-010 references

---

## Tests Passed

```
323 passed in 1.60s
```

All 323 tests pass including:
- 11 new lifecycle tests (withdraw, override, auto-supersede, 409 conflict, entity-type 422)
- All existing scoring, voting, governance, and schema tests updated and passing
