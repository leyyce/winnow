# Sprint 1 Retrospective Report
**Date Range:** 2026-03-10 – 2026-03-12
**Theme:** Core Framework Foundation — Registry, Scoring Pipeline, Governance Authority

---

## Goal

Establish the complete, layered skeleton of the Winnow QA framework: project scaffolding, design documentation, schema envelopes, the scoring pipeline, the Registry domain, the Governance Authority, and a first working test suite — all without any API or database layer.

---

## Commits in This Sprint

| Hash | Date | Message |
|---|---|---|
| `0cf5c82` | 2026-03-10 | Added design docs |
| `dc8e853` | 2026-03-10 | Added project scaffolding |
| `fc7d4db` | 2026-03-11 | Added new envelope and result schemas |
| `2b123cb` | 2026-03-11 | feat: initial implementation of core framework and tree project logic |
| `4fe6779` | 2026-03-11 | Added auto-discovery feature to `bootstrap.py` and more tests |
| `7ee71c1` | 2026-03-12 | refactor: address critical architectural flaws and introduce generic rules |
| `11bdd6d` | 2026-03-12 | refactor: address remaining architectural concerns and tighten validations |

---

## Architectural Milestones

### 1. Project Scaffolding & Design Docs (`0cf5c82`, `dc8e853`)
- Established the directory layout under `app/` with clean separation of `api/`, `scoring/`, `governance/`, `registry/`, `services/`, `schemas/`, `models/`, and `core/`.
- Published the initial architecture documents (`01_project_structure.md`, `02_architecture_patterns.md`, `03_api_contracts.md`, `04_risk_analysis.md`) defining all major contracts before any code was written — consistent with Rule 1 (Always Consult Architecture).
- Defined the 4-stage validation pipeline terminology: Stage 1 (Pydantic schema), Stage 2 (Confidence Score), Stage 3 (Governance), Stage 4 (Trust Advisory).

### 2. Envelope & Result Schemas (`fc7d4db`)
- Introduced the `SubmissionEnvelope` pattern: every inbound request wraps domain data in a stable outer structure carrying `project_id`, `submitter_role`, `trust_level`, and a raw `payload`.
- Added `ScoringResult` and `RuleResult` schemas to represent the output of Stage 2 in a structured, serialisable form.
- Established the principle that `payload` is accepted as raw JSON and validated server-side against the project-specific Pydantic schema, consistent with the API contract design principle "Dynamic Payload".

### 3. Core Framework & Tree Project Logic (`2b123cb`)
- Implemented the **Strategy Pattern** for scoring rules via the abstract `ScoringRule` base class, enabling pluggable, project-agnostic rule evaluation.
- Built the `ScoringPipeline` to orchestrate weighted rule evaluation and aggregate a final confidence score.
- Added the first three project-specific scoring rules for the Trees project: `HeightFactor`, `DistanceFactor`, `PlausibilityFactor`.
- Introduced the `TrustAdvisor` (Stage 4) to recommend trust-level adjustments after ground-truth finalisation, keeping Winnow advisory rather than authoritative over client data (Rule 5).
- Implemented the `GovernanceAuthority` for automated task orchestration: determining review requirements and task eligibility based on `ThresholdConfig` from `ProjectConfig`.
- Built the **Registry** domain using the **Builder Pattern** (`RegistryManager`, `ProjectRegistry`) and project-specific builders (e.g., `TreesProjectRegistry`), making the framework generically extensible to new projects.

### 4. Auto-Discovery & Bootstrap (`4fe6779`)
- Added the `bootstrap.py` auto-discovery mechanism to dynamically register project plugins at startup without hard-coded imports, enabling true project-agnosticism.
- Expanded the test suite to cover registry building and schema validation.

