# Product Requirements Document
# DriverBook Diagnostics Agent

**Version:** 1.1.0
**Date:** 2026-05-12
**Status:** Phase 1 Complete — Phase 2 Planned

---

## 1. Overview

DriverBook Diagnostics is an AI-powered fault code analysis service for commercial fleet management. It ingests raw J1939 SPN and OBD-II DTC records from vehicle telematics data, grounds every diagnosis in a curated knowledge base, and uses a local LLM to produce plain-language explanations and step-by-step resolution instructions written for fleet managers — not engineers.

The system is designed to be self-improving: every new fault code it encounters is auto-saved and auto-learned so future diagnoses for the same code have grounding data without manual intervention.

---

## 2. Problem Statement

Fleet managers receive raw fault code data from their vehicles — cryptic strings like `SPN 521133 FMI 13` — with no actionable context. Existing tools either:

- Surface the raw code with no explanation
- Require a trained mechanic to interpret
- Offer generic descriptions with no account for live vehicle conditions

This creates a bottleneck: actionable decisions (pull the vehicle, schedule maintenance, ignore) are delayed while managers wait for a technician to review data. Unknown codes that appear for the first time fall into a black hole with no tracking.

---

## 3. Goals

| Goal | Description |
|---|---|
| Plain-language diagnosis | Convert fault codes into manager-readable explanations of what is wrong, what impact it has, and how urgent it is |
| Resolution guidance | Provide step-by-step fix instructions, who can perform the work, parts needed, and estimated downtime |
| Telemetry-aware severity | Escalate severity automatically when live signals (coolant temp, oil pressure, DEF level) indicate conditions are worse than baseline |
| Knowledge base grounding | All LLM output is anchored to a curated KB — no hallucinated causes |
| Auto-learning | Unknown codes are captured, tracked, and promoted to the KB from LLM output so the system improves with every run |
| Tenant-scoped fleet view | Fleet operators can view all vehicles under their tenant ID with diagnostics inline |
| Batch scan | A CLI pipeline can sweep the entire source MongoDB collection and stage + analyze every DTC-bearing document |

---

## 4. Non-Goals (Phase 1)

- Real-time vehicle streaming (push/websocket)
- Multi-LLM provider support (Ollama only in Phase 1)
- Repair shop dispatch integration
- Mobile app or native UI
- Per-user authentication and role-based access (deferred to Phase 2)

---

## 5. Users

| User | Description | Primary Interaction |
|---|---|---|
| **Fleet Manager** | Manages a fleet for one tenant; non-technical | Streamlit UI — views vehicles and diagnostics by tenant |
| **Fleet Maintenance Team** | Performs scheduled maintenance | Reads resolution steps and parts lists from diagnostics |
| **System Admin / Developer** | Manages the service, seeds the KB, monitors unknown codes | API (`/knowledge-base`, `/unknown-faults`), batch scan CLI |

---

## 6. System Architecture

### 6.1 High-Level Components

```
Source MongoDB                  DriverBook Diagnostics Service          Fleet Manager
(driverbookv2_ai)                                                        (Browser)
        │                                                                    │
        │  batch scan / vehicle lookup                                       │
        ├──────────────────────────────────────────────────────────────────> │
        │                                                                    │
        │          ┌─────────────────────────────────────┐                  │
        │          │  FastAPI (api.py)                   │                  │
        │          │  ┌─────────────────────────────┐    │ <── Streamlit ───┤
        │          │  │  LangGraph Pipeline (DAG)   │    │    (tenant UI)   │
        │          │  │  parse → kb_lookup →        │    │                  │
        │          │  │  telemetry → llm →          │    │                  │
        │          │  │  explain → store            │    │                  │
        │          │  └──────────┬──────────────────┘    │                  │
        │          │             │                       │                  │
        │          │    Ollama (llama3.1)                │                  │
        │          │    Local LLM inference              │                  │
        │          └──────────┬──────────────────────────┘                  │
        │                     │                                              │
        │              Diagnostics MongoDB                                   │
        │              (diagnostics DB)                                      │
        │              - knowledge_base                                      │
        │              - fault_vehicles                                      │
        │              - unknown_faults                                      │
        │              - diagnostics_output                                  │
```

### 6.2 LangGraph Pipeline (6-Node DAG)

