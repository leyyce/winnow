# 03 — API Contracts

> JSON payload design for communication between client systems (e.g., Laravel tree-app) and the Winnow FastAPI microservice.

**Terminology reminder:** *"Validation"* = Stage 1 (Pydantic schema checks). *"Scoring"* = Stage 2 (Confidence Score factors). *"Trust Evaluation & Advisory"* = Stage 4 — dual role: (a) Tₙ as scoring input, (b) `trust_adjustment` recommendation after ground-truth finalization. See `01_project_structure.md` for the full convention.

---

## Design Principles

1. **Envelope Pattern** — Every request wraps domain data inside a stable, strictly-typed outer structure.
2. **Data on the Wire** — User metadata (role, trust level) travels with every request because databases are separated (Database-per-Service).
3. **Dynamic Payload** — The inner `payload` varies per project; it is accepted as raw JSON, then validated server-side against the project-specific Pydantic schema (Stage 1).
4. **Stage 1 as Prerequisite** — The raw payload **must** pass Stage 1 validation (Pydantic schema — types, ranges, completeness) before any scoring (Stage 2 + Stage 4 input) is attempted. A failed Stage 1 results in an immediate `422` error response with no scoring.
5. **Winnow Advises, Client Decides** — Winnow returns a Confidence Score and scoring breakdown. After expert/community finalization, it returns a `trust_adjustment` recommendation. The client (Laravel) owns the trust level and decides whether to apply it.
6. **Immutable Submission Snapshots** — Winnow stores submissions as point-in-time snapshots. Data corrections in the client trigger a new submission, not an update to the old one.
7. **RFC 7807 Problem Details** — All error responses follow a standardised structure.
8. **Winnow as Governance Authority** — Winnow owns the validation workflow state. It determines review requirements ("Target State") for each submission and controls which submissions are eligible for review by which users. The client (Laravel) acts as a **Task Client** — it renders whatever Winnow permits. See `02_architecture_patterns.md` §6 for the full Task Orchestration Pattern.
9. **Domain Ownership Separation** — Laravel owns domain data (trees, species, measurements) and user identity. Winnow owns the validation process state (submission lifecycle, review requirements, scoring results, audit trail). Neither system accesses the other's database.

---

## 1. Submission Request — Envelope Structure

### Endpoint

```
POST /api/v1/submissions
Content-Type: application/json
```

### Full JSON Example (Tree Measurement)

```json
{
  "metadata": {
    "project_id": "tree-app",
    "submission_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "submission_type": "tree_measurement",
    "submitted_at": "2026-03-10T17:30:00Z",
    "client_version": "1.2.0"
  },
  "user_context": {
    "user_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "username": "maria_oak",
    "role": "citizen",
    "trust_level": 3,
    "total_submissions": 42,
    "account_created_at": "2025-01-15T10:00:00Z"
  },
  "payload": {
    "tree_id": "b8f9e0d1-2a3b-4c5d-6e7f-8a9b0c1d2e3f",
    "species_id": "c1d2e3f4-5a6b-7c8d-9e0f-1a2b3c4d5e6f",
    "measurement": {
      "height": 18.5,
      "trunk_diameter": 45,
      "inclination": 5,
      "note": null
    },
    "step_length_measured": true,
    "photos": [
      {
        "path": "photos/abc123.jpg",
        "note": null
      },
      {
        "path": "photos/def456.jpg",
        "note": null
      }
    ],
    "species_stats": {
      "mean_height": 20.0,
      "std_height": 5.0,
      "mean_inclination": 3.0,
      "std_inclination": 2.0,
      "mean_trunk_diameter": 50.0,
      "std_trunk_diameter": 10.0
    }
  }
}
```

### Envelope Anatomy

