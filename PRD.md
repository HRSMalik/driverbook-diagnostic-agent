# Product Requirements Document
# DriverBook Diagnostics Agent

**Version:** 1.2.0
**Date:** 2026-05-14
**Status:** Phase 1 Complete — Phase 2 Planned

---

## 1. Overview

DriverBook Diagnostics is an AI-powered fault code analysis service for commercial fleet management. It ingests raw J1939 SPN and OBD-II DTC records from vehicle telematics data, grounds every diagnosis in a curated knowledge base, and uses a local LLM (Llama 3.1 via Ollama) to enrich unknown fault codes — producing plain-language diagnostics and resolution steps written for fleet managers, not engineers.

The system is self-improving: every new fault code it encounters triggers a single LLM call that saves a full KB entry permanently. All future occurrences of that code are served instantly from the KB with no further LLM calls.

---

## 2. Problem Statement

Fleet managers receive raw fault code data from their vehicles — cryptic strings like `SPN 521133 FMI 13` — with no actionable context. Existing tools either:

- Surface the raw code with no explanation
- Require a trained mechanic to interpret
- Call an AI model on every request, making the system slow and expensive at scale

This creates a bottleneck: actionable decisions (pull the vehicle, schedule maintenance, ignore) are delayed. Unknown codes that appear for the first time fall into a black hole with no tracking.

---

## 3. Goals

| Goal | Description |
| --- | --- |
| Plain-language diagnosis | Convert fault codes into manager-readable explanations of what is wrong, what the impact is, and how urgent it is |
| Resolution guidance | Provide step-by-step fix instructions, who can perform the work, and parts needed |
| Knowledge base grounding | All output is anchored to a curated KB — no hallucinated causes |
| KB-first performance | Known codes return full diagnostics instantly with zero LLM calls |
| Auto-learning | Unknown codes trigger one LLM call per unique code; the result is saved to the KB permanently so the system improves with every run |
| Tenant-scoped fleet view | Fleet operators can view all vehicles under their tenant ID with diagnostics inline |
| Async scan | Source DB scans run in background threads so the dashboard always returns instantly |
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
| --- | --- | --- |
| Fleet Manager | Manages a fleet for one tenant; non-technical | React dashboard — views vehicles and diagnostics by tenant |
| Fleet Maintenance Team | Performs scheduled maintenance | Reads resolution steps and parts lists from diagnostics |
| System Admin / Developer | Manages the service, seeds the KB, monitors unknown codes | API (`/knowledge-base`, `/unknown-faults`), batch scan CLI |

---

## 6. System Architecture

### 6.1 High-Level Components

```
Source MongoDB (remote, read-only)
        │
        │  background scan thread (non-blocking)
        ▼
  datascanpipeline.py
  - scans driverbookv2_stage.driverdiagnostics
  - filters: metaData.dtcRecords.dtcs exists and non-empty
  - stages documents into fault_vehicles (idempotent)
        │
        ▼
  Flow 1: LangGraph DAG (parse → kb_lookup → diagnose → store)
  No LLM — KB lookups only. Returns in milliseconds.
        │
        ▼
  Flow 2: enrich_unknown_codes()
  LLM called once per unique unknown code → KB entry saved permanently
  Affected documents re-diagnosed via Flow 1
        │
        ▼
  Local MongoDB (diagnostics DB)
  - knowledge_base      — seeded + LLM auto-learned definitions
  - fault_vehicles      — staged source documents
  - diagnostics_output  — per-fault results
  - unknown_faults      — review queue
        │
        ▼
  FastAPI (api.py)
  Returns data from local DB instantly — never blocks on scan
        │
        ▼
  React Dashboard (Vite)
  Tenant picker → vehicle table → fault cards with diagnostics
```

### 6.2 LangGraph Pipeline — Flow 1 (4-Node DAG, no LLM)

```
parse_node      Extracts structured fault dicts from raw dtcJson
     │
     ▼
kb_lookup_node  Checks each code against knowledge_base
     │          Known codes: full KB entry attached, is_unknown=False
     │          Unknown codes: flagged is_unknown=True
     ▼
diagnose_node   Known codes: full diagnostic built from KB instantly
     │          Unknown codes: placeholder returned (confidence=0, "explanation pending")
     ▼
store_node      Saves results to diagnostics_output
                Increments occurrence_count for known codes
                Saves unknown codes to unknown_faults queue
```

