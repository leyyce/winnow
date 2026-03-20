# Sprint 5 Report — The Lifecycle Ledger

**Date**: 2026-03-17
**Goal**: Transition Winnow to a fully immutable, event-sourced "Lifecycle Ledger" architecture with append-only state transitions, backward-chaining supersession, structured governance, and a webhook Single Source of Truth for the client system.

---

## Summary

Sprint 5 is a full architectural rewrite of the persistence and governance layers. The monolithic `scoring_results` table is replaced by three purpose-built tables (`submission_user_snapshots`, `scoring_snapshots`, `status_ledger`). Every state transition is now an append-only INSERT with a backward pointer (`supersedes`) to the entry it replaces. `submission_votes` also becomes fully append-only. All 325 tests pass.

---

## Changes Made

### Schema & Migration (in-place rewrite)

- **Removed**: `scoring_results` table, `user_context` JSONB blob from `submissions`.
- **Added**: `submission_user_snapshots` (1:1 user state snapshot), `scoring_snapshots` (1:N immutable analysis), `status_ledger` (append-only lifecycle log with `supersedes` self-referencing FK via `use_alter=True`).
- **Updated**: `submission_votes` — removed `UNIQUE(submission_id, user_id)` constraint (fully append-only), added Sprint 5 `CHECK` constraint, added `updated_at`.
- **Updated**: `webhook_outbox` — added `'dead'` to status `CHECK` constraint.
- All string columns use `sa.Text` (PostgreSQL `TEXT`) instead of `VARCHAR(n)`.

### ORM Models

| File | Action |
|---|---|
| `app/models/scoring_result.py` | **Deleted** |
| `app/models/submission_user_snapshot.py` | **New** |
| `app/models/scoring_snapshot.py` | **New** |
| `app/models/status_ledger.py` | **New** — includes `StatusLedgerStatus`, `SupersedeReason`, `TERMINAL_STATES` |
| `app/models/submission.py` | Removed `user_context`; added `user_snapshot`, `scoring_snapshots`, `ledger_entries` relationships |
| `app/models/submission_vote.py` | Removed unique constraint; updated CHECK; added `updated_at` |
| `app/models/__init__.py` | Replaced `ScoringResult` exports with new model exports |

### Schemas

- `app/schemas/results.py` — Added `RoleConfig`, upgraded `RequiredValidations` to `role_configs`/`default_config`/`blocked_roles`, updated `ScoringResultResponse` (new fields: `supersede_reason`, `trust_delta`, `current_user_vote`, `ledger_entry_id`, `entity_id`, `measurement_id`), added `StatusLedgerEntryResponse`.
- `app/schemas/webhooks.py` — Replaced old payload classes with `StatusLedgerWebhookPayload` (includes `event_id`, `event_type`, `occurred_at`, `trust_delta`, `supersede_reason`).
- `app/schemas/voting.py` — Expanded `vote` literal to include `'voided'` for admin overrides; `final_status` relaxed to `str | None`.
- `app/schemas/envelope.py` — Added `account_updated_at` and `custom_data` to `UserContext`.

### Governance & Registry

- `app/governance/base.py` — `is_eligible_reviewer` replaced by `get_vote_weight` which returns weight and raises `NotEligibleError` for all ineligible cases.
- `app/governance/projects/trees.py` — `GovernanceTier` upgraded from `role_weights`/`required_min_trust` to `role_configs`/`default_config`/`blocked_roles`; `get_vote_weight` implements three-step evaluation (blocked → config → trust floor → weight=0 check).
- `app/registry/projects/trees.py` — Three governance tiers (`peer_review`, `community_review`, `expert_review`) rebuilt with `RoleConfig` objects; `webhook_url` set.
- `app/registry/manager.py` — Added `webhook_url` field to `ProjectRegistryEntry`.

### Services

- `app/services/scoring_service.py` — Full rewrite: `submission_user_snapshots` INSERT with `total_submissions` calculation; `scoring_snapshots` INSERT; `status_ledger` INSERT; `WITH RECURSIVE` CTE lineage sum (`_resolve_lineage_sum`); `SELECT … FOR UPDATE` on triplet collision; cross-chain `supersede_reason='edited'` override; webhook enqueue on every ledger INSERT.
- `app/services/voting_service.py` — Full rewrite: append-only votes with latest-wins tally via subquery; `SELECT … FOR UPDATE` locking; `role_configs` eligibility via `get_vote_weight`; admin override path; trust delta via shared CTE helper.
- `app/services/governance_service.py` — Updated to query `status_ledger` + `scoring_snapshots` instead of removed `scoring_results`.
- `app/services/webhook_service.py` — Rewritten around `StatusLedgerWebhookPayload`; `enqueue_ledger_event` accepts all ledger fields; `'dead'` status handled in `attempt_delivery`.
- `app/services/submission_service.py` — `withdraw_submission` now INSERTs `status_ledger` with `supersede_reason='deleted'`.

### Tests

- `test_schemas.py` — `_required_validations` helper and `TestRequiredValidations` rewritten for Sprint 5 schema; `TestScoringResultResponse._make` updated with new required fields.
- `test_scoring_service.py` — `required_validations` assertions updated for `role_configs`/`default_config`/`blocked_roles`.
- `test_tree_rules.py` — `TestTreeGovernancePolicy` fully rewritten: `GovernanceTier` uses `RoleConfig`; `is_eligible_reviewer` replaced by `get_vote_weight`; new tests for `blocked_roles`, `default_config` fallback, `weight=0` ineligibility.
- `test_registry.py` — `TestTreeProjectBuilderGovernance` updated for `role_configs` model.
- `test_submissions.py` — `role_weights` assertion replaced by `role_configs`/`default_config`/`blocked_roles` assertion.
- `test_voting.py` — Duplicate-vote test rewritten as append-only test; webhook outbox event types updated; ineligibility test changed from `moderator` to blocked role `guest`.
- `test_supersede.py` — Auto-supersede test updated: old chain stays `pending_review` (never modified); new chain's `supersede_reason='edited'` asserted.

### Documentation

- `docs/architecture/05_database_design.md` — Full rewrite: Triple-Snapshot pattern, backward-chaining supersession, Trust Delta Algorithm, Advanced Governance Model, ADR-DB-004.

---

## Tests Passed

```
325 passed in 2.07s
```

Includes:
- All prior scoring, voting, governance, schema, registry, and lifecycle tests.
- New tests: `role_configs` eligibility, `blocked_roles` absolute exclusion, `default_config` fallback, trust delta accumulation, append-only vote change, cross-chain `supersede_reason='edited'`, webhook outbox Sprint 5 event types.

---

## Known Limitations / Follow-ups

- **Case G (Re-Scoring)**: `scoring_snapshots` schema supports 1:N for re-scoring; the service endpoint itself is not yet implemented (scaffolded at schema level).
- **Recursive CTE depth**: For very long edit chains (100+), the iterative `db.get()` loop in `_resolve_lineage_sum` may be replaced with the pure SQL `WITH RECURSIVE` CTE. Currently uses SQLAlchemy's `cte(recursive=True)` which handles this efficiently.
- **Dead-letter operator runbook**: `webhook_outbox` entries that reach `status='dead'` are flagged but no alerting pipeline is connected. Follow-up sprint.