```
POST /tenants/{tenant_id}/vehicles  OR  python -m core.datascanpipeline
          │
          ▼
     parse_node              Extracts structured fault dicts from raw dtcJson
          │
          ▼
    kb_lookup_node           Looks up each code in MongoDB knowledge_base
          │                  Marks is_unknown=True for misses
          │                  Marks skip_llm=True for full KB hits (short-circuit)
          ▼
    telemetry_node           Builds telemetry snapshot from 6 vehicle signals
          │                  Applies severity escalation rules
          ▼
      llm_node               DIAGNOSTIC AGENT
          │                  - KB hit → short-circuit, build result from KB
          │                  - KB miss / thin entry → call Ollama (llama3.1)
          │                  - Output: purpose, issue, impact, severity, urgency, confidence
          ▼
    explain_node             EXPLAINABILITY AGENT
          │                  - KB hit → pull explanation fields from KB
          │                  - KB miss → second Ollama call
          │                  - Output: explanation, resolution_steps, who_can_fix,
          │                            parts_likely_needed, estimated_downtime
          ▼
     store_node              For unknown codes:
                               → save_unknown_fault (occurrence tracking)
                               → auto_learn_from_diagnosis (write LLM output to KB)
                             For known codes:
                               → increment_occurrence
                             All diagnostics → diagnostics_output collection
```

---

## 7. Data Flow

### 7.1 Batch Scan Path (CLI)

```
1. datascanpipeline.py connects to source MongoDB (SOURCE_MONGO_URI)
2. Queries driverbookv2.driverdiagnostics for docs where metaData.dtcRecords.dtcs exists and is non-empty
3. For each matching document:
   a. Extracts structured faults + telemetry → raw_input
   b. Calls stage_fault_document() → upserts into fault_vehicles (idempotent on source_id)
   c. If newly staged (or --reanalyze flag): invokes LangGraph pipeline
   d. Marks document analyzed=True in fault_vehicles
4. Returns summary: scanned, staged_new, skipped_already_staged, analyzed
```

### 7.2 API / Tenant View Path

```
1. Streamlit sends GET /tenants/{tenant_id}/vehicles
2. API aggregates fault_vehicles by vehicleId for the tenant (latest doc per vehicle)
3. For each vehicle:
   a. If diagnostics_output has rows for the latest source_id → return cached
   b. If not yet analyzed → run graph on the spot, return fresh result
4. Response: list of vehicles with diagnostics inline
```

### 7.3 Reanalyze Path

```
1. Streamlit admin toggle → POST /vehicles/{vehicle_id}/reanalyze
2. API fetches latest staged document from fault_vehicles
3. Re-runs LangGraph regardless of cached state
4. Returns fresh diagnostics
```

---

## 8. Functional Requirements

### 8.1 Fault Parsing

| ID | Requirement |
|---|---|
| FR-1 | System must parse raw `metaData.dtcRecords.dtcs` nested JSON into a list of structured fault dicts |
| FR-2 | Each fault dict must contain: `code`, `ecu`, `fmi`, `description`, `vehicleId`, `timestamp`, `mil` |
| FR-3 | FMI must be extracted from the description string via regex `FMI\s+(\d+)` |
| FR-4 | Fault codes must be normalized to uppercase with whitespace stripped |

### 8.2 Knowledge Base

| ID | Requirement |
|---|---|
| FR-5 | KB must be seeded on first startup from `knowledge_base/seed_kb.json` (50 codes minimum) |
| FR-6 | Lookup must be case-insensitive |
| FR-7 | KB entries must carry: `code`, `system`, `component`, `meaning`, `causes`, `severity`, `urgency`, `source`, `occurrence_count`, `first_seen`, `last_seen` |
| FR-8 | `source` must distinguish: `seed` (hand-authored), `auto_learned` (from LLM output), `extracted_from_doc` (cheap-extract, no LLM yet) |
| FR-9 | Auto-learned entries must never overwrite seed entries |

### 8.3 Telemetry Severity Escalation

| ID | Requirement | Rule |
|---|---|---|
| FR-10 | Coolant temp escalation | If `engineCoolantTemperature > 105°C` AND fault ECU contains "engine" → escalate severity one level |
| FR-11 | Oil pressure escalation | If `engineOilPressure < 20 PSI` AND fault ECU contains "engine" → force severity to "Critical" |
| FR-12 | DEF level escalation | If `defLevel < 5%` AND fault code relates to DEF/emissions → escalate severity one level |
| FR-13 | Sentinel filtering | Values equal to `-6.128e18` must be treated as missing/null and excluded from snapshot |

