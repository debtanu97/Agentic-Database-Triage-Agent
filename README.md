# DB Triage AI Agent

> **Autonomous SQL diagnosis and rewrite at Oracle scale.**
> Detects, explains, and rewrites inefficient SQL queries using an LLM-based ReAct agent — with production-grade guardrails, continuous evaluation, and developer workflow integration.

---

## Table of Contents

- [Overview](#overview)
- [Key Metrics](#key-metrics)
- [System Architecture](#system-architecture)
  - [Telemetry Sources](#telemetry-sources)
  - [Ingestion Plane](#ingestion-plane)
  - [Agent Orchestration Plane](#agent-orchestration-plane)
  - [Developer Workflow Integration](#developer-workflow-integration)
  - [Observability & Evaluation Plane](#observability--evaluation-plane)
- [Ingestion & Pre-filter Funnel](#ingestion--pre-filter-funnel)
- [Agent Internals: The ReAct Loop](#agent-internals-the-react-loop)
  - [Context Assembly](#context-assembly)
  - [The ReAct Loop](#the-react-loop)
  - [Cumulative Evidence Map](#cumulative-evidence-map)
  - [Rewrite Generation](#rewrite-generation)
- [Guardrail & Validation Layer](#guardrail--validation-layer)
  - [Check 1 — Semantic Equivalence](#check-1--semantic-equivalence)
  - [Check 2 — Schema Binding](#check-2--schema-binding)
  - [Check 3 — Plan Regression](#check-3--plan-regression)
  - [Check 4 — Mutation Safety](#check-4--mutation-safety)
  - [Composite Confidence Scoring](#composite-confidence-scoring)
  - [Output Routing](#output-routing)
- [Evaluation & Continuous Improvement](#evaluation--continuous-improvement)
  - [Golden Evaluation Set](#golden-evaluation-set)
  - [Shadow Scoring](#shadow-scoring)
  - [Drift Detection & Auto-Rollback](#drift-detection--auto-rollback)
  - [Feedback Loop & Fine-tuning](#feedback-loop--fine-tuning)
  - [Prompt Versioning](#prompt-versioning)
- [Knowledge Base](#knowledge-base)
- [Developer Integration](#developer-integration)
- [Scale & Reliability](#scale--reliability)
- [Security & Compliance](#security--compliance)
- [Worked Example: Partition Pruning Bug](#worked-example-partition-pruning-bug)
- [Open Problems](#open-problems)

---

## Overview

At Oracle scale — hundreds of thousands of database instances across OCI Autonomous DB, Exadata clusters, and on-premises Oracle deployments — inefficient SQL is a constant drain on DBA time. A query that performs adequately at 10M rows silently degrades after a data migration triples the table size. A developer wraps a date column in `TO_DATE()` and unknowingly disables partition pruning across 12 partitions. A missing composite index forces a hash join to spill to disk under peak load.

The manual debugging cycle for these incidents costs 2–6 hours of DBA time per event, and they happen thousands of times per day.

The DB Triage AI Agent automates the diagnosis and rewrite cycle. It ingests slow query telemetry from all Oracle deployment types, runs a bounded ReAct agent loop that assembles evidence from database tools (EXPLAIN PLAN, table statistics, index inventory, AWR history), generates a candidate SQL rewrite with structured output, and validates it through four independent safety checks before surfacing it to the developer.

**The design principle:** the system's job is not to generate good rewrites. It is to know with high confidence *which rewrites are safe* — and to never surface the ones it isn't sure about.

---

## Key Metrics

| Metric | Value |
|---|---|
| Database instances monitored | 100,000+ |
| Raw slow query events / day | ~50M |
| LLM invocations / day (after pre-filter) | ~2M |
| Pre-filter reduction rate | **96%** |
| Pre-filter P99 latency | < 100ms |
| End-to-end triage P99 latency | < 30s |
| Rewrite precision (production) | **> 90%** |
| False positive rate | **< 5%** |
| Rewrite cache hit rate | ~60% |
| Auto-rollback time on regression | < 60s |

---

## System Architecture

The system is structured into four horizontal planes that interact through well-defined interfaces. Each plane scales independently.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TELEMETRY SOURCES                                 │
│   OCI Autonomous DB    ·    Exadata Clusters    ·    On-prem Oracle DB      │
│                      (via lightweight agent collectors)                     │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            INGESTION PLANE                                  │
│   OCI Streaming / Kafka  →  Telemetry Normalizer  →  Slow Query Classifier  │
│              Schema norm · Dedup by plan hash · Enrichment                  │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       AGENT ORCHESTRATION PLANE                             │
│                                                                             │
│   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────┐     │
│   │  Pre-filter     │    │  ReAct Loop     │    │  Guardrail Layer    │     │
│   │  (5 checks)     │───▶│  (max 5 iter)   │───▶│  (4 checks)         │     │
│   └─────────────────┘    └─────────────────┘    └─────────────────────┘     │
│                                   │                         │               │
│   ┌─────────────────┐    ┌────────┴────────┐    ┌──────────▼──────────┐     │
│   │  Knowledge Base │    │  Evidence Map   │    │  Confidence Gate    │     │
│   │  (vector index) │    │  (belief state) │    │  + Output Router    │     │
│   └─────────────────┘    └─────────────────┘    └─────────────────────┘     │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    DEVELOPER WORKFLOW INTEGRATION                           │
│    SQL IDE Plugin    ·    CI/CD Gate    ·    OCI Console DBA Panel          │
│         Notification Router    ·    Audit Log    ·    Human Review Queue    │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│              OBSERVABILITY & EVALUATION PLANE  (spans all above)            │
│   Eval Harness · Metrics Store · Model Registry · Retraining Pipeline       │
│   Golden Set (2K pairs) · Drift Detector · Prompt Versioning · DPO/LoRA     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Telemetry Sources

| Source | Collection Method | Key Telemetry |
|---|---|---|
| OCI Autonomous DB | Native OCI telemetry API | V$SQL, AWR, ASH snapshots |
| Exadata Clusters | DB Agent collector | CellStat, IORM, SQL Monitor |
| On-prem Oracle DB | Lightweight sidecar agent | V$SQL, EXPLAIN PLAN, DBA_HIST_SQLSTAT |

### Ingestion Plane

| Component | Technology | Responsibilities |
|---|---|---|
| Streaming Ingestor | OCI Streaming / Kafka | Partitioned by `db_id`; 7-day retention |
| Telemetry Normalizer | Java/Go stream processor | Schema normalisation, dedup by plan hash, enrichment |
| Slow Query Classifier | Rule + heuristic engine | Flags queries exceeding thresholds; priority scoring |

### Agent Orchestration Plane

| Component | Technology | Responsibilities |
|---|---|---|
| Agent Orchestrator | Python / LangGraph | ReAct loop, tool dispatch, iteration budget (max 5), result assembly |
| SQL Analysis Agent | Tool-calling LLM sub-agent | EXPLAIN, table stats, index usage, AWR plan regressions |
| Rewrite Agent | Fine-tuned Oracle SQL LLM | Structured JSON output: SQL + rationale + confidence + risks |
| Knowledge Base | Oracle NoSQL / pgvector | Indexed patterns, anti-patterns, vector-indexed rewrite history |
| Guardrail Layer | Synchronous validation service | Four independent checks (see below) |
| Feedback Store | Oracle DB + object storage | DBA labels, latency delta signals, CI block events |

### Developer Workflow Integration

| Component | Platform | Responsibilities |
|---|---|---|
| SQL IDE Plugin | VS Code / SQL Developer | Inline suggestions, one-click apply, amber underline on slow patterns |
| CI/CD Gate | OCI DevOps / GitHub Actions | Blocks PRs with slow patterns; posts rewrite as diff comment |
| OCI Console DBA Panel | OCI Console | Real-time dashboard: alerts, pending rewrites, accept/reject queue |
| Notification Router | OCI Notifications + PagerDuty | Severity-based routing: critical pages on-call, info sends digest |
| Audit Log | OCI Object Storage (WORM) | Every agent decision, input, output, and confidence score |
| Human Review Queue | Internal review UI | Low-confidence rewrites for DBA review; source of gold labels |

### Observability & Evaluation Plane

| Component | Responsibilities |
|---|---|
| Eval Harness | Continuous scoring against golden set; offline + shadow modes |
| Metrics Store | Rewrite precision, false positive rate, plan improvement rate (time-series) |
| Model Registry | Versioned model checkpoints + independently versioned prompt templates |
| Retraining Pipeline | Weekly DPO/LoRA fine-tuning gated by eval harness before promotion |
| Drift Detector | 24h rolling window; triggers auto-rollback on SLO breach |

---

## Ingestion & Pre-filter Funnel

Before any slow query reaches the LLM, it passes through five checks applied in strict order from cheapest to most expensive. The first failure short-circuits the rest.

```
Raw slow query events          ████████████████████████████████  50M  (100%)
                                                    │
                               ① Bloom filter       │  < 1ms · in-memory
After bloom filter             ███████████████████  18M  (64% drop)
                                                    │
                               ② Rewrite cache      │  2–5ms · Redis
After cache hit served         ████████████         7.2M
                                                    │
                               ③ Inflight dedup     │  2–3ms · Redis TTL 90s
After inflight dedup           ██████████           5.8M
                                                    │
                               ④ Quota check        │  3–5ms · deferred, not dropped
After quota check              █████████            5.1M
                                                    │
                               ⑤ Actionability      │  8–15ms · rule-based classifier
LLM invocations                █████                ~2M  (96% total reduction)
```

### Check Details

| # | Check | Latency | Drop Reason |
|---|---|---|---|
| ① | **Bloom filter** | < 1ms | Exact duplicate `(sql_hash, db_id, plan_hash)` seen in last 60s. Local in-memory, no network call. ~0.1% false positive rate acceptable. |
| ② | **Rewrite cache** | 2–5ms | Redis lookup by `(plan_hash, db_id)`. Cache entry must be < 24h old, ACCEPTED/AUTO_SURFACED state, and schema-version-valid. ~60% hit rate. |
| ③ | **Inflight dedup** | 2–3ms | Redis `inflight:(plan_hash, db_id)` key. Parallel duplicate events wait up to 30s for the in-flight result to fan out. TTL 90s — expired key allows fresh invocation. |
| ④ | **Quota check** | 3–5ms | Per-tenant daily budget. Soft limit (80%): warn + proceed. Hard limit: defer to off-peak queue, never drop. |
| ⑤ | **Actionability screen** | 8–15ms | Query type gate (SELECT only), complexity bounds check (min/max joins + subqueries), known-unresolvable pattern check (CONNECT BY, XML functions, external tables). |

> **Design principle:** Every rejected event has a reason code. Nothing is silently discarded — events are served from cache, deferred to off-peak, or routed to an alternative path with an observable reason.

---

## Agent Internals: The ReAct Loop

Each query that clears the pre-filter triggers an independent agent invocation. The agent runs a bounded ReAct loop (Reason → Act → Observe) with a maximum of 5 iterations.

```
Slow query event arrives
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  CONTEXT ASSEMBLY                                         │
│  · EXPLAIN PLAN (shadow read-replica)                     │
│  · Table statistics + stale stats flag                    │
│  · Index inventory + selectivity estimates                │
│  · Last 10 plan changes from AWR (SQL_ID history)         │
│  · Schema DDL for all referenced tables (truncated 2KB)   │
│  · Top-3 nearest neighbours from knowledge base (vector)  │
└───────────────────────────┬───────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────────┐
│  ReAct LOOP  (max 5 iterations)                           │
│                                                           │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                │
│  │ REASON  │───▶│   ACT   │───▶│ OBSERVE │──┐             │
│  │         │    │         │    │         │  │             │
│  │ Score   │    │ Tool    │    │ Parse   │  │ loop if     │
│  │ 14 hypo │    │ calls   │    │ → update│  │ not enough  │
│  │ types   │    │         │    │ evidence│  │ evidence    │
│  └─────────┘    └─────────┘    │ map     │◀─┘             │
│       ▲                        └─────────┘                │
│       └──── 200-token belief summary per iteration ───────│
└───────────────────────────┬───────────────────────────────┘
                            │  confirmed hypothesis
                            ▼
                  REWRITE GENERATION
                  Structured JSON output:
                  sql_rewrite · explanation
                  confidence · rewrite_type · risks
```

### Context Assembly

Before entering the ReAct loop, the orchestrator assembles a rich context packet via synchronous tool calls:

- **`run_explain(sql)`** — EXPLAIN PLAN on shadow read-replica
- **`get_table_stats(table)`** — row counts, last analyzed timestamp, stale flag, partition count
- **`list_indexes(table)`** — index names, columns, uniqueness, selectivity
- **`get_column_stats(table, col)`** — distinct values, null fraction, histogram type, top values
- **`fetch_sql_history(sql_id)`** — last 10 execution plans from AWR with timestamps and runtimes
- **`lookup_pattern(embedding)`** — top-3 nearest neighbours from knowledge base vector index

### The ReAct Loop

**Available tools during the loop:**

| Tool | What it returns |
|---|---|
| `run_explain(sql)` | Full EXPLAIN PLAN: operation types, cost, cardinality, index choices |
| `get_table_stats(table)` | Row count, last analyzed, stale flag, partition count |
| `list_indexes(table)` | Index inventory with column order and selectivity |
| `get_column_stats(table, col)` | Cardinality, histogram type, skew, top values |
| `fetch_sql_history(sql_id)` | AWR plan change history, correlation with runtime regressions |
| `lookup_pattern(embedding)` | Nearest-neighbour rewrites from knowledge base |

**Hypothesis taxonomy (fixed, 14 types):**

```
MISSING_INDEX              WRONG_INDEX_COLUMN_ORDER    FULL_TABLE_SCAN
STALE_STATISTICS           SKEWED_DISTRIBUTION         BAD_JOIN_ORDER
CARTESIAN_JOIN             N_PLUS_ONE_SUBQUERY          IMPLICIT_TYPE_CONVERSION
MISSING_PARTITION_PRUNING  BIND_VARIABLE_PEEKING        NESTED_LOOP_ON_LARGE_TABLE
MISSING_PARALLEL_HINT      ROWNUM_ANTIPATTERN
```

Fixing the taxonomy constrains the LLM's reasoning to actionable failure modes, enables per-category eval metrics, and allows targeted few-shot examples per type in the knowledge base.

### Cumulative Evidence Map

The evidence map is the agent's belief state — a compressed representation that the orchestrator reads at the start of each Reason step instead of raw tool output. This keeps context window usage to ~200 tokens per iteration regardless of tool output size.

```json
{
  "query_id": "triage-8f3a2c",
  "sql_fingerprint": "a3f9b2c1",
  "iteration": 3,
  "hypotheses": {
    "H1": {
      "type": "MISSING_INDEX",
      "target": { "table": "ORDERS", "column": "STATUS" },
      "confidence": 0.82,
      "status": "ACTIVE",
      "evidence_for": ["OBS-1", "OBS-3"],
      "evidence_against": [],
      "rewrite_candidate": "CREATE INDEX idx_orders_status ON ORDERS(STATUS)"
    },
    "H2": {
      "type": "STALE_STATISTICS",
      "target": { "table": "ORDERS" },
      "confidence": 0.31,
      "status": "WEAKENING",
      "evidence_for": ["OBS-2"],
      "evidence_against": ["OBS-3"],
      "rewrite_candidate": null
    }
  },
  "established_facts": {
    "table_stats": {
      "ORDERS": {
        "row_count": 4200000,
        "last_analyzed": "2026-04-01T08:00:00Z",
        "stale": true,
        "stale_age_days": 34,
        "partition_count": 12
      }
    },
    "indexes": {
      "ORDERS": [
        { "name": "IDX_ORDERS_CUSTOMER_ID", "columns": ["CUSTOMER_ID"], "selectivity": 0.94 }
      ]
    },
    "explain_plan": {
      "dominant_operation": "TABLE ACCESS FULL",
      "estimated_cost": 48200,
      "estimated_rows": 756000,
      "indexes_considered": [],
      "indexes_chosen": []
    }
  },
  "tool_trace": [
    {
      "obs_id": "OBS-1",
      "iteration": 1,
      "tool": "run_explain",
      "latency_ms": 340,
      "parsed_delta": {
        "new_facts": ["explain_plan"],
        "hypothesis_updates": {
          "H1": { "confidence_before": 0.50, "confidence_after": 0.71,
                  "reason": "full scan on ORDERS, no index considered for STATUS column" }
        }
      }
    }
  ],
  "loop_control": {
    "iterations_used": 3,
    "iterations_budget": 5,
    "confirmed_hypotheses": ["H1"],
    "recommended_action": "PROCEED_TO_REWRITE",
    "recommended_action_reason": "H1 confirmed at 0.82, diminishing returns on further tool calls"
  }
}
```

**Key design decisions:**
- `established_facts` is **write-once** per invocation — prevents data dictionary inconsistency from confusing the agent mid-loop
- `hypothesis_updates` track confidence deltas per observation — the agent reads these summaries, not raw tool output
- `loop_control.recommended_action` can be `CALL_TOOL | PROCEED_TO_REWRITE | ESCALATE_TO_HUMAN | ABANDON`
- Orchestrator forces `PROCEED_TO_REWRITE` at iteration 5 regardless of recommendation
- Orchestrator forces `ESCALATE_TO_HUMAN` if top hypothesis confidence < 0.50 at loop exit

### Rewrite Generation

The rewrite agent receives the full accumulated context and a structured prompt specifying:
- **Output schema:** JSON with `sql_rewrite`, `explanation`, `confidence` (0–1), `rewrite_type`, `risks`
- **Constraints:** preserve exact semantics; only reference tables/columns in provided DDL; no DDL or DML
- **Few-shot examples:** 3–5 examples of the same `rewrite_type` drawn from the knowledge base

---

## Guardrail & Validation Layer

Every candidate rewrite goes through four independent synchronous checks before being eligible for surfacing. All four must pass. Passing three does not compensate for failing the fourth.

```
Candidate rewrite
       │
       ├──▶ [1] Semantic Equivalence  ──── FAIL ──▶ investigate (artefact?) ──▶ HARD FAIL
       │                                                          │
       │                                                    ARTEFACT ──▶ confidence –5pts, continue
       ├──▶ [2] Schema Binding  ──────────────────────────── FAIL ──▶ HARD FAIL
       │
       ├──▶ [3] Plan Regression  ──────────────────────────── FAIL ──▶ HARD FAIL
       │
       └──▶ [4] Mutation Safety  ──────────────────────────── FAIL ──▶ UNCONDITIONAL HARD BLOCK
                                                                        (no override, no queue)
                          │
                   ALL PASS
                          │
                          ▼
              Composite Confidence Score
                          │
              ┌───────────┼───────────┐
           ≥0.85      0.65–0.84     <0.65
              │           │           │
         Auto-surface  Labelled   Human queue
```

### Check 1 — Semantic Equivalence

Execute both the original and candidate rewrite against a shadow read-replica on up to 10,000 rows. Structural diff: column count, column types, row count, 100-row sample.

**False fail handling:** When a discrepancy appears in an *aggregated* column at small magnitude, the guardrail service recognises this as a potential NLS/floating-point artefact pattern. It runs a secondary check: 50,000 rows with explicit precision normalisation (`CAST(col AS NUMBER(20,4))`). If the discrepancy disappears, it is logged as `PRECISION_ARTEFACT_FALSE_FAIL`, a −5pt confidence penalty is applied, and the check is marked **PASSED WITH WARNING**.

> **Limitation:** Sample equivalence does not guarantee full-dataset equivalence for non-deterministic queries (ROWNUM, ORDER BY on non-unique columns). These are flagged with a warning, not a hard block.

### Check 2 — Schema Binding

Every table, column, index, and function referenced in the candidate rewrite is validated against the **live** data dictionary (`ALL_TABLES`, `ALL_COLUMNS`, `ALL_INDEXES`) at validation time — not at context assembly time. The distinction matters: schema migrations happen continuously, and there is a 5–30s window between context assembly and validation during which an index could be dropped.

### Check 3 — Plan Regression

EXPLAIN PLAN is run on the candidate rewrite. The estimated cost must not exceed the original query's cost by more than **15%** (configurable per tenant). This guards against rewrites that are semantically correct and schema-valid but trigger worse execution paths due to data distribution differences the LLM could not anticipate.

### Check 4 — Mutation Safety

The candidate SQL text is parsed with a SQL grammar checker. Any DDL (`CREATE`, `DROP`, `ALTER`, `TRUNCATE`) or DML (`INSERT`, `UPDATE`, `DELETE`, `MERGE`) triggers an **unconditional hard block**:

- Rewrite is discarded immediately
- Incident is logged to the audit trail
- LLM output is flagged for prompt audit
- **Cannot be overridden by any confidence score or DBA approval**

### Composite Confidence Scoring

```
confidence = (0.35 × llm_self_certainty)
           + (0.30 × semantic_equivalence_score)
           + (0.20 × plan_improvement_margin)
           + (0.15 × knowledge_base_similarity)
```

| Factor | Weight | Description |
|---|---|---|
| LLM self-certainty | 0.35 | LLM-reported confidence in the output JSON |
| Semantic equiv result | 0.30 | 1.0 if full match; partial credit for near-miss with artefact penalty |
| Plan regression margin | 0.20 | Scaled by how much better the new plan cost is vs. original |
| Knowledge base similarity | 0.15 | Cosine similarity to accepted past rewrites of the same type |

### Output Routing

| Confidence | Classification | Action |
|---|---|---|
| ≥ 0.85 | High confidence | Auto-surface: IDE inline / CI diff comment / OCI Console |
| 0.65 – 0.84 | Medium confidence | Surface with explicit confidence label and caveats |
| < 0.65 | Low confidence | Route to human review queue; **not shown to developer** |
| Mutation safety fail | Hard block | Discard; incident log; prompt audit; never queued |

---

## Evaluation & Continuous Improvement

> **Design principle:** Measure quality continuously — not just at release time. Silent degradation in AI systems has no error rate spike; you only see it if you're actively measuring the right things.

### Golden Evaluation Set

A curated dataset of ~2,000 slow query + ideal rewrite pairs, manually labeled by DBAs and SQL experts.

- Covers **12 rewrite categories** with ≥ 100 examples per category
- Updated **quarterly** with new labels from the human review queue
- Used for both offline eval (pre-promotion) and as the baseline for shadow scoring

### Shadow Scoring

New model versions and new prompt versions are deployed in **shadow mode**: they process production traffic and generate candidate rewrites, but these are never surfaced to users. Shadow outputs are scored against the golden set and compared to the current production model.

**Promotion criteria:**
- Rewrite precision ≥ current model precision
- Plan improvement rate ≥ current model rate
- False positive rate ≤ current model rate

Promotion is **automatic** if all three criteria are met — no manual sign-off required.

### Drift Detection & Auto-Rollback

A **24-hour rolling window** monitors three live production metrics:

| Metric | SLO Target | Auto-Rollback Trigger |
|---|---|---|
| Rewrite precision | > 90% | < 85% |
| False positive rate | < 5% | > 8% |
| Plan improvement rate | > 70% | Monitored; alerts only |

On trigger: automatic rollback to previous model version within **60 seconds** (model versions are fetched at worker startup and cached; rollback takes effect on next worker restart). On-call engineer is paged. New model promotion blocked for 48 hours following a rollback.

### Feedback Loop & Fine-tuning

Three signal types are collected:

```
Explicit signal   ──▶  DBA accept/reject in IDE or OCI Console UI
Implicit signal   ──▶  P99 latency delta: 7 days before vs. after applying a rewrite
CI gate blocks    ──▶  Treated as negative labels
                                │
                                ▼
                   DPO preference dataset
                   (accepted rewrite, rejected rewrite) pairs
                                │
                                ▼
                   LoRA fine-tuning  (weekly cadence)
                                │
                                ▼
                   Eval harness gate  (must pass before promotion)
```

### Prompt Versioning

Three prompts are versioned **independently** of model checkpoints in the model registry:

| Prompt | Purpose | Versioning trigger |
|---|---|---|
| **Diagnosis prompt** | ReAct Reason step: score hypotheses | Changes to taxonomy or reasoning structure |
| **Rewrite prompt** | Generate candidate SQL + JSON output | Output schema changes, few-shot updates |
| **Confidence prompt** | Self-report certainty | Calibration adjustments |

Each prompt version record contains: template text with named variable slots, expected output JSON schema, model version it was evaluated against, per-category eval scores, author + changelog, rollback pointer.

**Why independent versioning matters:** When the drift detector fires a regression, you can shadow-test `new_model + old_prompt` vs `old_model + new_prompt`. In practice, ~60% of quality regressions were **prompt regressions**, not model regressions — a subtle instruction change causing output JSON schema drift that downstream parsing handled incorrectly. Without independent versioning, you'd roll back both and never learn which caused the problem.

---

## Knowledge Base

The knowledge base is the agent's long-term memory, structured in three layers:

### Layer 1 — Static Pattern Library (curated, updated quarterly)

Hand-crafted index of known SQL anti-patterns paired with canonical fixes. Each entry contains: anti-pattern signature, rewrite strategy, and 3–5 worked Oracle SQL examples. Covers 12 rewrite categories aligned with the hypothesis taxonomy.

### Layer 2 — Vector-indexed Rewrite History (live, grows continuously)

Every accepted DBA rewrite is embedded using a SQL-tuned encoder model and stored in a vector index (pgvector or Oracle 23ai vector search). At agent runtime, a nearest-neighbour lookup retrieves the top-3 most similar past queries and their accepted rewrites.

- Similarity threshold enforced — below threshold, nothing is retrieved (prevents misleading matches)
- Retrieval results contribute 15% weight to composite confidence score
- New accepted rewrites are indexed asynchronously after DBA approval

### Layer 3 — Schema-aware Metadata (near real-time, 15-minute sync)

Per-monitored-database snapshot of:
- Existing indexes and selectivity estimates
- Stale statistics flags
- Column cardinality distributions for frequently queried columns
- Foreign key relationships (for join order reasoning)

**Retrieval strategy:** Layers 1 and 3 are retrieved deterministically (always included in context assembly). Layer 2 is probabilistic (only included if similarity exceeds threshold).

---

## Developer Integration

### SQL IDE Plugin (VS Code / SQL Developer)

- Surfaces suggestions inline as the developer writes — **amber underline** on detected slow patterns
- Suggestion panel shows: confidence score, explanation, rewrite diff, secondary DBA recommendations
- One-click apply
- Explicit accept/reject feeds directly to the feedback store as training labels

### CI/CD Gate (OCI DevOps / GitHub Actions)

- Hooks into the PR pipeline; classifies all SQL in the diff
- Blocks PRs containing queries matching slow patterns
- Posts the suggested rewrite as a **diff comment** at the exact moment the developer would otherwise merge
- CI gate blocks are treated as negative training labels in the fine-tuning pipeline

### OCI Console DBA Panel

- Real-time dashboard: active alerts, pending rewrites, accept/reject queue
- Trend charts by rewrite category and tenant
- Human review queue: DBA reviews low-confidence rewrites before surfacing
- Primary source of gold labels for the evaluation golden set

---

## Scale & Reliability

### Horizontal Scaling Strategy

| Component | Scaling approach |
|---|---|
| Ingestion plane | Kafka partitioned by `db_id`; consumer groups scale independently on lag |
| Agent orchestrator | Stateless worker pool behind OCI Queue Service; auto-scales on queue depth |
| LLM calls | Separate pools for interactive (IDE/CI) vs. batch traffic with different latency SLAs |
| Guardrail validation | Stateless microservice; horizontally scaled; shadow DB replicas per region |
| Knowledge base | Read replicas for vector similarity; write path async + eventually consistent |

### Fault Tolerance

| Failure scenario | System behaviour |
|---|---|
| LLM unavailable | Circuit breaker + exponential backoff; fallback to rule-based heuristic suggestions labelled "AI unavailable" |
| Shadow DB unavailable | Semantic equivalence check demoted to best-effort; confidence penalised −0.15 pts |
| Guardrail service down | **Hard block all rewrites** — zero output in degraded state; never surface unvalidated rewrites |
| Streaming message loss | At-least-once delivery with idempotency keys; 60s dedup window prevents double-processing |

> **Design principle:** In degraded state, output nothing rather than output unvalidated rewrites. Safety over availability.

---

## Security & Compliance

| Requirement | Implementation |
|---|---|
| Tenant isolation | Every LLM call scoped to single tenant; no cross-tenant context contamination |
| No production SQL execution | Rewrites are suggestions only; shadow DB is a read-only replica |
| Immutable audit log | Every agent decision in OCI Object Storage with WORM policy |
| Data residency | Telemetry processing region-local; no cross-region transfer without explicit tenant opt-in |
| SQL text retention | 30-day maximum; only anonymised plan hashes + rewrite metadata retained after |
| Secret management | API keys in OCI Vault; rotated quarterly; no secrets in code or config |

---

## Worked Example: Partition Pruning Bug

A concrete end-to-end example of a real failure mode the system detected and fixed.

### The Slow Query

```sql
SELECT
    c.customer_name,
    c.email,
    SUM(oi.unit_price * oi.quantity) AS total_spend
FROM
    customers c,
    order_items oi
WHERE
    oi.created_date >= TO_DATE('2026-01-01', 'YYYY-MM-DD')  -- ← the problem
    AND oi.status_code = 3
    AND c.customer_id = oi.customer_id
    AND c.region_id = 42
GROUP BY
    c.customer_name,
    c.email
ORDER BY
    total_spend DESC;
```

**Runtime:** 47 seconds P99. SLA: 3 seconds. Table: `ORDER_ITEMS` — 180M rows, partitioned by `CREATED_MONTH` (12 partitions).

**Root cause:** `TO_DATE()` with a string literal is a runtime expression. The Oracle optimizer cannot evaluate partition boundaries statically at parse time for runtime expressions, so it conservatively scans all 12 partitions instead of the 2 relevant to Q1 2026. EXPLAIN showed full scan, estimated cost 284,000, no partition pruning.

### The Agent's Diagnosis

After 3 ReAct iterations:
- **H1 `MISSING_PARTITION_PRUNING`** → confidence 0.88, CONFIRMED
- **H2 `STALE_STATISTICS`** → confidence 0.31, WEAKENING (contributing factor but not rewritable)

### The Candidate Rewrite

```sql
SELECT
    c.customer_name,
    c.email,
    SUM(oi.unit_price * oi.quantity) AS total_spend
FROM
    customers c,
    order_items oi
WHERE
    oi.created_date >= DATE '2026-01-01'  -- ← ANSI date literal: compile-time constant
    AND oi.status_code = 3
    AND c.customer_id = oi.customer_id
    AND c.region_id = 42
GROUP BY
    c.customer_name,
    c.email
ORDER BY
    total_spend DESC;
```

**Change:** one token. `TO_DATE('2026-01-01', 'YYYY-MM-DD')` → `DATE '2026-01-01'`. The ANSI date literal is a compile-time constant — the optimizer can evaluate partition boundaries statically and prune to 2 of 12 partitions.

### The Guardrail Story

The system's most interesting moment was **not the rewrite — it was a guardrail false fail**.

**Semantic equivalence check — FAILED (first pass).** Shadow DB execution on 10,000 rows showed a 3-row discrepancy in `total_spend`. Normally a hard fail.

**Investigation.** The guardrail service detected the pattern: discrepancy in an aggregated column, small magnitude. This is the signature of an NLS floating-point artefact, not a semantic error. The shadow DB had a slightly different `NLS_NUMERIC_CHARACTERS` session parameter, causing `unit_price * quantity` to accumulate rounding differences in the hash join buffer.

**Secondary check.** Re-ran on 50,000 rows with `CAST(total_spend AS NUMBER(20,4))` on both sides. Zero discrepancy. Logged as `PRECISION_ARTEFACT_FALSE_FAIL`.

**Result:** −5pt confidence penalty applied; check marked **PASSED WITH WARNING**.

**Plan regression check:** EXPLAIN on rewrite — cost dropped from 284,000 to 31,000 (89% improvement). +8pt confidence boost.

**Final confidence score: 0.87.** Auto-surfaced to developer. Post-deployment P99: **2.1 seconds**.

```
47s  ────────────────────────────────────────────────  original
2.1s ██  rewrite
     └── 95% latency reduction from one token change
```

---

## Open Problems

| Topic | Description |
|---|---|
| **Multi-statement rewrite** | Agent currently rewrites single SQL statements. Stored procedures, PL/SQL blocks, and multi-statement batches require a different parsing and context assembly strategy with a much larger context window. |
| **Full-dataset semantic equivalence** | Sample-based equivalence checking (10K rows) does not guarantee correctness for non-deterministic queries. Property-based testing across multiple sample distributions would provide stronger guarantees. |
| **LLM provider portability** | Fine-tuning artifacts are model-specific. A migration strategy is needed if the base model changes — the prompt-model binding in the registry would need to be re-evaluated against the new model family. |
| **Cross-tenant learning** | High-value signal exists in patterns affecting many tenants. Federated or anonymised learning could improve the knowledge base without compromising tenant isolation. |
| **Confounding in implicit feedback** | Post-deployment latency delta has confounding variables (concurrent DBA actions like GATHER_STATS). A proper A/B holdout at the database level would provide cleaner signals but requires database-side infrastructure changes. |

---

## Author

**Debtanu Pal** — Senior Software Engineer, Distributed Systems & AI Platform

- Email: [debtanu97@gmail.com](mailto:debtanu97@gmail.com)
- LinkedIn: [debtanup](https://www.linkedin.com/in/debtanu-pal-98866b126/)
- GitHub: [debtanu](https://github.com/debtanu)

---

*This document describes the high-level system design of the DB Triage AI Agent. Implementation details, internal APIs, and proprietary configurations are not included.*