### 5. Critical Architectural Hardening — Refactor Round 1 (`7ee71c1`)
- **Template Method + Generics:** Replaced unsafe `assert isinstance` calls in `ScoringRule` with a `Generic[P]` Template Method pattern enforcing centralized, robust runtime type-checking.
- **Bootstrap Isolation:** Wrapped auto-discovery in `try/except` blocks to prevent a broken plugin from crashing the entire application.
- **Weight Integrity:** Added strict validation to `ScoringPipeline` requiring rule weights to sum exactly to `1.0` (tolerance `1e-6`), preventing silent scoring errors.
- **Threshold Logic:** Added Pydantic cross-field validation to `ThresholdConfig` to enforce `approve >= review >= reject` ordering.
- Deleted the deprecated `scoring/registry.py` shim.

### 6. Final Validation Hardening — Refactor Round 2 (`11bdd6d`)
- Enforced strict `[0.0, 1.0]` score boundaries in `RuleResult` via `__post_init__`.
- Updated `TrustAdvisor` to fail-fast (`raise ValueError`) on any unknown `final_status`, eliminating silent misclassification.
- Exposed pipeline rules as a public, read-only `rules` tuple property, removing test access to private attributes.
- Extracted duplicated test factories into a central `conftest.py`, establishing a shared test-fixture pattern for all future sprints.
- Added per-weight bounds validation (`0.0 <= w <= 1.0`) to `ScoringPipeline` alongside the existing sum validation.
- Documented the pragmatic domain-import trade-off in `manager.py` and synchronised `02_architecture_patterns.md`.

---

## Files Introduced / Modified

| File | Status |
|---|---|
| `docs/architecture/01_project_structure.md` | Created |
| `docs/architecture/02_architecture_patterns.md` | Created |
| `docs/architecture/03_api_contracts.md` | Created |
| `docs/architecture/04_risk_analysis.md` | Created |
| `app/schemas/envelope.py` | Created |
| `app/schemas/results.py` | Created |
| `app/scoring/base.py` | Created |
| `app/scoring/pipeline.py` | Created |
| `app/scoring/common/trust_level.py` | Created |
| `app/scoring/common/trust_advisor.py` | Created |
| `app/scoring/projects/trees/height_factor.py` | Created |
| `app/scoring/projects/trees/distance_factor.py` | Created |
| `app/scoring/projects/trees/plausibility_factor.py` | Created |
| `app/scoring/projects/trees/comment_factor.py` | Created |
| `app/governance/base.py` | Created |
| `app/governance/projects/trees.py` | Created |
| `app/registry/base.py` | Created |
| `app/registry/manager.py` | Created |
| `app/registry/projects/trees.py` | Created |
| `app/bootstrap.py` | Created |
| `app/models/project_config.py` | Created |
| `app/models/scoring_result.py` | Created |
| `app/tests/conftest.py` | Created |
| `app/tests/scoring/test_pipeline.py` | Created |
| `app/tests/scoring/test_tree_rules.py` | Created |
| `app/tests/scoring/test_common_rules.py` | Created |
| `app/tests/registry/test_registry.py` | Created |

---

## Tests Status

All tests introduced in this sprint passed by the end of `11bdd6d`, covering:
- Scoring pipeline weight validation (sum = 1.0, individual bounds).
- Rule type-safety via Generics.
- `TrustAdvisor` positive, negative, and fail-fast paths.
- `ThresholdConfig` cross-field ordering invariants.
- Registry auto-discovery and builder pattern.
- Schema envelope validation.

---

## Architectural Rules Applied

| Rule | How Applied |
|---|---|
| Rule 1 | Architecture docs written before code. |
| Rule 2 | No hardcoded weights or thresholds in scoring logic; all from `ProjectConfig`. |
| Rule 3 | `ThresholdConfig`, `role_weights` always sourced from registry. |
| Rule 5 | No domain tables created; `TrustAdvisor` is advisory only. |
| Rule 6 | All models use Pydantic V2 `ConfigDict`, `model_validate`, `Field`. |
| Rule 7 | Framework skeleton completed before API layer was introduced. |
| Rule 13 | Edge cases (weight bounds, score boundaries, unknown statuses) all covered. |