```mermaid
graph TD
    E["SubmissionEnvelope"]
    M["metadata"]
    U["user_context"]
    P["payload (dynamic)"]

    E --> M
    E --> U
    E --> P

    M --> M1["project_id: str"]
    M --> M2["submission_id: UUID"]
    M --> M3["submission_type: str"]
    M --> M4["submitted_at: datetime"]
    M --> M5["client_version: str | None"]

    U --> U1["user_id: UUID"]
    U --> U2["username: str"]
    U --> U3["role: str (project-specific)"]
    U --> U4["trust_level: int (project-specific scale)"]
    U --> U5["total_submissions: int"]
    U --> U6["account_created_at: datetime"]

    P --> P1["dict — validated per project_id (Stage 1)"]
```

---

## 2. Pydantic Schema Design (Conceptual)

> These are **design sketches**, not implementation code. They show the intended structure.

### Envelope (stable across all projects)

```python
# app/schemas/envelope.py  (conceptual)

class SubmissionMetadata(BaseModel):
    project_id: str                       # e.g. "tree-app"
    submission_id: UUID
    submission_type: str                  # e.g. "tree_measurement", "tree_registration"
    submitted_at: datetime
    client_version: str | None = None

class UserContext(BaseModel):
    user_id: UUID
    username: str
    role: str = Field(min_length=1)      # project-specific roles
    trust_level: int = Field(ge=0)        # scale is project-specific
    total_submissions: int = Field(ge=0)
    account_created_at: datetime

class SubmissionEnvelope(BaseModel):
    metadata: SubmissionMetadata
    user_context: UserContext
    payload: dict[str, Any]               # Validated later by project-specific schema (Stage 1)
```

### Tree Project Payload — Stage 1 Validation Schema

> These Pydantic models in `app/schemas/projects/trees.py` enforce **all Stage 1 checks**: required fields (completeness), type correctness, and range constraints. Any submission that fails these checks is immediately rejected with a `422` error — the scoring pipeline is never invoked.

```python
# app/schemas/projects/trees.py  (conceptual)

class TreePhotoPayload(BaseModel):
    path: str = Field(min_length=1)       # relative or absolute path/URL to stored photo
    note: str | None = None               # optional free-text note for this photo

class TreeMeasurementPayload(BaseModel):
    height: float = Field(gt=0)           # metres (must be positive; no upper cap — governed by scoring)
    trunk_diameter: int = Field(gt=0)     # centimetres (DBH)
    inclination: int = Field(ge=0, le=90) # degrees from vertical
    note: str | None = None

class SpeciesStats(BaseModel):
    """Historical species statistics forwarded by the client for Pₙ scoring.
    Winnow is stateless w.r.t. species data — Laravel supplies μ/σ per submission."""
    mean_height: float = Field(gt=0)
    std_height: float = Field(ge=0)
    mean_inclination: float = Field(ge=0, le=90)
    std_inclination: float = Field(ge=0)
    mean_trunk_diameter: float = Field(gt=0)
    std_trunk_diameter: float = Field(ge=0)

class TreePayload(BaseModel):
    tree_id: UUID
    species_id: UUID
    measurement: TreeMeasurementPayload
    photos: list[TreePhotoPayload] = Field(min_length=2)  # ≥ 2 photos required
    step_length_measured: bool          # True = physically measured; False = estimated (Aₙ input)
    species_stats: SpeciesStats         # mandatory — required for Pₙ plausibility scoring

    @model_validator(mode="after")
    def photos_have_unique_paths(self) -> "TreePayload":
        """Guard: no duplicate photo paths within one submission."""
        ...
```

> **Key design notes vs. earlier drafts:**
> - The payload is **flat** — `tree_id` and `species_id` sit directly on `TreePayload`, not inside a nested `TreeInfo` sub-object. Fields like `condition`, `location`, and `location_confidence` belong to Laravel's domain data and are not forwarded to Winnow.
> - `trunk_diameter` is in **centimetres** (not mm).
> - The distance field is `step_length_measured: bool` (was it physically measured?), not a separate `distance_to_tree` float — Winnow only needs the boolean for the Aₙ factor.
> - Photos carry a `path` string and an optional `note`; there is no `photo_id`, `type`, or `url` field — photo identity and classification are Laravel's concern.
> - `species_stats` (μ/σ values for height, inclination, and trunk diameter) replaces the old allometric `species_reference` (a–g coefficients). It is **required** — every submission must include species statistics for Pₙ plausibility scoring.