### 6.3 Flow 2 — LLM Enrichment (runs after Flow 1 for unknowns)

```
1. Collect all unique unknown codes from the Flow 1 batch
2. For each unique code — one LLM call with KB_ENRICH_SYSTEM_PROMPT
3. LLM returns: meaning, system, component, causes, severity, urgency,
   explanation, resolution_steps, who_can_fix, parts_likely_needed, estimated_downtime
4. Save to knowledge_base permanently via auto_learn_from_diagnosis()
5. Re-run Flow 1 for documents that had that unknown code
   → now served from KB with full data, not placeholder
```

After Flow 2 completes, the code is in the KB forever. Every future occurrence is handled by Flow 1 alone with no LLM.

---

## 7. Data Flow

### 7.1 Batch Scan Path (CLI)

```
1. datascanpipeline.py connects to source MongoDB (SOURCE_MONGO_URI)
2. Queries for docs where metaData.dtcRecords.dtcs exists and is non-empty
3. Phase 1: for each matching document:
   a. Extracts structured faults + telemetry → raw_input
   b. stage_fault_document() → upserts into fault_vehicles (idempotent on source_id)
   c. If newly staged or --reanalyze: runs Flow 1 LangGraph
   d. Collects unknown faults from results
4. Phase 2: enrich_unknown_codes() called for all unique unknowns
   a. One LLM call per unique code → KB entry saved
   b. Affected documents re-diagnosed via Flow 1
5. Returns summary: scanned, staged_new, skipped_already_staged, flow1_analyzed, flow2_enriched
```

### 7.2 API / Tenant View Path

```
1. Dashboard calls GET /tenants/{tenant_id}/vehicles
2. API fires background thread: run_data_scan_pipeline(query={"tenantId": tenant_id})
3. API reads fault_vehicles + diagnostics_output for the tenant in 2 indexed queries
4. Returns immediately — does not wait for background scan
5. Response: vehicles with diagnostics inline, sorted by fault_count descending
```

### 7.3 Reanalyze Path

```
1. Dashboard → POST /vehicles/{vehicle_id}/reanalyze
2. API fetches latest staged document from fault_vehicles
3. Re-runs Flow 1 synchronously regardless of cached state
4. Returns fresh diagnostics
```

---

## 8. Functional Requirements

### 8.1 Fault Parsing

| ID | Requirement |
| --- | --- |
| FR-1 | System must parse raw `metaData.dtcRecords.dtcs` nested JSON into a list of structured fault dicts |
| FR-2 | Each fault dict must contain: `code`, `ecu`, `fmi`, `description`, `vehicleId`, `timestamp`, `mil` |
| FR-3 | FMI must be extracted from the description string via regex `FMI\s+(\d+)` |
| FR-4 | Fault codes must be normalized to uppercase with whitespace stripped |

### 8.2 Knowledge Base

| ID | Requirement |
| --- | --- |
| FR-5 | KB must be seeded on first startup from `knowledge_base/seed_kb.json` (50 codes minimum) |
| FR-6 | Lookup must be case-insensitive |
| FR-7 | KB entries must carry: `code`, `system`, `component`, `meaning`, `causes`, `severity`, `urgency`, `explanation`, `resolution_steps`, `who_can_fix`, `parts_likely_needed`, `estimated_downtime`, `source`, `occurrence_count`, `first_seen`, `last_seen` |
| FR-8 | `source` must distinguish: seed (hand-authored) vs auto_learned (from LLM Flow 2) |
| FR-9 | Auto-learned entries must never overwrite seed entries |

### 8.3 Flow 1 — KB-First Diagnosis

| ID | Requirement |
| --- | --- |
| FR-10 | Known codes must return full diagnostics from KB with zero LLM calls |
| FR-11 | Unknown codes must return a placeholder diagnostic with `is_unknown=True` and `confidence=0` |
| FR-12 | All results must be persisted to `diagnostics_output` after every run |
| FR-13 | Known KB codes must have `occurrence_count` incremented on each encounter |

