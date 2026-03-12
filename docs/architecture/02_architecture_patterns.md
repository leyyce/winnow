# 02 — Architecture Patterns

> Design patterns that make Winnow's scoring logic dynamic, extensible, and project-agnostic.

**Terminology reminder:** *"Validation"* = Stage 1 (Pydantic schema checks). *"Scoring"* = Stage 2 (Confidence Score factors). *"Trust Evaluation & Advisory"* = Stage 4 — dual role: (a) Tₙ as scoring input from the wire, (b) `trust_adjustment` recommendation computed after ground-truth finalization. See `01_project_structure.md` for the full convention.

---

## Overview of Applied Patterns

| Pattern | Where | Why |
|---|---|---|
| **Strategy Pattern** | `app/scoring/` | Swap scoring rules per project without changing the pipeline. |
| **Envelope Pattern** | `app/schemas/envelope.py` | Separate stable metadata from variable domain payloads. |
| **Registry Pattern** | `app/registry/` | Dynamically resolve which Pydantic schema (Stage 1), scoring strategies (Stage 2 + 4), and governance policy apply to a given `project_id`. |
| **Builder Pattern** | `app/registry/base.py` + `app/registry/projects/` | `ProjectBuilder` ABC defines a standard interface for composing a `ProjectRegistryEntry`. Each project implements its own builder — no registry code changes when a new project is added. |
| **Bootstrap Pattern** | `app/bootstrap.py` | Auto-discovery startup module: dynamically scans `app.registry.projects`, finds every concrete `ProjectBuilder` subclass, and loads each into the registry — no manual imports required when adding a new project. |
| **Trust Advisor Pattern** | `app/scoring/common/trust_advisor.py` | Winnow advises, the client decides. Computes per-submission `trust_adjustment` deltas based on ground-truth finalization signals. |
| **Task Orchestration Pattern** | `app/governance/` + `app/services/governance_service.py` | Winnow is the **Governance Authority**: it determines review requirements (Target State) per submission and controls which tasks are available to which reviewers. Client projects act as Task Clients. |
| **Repository Pattern** | `app/services/` + `app/models/` | Abstract database access behind service functions so domain logic stays DB-free. |
| **Dependency Injection** | FastAPI `Depends` | Wire sessions, configs, and registries into handlers at runtime. |

---

## 1. Strategy Pattern — Dynamic Scoring Rules

The **Strategy Pattern** is the architectural backbone of Winnow. Each scoring factor is encapsulated as a self-contained *strategy* that conforms to a common interface. The `ScoringPipeline` does not know which rules it runs — it simply iterates over whatever strategies the registry provides for the current project.

### Class Diagram

```mermaid
classDiagram
    class ScoringRule~P~ {
        <<abstract, Generic[P]>>
        +name: str
        +weight: float
        +payload_type: type~P~
        +evaluate(payload: BaseModel, context: UserContext) RuleResult
        #_evaluate(payload: P, context: UserContext) RuleResult
    }

    note for ScoringRule "evaluate() is the public entry point.\nIt performs isinstance(payload, payload_type)\nand delegates to _evaluate().\nSubclasses implement _evaluate() only."

    class RuleResult {
        +rule_name: str
        +score: float
        +details: str | None
    }

    class TrustLevelRule {
        +payload_type: type~BaseModel~
        +max_trust_level: int
        #_evaluate(payload, context) RuleResult
    }

    note for TrustLevelRule "Stage 4 input: uses\ntrust_level from the wire"

    class HeightFactorRule {
        +payload_type: type~TreePayload~
        +h_max: float
        #_evaluate(payload, context) RuleResult
    }

    class DistanceFactorRule {
        +payload_type: type~TreePayload~
        #_evaluate(payload, context) RuleResult
    }

    class PlausibilityFactorRule {
        +payload_type: type~TreePayload~
        +species_params: dict
        #_evaluate(payload, context) RuleResult
    }

    class CommentFactorRule {
        +payload_type: type~TreePayload~
        +penalty: float
        #_evaluate(payload, context) RuleResult
    }

    ScoringRule <|-- TrustLevelRule : implements
    ScoringRule <|-- HeightFactorRule : implements
    ScoringRule <|-- DistanceFactorRule : implements
    ScoringRule <|-- PlausibilityFactorRule : implements
    ScoringRule <|-- CommentFactorRule : implements

    ScoringRule ..> RuleResult : returns

    class ScoringPipeline {
        +rules: tuple~ScoringRule~ <<property, read-only>>
        +run(payload: BaseModel, context: UserContext) ScoringResult
    }
    note for ScoringPipeline "Validates sum(weights) == 1.0 at\nconstruction time (math.isclose, tol=1e-6).\nAlso validates each individual weight ∈ [0,1].\nRaises ValueError if misconfigured."

    ScoringPipeline o-- ScoringRule : iterates over
```

