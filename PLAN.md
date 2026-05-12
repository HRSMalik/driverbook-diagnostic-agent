# PLAN.md — DriverBook Diagnostics Agent

## Status
This document defines the **intended end-to-end pipeline** for the diagnostics service. CLAUDE.md holds coding conventions and the file-by-file scan; README.md holds setup and API examples; this file holds the workflow contract every implementation must satisfy.

## System Summary
- **Purpose:** Analyze J1939 SPN / OBD-II DTC fault codes for commercial fleets and return plain-language diagnostics + resolution steps.
- **Stack:** Python 3.11+, FastAPI, MongoDB (PyMongo), LangChain + LangGraph, Ollama (local Llama 3.1), Streamlit, Docker.
- **LLM pattern:** Two agents — diagnostic (purpose / issue / impact / severity / urgency / confidence) + explainability (root cause / steps / who fixes / parts / downtime).
- **Two entry paths** converge on a shared LangGraph DAG:
  - **Path A — Single-vehicle lookup:** `POST /vehicles/{vehicle_id}/reanalyze` forces a fresh graph run on the latest staged document for that vehicle.
  - **Path B — Batch scan:** `core/datascanpipeline.py` CLI scans the entire source MongoDB collection, stages, and analyzes.
- **Primary read surface:** `GET /tenants/{tenant_id}/vehicles` reads `fault_vehicles` for a tenant, joins cached diagnostics from `diagnostics_output`, lazily runs the graph for any unanalyzed staged vehicles, and returns everything inline in one response.

## Authoritative Pipeline

```
Source MongoDB Collection (read-only)
        │  cursor scan, batched
        ▼
   has_dtc_codes?  ── no ──▶ SKIP (excluded)
        │ yes
        ▼
fault_vehicles  (app DB; created on first use; document-level idempotent insert keyed on source_id)
        │
        ▼
   for each fault code in document:
        │
        ▼
   knowledge_base.lookup(code)
        │
        ├── HIT (seed / auto_learned)  ──▶ skip_llm=True
        │                                  return stored KB description (no LLM call)
        │
        ├── HIT (extracted_from_doc)   ──▶ needs_enrichment=True
        │                                  run LLM → store_node calls auto_learn_from_diagnosis
        │                                  → row upgraded to source="auto_learned" with full fields
        │
        └── MISS                       ──▶ extract_and_insert_from_document (cheap path — KB grows)
                                           save_unknown_fault (review queue)
                                           run LLM → auto_learn_from_diagnosis (optional enrichment)
        ▼
   diagnostics returned to caller / persisted to diagnostics_output
```

## Collections (App DB = `diagnostics`)

| Collection | Purpose | Key constraint |
|---|---|---|
| `fault_vehicles` | Staging — every source document containing at least one DTC | unique on `source_id` |
| `knowledge_base` | Canonical fault definitions; grows monotonically | unique on `code` |
| `unknown_faults` | Review queue for codes that needed extraction-from-doc | unique on `code` |
| `diagnostics_output` | Per-request LLM output, keyed by `source_id` | recommended: `vehicleId + timestamp` |

## KB Entry Sources (auto-upgrade lifecycle)

| `source` value | How created | Has rich LLM fields? | Can be upgraded? |
|---|---|---|---|
| *(unset)* | `seed_kb.json` — hand-authored | ✅ Yes | ❌ Never — curated truth |
| `extracted_from_doc` | Cheap path on KB miss | ❌ No — raw description only | ✅ Yes — upgraded on next analyze |
| `auto_learned` | LLM-enriched (insert or upgrade) | ✅ Yes | ❌ Stable |

## Module Map