### 8.4 Flow 2 — LLM KB Enrichment

| ID | Requirement |
| --- | --- |
| FR-14 | LLM must be called at most once per unique unknown code across an entire pipeline run |
| FR-15 | LLM output must be saved to `knowledge_base` permanently before re-diagnosis |
| FR-16 | All documents containing the newly enriched code must be re-diagnosed via Flow 1 |
| FR-17 | LLM output must be a single-line raw JSON object matching the KB entry schema |
| FR-18 | LLM failures per code must be isolated — one failure must not abort the enrichment loop |

### 8.5 Auto-Learning

| ID | Requirement |
| --- | --- |
| FR-19 | Unknown codes must be upserted into `unknown_faults` with `status: "unresolved"` on first encounter |
| FR-20 | Subsequent encounters of the same unknown code must increment `occurrence_count` and refresh `last_seen` |
| FR-21 | After LLM enrichment, a full KB entry must be created (`source: "auto_learned"`) |

### 8.6 Persistence

| ID | Requirement |
| --- | --- |
| FR-22 | All source documents containing DTCs must be staged into `fault_vehicles` (idempotent on `source_id`) |
| FR-23 | Staged documents must carry an `analyzed` flag set to `True` after the graph completes |
| FR-24 | Full diagnostic output must be written to `diagnostics_output` after every graph run |

### 8.7 API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness check — confirms process is up |
| GET | `/ready` | Readiness check — confirms MongoDB and Ollama are reachable |
| GET | `/tenants` | Returns all unique tenant IDs from staged fault_vehicles documents |
| GET | `/tenants/{tenant_id}/vehicles` | Returns all vehicles for a tenant with diagnostics inline; fires background scan |
| POST | `/vehicles/{vehicle_id}/reanalyze` | Force re-run Flow 1 on the latest staged document for a vehicle |
| POST | `/scan` | Kick off a full source-collection scan in a background thread |
| GET | `/knowledge-base` | List all KB entries |
| GET | `/unknown-faults` | List unresolved unknown codes sorted by occurrence count descending |

### 8.8 Batch Scan CLI

| ID | Requirement |
| --- | --- |
| FR-25 | CLI must accept `--limit`, `--skip`, `--batch-size`, `--query` (JSON filter), `--reanalyze` flags |
| FR-26 | Must connect to source MongoDB via `SOURCE_MONGO_URI` and app MongoDB via `MONGO_URI` |
| FR-27 | Must print a JSON summary of scanned / staged / flow1_analyzed / flow2_enriched counts |

### 8.9 React Dashboard

| ID | Requirement |
| --- | --- |
| FR-28 | User must be able to enter a Tenant ID and fetch all vehicles with fault codes in one action |
| FR-29 | Known tenant IDs must be displayed as a clickable picker auto-loaded on page open |
| FR-30 | Vehicle table must show fault count, severity breakdown (Critical / High / Medium), and unknown count per vehicle |
| FR-31 | Clicking a vehicle must show per-fault diagnostic cards with severity, urgency, explanation, resolution steps, and who can fix |
| FR-32 | Unknown fault cards must not appear in the vehicle detail view |
| FR-33 | Fleet overview tab must show all vehicles in a sortable table |
| FR-34 | Knowledge Base tab must show all KB entries with search and pagination |
| FR-35 | Per-vehicle reanalyze button must re-run Flow 1 and refresh the displayed diagnostics |

---

## 9. Non-Functional Requirements

| Category | Requirement |
| --- | --- |
| Idempotency | All Mongo writes use upsert with `$setOnInsert` for first-write fields — safe to re-run |
| Isolation | Per-code LLM failures in Flow 2 are captured individually; one failure must not abort the enrichment loop |
| Grounding | LLM output is always anchored to the fault code, ECU, FMI, and raw description — system prompt forbids hallucination |
| Determinism | LLM temperature fixed at 0.0 |
| Token economy | LLM output capped at 512 tokens |
| Performance | Flow 1 returns in milliseconds for any code in KB — no LLM call |
| API latency | Tenant endpoint reads from local DB in 2 indexed queries regardless of how many vehicles the tenant has |
| CORS | API must allow requests from localhost:5173 and localhost:3000 for local development |
| Config | All URIs, DB names, model names via `.env` — no hardcoded values in source |

