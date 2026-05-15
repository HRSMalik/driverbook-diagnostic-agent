# PLAN.md — DriverBook Diagnostics Agent

## Status
API-only service. No frontend. Integrates into the client's existing vehicle view via two endpoints. Two-flow KB-first pipeline is unchanged.

---

## System Summary

- **Purpose:** Analyse J1939 SPN / OBD-II DTC fault codes for commercial fleets and return plain-language diagnostics for fleet managers.
- **Stack:** Python 3.11+, FastAPI, MongoDB (PyMongo), LangChain + LangGraph, Ollama (local Llama 3.1), Docker.
- **Integration pattern:** Client app opens a vehicle → calls `/faults` to show fault list with severity → user clicks a fault → calls `/faults/diagnose` to get full diagnostic detail.
- **LLM pattern:** KB-first. LLM is called only once per unique unknown fault code. Result saved to KB permanently. All future clicks on the same code return instantly from KB with no LLM.

---

## API Endpoints

| Method | Path | When called | What it does |
| --- | --- | --- | --- |
| `GET` | `/vehicles/{vehicle_id}/faults` | Vehicle opened | Returns fault code list with severity. Reads from local DB only — no source DB hit. Cache-first from diagnostics_output, falls back to raw staged faults if not yet analyzed. |
| `GET` | `/vehicles/faults/diagnose` | Fault clicked | KB-first diagnostic for a specific code. Returns instantly if in KB. On KB miss: calls LLM once, saves to KB permanently, returns full diagnostic. |
| `POST` | `/vehicles/{vehicle_id}/reanalyze` | Manual trigger | Force re-run Flow 1+2 for a vehicle's latest staged document. |
| `POST` | `/scan` | Admin / scheduled | Kick off full source collection scan in background thread. |
| `GET` | `/tenants` | Admin | List all staged tenants with names. |
| `GET` | `/tenants/{tenant_id}/vehicles` | Admin | List all vehicles for a tenant with diagnostics inline. |
| `GET` | `/knowledge-base` | Admin | List all KB entries. |
| `GET` | `/unknown-faults` | Admin | List unresolved unknown codes. |
| `GET` | `/health` | Infra | Liveness check. |
| `GET` | `/ready` | Infra | Readiness — checks Mongo + Ollama. |

---

## Pipeline Architecture

### Flow 1 — Fast KB Lookup (always runs, no LLM)

```
parse → kb_lookup → diagnose → store
```

| Node | What it does |
| --- | --- |
| parse | Reads raw dtcJson, builds structured fault list (code, ECU, FMI, description, vehicleId, MIL) |
| kb_lookup | Checks each code against knowledge_base. Known: full KB entry attached. Unknown: flagged is_unknown=True |
| diagnose | Known codes: full diagnostic from KB instantly. Unknown codes: placeholder (confidence=0, "explanation pending"). Severity escalated using telemetry signals where available. |
| store | Saves results to diagnostics_output. Increments occurrence_count for known codes. Saves unknowns to unknown_faults queue. |

No LLM calls. Milliseconds for any code already in KB.

### Flow 2 — LLM Enrichment (on KB miss only)

```
collect unknown codes → LLM once per code → save to KB → re-diagnose
```

Triggered automatically when `/faults/diagnose` hits a code not in KB, and as part of the background batch scan. One LLM call per unique unknown code. Result saved permanently — all future requests for that code go through Flow 1 only.

---

## Collections (App DB: `diagnostics`)

| Collection | Purpose | Key index |
| --- | --- | --- |
| fault_vehicles | Staged source documents containing at least one DTC | source_id (unique), tenantId |
| knowledge_base | Fault code definitions — seeded + LLM auto-learned | code (unique) |
| unknown_faults | Codes not in KB at time of analysis — enriched by Flow 2 | code (unique) |
| diagnostics_output | Per-fault diagnostic results keyed by source_id | source_id, vehicleId |
| tenant_names | Tenant ID to company name mapping | tenantId (unique) |

