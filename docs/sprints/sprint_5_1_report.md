# Sprint 5.1 Report — Post-Sprint 5 Architectural Refinements

**Date**: 2026-03-20
**Status**: ✅ Complete — 325 tests passed

---

## Goal

Apply five targeted refinements to the Lifecycle Ledger architecture: database constraint correctness, admin override scope expansion, stateless active-vote exposure, cumulative governance tier evaluation, and universal governance engine abstraction.

---

## Changes Made

### 1. `app/models/webhook_outbox.py`
- Changed `event_type` and `status` columns from `String(50)`/`String(20)` to `sa.Text` (PostgreSQL `TEXT`).
- Added `CheckConstraint` enforcing `status IN ('pending', 'processing', 'delivered', 'failed', 'dead')`.
- Fixed `OutboxStatus` enum values to match constraint: `PROCESSING`, `DEAD` (replacing old `IN_PROGRESS`, `DEAD_LETTER`).

### 2. `app/services/voting_service.py`
- Bypassed the `pending_review` guard when `request.is_override=True` — admins can now force a state change regardless of current terminal status.
- Updated multi-tier tally: iterates over all applicable `RequiredValidations` tiers; finalizes when ANY tier's `threshold_score` is met (most-restrictive first).
- Parsed `snapshot.required_validations` as `list[RequiredValidations]` (JSON array).
- Fixed stale `approve_weight`/`reject_weight`/`threshold` variable references to `winning_approve`/`winning_reject`.

### 3. `app/schemas/voting.py`
- Added `ActiveVoteItem` schema with fields: `user_id`, `user_role`, `vote`, `is_override`, `note`, `created_at`.

### 4. `app/schemas/results.py`
- Removed `current_user_vote` field from `ScoringResultResponse`.
- Changed `required_validations: RequiredValidations` → `required_validations: list[RequiredValidations]`.
- Added `active_votes: list[ActiveVoteItem]` (latest-wins resolved vote list per reviewer).

### 5. `app/schemas/tasks.py`
- Removed single `review_tier: str` field.
- Replaced `required_validations: RequiredValidations` with `review_tiers: list[RequiredValidations]`.
- Added `active_votes: list[ActiveVoteItem]`.

### 6. `app/governance/base.py` *(full rewrite)*
- Moved `GovernanceTier` dataclass here from `governance/projects/trees.py`.
- Created `GovernancePolicy` — project-agnostic engine driven by injected tier config.
- `determine_requirements()` returns `list[RequiredValidations]` — ALL tiers where `confidence_score >= score_threshold` (cumulative, most-restrictive first; fallback to least-restrictive if none match).
- `get_vote_weight()` evaluates a single tier: blocked → role_configs → default_config → trust floor.

### 7. `app/governance/projects/trees.py` + `app/governance/projects/__init__.py` *(deleted)*
- Removed project-specific governance implementations entirely.

### 8. `app/registry/projects/trees.py`
- Updated import: `GovernanceTier, GovernancePolicy` from `app.governance.base`.
- Replaced `TreeGovernancePolicy(...)` constructor with `GovernancePolicy(...)`.

### 9. `app/services/scoring_service.py`
- Replaced `_get_current_user_vote` with `_get_active_votes` — returns `list[ActiveVoteItem]` using latest-wins subquery.
- Updated `_build_response` to accept `active_votes: list[ActiveVoteItem]` and parse `required_validations` as list.
- Stores `required_validations` in `scoring_snapshots` as a JSON array: `[r.model_dump(mode="json") for r in required]`.
- All call sites (idempotency path, `get_submission_result`) updated.

### 10. `app/services/governance_service.py`
- Parses `snapshot_row.required_validations` as `list[RequiredValidations]`.
- Eligibility check: reviewer is eligible if they qualify in at least one tier.
- Populates `active_votes` via `_get_active_votes` for each `TaskItem`.
- Uses `review_tiers` field name in `TaskItem`.

---

## Files Modified

| File | Change |
|---|---|
| `app/models/webhook_outbox.py` | sa.Text + CheckConstraint + OutboxStatus fix |
| `app/services/voting_service.py` | Admin override bypass + multi-tier tally |
| `app/schemas/voting.py` | Added ActiveVoteItem |
| `app/schemas/results.py` | list[RequiredValidations] + active_votes |
| `app/schemas/tasks.py` | review_tiers list + active_votes |
| `app/governance/base.py` | GovernancePolicy (full rewrite) |
| `app/governance/projects/trees.py` | **Deleted** |
| `app/governance/projects/__init__.py` | **Deleted** |
| `app/registry/projects/trees.py` | Use GovernancePolicy |
| `app/services/scoring_service.py` | _get_active_votes + list storage |
| `app/services/governance_service.py` | Multi-tier eligibility + active_votes |
| `app/tests/api/test_submissions.py` | required_validations[0] indexing |
| `app/tests/api/test_supersede.py` | Override-anytime test |
| `app/tests/scoring/test_tree_rules.py` | GovernancePolicy + list access |
| `app/tests/registry/test_registry.py` | GovernancePolicy import |
| `app/tests/scoring/test_common_rules.py` | GovernancePolicy import |
| `app/tests/test_schemas.py` | required_validations as list |
| `app/tests/services/test_scoring_service.py` | required_validations list access |

---

## Tests Passed

```
325 passed in 2.13s
```

All existing tests updated and passing. New test `test_override_already_finalized_succeeds` verifies double-override (pending→approved→voided) succeeds with HTTP 200 for both transitions.