| File | State | Responsibility |
|---|---|---|
| `api.py` | active | FastAPI endpoints — `GET /tenants/{id}/vehicles`, `POST /vehicles/{id}/reanalyze`, `GET /knowledge-base`, `GET /unknown-faults` |
| `core/dtc_parser.py` | unchanged | `parse_dtc_records()` — raw `dtcJson` → structured fault dicts |
| `core/knowledge_base.py` | active | Seed loader; `lookup()`; `extract_and_insert_from_document()` (cheap); `auto_learn_from_diagnosis()` (insert + upgrade) |
| `core/telemetry_context.py` | unchanged | Telemetry snapshot + severity escalation rules |
| `core/datascanpipeline.py` | active | Batch scanner: source-Mongo cursor → DTC filter → `fault_vehicles` staging → graph invoke |
| `db/connection.py` | unchanged | Cached `MongoClient` per URI |
| `db/fault_vehicles.py` | active | `ensure_fault_vehicles_collection()`, `stage_fault_document()` upsert, `mark_analyzed()` |
| `db/unknown_faults.py` | unchanged | Unknown-fault upsert |
| `llm/hf_client.py` | unchanged | `ChatOllama` factory |
| `llm/prompts.py` | unchanged | Diagnostic + explainability prompts |
| `orchestration/diagnostic_graph.py` | active | KB-hit short-circuit (`skip_llm`); enrichment trigger (`needs_enrichment`); idempotent `diagnostics_output` writes keyed on `source_id` |
| `frontend/streamlit_app/app.py` | active | Two-step UI: tenant lookup → vehicle cards with inline diagnostics; sidebar toggle for admin reanalyze controls |

## Idempotency Rules
- `fault_vehicles`: keyed on `source_id = str(source_doc["_id"])`. Upsert via `update_one(..., {"$setOnInsert": {...}}, upsert=True)`.
- `knowledge_base`: keyed on `code` (unique-indexed). Insert with `$setOnInsert` only — never overwrite seeded or earlier-extracted entries. Upgrade path is filtered to `source: "extracted_from_doc"` only, so curated rows are safe.
- `unknown_faults`: keyed on `code` (unique-indexed).
- `diagnostics_output`: `store_node` deletes existing rows for the current `source_id` before inserting new ones. Re-runs replace stale rows instead of duplicating.
- Re-running the scan over the same source data is a no-op for `fault_vehicles`; `knowledge_base` and `diagnostics_output` are refreshed monotonically.

## Environment Variables
`MONGO_URI`, `MONGO_DB`, `SOURCE_MONGO_URI`, `SOURCE_MONGO_DB`, `SOURCE_COLLECTION`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`. Defaults in `.env.example`.

## Entry Point Commands
```bash
uvicorn api:app --reload --port 8000               # API (Path A: tenant browse + reanalyze)
python -m core.datascanpipeline --limit 50         # Batch scan (Path B)
streamlit run frontend/streamlit_app/app.py        # UI
docker build -t driverbook-diagnostics .           # Container
docker run -p 8000:8000 --env-file .env driverbook-diagnostics
```

## Departures From the Old Phase-1 Plan
- LLM provider: HuggingFace endpoint → Ollama (local Llama 3.1).
- LLM agents: single agent → two agents (diagnose + explain).
- New: `fault_vehicles` staging collection with document-level idempotency.
- New: KB-hit short-circuit (no LLM call when KB already has the code as seed / auto_learned).
- New: cheap "extract from document" KB-insert path on miss (LLM enrichment is additive).
- New: auto-upgrade — thin `extracted_from_doc` entries automatically promoted to `auto_learned` on next analyze, filling in resolution_steps / parts / downtime.
- New: tenant-first browse endpoint (`GET /tenants/{id}/vehicles`) returning vehicles with cached diagnostics inline.
- Removed: planned `faults` collection (collapsed into `diagnostics_output`).
- Dropped: sibling-project (`mydriverbook-compliance/`) references and `dtc_codes.csv` seeding strategy. KB is hand-authored in `seed_kb.json`.

## Next-Phase TODOs
Tracked in `CLAUDE.md §12` (production hardening: startup events, /health + /ready, config/settings.py, structured logging, splitting datascanpipeline, llm/parsers.py extraction, pinned deps, hardened Dockerfile, tests/, schema versioning, etc.).
