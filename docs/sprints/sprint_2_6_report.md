# Sprint 2.6 Retrospective Report
**Date Range:** 2026-03-13
**Theme:** Active Governance Engine — Multi-Vote Tracking, Dynamic Role-Weights, Webhooks & Outbox Pattern

---

## Goal

Elevate Winnow from a passive scoring microservice into a full **active Governance Engine**: implement multi-vote tracking, replace hardcoded role checks with a fully config-driven policy engine, introduce the Transactional Outbox pattern for guaranteed asynchronous webhook delivery, and lock down the supersede flow to prevent client-side finalization bypass.

---

## Commits in This Sprint

| Hash | Date | Message |
|---|---|---|
| `78c186a` | 2026-03-13 | feat(governance): upgrade to active governance engine, dynamic role-weights, and webhooks |

---

## Architectural Milestones

### 1. Multi-Vote Tracking — `POST /submissions/{id}/votes` (`app/api/v1/voting.py`)
- Introduced the `POST /api/v1/submissions/{id}/votes` endpoint, making Winnow the authoritative workflow engine for the manual review phase (Stage 3).
- Each vote carries a `voter_id`, `voter_role`, and `vote` value; votes are persisted in the `votes` table with a unique constraint on `(submission_id, voter_id)` for idempotency (Rule 10).
- After each vote, `GovernanceService` re-evaluates the weighted vote aggregate against the configured `threshold_score`. If the threshold is crossed, the submission is automatically transitioned to a final state (`approved` / `rejected`) and a webhook event is enqueued.
- Introduced `app/services/voting_service.py` and `app/services/governance_service.py` to encapsulate vote persistence and governance state-machine logic respectively, keeping the API layer thin.
- Added `app/schemas/voting.py` for the vote request/response Pydantic V2 models.

### 2. Dynamic, Config-Driven Role-Weights (`ProjectConfig` → `role_weights`)
- Replaced all hardcoded role checks and static vote-weight constants with a dynamic lookup from `ProjectConfig.role_weights` (a dict mapping role name → float weight).
- `GovernanceService` computes a weighted vote score at runtime: `score = Σ(vote_value × role_weight)` with all weights and thresholds sourced exclusively from the project's `ProjectConfig` in the registry (Rules 2 & 3).
- This makes the governance policy fully project-agnostic: different projects can configure entirely different voting weights and approval thresholds without touching framework code.

### 3. Transactional Outbox Pattern — Guaranteed Webhook Delivery (`app/services/webhook_service.py`)
- Implemented the **Transactional Outbox** pattern to decouple state-change events from synchronous HTTP requests (Rule 11).
- When `GovernanceService` finalises a submission, it writes a `submission.finalized` event row to the `outbox_events` table **within the same database transaction** as the state change, guaranteeing atomicity.
- `app/services/webhook_service.py` is responsible for polling the outbox and dispatching HTTP callbacks to the client's configured webhook URL with retry logic — ensuring at-least-once delivery even under downstream failure.
- Added `app/schemas/webhooks.py` to define the standardised `WebhookPayload` Pydantic V2 model sent to client systems.

### 4. `PATCH /submissions/{id}/supersede` — Locked-Down Supersede Flow
- Refactored the legacy `final-status` route to `PATCH /api/v1/submissions/{id}/supersede` (`app/api/v1/supersede.py`).
- The new schema (`app/schemas/supersede.py`) strictly limits the accepted `status` value to `superseded` only, using a Pydantic `Literal["superseded"]` field — preventing clients from setting any other final state (e.g., `approved`, `rejected`) through this endpoint, closing a potential finalization bypass vulnerability.
- This enforces the architectural invariant that only Winnow's governance engine can transition a submission to `approved` or `rejected`; clients can only mark their own submissions as `superseded` (corrected/replaced).

