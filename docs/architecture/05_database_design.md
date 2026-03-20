# 05 — Database Design

> **Sprint 5 (Lifecycle Ledger)**: Complete architectural rewrite. See [sprint_5_report.md](../sprints/sprint_5_0_report.md) for the change narrative.

---

## Design Philosophy

### 100% Immutability Principle

Every table in Winnow is **strictly append-only**. Once a row is written it is never `UPDATE`d. This applies to:

- `submissions` — root audit log, written once on POST
- `submission_user_snapshots` — user state at submission time, written once
- `scoring_snapshots` — technical analysis, written once per scoring event
- `status_ledger` — lifecycle events, only new rows are ever inserted
- `submission_votes` — voting log, only new rows are ever inserted

The only exception is `webhook_outbox`, which transitions through delivery states (`pending → processing → sent / failed → dead`) — this is a controlled operational state machine, not domain data.

### Backward-Chaining Supersession

State transitions use a **backward pointer** pattern rather than updating old rows. Every new `status_ledger` row carries a `supersedes` FK pointing to the entry it replaces. Old entries are never touched. The active state for a submission is always the row with the highest `created_at` for that `submission_id`.

This enables:
- Full, reconstructable lifecycle history via the `supersedes` chain.
- Correct trust delta calculation via `WITH RECURSIVE` CTE traversal.
- Zero-risk admin overrides — old state is always preserved.

---

## Tables

### `submissions` (Root Anchor)

The strictly write-once audit log. Contains only the identity anchor and immutable raw payload.

| Column | Type | Notes |
|---|---|---|
| `submission_id` | UUID PK | Client-supplied natural key (idempotency) |
| `project_id` | Text | Indexed |
| `entity_type` | Text | Validated against `valid_entity_types` in registry |
| `entity_id` | UUID | Identity triplet |
| `measurement_id` | UUID | Identity triplet |
| `user_id` | UUID | Indexed; FK to client system (no JOIN in Winnow) |
| `raw_payload` | JSONB | Immutable domain data snapshot |
| `created_at` | TIMESTAMPTZ | Via `TimestampMixin` |
| `updated_at` | TIMESTAMPTZ | Via `TimestampMixin` |

**Indexes**: `ix_submissions_project_id`, `ix_submissions_user_id`, composite `ix_submissions_triplet (project_id, entity_id, measurement_id)`, composite `ix_submissions_project_user (project_id, user_id)`.

---

### `submission_user_snapshots` (User State Snapshot — 1:1)

Captures the submitting user's identity and computed stats at the exact moment of submission. Written once alongside `submissions`. Never updated.

| Column | Type | Notes |
|---|---|---|
| `submission_id` | UUID PK + FK → `submissions` | Enforces strict 1:1 |
| `user_id` | UUID | Indexed |
| `role` | Text | Role at submission time |
| `trust_level` | Integer | Trust at submission time |
| `username` | Text | Nullable |
| `total_submissions` | Integer | Calculated by Winnow at creation time (see below) |
| `user_account_created_at` | TIMESTAMPTZ | Sourced from client payload |
| `user_account_updated_at` | TIMESTAMPTZ | Sourced from client payload |
| `custom_data` | JSONB | Project-specific user metadata |
| `created_at` | TIMESTAMPTZ | Via `TimestampMixin` |
| `updated_at` | TIMESTAMPTZ | Via `TimestampMixin` |

**`total_submissions` calculation**: Count of active chains for the user at the time of this submission — excludes chains voided with `supersede_reason IN ('edited', 'deleted')`. Calculated once, stored as a static integer.

---

### `scoring_snapshots` (Technical Analysis — 1:N)

Static scoring output written once per scoring event. The 1:N relationship (not 1:1) supports **Case G (Re-Scoring)** — a re-score event creates a new snapshot without modifying the original.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | Server-generated |
| `submission_id` | UUID FK → `submissions` | Not unique — 1:N for re-scoring |
| `confidence_score` | Float | `CHECK (0 ≤ x ≤ 100)` |
| `breakdown` | JSONB | Per-rule contributions |
| `required_validations` | JSONB | Full governance snapshot (role_configs, thresholds) |
| `thresholds` | JSONB | Advisory routing thresholds |
| `created_at` | TIMESTAMPTZ | Via `TimestampMixin` |
| `updated_at` | TIMESTAMPTZ | Via `TimestampMixin` |

**Indexes**: `ix_scoring_snapshots_submission_id`, `ix_scoring_snapshots_confidence_score`, composite `(submission_id, created_at DESC)` for latest-snapshot lookups.

---

### `status_ledger` (Lifecycle Log — Append-Only)

The authoritative, append-only record of every lifecycle state transition. Every state change is a new INSERT. Nothing is ever updated. This is the **Single Source of Truth** for submission status.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | Server-generated |
| `submission_id` | UUID FK → `submissions` | Indexed |
| `scoring_snapshot_id` | UUID FK → `scoring_snapshots` | Links state to the analysis |
| `status` | Text | `CHECK IN ('pending_review', 'approved', 'rejected', 'voided')` |
| `trust_delta` | Integer | Incremental trust change (may be negative) |
| `supersedes` | UUID FK → `status_ledger.id` (self-ref, `use_alter=True`) | Backward pointer; NULL = first in chain |
| `supersede_reason` | Text | `CHECK` (see below); NULL = initial entry |
| `created_at` | TIMESTAMPTZ | Primary ordering key |
| `updated_at` | TIMESTAMPTZ | Via `TimestampMixin` |