### 8.4 LLM Diagnostic Agent

| ID | Requirement |
|---|---|
| FR-14 | Must output: `purpose`, `issue`, `impact`, `severity` (Low/Medium/High/Critical), `urgency` (Ignore/Monitor/Schedule Maintenance/Immediate Action), `confidence` (0–100) |
| FR-15 | Output must be a single-line raw JSON object — no markdown, no code blocks |
| FR-16 | Severity from LLM must be overridden by telemetry-adjusted severity when available |
| FR-17 | KB hits with full data must short-circuit the LLM call (no Ollama invocation) |

### 8.5 LLM Explainability Agent

| ID | Requirement |
|---|---|
| FR-18 | Must output: `explanation`, `resolution_steps` (ordered list), `who_can_fix` (Driver only / Fleet maintenance team / Certified technician required), `parts_likely_needed`, `estimated_downtime` |
| FR-19 | Output must be a single-line raw JSON object |
| FR-20 | KB hits must reuse stored KB explanation fields and short-circuit the LLM call |

### 8.6 Auto-Learning

| ID | Requirement |
|---|---|
| FR-21 | Unknown codes must be upserted into `unknown_faults` with `status: "unresolved"` on first encounter |
| FR-22 | Subsequent encounters of the same unknown code must increment `occurrence_count` and refresh `last_seen` |
| FR-23 | After LLM diagnosis of an unknown code, a KB entry must be created from the LLM output (`source: "auto_learned"`) |
| FR-24 | Existing `extracted_from_doc` KB rows must be upgraded in-place when LLM output is available |

### 8.7 Persistence

| ID | Requirement |
|---|---|
| FR-25 | All source documents containing DTCs must be staged into `fault_vehicles` (idempotent on `source_id`) |
| FR-26 | Staged documents must carry an `analyzed` flag flipped to `True` after the graph completes |
| FR-27 | Full diagnostic output must be written to `diagnostics_output` after every graph run |
| FR-28 | Known KB codes must have `occurrence_count` incremented on each encounter |

### 8.8 API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/tenants/{tenant_id}/vehicles` | Returns all vehicles for a tenant with diagnostics inline; runs graph for any unanalyzed vehicles |
| POST | `/vehicles/{vehicle_id}/reanalyze` | Force re-run the graph on the latest staged document for a vehicle |
| GET | `/knowledge-base` | List all KB entries (admin) |
| GET | `/unknown-faults` | List unresolved unknown codes sorted by occurrence count descending |

### 8.9 Batch Scan CLI

| ID | Requirement |
|---|---|
| FR-29 | CLI must accept `--limit`, `--skip`, `--batch-size`, `--query` (JSON filter), `--reanalyze` flags |
| FR-30 | Must connect to source MongoDB via `SOURCE_MONGO_URI` and app MongoDB via `MONGO_URI` |
| FR-31 | Must print a JSON summary of scanned / staged / analyzed counts on completion |

### 8.10 Streamlit Frontend

| ID | Requirement |
|---|---|
| FR-32 | User must be able to enter a Tenant ID and fetch all vehicles + diagnostics in one action |
| FR-33 | Each vehicle must be expandable to show all diagnostic fields per fault code |
| FR-34 | Admin toggle must expose a per-vehicle reanalyze button |
| FR-35 | API URL must be configurable via `API_URL` environment variable |

---

## 9. Non-Functional Requirements

| Category | Requirement |
|---|---|
| **Idempotency** | All Mongo writes use upsert with `$setOnInsert` for first-write fields — safe to re-run |
| **Isolation** | Per-fault LLM failures are captured per record; one bad fault must not abort the pipeline |
| **Grounding** | LLM output must always be anchored to KB or raw fault data — system prompt explicitly forbids hallucination |
| **Determinism** | LLM temperature is fixed at 0.0 for both agents |
| **Token economy** | LLM output capped at 512 tokens (`num_predict=512`) |
| **Batch efficiency** | KB hits short-circuit LLM calls entirely — only genuine unknowns or thin entries invoke Ollama |
| **Config** | All URIs, DB names, model names via `.env` — no hardcoded values in source |