### 5. Test Suite Expansion to 320 Tests
- Hardened the entire test suite with rigorous assertions aligned with Rules 13 & 6:
  - **RFC 7807 field-level assertions**: error responses assert the specific failing field name is present in the `detail` body, not just the HTTP status code.
  - **Physical boundary tests**: negative heights, percentages > 100%, out-of-range trust levels all validated at the schema layer.
  - **Idempotency tests**: duplicate vote submissions assert the correct `409 Conflict` response with a Problem Details body.
  - **Finalization bypass tests**: attempts to set `approved`/`rejected` via the supersede endpoint assert `422 Unprocessable Entity`.
  - **Webhook outbox tests**: verify that a `submission.finalized` outbox row is created atomically when a vote threshold is crossed.
- Added `app/tests/api/test_voting.py` and `app/tests/api/test_supersede.py` as new test modules.
- Updated `app/tests/services/test_scoring_service.py` to cover new governance service paths.

### 6. Architecture Document Updates
- Updated `03_api_contracts.md` to document the new `POST /votes` endpoint, the `PATCH /supersede` endpoint, the webhook payload contract, and the full submission state machine diagram.
- Updated `05_database_design.md` to reflect the finalised `votes` and `outbox_events` table schemas with all constraints, confirming alignment with the implementation.

---

## Files Introduced / Modified

| File | Status |
|---|---|
| `app/api/v1/voting.py` | Created |
| `app/api/v1/supersede.py` | Created |
| `app/api/v1/tasks.py` | Created |
| `app/schemas/voting.py` | Created |
| `app/schemas/supersede.py` | Created |
| `app/schemas/webhooks.py` | Created |
| `app/schemas/tasks.py` | Created |
| `app/services/voting_service.py` | Created |
| `app/services/governance_service.py` | Created |
| `app/services/webhook_service.py` | Created |
| `app/tests/api/test_voting.py` | Created |
| `app/tests/api/test_supersede.py` | Created |
| `app/tests/services/test_scoring_service.py` | Updated |
| `app/governance/base.py` | Updated |
| `app/governance/projects/trees.py` | Updated |
| `app/models/project_config.py` | Updated |
| `app/api/v1/router.py` | Updated |
| `docs/architecture/03_api_contracts.md` | Updated |
| `docs/architecture/05_database_design.md` | Updated |

---

## Tests Status

320 tests passing by end of `78c186a`, covering:
- Multi-vote happy path: votes accumulate, threshold triggers finalization.
- Idempotency: duplicate votes from the same voter return `409 Conflict` with RFC 7807 body.
- Dynamic role-weights: different weight configurations produce different finalization outcomes.
- Webhook outbox: `outbox_events` row created atomically on threshold crossing.
- Supersede endpoint: only `superseded` status accepted; `approved`/`rejected` return `422`.
- Physical boundary and negative-value rejections with field-level RFC 7807 assertions.

---

## Architectural Rules Applied

| Rule | How Applied |
|---|---|
| Rule 1 | `03_api_contracts.md` and `05_database_design.md` updated to reflect new state machine and endpoints. |
| Rule 2 | No hardcoded vote weights or threshold values; all sourced from `ProjectConfig.role_weights`. |
| Rule 3 | Governance policy entirely config-driven; framework code is value-agnostic. |
| Rule 5 | No domain entity tables added; votes and outbox rows are Winnow-owned process state only. |
| Rule 6 | All new schemas (`VoteRequest`, `WebhookPayload`, `SupersedeRequest`) use Pydantic V2 `ConfigDict`, `Literal`, `Field`. |
| Rule 9 | `GovernanceService` and `VotingService` raise only domain exceptions; API layer maps to HTTP. |
| Rule 10 | Vote and supersede endpoints are idempotent; submission payload is never mutated. |
| Rule 11 | Webhook dispatch fully decoupled via Transactional Outbox; no synchronous HTTP calls during request handling. |
| Rule 12 | Outbox pattern choice and finalization bypass prevention documented as ADRs in architecture files. |
| Rule 13 | 320 tests covering edge cases, security guardrails, and RFC 7807 field-level assertions. |
| Rule 15 | This sprint report documents the goal, all changes, files touched, and test outcomes. |
