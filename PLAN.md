# PLAN.md — DriverBook Diagnostics Agent

## Status
This document defines the current pipeline architecture and workflow contract. CLAUDE.md holds coding conventions; README.md holds setup and API reference; this file holds the system design every implementation must satisfy.

---

## System Summary

- **Purpose:** Analyze J1939 SPN / OBD-II DTC fault codes for commercial fleets and return plain-language diagnostics and resolution steps for fleet managers.
- **Stack:** Python 3.11+, FastAPI, MongoDB (PyMongo), LangChain + LangGraph, Ollama (local Llama 3.1), React (Vite), Docker.
- **LLM pattern:** KB-first. LLM is called only once per unique unknown fault code. Result is saved to the knowledge base permanently. All future occurrences are served from KB with no LLM.
- **Entry paths:**
  - **Dashboard query:** `GET /tenants/{tenant_id}/vehicles` — fires a background scan for that tenant, returns data from local DB instantly.
  - **Batch scan CLI:** `python -m core.datascanpipeline` — scans the entire source collection or a filtered subset.
  - **Force reanalyze:** `POST /vehicles/{vehicle_id}/reanalyze` — re-runs Flow 1 synchronously for a specific vehicle.

---

## Pipeline Architecture

### Flow 1 — Fast KB Lookup (always runs, no LLM)

```
parse → kb_lookup → diagnose → store
```

| Node | What it does |
| --- | --- |
| parse | Reads raw dtcJson from source document, builds structured fault list (code, ECU, FMI, description, vehicleId, MIL) |
| kb_lookup | Checks each fault code against knowledge_base collection. Known codes get full KB entry attached. Unknown codes flagged is_unknown=True |
| diagnose | Known codes get full diagnostic instantly from KB (severity, urgency, explanation, resolution steps). Unknown codes get placeholder (confidence=0, "explanation pending") |
| store | Saves results to diagnostics_output. Increments occurrence_count for known codes. Saves unknown codes to unknown_faults queue |

No LLM calls. Returns in milliseconds for any code in KB.

### Flow 2 — LLM Enrichment (runs after Flow 1, only if unknowns exist)

```
collect unique unknown codes → LLM once per code → save to KB → re-run Flow 1
```

| Step | What it does |
| --- | --- |
| Collect | All unique unknown codes from the current Flow 1 batch are gathered |
| LLM call | One call per unique code with KB_ENRICH_SYSTEM_PROMPT — returns full KB entry |
| Save | auto_learn_from_diagnosis() upserts the KB entry permanently. Never overwrites seeded or previously curated entries |
| Re-diagnose | Any document that had that unknown code is re-run through Flow 1, now returning full KB data instead of a placeholder |

After Flow 2, the code is in the KB forever. Every future occurrence is handled by Flow 1 alone.

### KB Lifecycle

| Situation | What happens |
| --- | --- |
| Code in KB (seeded or LLM-learned) | Flow 1 serves full diagnostic instantly, no LLM |
| New unknown code | Flow 1 returns placeholder; Flow 2 calls LLM once, saves to KB, re-diagnoses |
| Same unknown code seen again | Already in KB — Flow 1 instant |

---

## API Request Lifecycle

```
GET /tenants/{tenant_id}/vehicles
        │
        ├── background thread fires:
        │       run_data_scan_pipeline(query={"tenantId": tenant_id})
        │           ├── scan source MongoDB for new DTC documents
        │           ├── stage new documents in fault_vehicles
        │           ├── Flow 1: parse → kb_lookup → diagnose → store
        │           └── Flow 2: LLM for unknowns → KB → re-diagnose
        │
        └── reads fault_vehicles + diagnostics_output (2 queries, indexed)
            returns instantly
```

First request: returns existing staged data. Background scan enriches any new documents. Next request: full data available.

---

## Collections (App DB: `diagnostics`)

| Collection | Purpose | Key index |
| --- | --- | --- |
| fault_vehicles | Staged source documents containing at least one DTC | source_id (unique), tenantId |
| knowledge_base | Canonical fault code definitions — seeded + LLM auto-learned | code (unique) |
| unknown_faults | Codes not in KB at time of scan — review queue | code (unique) |
| diagnostics_output | Per-fault diagnostic results keyed by source_id | source_id, vehicleId |

---

## Module Map