> **Note:** `RangeCheckRule` and `CompletenessRule` are deliberately **absent** from this diagram. Those concerns are handled entirely by Pydantic `Field` constraints in `app/schemas/projects/` (Stage 1). Scoring rules only receive data that has already passed Stage 1.
>
> The **Trust Advisor** (Stage 4 output) is also absent here — it is not a `ScoringRule`. It runs *after* finalization, not during the scoring pipeline. See [Section 6](#6-trust-advisor-pattern--finalization-loop) below.

### How It Works

`ScoringRule` implements the **Template Method Pattern**. The base class owns the public `evaluate()` method; concrete rules implement the protected `_evaluate()` method.

1. **`evaluate(payload, context)` — the public entry point (base class, do not override).** It verifies that `isinstance(payload, self.payload_type)` and raises a `TypeError` if the wrong payload type is passed. It then delegates to `_evaluate()`. This centralises runtime type safety across all rules, eliminating the need for per-rule `assert isinstance` guards.
2. **`_evaluate(payload: P, context)` — the abstract hook (subclass responsibility).** Receives a payload already confirmed to be of type `P` and returns a `RuleResult`. The payload is a **validated Pydantic model instance** (e.g., `TreePayload`), not a raw dict — Stage 1 validation is guaranteed to have run first.
3. `RuleResult` contains a normalised `score ∈ [0, 1]` and an optional human-readable `details` string. The `score` is validated at instantiation via `__post_init__` — a value outside `[0.0, 1.0]` raises `ValueError` immediately, preventing malformed results from propagating.
4. The `ScoringPipeline` collects all `RuleResult` objects, multiplies each by its `weight`, sums them, and produces the final **Confidence Score (CS)**. The pipeline verifies `∑weights = 1.0` at construction time (see [Strict Weight Validation](#strict-weight-validation) below).

### Adding a New Rule

To add a scoring rule for a new project (e.g., a biodiversity observation app):

1. Create `app/scoring/projects/biodiversity/observation_plausibility.py`.
2. Subclass `ScoringRule[YourPayloadType]`, specifying the concrete payload type as the generic parameter.
3. Define the `payload_type` property returning the same concrete type (e.g., `return ObservationPayload`). The base class uses this at runtime to enforce type safety in `evaluate()`.
4. Implement `_evaluate(self, payload: YourPayloadType, context: UserContext) → RuleResult` with your scoring logic. Do **not** override `evaluate()` — type checking is handled for you.
5. Register the rule instance in the project's `ProjectBuilder.build()` method (e.g., `app/registry/projects/biodiversity.py`), providing its configured `weight`.

**No existing code needs to change — including `bootstrap.py`.** The auto-discovery mechanism (see [Section 3b](#3b-bootstrap-pattern--auto-discovery)) picks up the new builder automatically. This is the [Open/Closed Principle](https://en.wikipedia.org/wiki/Open%E2%80%93closed_principle) in action.

### Strict Weight Validation

The `ScoringPipeline` enforces two mathematical invariants at construction time:

1. **Individual bounds** — every rule weight must satisfy `0.0 ≤ wᵢ ≤ 1.0`. A `ValueError` is raised for the first out-of-bounds weight found.
2. **Sum constraint** — the weights must satisfy $\sum_{i} w_i = 1.0$ (checked with `math.isclose`, tolerance `1e-6`). Raises `ValueError` if violated.

Both checks run at construction time, before any submission is processed. A misconfigured `ProjectBuilder` is caught at bootstrap, never silently at runtime. Empty pipelines (zero rules) are exempt from both checks.

---

## 2. Envelope Pattern — Dynamic API Payloads

Detailed in [03_api_contracts.md](03_api_contracts.md). In summary:

```text
┌──────────────────────────────────┐
│  SubmissionEnvelope              │
│  ┌────────────────────────────┐  │
│  │ metadata (strictly typed)  │  │  ← project_id, submission_id, timestamp
│  ├────────────────────────────┤  │
│  │ user_context (typed)       │  │  ← user_id, role, trust_level (Data on the wire)
│  ├────────────────────────────┤  │
│  │ payload (dynamic JSON)     │  │  ← domain data; shape depends on project_id
│  └────────────────────────────┘  │
└──────────────────────────────────┘
```

The envelope's `metadata` and `user_context` sections are **always** validated by Pydantic. The `payload` section is accepted as a raw `dict[str, Any]` at the API level, then validated by project-specific Pydantic schemas resolved through the registry (Stage 1).

---

## 3. Registry Pattern — Project-to-Rules Mapping

### Structure

The registry domain lives in `app/registry/` and is composed of three layers:

| Module | Responsibility |
|---|---|
| `manager.py` | `_Registry` singleton + `ProjectRegistryEntry` dataclass. Value-agnostic — stores whatever a builder hands it. See [Pragmatic Domain Imports](#pragmatic-domain-imports-w2-trade-off) note below. |
| `base.py` | `ProjectBuilder` ABC — declares `project_id` property and `build() → ProjectRegistryEntry` method. |
| `projects/<name>.py` | Concrete builder per project — the single authoritative source for all project-specific numeric config. |
| `app/bootstrap.py` | Auto-discovers every concrete `ProjectBuilder` in `app.registry.projects` via `pkgutil`/`importlib`/`inspect` and calls `registry.load(builder)` for each. Adding a new project file is sufficient — `bootstrap.py` never needs to be edited. |

### What the Registry Provides

Given a `project_id`, the registry returns a `ProjectRegistryEntry` containing:

1. The **Pydantic schema class** to validate the raw payload against (Stage 1).
2. The **ordered list of `ScoringRule` instances** (with their configured weights) to run (Stage 2 + Stage 4 input).
3. The **scoring thresholds** (e.g., auto-approve ≥ 80, manual review 50–79, auto-reject < 50).
4. The **Trust Advisor configuration** (reward/penalty rules for Stage 4 output).
5. The **GovernancePolicy** instance (review-tier rules for task orchestration).

```mermaid
flowchart LR
    subgraph Registry
        direction TB
        TREE["project: 'tree-app'"]
        BIO["project: 'biodiversity'"]
        FUTURE["project: '...'"]
    end

    TREE --> TS["TreePayload schema (Stage 1)"]
    TREE --> TR["HeightFactor, DistanceFactor, PlausibilityFactor, CommentFactor, TrustLevel (Stage 2 + 4 input)"]
    TREE --> TT["Thresholds: approve≥80, review≥50, reject<50"]
    TREE --> TA["Trust Advisor config (Stage 4 output)"]
    TREE --> TG["TreeGovernancePolicy (review tiers)"]

    BIO --> BS["ObservationPayload schema (Stage 1)"]
    BIO --> BR["PhotoQuality, TaxonomyCheck, TrustLevel (Stage 2 + 4 input)"]
    BIO --> BT["Thresholds: approve≥75, review≥40, reject<40"]
    BIO --> BA["Trust Advisor config (Stage 4 output)"]
    BIO --> BG["BioGovernancePolicy (review tiers)"]
```

### How `scoring_service.py` Uses the Registry

This is the critical orchestration flow that enforces the **Stage 1 → Stage 2 → Governance → Stage 4-input** order:

```python
# Conceptual pseudo-code — NOT implementation

async def process_submission(envelope: SubmissionEnvelope) -> ScoringResultResponse:
    # 1. Resolve project configuration from the registry
    config = registry.get_config(envelope.metadata.project_id)
    #    → config contains: PayloadSchema, rules[], thresholds, trust_advisor_config, governance_policy

    # 2. STAGE 1 — Validate raw payload against project-specific Pydantic schema
    #    This enforces completeness, types, range bounds — all structural checks.
    #    If this fails, a 422 error is raised immediately. No scoring occurs.
    validated_payload = config.payload_schema.model_validate(envelope.payload)

    # 3. Persist submission (status = "pending_finalization")
    submission = await persist_submission(envelope, status="pending_finalization")

    # 4. STAGE 2 + STAGE 4 INPUT — Run scoring pipeline with validated data
    #    The pipeline receives a Pydantic model instance, NOT a raw dict.
    #    TrustLevelRule uses user_context.trust_level from the wire as Tₙ.
    result = config.pipeline.run(
        payload=validated_payload,       # ← already validated (Stage 1 passed)
        context=envelope.user_context,
    )

    # 5. GOVERNANCE — Determine review requirements ("Target State")
    #    The governance policy uses the Confidence Score + project rules
    #    to compute who must review this submission and how many reviewers are needed.
    required_validations = config.governance_policy.determine_requirements(
        confidence_score=result.total_score,
        user_context=envelope.user_context,
    )

    # 6. Persist scoring result + governance metadata (status remains "pending_finalization")
    #    The submission awaits ground-truth finalization from the client.
    await persist_result(submission.id, result, required_validations, status="pending_finalization")
    return build_response(result, required_validations, status="pending_finalization", config.thresholds)


async def finalize_submission(submission_id: UUID, final_status: str) -> FinalizationResponse:
    """Called when the client sends PATCH /submissions/{id}/final-status.
    
    The ground-truth decision (approved/rejected by expert/community)
    triggers the Trust Advisor to compute a trust_adjustment delta.
    """
    # 1. Load submission + scoring result
    submission = await get_submission(submission_id)

    # 2. Persist ground-truth status
    await update_submission_status(submission_id, final_status)  # "approved" or "rejected"

    # 3. STAGE 4 OUTPUT — Trust Advisor computes recommendation
    #    Derives user reliability from Winnow's own submissions table,
    #    then computes a per-submission trust_adjustment delta.
    config = registry.get_config(submission.project_id)
    trust_result = config.trust_advisor.compute_adjustment(
        user_id=submission.user_id,
        final_status=final_status,
        user_history=await get_user_submission_stats(submission.user_id),
    )

    return build_finalization_response(submission_id, final_status, trust_result)
```

> **Key insight:** The scoring pipeline, the governance policy, and the Trust Advisor run at **different times**. The pipeline + governance run synchronously on submission (Stage 2 + Tₙ + governance). The Trust Advisor runs only when the client sends the finalization signal with ground-truth data. This ensures that trust recommendations are based on confirmed outcomes, not preliminary scores.
>
> The `validated_payload` passed to `pipeline.run()` is a typed Pydantic model (e.g., `TreePayload`), not a `dict`. This means scoring rules can access fields with type safety (e.g., `payload.measurement.height`) instead of doing error-prone dict lookups.
>
> The `required_validations` returned by the governance policy tells the client exactly who must review this submission (Target State). This is Winnow acting as the **Governance Authority** — the client (Laravel) renders whatever Winnow permits.

### Pragmatic Domain Imports (W2 trade-off)

`ProjectRegistryEntry` in `manager.py` imports concrete types from the scoring and governance layers (`ScoringPipeline`, `TrustAdvisor`, `GovernancePolicy`). Architecturally the registry should be fully domain-agnostic; however, replacing these typed fields with `Any` would destroy IDE type-hinting and auto-complete for every service and test that consumes `ProjectRegistryEntry`.

**Decision:** accept this as a pragmatic trade-off.
- Only **abstract base types / infrastructure classes** are imported — never project-specific rule implementations.
- The registry never inspects or invokes domain logic; it is a typed container only.
- This decision is documented in the `manager.py` module docstring and here.

### Configuration Source

The registry can be populated from:

- **Code** (simple dict in `registry.py`) — easiest for the first prototype.
- **Database** (`project_config` table) — allows runtime changes without redeployment.
- **A combination** — code defines available rule classes; DB stores weights and thresholds.

For the Bachelor's thesis prototype, starting with code-based configuration and migrating to DB-backed configuration later is the recommended approach.

---

## 3b. Bootstrap Pattern — Auto-Discovery

The bootstrap module (`app/bootstrap.py`) is responsible for populating the registry at application startup. Rather than requiring manual imports for every new project, it uses Python's standard introspection tools to discover all registered project builders automatically.

### How It Works

1. `pkgutil.walk_packages` recursively iterates every module inside the `app.registry.projects` package.
2. `importlib.import_module` imports each module, triggering its top-level definitions.
3. `inspect.getmembers` enumerates all classes exported by the module.
4. Any class that is a concrete subclass of `ProjectBuilder` (i.e., `issubclass(cls, ProjectBuilder)` and `cls is not ProjectBuilder`) and is **defined in that module** (not just imported into it) is instantiated and passed to `registry.load(builder)`.

### Developer Experience

To add a new project to Winnow:

1. Create `app/registry/projects/<new_project>.py`.
2. Implement a concrete `ProjectBuilder` subclass inside it.
3. **Done.** `bootstrap.py` discovers and loads it automatically on next startup.

No changes to `bootstrap.py`, `registry/manager.py`, or any other infrastructure file are required.

### Integration with FastAPI

`bootstrap()` must be **explicitly called** — it does **not** execute on import. The canonical integration points are:

- **Production:** call `bootstrap()` inside the FastAPI `lifespan` async context manager in `main.py`, before the application begins serving requests.
- **Tests:** call `bootstrap()` once via a session-scoped `autouse` fixture in `conftest.py`. This ensures the registry is populated for all tests without duplicating setup.

This design prevents import-time side effects: a syntax error or misconfiguration in any project builder file cannot crash the application or contaminate unrelated test modules.

### Fault-Tolerant Loading

Each module import and each `registry.load()` call is individually wrapped in `try/except`. If a project builder raises any exception during discovery (e.g., a missing dependency, a bad configuration value, a `ValueError` from weight validation), Winnow logs a structured error for that project and **continues loading all remaining projects**. A single broken project file never prevents the rest of the application from starting.

```python
# Conceptual pseudo-code — NOT implementation

import pkgutil, importlib, inspect, logging
from app.registry.base import ProjectBuilder
from app.registry.manager import registry
import app.registry.projects as _projects_pkg

logger = logging.getLogger(__name__)

def bootstrap() -> None:
    """Discover and load all ProjectBuilder subclasses. Call explicitly from
    the FastAPI lifespan handler or a test fixture — never on import."""
    for finder, module_name, _ in pkgutil.walk_packages(
        path=_projects_pkg.__path__,
        prefix=_projects_pkg.__name__ + ".",
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            logger.exception("Failed to import project module %s — skipping", module_name)
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, ProjectBuilder)
                and obj is not ProjectBuilder
                and obj.__module__ == module.__name__  # defined here, not re-imported
            ):
                try:
                    registry.load(obj())
                except Exception:
                    logger.exception("Failed to load builder %s — skipping", obj.__name__)
```

---

## 4. Scoring Pipeline — Sequence Diagram (Initial Submission)

```mermaid
sequenceDiagram
    participant Client as Laravel App
    participant API as FastAPI Endpoint
    participant Service as ScoringService
    participant Registry as ScoringRegistry
    participant Schema as Pydantic PayloadSchema
    participant Pipeline as ScoringPipeline
    participant Rule as ScoringRule(s)
    participant DB as PostgreSQL

    Client->>API: POST /api/v1/submissions {envelope}
    API->>API: Validate envelope structure (Pydantic)
    API->>Service: process(envelope)

    Note over Service,Registry: Stage 1 — Validation
    Service->>Registry: get_config(project_id)
    Registry-->>Service: PayloadSchema, rules[], thresholds, trust_advisor_config
    Service->>Schema: model_validate(envelope.payload)
    Schema-->>Service: validated_payload (TreePayload instance)
    alt Stage 1 fails
        Schema-->>API: ValidationError → 422 response
    end

    Service->>DB: Persist Submission (status=pending_finalization)

    Note over Service,Rule: Stage 2 + Stage 4 input — Scoring (incl. Tₙ from wire)
    Service->>Pipeline: run(validated_payload, user_context)
    loop For each ScoringRule (incl. TrustLevelRule)
        Pipeline->>Rule: evaluate(validated_payload, user_context)
        Rule-->>Pipeline: RuleResult(score, details)
    end
    Pipeline-->>Service: ScoringResult(total_score, breakdown[])

    Service->>DB: Persist ScoringResult (status=pending_finalization)
    Service-->>API: ScoringResultResponse
    API-->>Client: 201 Created {result, status: pending_finalization}
```

## 4b. Finalization & Trust Advisory — Sequence Diagram

```mermaid
sequenceDiagram
    participant Client as Laravel App
    participant API as FastAPI Endpoint
    participant Service as ScoringService
    participant Advisor as TrustAdvisor
    participant DB as PostgreSQL

    Note over Client: Expert/community makes final decision
    Client->>API: PATCH /api/v1/submissions/{id}/final-status {final_status: "approved"}
    API->>Service: finalize(submission_id, final_status)

    Service->>DB: Load submission + scoring result
    Service->>DB: Update status → "approved" (ground truth)

    Note over Service,Advisor: Stage 4 output — Trust Evaluation & Advisory
    Service->>DB: Query user’s submission history (approval rate, streaks)
    DB-->>Service: UserSubmissionStats
    Service->>Advisor: compute_adjustment(user_id, final_status, user_stats)
    Advisor-->>Service: TrustAdjustment(delta: +2, reason: "...")

    Service->>DB: Persist trust_adjustment alongside finalized result
    Service-->>API: FinalizationResponse
    API-->>Client: 200 OK {final_status, trust_adjustment}
    Note over Client: Laravel applies trust delta to users.trust_level
```

---

## 5. Status Lifecycle

Submissions move through a defined lifecycle:

```mermaid
stateDiagram-v2
    [*] --> pending_finalization: POST /submissions (Stages 1→2→4-input)
    pending_finalization --> approved: PATCH /final-status (expert/community)
    pending_finalization --> rejected: PATCH /final-status (expert/community)
    approved --> [*]
    rejected --> [*]

    note right of pending_finalization: Confidence Score computed.\nAwaiting ground-truth decision.
    note right of approved: Trust Advisor computes\ntrust_adjustment delta.
    note right of rejected: Trust Advisor computes\ntrust_adjustment delta.
```

### Confidence Score Thresholds (Advisory)

The Confidence Score is included in the initial response to **help the client decide** how to route the submission (auto-approve, manual review, auto-reject). However, the definitive status is always set by the finalization signal.

| Confidence Score | Suggested Action (for client) |
|---|---|
| **≥ upper threshold** (e.g., 80) | Client may auto-approve without expert review. |
| **≥ lower threshold** (e.g., 50) | Flag for community or expert validation. |
| **< lower threshold** (e.g., 50) | Client may auto-reject; submitter notified. |

Thresholds are **per-project** and stored in the registry/config. This allows each Citizen Science project to tune its own tolerance. **Winnow advises; the client decides.**

### ThresholdConfig Cross-Field Validation

`ThresholdConfig` enforces a cross-field semantic constraint via a `@model_validator(mode="after")`:

$$approve \geq review \geq reject$$

A configuration where, for example, `approve=20, review=80, reject=90` would be logically nonsensical (the auto-approval bar would be lower than the rejection bar). Pydantic raises a `ValueError` at model construction time if this ordering is violated, preventing silent governance corruption. Each value is still independently constrained to `[0, 100]`.

---

## 6. Task Orchestration Pattern — Governance Authority

The **Task Orchestration Pattern** positions Winnow as the authoritative **Governance Engine** for the validation workflow. While the client project (Laravel) owns the domain data (trees, species, measurements), **Winnow owns the validation process state**: it decides which submissions need review, by whom, and how many validators are required.

### Design Principles

| Principle | Detail |
|---|---|
| **Winnow is the Governance Authority** | Winnow is the single source of truth for the validation workflow status. It determines review requirements ("Target State") and controls task eligibility. |
| **Client as Task Client** | The client project (Laravel) acts as a Task Client: it renders whatever Winnow permits. It calls `GET /tasks/available?user_trust=X` to discover which submissions the current user may review, and displays them accordingly. |
| **Score-driven governance** | Review requirements are determined by the Confidence Score and project-specific governance rules (e.g., "score > 90% needs 1 peer review; score < 50% needs expert review"). |
| **Project-configurable** | Each project registers its own `GovernancePolicy` with custom review tiers. New projects can define entirely different governance rules. |

### Conceptual Interface

```python
# Conceptual pseudo-code — NOT implementation

class RequiredValidations(BaseModel):
    """The 'Target State' — what must happen before this submission can be finalized."""
    min_validators: int            # e.g., 1, 2, 3
    required_min_trust: int        # minimum trust level for eligible reviewers
    required_role: str | None      # e.g., "expert", None = any role
    review_tier: str               # e.g., "auto_approve", "peer_review", "expert_review"


class GovernancePolicy(ABC):
    """Abstract base for project-specific governance rules."""

    @abstractmethod
    def determine_requirements(
        self,
        confidence_score: float,
        user_context: UserContext,
    ) -> RequiredValidations:
        """Given a scored submission, determine the review requirements."""
        ...

    @abstractmethod
    def is_eligible_reviewer(
        self,
        submission_score: float,
        submission_requirements: RequiredValidations,
        reviewer_trust: int,
        reviewer_role: str,
    ) -> bool:
        """Can a reviewer with the given trust/role review this submission?"""
        ...
```

### Tree-App Governance Example

```python
# Conceptual pseudo-code — NOT implementation

class TreeGovernancePolicy(GovernancePolicy):
    """Governance rules for the tree-tracking project."""

    def determine_requirements(self, confidence_score, user_context):
        if confidence_score >= 90:
            return RequiredValidations(
                min_validators=1, required_min_trust=3,
                required_role=None, review_tier="peer_review"
            )
        elif confidence_score >= 50:
            return RequiredValidations(
                min_validators=2, required_min_trust=5,
                required_role=None, review_tier="community_review"
            )
        else:  # score < 50
            return RequiredValidations(
                min_validators=1, required_min_trust=7,
                required_role="expert", review_tier="expert_review"
            )
```

### Task Query Flow

```mermaid
sequenceDiagram
    participant Laravel as Laravel App (Task Client)
    participant API as Winnow API
    participant GovService as GovernanceService
    participant DB as Winnow PostgreSQL

    Note over Laravel: User opens "Review Queue" page
    Laravel->>API: GET /api/v1/tasks/available?project_id=tree-app&user_trust=5&user_role=trusted
    API->>GovService: get_available_tasks(project_id, user_trust=5, user_role="trusted")

    GovService->>DB: Query submissions WHERE status="pending_finalization"
    DB-->>GovService: [submissions with required_validations]

    GovService->>GovService: Filter: for each submission,<br/>policy.is_eligible_reviewer(score, requirements, trust=5, role="trusted")

    GovService-->>API: [eligible submissions]
    API-->>Laravel: 200 OK {tasks: [{submission_id, score, review_tier, ...}]}
    Laravel->>Laravel: Render review queue UI
```

### Domain Ownership Diagram

```mermaid
graph LR
    subgraph "Laravel (Domain Data Owner)"
        TREES["Trees, Species,\nMeasurements, Photos"]
        USERS["Users, Trust Levels"]
        UI["UI / Review Queue"]
    end

    subgraph "Winnow (Governance Authority)"
        SCORING["Confidence Scores"]
        GOV["Review Requirements\n(Target State)"]
        TASKS["Task Eligibility\n(Who reviews what)"]
        AUDIT["Submission Snapshots\n& Audit Trail"]
    end

    TREES -->|payload on the wire| SCORING
    USERS -->|trust on the wire| SCORING
    SCORING --> GOV
    GOV --> TASKS
    TASKS -->|GET /tasks/available| UI
    UI -->|renders whatever\nWinnow permits| USERS
```

---

## 7. Trust Advisor Pattern — Finalization Loop

The **Trust Advisor** is a dedicated component (not a `ScoringRule`) that encapsulates the reward/penalty logic for Stage 4 output. It is invoked only when the client sends a finalization signal with a ground-truth decision.

### Design Principles

| Principle | Detail |
|---|---|
| **Winnow advises, client decides** | The Trust Advisor returns a `trust_adjustment` recommendation. The client (Laravel) is free to apply, modify, or ignore it. |
| **Ground-truth only** | Recommendations are computed from finalized outcomes (expert/community verdicts), never from preliminary scores. |
| **No user table in Winnow** | The Advisor derives user reliability metrics (approval rate, streak length, total finalized submissions) from Winnow's own `submissions` table. No synchronized `users` table is needed. |
| **Configurable per project** | Reward/penalty rules (e.g., "+2 for 5 consecutive approvals", "−3 for a rejection") are part of the project's registry configuration. |
| **Fail-fast on unknown status** | `compute_adjustment` raises `ValueError` if `final_status` is neither `"approved"` nor `"rejected"`. This prevents silent no-ops that could mask client-side integration bugs. |

### Conceptual Interface

```python
# Conceptual pseudo-code — NOT implementation

class TrustAdvisor:
    """Computes trust_adjustment deltas based on ground-truth finalization."""

    def __init__(self, config: TrustAdvisorConfig):
        self.reward_per_approval = config.reward_per_approval    # e.g., +1
        self.penalty_per_rejection = config.penalty_per_rejection # e.g., -3
        self.streak_bonus = config.streak_bonus                   # e.g., +2 for 5 consecutive approvals
        self.max_trust = config.max_trust                         # e.g., 10
        self.min_trust = config.min_trust                         # e.g., 0

    def compute_adjustment(
        self,
        user_id: UUID,
        final_status: str,               # "approved" or "rejected"
        user_history: UserSubmissionStats, # derived from Winnow's submissions table
    ) -> TrustAdjustment:
        # Apply reward/penalty based on the finalization outcome
        # Consider streaks, approval rate, total submissions
        # Clamp recommended new level to [min_trust, max_trust]
        ...
```

### Data Flow

```text
Laravel                          Winnow
  │                                │
  │  POST /submissions {envelope}  │
  │ ─────────────────────────────► │  → Stage 1 → Stage 2 → Tₙ input
  │  ◄───────────────────────────  │  ← 201 {score, status: pending_finalization}
  │                                │
  │  (expert reviews data)         │
  │                                │
  │  PATCH /submissions/{id}/      │
  │    final-status {approved}     │
  │ ─────────────────────────────► │  → Stage 4 output: Trust Advisor
  │  ◄───────────────────────────  │  ← 200 {trust_adjustment: {delta: +2, reason: "..."}}
  │                                │
  │  applies delta to              │
  │  users.trust_level             │
```

---

## 8. Separation of Concerns Summary

```mermaid
graph TB
    subgraph "Presentation (api/)"
        A[HTTP Handlers]
        A2[Finalization Endpoint]
        A3[Tasks Endpoint]
    end
    subgraph "Application (services/)"
        B[SubmissionService]
        C[ScoringService]
        GS[GovernanceService]
    end
    subgraph "Stage 1 — Validation (schemas/projects/)"
        D1[TreePayload]
        D2[Future ProjectPayload...]
    end
    subgraph "Stage 2 + 4-input — Scoring (scoring/)"
        E0[ScoringRule ABC]
        E1[Concrete Rules — Hₙ Aₙ Pₙ Kₙ Tₙ]
        F[ScoringRegistry]
        G[ScoringPipeline]
    end
    subgraph "Governance Authority (governance/)"
        GP[GovernancePolicy ABC]
        GP1[TreeGovernancePolicy]
    end
    subgraph "Stage 4-output — Trust Advisory (scoring/common/)"
        TA[TrustAdvisor]
    end
    subgraph "Infrastructure (db/ + models/)"
        H[SQLAlchemy Models]
        I[Async Session]
    end

    A -->|calls| B
    A2 -->|calls| C
    A3 -->|calls| GS
    B -->|delegates| C
    C -->|resolves config| F
    C -->|validates payload| D1
    C -->|runs| G
    C -->|determines requirements| GP1
    C -->|on finalization| TA
    GS -->|filters eligible tasks| GP1
    G -->|iterates| E1
    E1 -.->|implements| E0
    GP1 -.->|implements| GP
    B -->|persists via| H
    H -->|uses| I
```

> **The golden rule:** Domain logic (`scoring/`, `governance/`) never imports from infrastructure (`db/`, `models/`) or presentation (`api/`). It receives validated data and returns plain results. The Trust Advisor receives pre-computed user stats (derived by the service layer from the submissions table) — it does not query the database itself. The Governance Policy receives scoring results and returns review requirements — it does not query the database itself.