### Stage 1 → Stage 2 Validation Flow

```mermaid
flowchart TD
    A["Incoming JSON request"] --> B["Pydantic: validate SubmissionEnvelope"]
    B -->|valid| C["Extract project_id from metadata"]
    B -->|invalid| ERR1["400 — envelope structure error"]
    C --> D["Registry: lookup PayloadSchema for project_id"]
    D -->|not found| ERR2["422 — unknown project_id"]
    D -->|found| E["Stage 1: validate payload against PayloadSchema"]
    E -->|invalid| ERR3["422 — payload validation error (Stage 1 failed)"]
    E -->|valid| F["Stage 1 passed ✓ — Proceed to Scoring Pipeline (Stage 2 + 4)"]

    style E fill:#e6f3ff,stroke:#0066cc
    style F fill:#e6ffe6,stroke:#009900
    style ERR3 fill:#ffe6e6,stroke:#cc0000
```

> **Stage 1 is the gatekeeper:** If the payload fails Pydantic validation (missing required fields, out-of-range values, wrong types), the request is rejected immediately. The scoring pipeline **never** receives invalid data.

---

## 3. Scoring Response (Initial Submission)

### Endpoint

```
← 201 Created
Content-Type: application/json
```

### JSON Example

```json
{
  "submission_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "project_id": "tree-app",
  "status": "pending_finalization",
  "confidence_score": 67.5,
  "breakdown": [
    {
      "rule": "height_factor",
      "weight": 0.20,
      "score": 0.85,
      "weighted_score": 17.0,
      "details": "Height 18.5m normalised against h_max=72m"
    },
    {
      "rule": "distance_factor",
      "weight": 0.20,
      "score": 1.00,
      "weighted_score": 20.0,
      "details": "Distance was measured (not estimated)"
    },
    {
      "rule": "trust_level",
      "weight": 0.25,
      "score": 0.30,
      "weighted_score": 7.5,
      "details": "Trust level 3/10 (Stage 4 input: from wire)"
    },
    {
      "rule": "comment_factor",
      "weight": 0.05,
      "score": 1.00,
      "weighted_score": 5.0,
      "details": "No comment present — no penalty"
    },
    {
      "rule": "plausibility_factor",
      "weight": 0.30,
      "score": 0.60,
      "weighted_score": 18.0,
      "details": "Height deviates ~1.2σ from species average"
    }
  ],
  "required_validations": {
    "min_validators": 2,
    "required_min_trust": 5,
    "required_role": null,
    "review_tier": "community_review"
  },
  "thresholds": {
    "approve": 80,
    "review": 50,
    "reject": 50
  },
  "created_at": "2026-03-10T17:30:01Z"
}
```

> **Note:** The initial response always returns `status: "pending_finalization"`. The Confidence Score, thresholds, and `required_validations` are provided so the client has **immediate metadata for its UI**. The `required_validations` object defines the "Target State" — how many validators are needed, what minimum trust level they require, and whether a specific role (e.g., expert) is mandatory. The client renders its review queue and permissions accordingly. The definitive status is set by the finalization signal.

### Response Schema (Conceptual)

```python
# app/schemas/results.py  (conceptual)

class RuleBreakdown(BaseModel):
    rule: str
    weight: float = Field(ge=0.0, le=1.0)   # fractional weight (0–1)
    score: float = Field(ge=0.0, le=1.0)    # normalised rule output (0–1)
    weighted_score: float = Field(ge=0.0)   # score × weight × 100
    details: str | None = None

class ThresholdConfig(BaseModel):
    approve: float = Field(ge=0.0, le=100.0)
    review: float = Field(ge=0.0, le=100.0)
    reject: float = Field(ge=0.0, le=100.0)
    # Cross-field invariant enforced by @model_validator(mode="after"):
    # approve >= review >= reject

class RequiredValidations(BaseModel):
    min_validators: int          # e.g., 1, 2, 3
    required_min_trust: int      # minimum trust level for eligible reviewers
    required_role: str | None    # e.g., "expert", None = any role
    review_tier: str             # e.g., "peer_review", "community_review", "expert_review"

class ScoringResultResponse(BaseModel):
    submission_id: UUID
    project_id: str
    status: Literal["pending_finalization", "approved", "rejected"]
    confidence_score: float = Field(ge=0.0, le=100.0)  # 0–100
    breakdown: list[RuleBreakdown]
    required_validations: RequiredValidations
    thresholds: ThresholdConfig
    created_at: datetime
```