---

## Module Map

| File | Responsibility |
| --- | --- |
| api.py | FastAPI endpoints only — no business logic |
| core/dtc_parser.py | Raw dtcJson → structured fault dicts |
| core/knowledge_base.py | seed, lookup, increment_occurrence, auto_learn_from_diagnosis |
| core/datascanpipeline.py | Two-phase batch orchestrator (Flow 1 + Flow 2) + CLI |
| core/telemetry_context.py | Telemetry snapshot + severity escalation rules |
| db/connection.py | Cached MongoClient per URI |
| db/fault_vehicles.py | stage_fault_document, mark_analyzed, ensure collection |
| db/unknown_faults.py | save_unknown_fault upsert |
| db/diagnostics_output.py | save_diagnostics writes |
| llm/hf_client.py | ChatOllama factory (Llama 3.1, temperature=0.0) |
| llm/prompts.py | All prompt templates including KB_ENRICH_SYSTEM_PROMPT |
| llm/parsers.py | JSON extraction from LLM responses |
| orchestration/diagnostic_graph.py | build_graph() for Flow 1 + enrich_unknown_codes() for Flow 2 |

---

## Current State (as of 2026-05-15)

| Metric | Value |
| --- | --- |
| Tenants | 24 |
| Documents staged | 578 |
| KB entries | 156 (50 seeded + 106 LLM learned) |
| React dashboard | Removed |
| New endpoints | /faults and /faults/diagnose live on main |

---

## Remaining Backlog

**B-1 — Silent LLM failures in Flow 2**
`enrich_unknown_codes()` swallows all LLM exceptions with bare `except: pass`. Add per-code logging, track failed codes in DB, retry once on failure.

**B-2 — LLM call timeout**
`llm.invoke()` has no timeout. A stuck Ollama process blocks Flow 2 indefinitely. Add `ChatOllama(timeout=60)` or wrap with `concurrent.futures`.

**B-3 — Background scan errors are invisible**
Background thread logs warnings but the caller never knows if the scan failed. Add a scan status record in MongoDB.

**B-4 — Import-time side effects in api.py**
KB seed, graph build, and collection setup run at import time. Move into FastAPI `lifespan` handler with proper error handling.

**B-5 — Telemetry escalation not available on /faults/diagnose**
The diagnose endpoint has no staged document to pull telemetry from — severity escalation is skipped. Accept optional telemetry fields as query params so the client can pass live vehicle signals.

**B-6 — Centralise config in config/settings.py**
`os.getenv` calls scattered across multiple files. A single `Settings` class validates required vars on startup.

**B-7 — Request tracing**
No `request_id` propagated through API calls or background scans. Add UUID4 `request_id` at API boundary, pass through LangGraph state.

**B-8 — Harden Dockerfile**
No multi-stage build, no non-root user, no `HEALTHCHECK`, no `.dockerignore`. Required before any non-local deployment.

---

## Future Improvements

**F-1 — Multi-Fault Correlation**
When a vehicle has multiple simultaneous faults, a Flow 3 step sends the full resolved diagnostic set to the LLM in one call to identify clusters and cross-fault implications.

**F-2 — Vehicle History Context in KB Enrichment**
Pass prior occurrence history (count, first_seen, last_seen) for a code+vehicle into the enrichment prompt to improve urgency signals.

**F-3 — Fleet-Wide Pattern Detection**
`GET /tenants/{tenant_id}/patterns` — aggregate diagnostics_output to flag codes hitting 3+ vehicles simultaneously as fleet-level alerts.

**F-4 — Severity Trend Tracking**
Track severity changes across consecutive Flow 1 runs per code+vehicle. Write `severity_trend: escalating | stable | improving` on each diagnostic.

**F-5 — Resolution Feedback Loop**
`PATCH /faults/{code}/resolve` — write real-world fix outcomes back into KB so future LLM enrichments are grounded in confirmed resolutions.