**Valid `supersede_reason` values**: `NULL` (initial), `edited`, `deleted`, `voting_concluded`, `auto_approve`, `auto_reject`, `admin_overwrite`, `re-scored`.

**Active-state resolution**: The active entry for a submission is the row with the **highest `created_at`** for that `submission_id`. Its `id` will not appear in any other row's `supersedes` column.

**Indexes**: `ix_status_ledger_submission_id`, `ix_status_ledger_status`, composite `(submission_id, created_at DESC)` for O(log N) active-state resolution.

---

### `submission_votes` (Voting Log — Append-Only)

Fully append-only. No UNIQUE constraint on `(submission_id, user_id)`. When a reviewer changes their vote they insert a new row. The `VotingService` resolves the "active" vote as the latest row by `created_at` per `(submission_id, user_id)`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `submission_id` | UUID FK → `submissions` | Indexed |
| `user_id` | UUID | |
| `vote` | Text | `CHECK (vote IN ('approve','reject') OR (is_override=true AND vote='voided'))` |
| `is_override` | Boolean | `True` = Admin Override (bypasses eligibility) |
| `user_trust_level` | Integer | Snapshotted at cast time |
| `user_role` | Text | Snapshotted at cast time |
| `note` | Text | Nullable |
| `created_at` | TIMESTAMPTZ | Via `TimestampMixin` |
| `updated_at` | TIMESTAMPTZ | Via `TimestampMixin` |

---

### `webhook_outbox` (Transactional Outbox)

Every `status_ledger` INSERT triggers a corresponding `webhook_outbox` INSERT in the same transaction (Transactional Outbox pattern — Rule 11). The background poller delivers these to the project's `webhook_url`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `submission_id` | UUID FK → `submissions` | |
| `event_type` | Text | e.g. `submission.created`, `submission.approved` |
| `payload` | JSONB | `StatusLedgerWebhookPayload` serialised |
| `status` | Text | `CHECK IN ('pending','processing','sent','failed','dead')` |
| `attempts` | Integer | Delivery attempt count |
| `max_attempts` | Integer | Default 5; transition to `dead` on exhaustion |
| `next_retry_at` | TIMESTAMPTZ | Back-off timestamp for retries |
| `last_error` | Text | Last delivery error message |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

---

## Trust Delta Algorithm

Every `status_ledger` row stores `trust_delta` — the **incremental** change needed to reach the target trust level. This enables full reversal without a separate audit table.

**Formula**: `trust_delta = target_trust(new_status) − SUM(all prior trust_deltas in lineage)`

The lineage sum is computed via a `WITH RECURSIVE` CTE traversing the `supersedes` chain — O(chain depth), single DB round-trip.

**Example (two-chain edit)**:

| Entry | Chain | Status | Lineage SUM before | trust_delta |
|---|---|---|---|---|
| L1 | A | `pending_review` | 0 | **0** |
| L2 | A | `approved` | 0 | **+10** |
| L3 | B | `pending_review` (edit) | 10 | **−10** |
| L4 | B | `approved` | 0 | **+10** |

Net trust after full approval of Chain B = **+10** ✅

---

## Advanced Governance Model

The `required_validations` JSONB snapshot (stored on `scoring_snapshots`) contains the full governance configuration at scoring time:

```json
{
  "threshold_score": 2,
  "role_configs": {
    "expert":  {"weight": 2, "min_trust": 0},
    "citizen": {"weight": 1, "min_trust": 50}
  },
  "default_config": {"weight": 1, "min_trust": 50},
  "blocked_roles": ["guest", "banned"],
  "review_tier": "community_review"
}
```

**Eligibility evaluation order** (in `GovernancePolicy.get_vote_weight`):
1. If role in `blocked_roles` → raise `NotEligibleError` (absolute, no fallback).
2. Lookup `cfg = role_configs.get(role, default_config)`.
3. If `trust_level < cfg.min_trust` → raise `NotEligibleError`.
4. If `cfg.weight == 0` → raise `NotEligibleError`.
5. Return `cfg.weight`.

---

## Performance Notes

- All "latest state" lookups use the composite index `(submission_id, created_at DESC)` — O(log N).
- The `WITH RECURSIVE` CTE for trust delta traversal is O(chain depth) in a single round-trip.
- `total_submissions` is a static snapshot integer (calculated once at creation) — not a live query.
- `SELECT … FOR UPDATE` on the latest `status_ledger` row serialises concurrent vote finalization and ledger appends (ADR-DB-004).

---

## ADR-DB-004: Row-Level Locking for Ledger Appends

The `VotingService` and `ScoringService` both lock the latest `status_ledger` row using `SELECT … FOR UPDATE` before computing trust deltas or finalizing state. This prevents double-trust-award in concurrent scenarios. SQLite (test environment) ignores `FOR UPDATE` — table-level write serialisation provides equivalent safety.

*See also: [04_risk_analysis.md](04_risk_analysis.md)*