---

## 3b. Finalization Request & Response

After the client’s expert or community makes a final decision, the client notifies Winnow. This closes the feedback loop and triggers the Trust Advisor (Stage 4 output).

### Endpoint

```
PATCH /api/v1/submissions/{submission_id}/final-status
Content-Type: application/json
```

### Request Body

```json
{
  "final_status": "approved",
  "reviewed_by": "expert_user_42",
  "review_note": "Measurement confirmed on-site."
}
```

### Request Schema (Conceptual)

```python
# app/schemas/finalization.py  (conceptual)

class FinalizationRequest(BaseModel):
    final_status: Literal["approved", "rejected"]
    reviewed_by: str | None = None       # who made the decision
    review_note: str | None = None       # optional explanation
```

### Response (200 OK)

```json
{
  "submission_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "final_status": "approved",
  "confidence_score": 67.5,
  "trust_adjustment": {
    "user_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "recommended_delta": 2,
    "reason": "5 consecutive approved submissions (streak bonus)",
    "current_trust_level": 3,
    "project_min_trust": 0,
    "project_max_trust": 500
  },
  "finalized_at": "2026-03-11T09:15:00Z"
}
```

### Response Schema (Conceptual)

```python
# app/schemas/finalization.py  (conceptual)

class TrustAdjustment(BaseModel):
    user_id: UUID
    recommended_delta: int               # positive = reward, negative = penalty
    reason: str
    current_trust_level: int             # as received on the wire at submission time
    project_min_trust: int               # project-configured minimum trust value
    project_max_trust: int               # project-configured maximum trust value

class FinalizationResponse(BaseModel):
    submission_id: UUID
    final_status: Literal["approved", "rejected"]
    confidence_score: float              # original score for reference
    trust_adjustment: TrustAdjustment
    finalized_at: datetime
```

> **Important:** The `trust_adjustment` is a **recommendation**. Laravel receives it and decides whether to apply it to `users.trust_level`. Winnow never directly modifies the client’s user data.


> **Race-condition safeguard:** The `TrustAdjustment` response deliberately omits a `recommended_new_level` field. Returning a pre-computed new level would tempt the client to blindly overwrite its DB value, causing race conditions when parallel submissions produce concurrent deltas. The client **MUST** apply only the `recommended_delta` atomically, using the returned bounds to clamp the result (e.g., `UPDATE users SET trust_level = CLAMP(trust_level + delta, project_min_trust, project_max_trust) WHERE id = ?`). The trust scale boundaries (`project_min_trust`, `project_max_trust`) are project-specific and configured in the Winnow registry.

---

## 4. Error Responses — RFC 7807 Problem Details

