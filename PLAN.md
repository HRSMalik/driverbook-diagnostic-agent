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

## Future Improvements

### F-1 — Multi-Fault Correlation
Each fault is diagnosed in isolation. A third LLM agent should receive the full fault set for a vehicle and identify clusters — e.g. coolant sensor fault + engine overheat fault together imply overheating, not two separate issues. Add a `correlate_node` after `diagnose_node`.

### F-2 — Vehicle History Context
The LLM has no awareness of repeat occurrences. Recurring faults on the same vehicle warrant higher urgency. Pass `occurrence_history: {count, first_seen, last_seen}` per code+vehicle into the enrich prompt.

### F-3 — Fleet-Wide Pattern Detection
The same code hitting multiple vehicles simultaneously signals a systemic issue. Add `GET /tenants/{tenant_id}/patterns` — aggregate diagnostics_output by code across all vehicles within a time window and flag codes exceeding a frequency threshold.

### F-4 — Severity Trend Tracking
A fault escalating from Low to High over consecutive runs is more alarming than a stable High. In store_node, compare new severity against the most recent stored severity for the same code+vehicle and write `severity_trend: escalating | stable | improving`.

### F-5 — Resolution Feedback Loop
When a fault is fixed, the actual cause and fix applied are more valuable than LLM inference. Add `PATCH /faults/{code}/resolve` accepting actual_cause, fix_applied, resolved_by. Write back into the KB entry under `real_world_resolutions` and include in future enrichment prompts.

### F-6 — Confidence-Based Review Queue
The `confidence` score (0-100) is currently unused. A Critical severity diagnosis with confidence 32 should not silently pass. In store_node, write to a `review_queue` collection when `confidence < 60` and `severity in [High, Critical]`. Add `GET /review-queue` endpoint.

### F-7 — Scheduled Full Scan
The pipeline is currently query-driven — data is only refreshed when a tenant is actively viewed. A cron-based scheduled scan across all tenants would ensure data stays current without requiring dashboard activity.