| File | Responsibility |
| --- | --- |
| api.py | FastAPI endpoints only — no business logic |
| core/dtc_parser.py | Raw dtcJson → structured fault dicts |
| core/knowledge_base.py | seed_knowledge_base, lookup, increment_occurrence, auto_learn_from_diagnosis |
| core/datascanpipeline.py | Two-phase batch orchestrator (Flow 1 + Flow 2) + CLI entry point |
| core/telemetry_context.py | Telemetry snapshot + severity escalation rules |
| db/connection.py | Cached MongoClient per URI |
| db/fault_vehicles.py | stage_fault_document (upsert), mark_analyzed, ensure_fault_vehicles_collection |
| db/unknown_faults.py | save_unknown_fault upsert |
| db/diagnostics_output.py | save_diagnostics writes |
| llm/hf_client.py | ChatOllama factory (Llama 3.1, temperature=0.0) |
| llm/prompts.py | All prompt templates including KB_ENRICH_SYSTEM_PROMPT |
| llm/parsers.py | JSON extraction from LLM responses |
| orchestration/diagnostic_graph.py | build_graph() for Flow 1 + enrich_unknown_codes() for Flow 2 |
| frontend/react_app/ | React dashboard (Vite) — tenant picker, vehicle table, fault cards, KB browser |

---

## Idempotency Rules

- **fault_vehicles:** keyed on `source_id`. Upsert via `$setOnInsert` — re-running the scan on the same source data is a no-op.
- **knowledge_base:** keyed on `code` (unique index). `auto_learn_from_diagnosis` uses `$setOnInsert` only — never overwrites seeded or previously learned entries.
- **unknown_faults:** keyed on `code` (unique index). Occurrence count increments on each encounter.
- **diagnostics_output:** existing rows for a `source_id` are replaced on re-analysis, not duplicated.

---

## LangGraph State Contract

```python
class DiagnosticState(TypedDict):
    raw_input: dict       # {vehicleId, dtcJson, telemetry, source_id}
    parsed_faults: list   # structured faults, annotated as they flow through nodes
    diagnostics: list     # final per-fault diagnostic results
    unknown_codes: list   # codes not found in KB after kb_lookup
```

Every node must return `{**state, <updated keys>}`. Never return a partial state.

---

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| MONGO_URI | mongodb://localhost:27017 | App database connection |
| MONGO_DB | diagnostics | App database name |
| SOURCE_MONGO_URI | — | Remote source cluster (read-only) |
| SOURCE_MONGO_DB | driverbookv2_stage | Source database name |
| SOURCE_COLLECTION | driverbookv2.driverdiagnostics | Source collection |
| OLLAMA_BASE_URL | http://localhost:11434 | Ollama server |
| OLLAMA_MODEL | llama3.1 | Model for KB enrichment |

---

## Entry Point Commands

```bash
# API
conda run -n driverbook uvicorn api:app --port 8000

# Frontend
cd frontend/react_app && npm run dev

# Full collection batch scan (one-time or scheduled)
conda run -n driverbook python -m core.datascanpipeline

# Scan with limit
conda run -n driverbook python -m core.datascanpipeline --limit 1000

# Force re-analyze all already-staged documents
conda run -n driverbook python -m core.datascanpipeline --reanalyze

# Docker
docker build -t driverbook-diagnostics .
docker run -p 8000:8000 --env-file .env driverbook-diagnostics
```

---

## Current State (as of 2026-05-14)

| Metric | Value |
| --- | --- |
| Tenants | 20 |
| Documents staged | 154 |
| Documents analyzed | 154 |
| KB entries (seeded) | 50 |
| KB entries (LLM learned) | 44 |
| LLM calls on next request | 0 — all known codes are in KB |

---

## Backlog — System Improvements (from audit 2026-05-14)

### High Priority

**B-1 — Silent LLM failures in Flow 2**
`enrich_unknown_codes()` swallows all LLM exceptions with bare `except: pass`. Failed enrichments are invisible — no log, no retry, no record. Add per-code logging, track failed codes, and optionally retry once.

**B-2 — LLM call timeout**
`llm.invoke()` has no timeout. A stuck Ollama process blocks the entire Flow 2 enrichment indefinitely. Add a timeout via `ChatOllama(timeout=60)` or wrap with `signal`/`concurrent.futures`.

**B-3 — Background scan errors are invisible**
The background thread in `list_tenant_vehicles` logs warnings but the caller never learns if the scan failed. Add a scan status record in MongoDB so the dashboard can reflect scan state.

**B-4 — Import-time side effects in `api.py`**
KB seed, graph build, and collection setup run at import time. Any failure on startup crashes the whole process with no graceful degradation. Move into a FastAPI `lifespan` handler with proper error handling.

### Medium Priority

**B-5 — Dashboard hides vehicles with only unknown codes**
Vehicles where all faults are `is_unknown` are filtered out entirely — fleet managers never see them. Should show them with an "analysis pending" badge instead of hiding them.

**B-6 — Fake API health indicator in sidebar**
"API Connected" is hardcoded — it never actually checks the API. Should ping `/health` on load and reflect real status.

**B-7 — No pagination on tenant endpoint**
All diagnostics for all vehicles are loaded into memory per request. Fine at 154 docs today, will degrade at scale. Add `skip`/`limit` params and paginate the vehicle list.

**B-8 — No client-side Tenant ID validation**
User can type any string — validation only fails after the API call returns an error. Should check for valid 24-char hex ObjectId format before making the request.