---

## 10. MongoDB Collections

| Collection | Purpose | Key Index |
|---|---|---|
| `knowledge_base` | Known fault code definitions — seeded + auto-learned | `code` (unique) |
| `fault_vehicles` | Staging — every source document with at least one DTC | `source_id` (unique) |
| `unknown_faults` | Codes not found in KB; review queue | `code` (unique) |
| `diagnostics_output` | Full LLM diagnostic + explanation per request | `vehicleId + timestamp` |

---

## 11. Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | App MongoDB (diagnostics DB) |
| `MONGO_DB` | `diagnostics` | App database name |
| `SOURCE_MONGO_URI` | _(required for batch scan)_ | Source fleet database URI |
| `SOURCE_MONGO_DB` | `driverbookv2_ai` | Source database name |
| `SOURCE_COLLECTION` | `driverbookv2.driverdiagnostics` | Source collection name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama inference server |
| `OLLAMA_MODEL` | `llama3.1` | Model used for both agents |
| `API_URL` | `http://localhost:8000` | Streamlit → API base URL |

---

## 12. Diagnostic Output Schema

Each fault in the `diagnostics` array carries:

```json
{
  "code": "SPN 521133",
  "ecu": "Engine #2",
  "fmi": 13,
  "vehicleId": "...",
  "timestamp": "ISO_DATE",
  "is_unknown": false,
  "from_kb": true,
  "purpose": "Controls fuel injection timing and calibration",
  "issue": "Engine calibration mismatch detected",
  "impact": "Reduced fuel efficiency and potential engine damage if ignored",
  "severity": "Medium",
  "urgency": "Schedule Maintenance",
  "confidence": 82,
  "explanation": "The engine control unit detected a calibration offset...",
  "resolution_steps": ["Step 1: ...", "Step 2: ..."],
  "who_can_fix": "Certified technician required",
  "parts_likely_needed": [],
  "estimated_downtime": "2–4 hours"
}
```

---

## 13. Auto-Learning Logic

```
First encounter of a new code:
  1. kb_lookup → miss → is_unknown = True
  2. LLM diagnoses it (no KB grounding — uses raw fault + telemetry only)
  3. store_node:
       a. save_unknown_fault()     → unknown_faults (status: unresolved)
       b. auto_learn_from_diagnosis() → knowledge_base (source: auto_learned, $setOnInsert)

Second encounter of the same code:
  1. kb_lookup → hit (auto_learned entry from step 3b)
  2. skip_llm = True (unless entry is extracted_from_doc)
  3. Diagnostic built directly from KB — no Ollama call
  4. increment_occurrence() called on KB entry
```

---

## 14. Phase 2 Roadmap

| Item | Description |
|---|---|
| API key authentication | Per-tenant API keys with middleware in `api.py` |
| `/health` and `/ready` endpoints | Process liveness + Mongo/Ollama reachability checks |
| Structured logging | JSON logs in production via `core/logging_config.py` |
| Request tracing | UUID4 `request_id` propagated through LangGraph state and all log lines |
| Pinned dependencies | `requirements.lock` generated by `pip-compile` for reproducible deploys |
| Hardened Dockerfile | Multi-stage build, non-root user, `HEALTHCHECK` directive, `.dockerignore` |
| Test suite | `tests/unit/` (pure logic, `mongomock`) + `tests/integration/` (graph with stubbed LLM) |
| `config/settings.py` | Pydantic `BaseSettings` singleton replacing scattered `os.getenv` calls |
| `diagnostics_output.py` | Extract Mongo writes from `store_node` into `db/diagnostics_output.py` |
| `llm/parsers.py` | Extract JSON normalization from `llm_node` and `explain_node` (currently duplicated) |
| Mongo indexes | Add compound index on `diagnostics_output(vehicleId, timestamp)` |
| Schema versioning | `schema_version` field on all persisted documents |
| Prometheus metrics | KB hit rate, LLM latency, unknown fault rate emitted as metrics |
| Streamlit form | Real DTC input form replacing hardcoded payload in frontend |

---

## 15. Repository

**GitHub:** https://github.com/HRSMalik/driverbook-diagnostic-agent
**Stack:** Python 3.11 · FastAPI · LangGraph · LangChain · Ollama (llama3.1) · MongoDB · Streamlit · Docker