All error responses use a consistent structure based on [RFC 7807](https://www.rfc-editor.org/rfc/rfc7807).

### Schema

```json
{
  "type": "https://winnow.example.com/errors/validation-error",
  "title": "Payload Validation Failed",
  "status": 422,
  "detail": "Stage 1 validation failed: 2 errors in tree measurement payload.",
  "instance": "/api/v1/submissions",
  "errors": [
    {
      "field": "payload.measurement.height",
      "message": "Value must be greater than 0.",
      "type": "value_error"
    },
    {
      "field": "payload.photos",
      "message": "At least 2 photos are required.",
      "type": "value_error"
    }
  ]
}
```

### Error Types

| HTTP Status | `type` suffix | When |
|---|---|---|
| `400` | `/errors/bad-request` | Malformed JSON, missing required envelope fields. |
| `404` | `/errors/not-found` | Submission ID not found when querying results. |
| `422` | `/errors/validation-error` | Stage 1 Pydantic validation failure on envelope or payload. |
| `422` | `/errors/unknown-project` | `project_id` not registered in the system. |
| `409` | `/errors/already-finalized` | Submission already finalized with a different status (conflict). |
| `500` | `/errors/internal` | Unexpected server error. |

---

## 5. Data Flow — Full Lifecycle (Laravel ↔ Winnow)

```mermaid
sequenceDiagram
    participant User as Citizen Scientist
    participant Laravel as Laravel Tree-App
    participant Winnow as Winnow (FastAPI)
    participant WinnowDB as Winnow PostgreSQL

    rect rgb(230, 243, 255)
    Note over User,WinnowDB: Phase 1 — Submission & Scoring
    User->>Laravel: Submit tree measurement via form
    Laravel->>Laravel: Client-side form validation (required fields, types)
    Laravel->>Laravel: Build envelope (metadata + user_context + payload)
    Laravel->>Winnow: POST /api/v1/submissions {envelope}

    Note over Winnow: Stage 1 — Validation (Pydantic)
    Winnow->>Winnow: Validate envelope structure
    Winnow->>Winnow: Resolve project → validate payload against schema
    alt Stage 1 fails
        Winnow-->>Laravel: 422 {validation errors}
    end

    Note over Winnow: Stage 2 + Stage 4 input — Scoring (Hₙ, Aₙ, Pₙ, Kₙ, Tₙ)
    Winnow->>WinnowDB: Persist submission + scoring result (pending_finalization)
    Winnow-->>Laravel: 201 {confidence_score, breakdown, required_validations, status: pending_finalization}
    Laravel->>Laravel: Route based on score + required_validations (render review queue)
    Laravel-->>User: Show preliminary result in UI
    end

    rect rgb(230, 255, 230)
    Note over User,WinnowDB: Phase 2 — Finalization & Trust Advisory
    User->>Laravel: Expert/community reviews & makes final decision
    Laravel->>Winnow: PATCH /api/v1/submissions/{id}/final-status {approved}

    Note over Winnow: Stage 4 output — Trust Evaluation & Advisory
    Winnow->>WinnowDB: Update status → approved (ground truth)
    Winnow->>WinnowDB: Query user’s submission history
    Winnow->>Winnow: Trust Advisor computes trust_adjustment delta
    Winnow->>WinnowDB: Persist trust_adjustment
    Winnow-->>Laravel: 200 {final_status, trust_adjustment: {delta: +2, reason: "..."}}
    Laravel->>Laravel: Apply trust delta to users.trust_level
    Laravel-->>User: Updated trust level reflected in UI
    end

    rect rgb(255, 243, 230)
    Note over User,WinnowDB: Phase 3 — Task Query (Governance)
    User->>Laravel: Open "Review Queue" page
    Laravel->>Winnow: GET /api/v1/tasks/available?project_id=tree-app&user_trust=5&user_role=trusted
    Winnow->>WinnowDB: Query pending_finalization submissions
    Winnow->>Winnow: Apply governance policy (filter by eligibility)
    Winnow-->>Laravel: 200 {tasks: [{submission_id, score, review_tier, ...}]}
    Laravel-->>User: Render eligible review tasks
    end
```

---

## 6. Task Query — Available Review Tasks (Governance)

This endpoint enables the client to ask Winnow: *"Which submissions are currently eligible for review by a user with Trust Level X?"*

Winnow filters its `submissions` table using the project's governance policy (registered in the registry). The client acts as a **Task Client** — it renders whatever Winnow permits.

### Endpoint

```
GET /api/v1/tasks/available?project_id=tree-app&user_trust=5&user_role=trusted
```

### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_id` | string | Yes | Which project's submissions to query. |
| `user_trust` | int | Yes | The reviewer's current trust level (sent by the client). Scale is project-specific. |
| `user_role` | string | No | The reviewer's role. Project-specific (e.g., roles defined in the project's governance config). |
| `page` | int | No | Page number for pagination (default: 1). |
| `per_page` | int | No | Items per page (default: 20, max: 100). |

### Response (200 OK)