### Lower Priority

**B-9 — Centralise config in `config/settings.py`**
`os.getenv` calls are scattered across `api.py`, `datascanpipeline.py`, and `hf_client.py`. A single `Settings` class validates required vars on startup and makes config auditable.

**B-10 — Request tracing**
No `request_id` propagated through API calls or background scans. Hard to correlate logs when something fails. Add UUID4 `request_id` at the API boundary, pass through LangGraph state.

**B-11 — Centralise LLM JSON parsing**
The regex extraction pattern is duplicated across multiple nodes. Should live only in `llm/parsers.py` with a single tested implementation.

**B-12 — Harden Dockerfile**
No multi-stage build, no non-root user, no `HEALTHCHECK`, no `.dockerignore`. Required before any non-local deployment.

---

## Future Improvements

### F-1 — Multi-Fault Correlation
Each fault is diagnosed in isolation by Flow 1. When a vehicle has multiple simultaneous fault codes, a new Flow 3 step should run after Flow 1 completes — sending the full set of KB-resolved diagnostics for that vehicle to the LLM in one call to identify clusters and cross-fault implications (e.g. coolant sensor + engine overheat together imply overheating, not two separate issues).

**Implementation:** Add `correlate_faults(db, vehicle_id, diagnostics)` in `core/` that calls the LLM with all fault diagnostics for a vehicle. Run it in `run_data_scan_pipeline` after Flow 1, only for vehicles with more than one fault. Save result as `correlation_summary` on the `diagnostics_output` document. Surface in the dashboard vehicle detail view.

---

### F-2 — Vehicle History Context in KB Enrichment
Flow 2 enriches unknown codes without knowing how often that code has appeared on a specific vehicle. Recurring faults warrant higher urgency regardless of raw severity.

**Implementation:** In `enrich_unknown_codes()`, before calling the LLM for each code, query `diagnostics_output` for prior occurrences of that `code + vehicleId`. Pass `occurrence_history: {count, first_seen, last_seen}` into `KB_ENRICH_HUMAN_PROMPT`. Update the prompt template to use this as an urgency escalation signal.

---

### F-3 — Fleet-Wide Pattern Detection
The same fault code hitting multiple vehicles in a fleet simultaneously signals a systemic issue (bad parts batch, firmware, route conditions) that per-vehicle Flow 1 analysis cannot surface.

**Implementation:** Add `GET /tenants/{tenant_id}/patterns` endpoint. Aggregate `diagnostics_output` by `code` across all vehicles for the tenant within a configurable time window (default 7 days). Return codes that appear on 3 or more vehicles flagged as `fleet_alert: true`. No pipeline changes needed — pure read aggregation on existing data.

---

### F-4 — Severity Trend Tracking
A fault escalating from Low to High over consecutive Flow 1 runs is more alarming than a stable High. Flow 1 currently overwrites the previous diagnostic with no memory of prior severity.

**Implementation:** In `store_node`, before writing to `diagnostics_output`, query the most recent stored severity for the same `code + vehicleId`. Compare and write `severity_trend: "escalating" | "stable" | "improving"` on the new document. Surface trend indicators in the dashboard fault cards.

---

### F-5 — Resolution Feedback Loop
When maintenance teams fix a fault, the actual cause and fix applied are more valuable than LLM inference from Flow 2. Currently that outcome is lost.

**Implementation:** Add `PATCH /faults/{code}/resolve` endpoint accepting `{actual_cause, fix_applied, resolved_by, vehicle_id}`. Write the resolution back into the `knowledge_base` entry under a `real_world_resolutions` array. On the next Flow 2 enrichment for that code, include the most recent real-world resolution in `KB_ENRICH_HUMAN_PROMPT` so the LLM grounds its output in confirmed fix history.

---

### F-6 — Enrichment Quality Score + Review Queue
Flow 2 enriches unknown codes via LLM but has no quality signal on the output. A poorly formed KB entry (vague meaning, empty resolution steps) passes through silently.

**Implementation:** Add a `confidence` field to the `KB_ENRICH_SYSTEM_PROMPT` output schema (0–100). In `auto_learn_from_diagnosis()`, if `confidence < 60` and `severity in [High, Critical]`, write the entry to a `review_queue` collection alongside `knowledge_base`. Add `GET /review-queue` endpoint returning items sorted by severity + lowest confidence. Surface in the dashboard as a separate admin tab.

---

### F-7 — Scheduled Full Scan
The pipeline is query-driven — source DB is only scanned when a tenant is actively queried from the dashboard. New fault documents accumulate unprocessed until someone opens that tenant.

**Implementation:** Add a `scripts/scheduled_scan.py` that calls `run_data_scan_pipeline()` with no tenant filter, covering all tenants in one pass. Register it as a cron job (e.g. every 15 minutes). Alternatively expose `POST /scan` (already implemented) and call it from an external scheduler.