---

## 10. MongoDB Collections

| Collection | Purpose | Key Index |
| --- | --- | --- |
| `knowledge_base` | Known fault code definitions — seeded + LLM auto-learned | `code` (unique) |
| `fault_vehicles` | Staging — every source document with at least one DTC | `source_id` (unique), `tenantId` |
| `unknown_faults` | Codes not found in KB at time of scan — review queue | `code` (unique) |
| `diagnostics_output` | Per-fault diagnostic results per source document | `source_id`, `vehicleId` |

---

## 11. Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `MONGO_URI` | `mongodb://localhost:27017` | App MongoDB connection |
| `MONGO_DB` | `diagnostics` | App database name |
| `SOURCE_MONGO_URI` | required | Remote source cluster URI (read-only) |
| `SOURCE_MONGO_DB` | `driverbookv2_stage` | Source database name |
| `SOURCE_COLLECTION` | `driverbookv2.driverdiagnostics` | Source collection name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama inference server |
| `OLLAMA_MODEL` | `llama3.1` | Model used for KB enrichment (Flow 2) |

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
  "confidence": 100,
  "explanation": "The engine control unit detected a calibration offset...",
  "resolution_steps": ["Step 1: ...", "Step 2: ..."],
  "who_can_fix": "Certified technician required",
  "parts_likely_needed": []
}
```

---

## 13. Auto-Learning Logic

```
First encounter of a new code (Flow 1):
  1. kb_lookup → miss → is_unknown=True
  2. diagnose_node returns placeholder (confidence=0, "explanation pending")
  3. store_node saves placeholder to diagnostics_output
  4. Code added to all_unknown_faults list

After Flow 1 completes (Flow 2):
  1. Unique unknown codes deduplicated
  2. One LLM call per unique code → full KB entry
  3. auto_learn_from_diagnosis() → saves to knowledge_base (source: auto_learned)
  4. Flow 1 re-runs for documents that had that code
  5. Full KB-backed diagnostic replaces placeholder in diagnostics_output

Second encounter of the same code (any future run):
  1. kb_lookup → hit (auto_learned entry from Flow 2)
  2. diagnose_node returns full diagnostic from KB
  3. No LLM call — served in milliseconds
  4. increment_occurrence() called on KB entry
```

---

## 14. Phase 2 Roadmap

| Item | Description |
| --- | --- |
| API key authentication | Per-tenant API keys with middleware in `api.py` |
| Structured logging | JSON logs in production via `core/logging_config.py` |
| Request tracing | UUID4 `request_id` propagated through LangGraph state and all log lines |
| Pinned dependencies | `requirements.lock` generated by `pip-compile` for reproducible deploys |
| Hardened Dockerfile | Multi-stage build, non-root user, `HEALTHCHECK` directive, `.dockerignore` |
| Test suite | `tests/unit/` (pure logic, `mongomock`) + `tests/integration/` (graph with stubbed LLM) |
| `config/settings.py` | Pydantic `BaseSettings` singleton replacing scattered `os.getenv` calls |
| Scheduled full scan | Cron-based scan across all tenants so data stays current without dashboard activity |
| Multi-fault correlation | Third LLM agent to identify fault clusters on vehicles with multiple simultaneous codes |
| Vehicle history context | Pass occurrence history per code+vehicle into enrichment prompts for better urgency signals |
| Fleet-wide pattern detection | Flag codes hitting multiple vehicles simultaneously as systemic alerts |
| Severity trend tracking | Track severity escalation over consecutive runs per code+vehicle |
| Resolution feedback loop | `PATCH /faults/{code}/resolve` to write real-world outcomes back into KB |
| Confidence-based review queue | Route low-confidence Critical/High diagnostics to a human review queue |

---

## 15. Repository

**GitHub:** https://github.com/HRSMalik/driverbook-diagnostic-agent
**Stack:** Python 3.11 · FastAPI · LangGraph · LangChain · Ollama (llama3.1) · MongoDB · React (Vite) · Docker