```json
{
  "tasks": [
    {
      "submission_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "project_id": "tree-app",
      "submission_type": "tree_measurement",
      "confidence_score": 67.5,
      "review_tier": "community_review",
      "required_validations": {
        "min_validators": 2,
        "required_min_trust": 5,
        "required_role": null,
        "review_tier": "community_review"
      },
      "submitted_at": "2026-03-10T17:30:01Z"
    },
    {
      "submission_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
      "project_id": "tree-app",
      "submission_type": "tree_measurement",
      "confidence_score": 42.0,
      "review_tier": "expert_review",
      "required_validations": {
        "min_validators": 1,
        "required_min_trust": 7,
        "required_role": "expert",
        "review_tier": "expert_review"
      },
      "submitted_at": "2026-03-10T18:00:00Z"
    }
  ],
  "total": 2,
  "page": 1,
  "per_page": 20
}
```

> **Note:** The second task (`expert_review`) would only appear for a reviewer with `user_trust ≥ 7` AND `user_role = expert`. Winnow applies the governance policy's `is_eligible_reviewer()` logic server-side to filter results. The client never needs to implement this logic.

### Response Schema (Conceptual)

```python
# app/schemas/tasks.py  (conceptual)

class TaskItem(BaseModel):
    submission_id: UUID
    project_id: str
    submission_type: str
    confidence_score: float
    review_tier: str
    required_validations: RequiredValidations
    submitted_at: datetime

class TaskListResponse(BaseModel):
    tasks: list[TaskItem]
    total: int
    page: int
    per_page: int
```

---

## 7. Querying Results

### Endpoint

```
GET /api/v1/results/{submission_id}
```

### Response

Returns the `ScoringResultResponse` schema (including `required_validations`) for the initial scoring data. If the submission has been finalized, use `GET /api/v1/results/{submission_id}/finalization` or check the persisted `FinalizationResponse` — the finalization response (`TrustAdjustment`, `final_status`) is a **separate schema** (`app/schemas/finalization.py`) and is not embedded inside `ScoringResultResponse`.

### List Endpoint (optional, for dashboards)

```
GET /api/v1/results?project_id=tree-app&status=pending_finalization&page=1&per_page=20
```

---

## 8. Questions & Assumptions

> These are documented here for discussion; they do not block the initial prototype.

1. **Authentication between Laravel and Winnow:** Assumed to use a shared API key (`X-API-Key` header) for the prototype. A more robust solution (e.g., mutual TLS, OAuth2 client credentials) can be added later.

2. **Photo handling:** Winnow does **not** receive raw image files. The Laravel app uploads photos to its own storage; only URLs are passed in the payload. Future ML-based image validation could fetch images on demand.

3. **Submission types:** The `submission_type` field (e.g., `tree_measurement` vs. `tree_registration`) allows different validation rule sets within the same project. For the tree-app prototype, `tree_measurement` is the primary type.

4. **Species reference data:** The allometric coefficients (`a`–`g`) from `tree_species` are sent in the payload so Winnow can compute plausibility without accessing the Laravel database. If this data rarely changes, a caching mechanism on Winnow's side could be considered.

5. **Idempotency:** The `submission_id` is generated by the client (Laravel). If a submission with the same ID is sent twice, Winnow should return the existing result rather than re-processing (idempotent POST). The finalization endpoint is also idempotent — re-sending the same `final_status` returns the existing result.

6. **Synchronous flow (Phase 1):** The current design is synchronous (request → response) for both submission and finalization. If scoring becomes slow (e.g., ML inference), a future Phase 2 iteration could accept the submission with `202 Accepted` and POST results back to a Laravel webhook.

7. **Trust Advisor as recommendation:** The `trust_adjustment` returned in the finalization response is advisory. Laravel owns the `trust_level` field and has the final say on whether to apply the delta. This avoids dual-write consistency issues.

8. **Data corrections:** If the original data in Laravel is corrected after submission, the client should send a **new submission** to Winnow with the corrected data. The old submission and its score remain as an immutable historical record. Winnow stores submissions, not entities.
