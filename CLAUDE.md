# CLAUDE.md

Guidance for Codex / Claude Code when working in this repository. Read this file before generating, refactoring, or extending code.

---

## 1. Project Identity

**Name:** DriverBook Diagnostics Agent
**Purpose:** AI-powered J1939 SPN / OBD-II DTC fault analysis service for commercial fleet management.
**Stack:** Python 3.11+, FastAPI, Uvicorn, MongoDB (PyMongo), LangChain + LangGraph, Ollama (Llama 3.1), Streamlit (frontend), Docker.
**Entry point (API):** `api.py` (FastAPI app `app`).
**Entry point (batch scan):** `core/datascanpipeline.py` (CLI).
**Frontend:** `frontend/streamlit_app/app.py`.

The pipeline DAG is: `parse → kb_lookup → telemetry → llm (diagnose) → explain → store`.

---

## 2. Hard Rules (Non-Negotiable)

1. **No extra blank lines, no double spaces, no trailing whitespace.** Token economy matters. One blank line between functions, zero blank lines inside functions unless a logical block break is required.
2. **Functional + modular only.** No god-classes. Prefer pure functions. Stateful objects only when an external resource (Mongo client, LLM client, Streamlit session) demands it.
3. **Small functions.** Target ≤ 30 lines, single responsibility, single return type. If you exceed 50 lines, split.
4. **Separate concerns by directory** (see §4). A new responsibility = a new module, never an `_extra` block in an existing file.
5. **Data fetching, preprocessing, business logic, persistence, LLM calls, and orchestration are NEVER mixed in the same function.** Each lives in its own module under §4.
6. **Type hints on every public function** (`def f(x: int) -> str: ...`). Use `from __future__ import annotations` if needed for forward refs.
7. **No hardcoded secrets, URIs, or model names.** All config via `.env` + `os.getenv(KEY, default)`. Defaults must be safe for local dev only.
8. **No print debugging in committed code.** Use the logger configured in `core/logging_config.py` (create it if missing — see §6).
9. **Errors at boundaries only.** Validate user input at FastAPI / CLI / Streamlit entry. Trust internal calls. Do not wrap every call in try/except.
10. **Idempotent DB writes.** Use `update_one(..., upsert=True)` with `$setOnInsert` for first-write fields; never blind `insert_many` without dedup logic for retried operations.
11. **Never break the LangGraph state contract.** Every node returns `{**state, ...}` — never a partial state.
12. **No comments that restate code.** Only document *why*, hidden invariants, or non-obvious constraints. Module-level one-line header comment is allowed.

---

## 3. Environment Setup (Mandatory Steps)

Always create a fresh virtual environment per project clone. Do **not** install into the system Python.

### 3.1 Create environment
```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```
```bash
# Linux / macOS / WSL
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3.2 Configure `.env`
```bash
cp .env.example .env
# Then edit MONGO_URI, SOURCE_MONGO_URI, OLLAMA_BASE_URL, OLLAMA_MODEL
```
Required keys: `MONGO_URI`, `MONGO_DB`, `SOURCE_MONGO_URI`, `SOURCE_MONGO_DB`, `SOURCE_COLLECTION`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`.

### 3.3 External services
- **MongoDB** running on `MONGO_URI` (default `mongodb://localhost:27017`).
- **Ollama** with the configured model pulled: `ollama pull llama3.1 && ollama serve`.

### 3.4 Run
```bash
uvicorn api:app --reload --port 8000          # API
streamlit run frontend/streamlit_app/app.py    # UI
python -m core.datascanpipeline --limit 50     # Batch scan CLI
```

### 3.5 Docker
```bash
docker build -t driverbook-diagnostics .
docker run -p 8000:8000 --env-file .env driverbook-diagnostics
```
Inside Docker Desktop, replace `localhost` with `host.docker.internal` for Mongo / Ollama.

---

## 4. Production Folder Structure (Authoritative)

```
driverbook-diagnostic-agent/
├── api.py                          # FastAPI HTTP entry point only — no logic
├── requirements.txt
├── Dockerfile
├── .env.example
├── .gitignore
├── README.md
├── CLAUDE.md                       # This file
│
├── config/                         # NEW — centralized settings (create when needed)
│   └── settings.py                 # Pydantic Settings or dataclass; loads .env once
│
├── core/                           # Pure business logic — no I/O side effects unless noted
│   ├── dtc_parser.py               # Raw DTC JSON → structured fault dicts
│   ├── knowledge_base.py           # KB seed / lookup / auto-learn (Mongo I/O, isolated)
│   ├── telemetry_context.py        # Telemetry snapshot + severity escalation rules
│   ├── datascanpipeline.py         # Batch scan orchestrator + CLI
│   └── logging_config.py           # NEW — single logger factory (create when needed)
│
├── data/                           # NEW — data fetching & preprocessing (create when needed)
│   ├── fetch_mongo.py              # All MongoDB reads from source collections
│   ├── fetch_telemetry.py          # Telemetry-only fetchers
│   ├── preprocess.py               # Cleaning / normalization / sentinel handling
│   └── pipelines.py                # Compose fetch + preprocess into named pipelines
│
├── db/                             # MongoDB write-side only
│   ├── connection.py               # Cached MongoClient per URI
│   ├── fault_vehicles.py           # Staging collection for DTC-bearing source documents
│   ├── unknown_faults.py           # Upsert unknown faults
│   └── diagnostics_output.py       # NEW — diagnostics_output writes (extract from store_node)
│
├── llm/                            # LLM client + prompts only
│   ├── hf_client.py                # ChatOllama factory
│   ├── prompts.py                  # All system + human prompt templates
│   └── parsers.py                  # NEW — JSON extraction / response normalizers
│
├── orchestration/                  # Graph wiring only — no business logic in node bodies
│   └── diagnostic_graph.py         # LangGraph DAG (parse→kb→telemetry→llm→explain→store)
│
├── knowledge_base/
│   └── seed_kb.json                # 50-code seed KB
│
├── frontend/
│   └── streamlit_app/
│       ├── app.py                  # Streamlit UI
│       └── README.md
│
├── tests/                          # NEW — pytest tests (create alongside any new module)
│   ├── unit/                       # Pure logic tests, no Mongo / Ollama
│   ├── integration/                # Mongo + LLM stubbed
│   └── fixtures/                   # Sample dtcJson / telemetry payloads
│
└── scripts/                        # NEW — one-off ops scripts (create when needed)
    └── reseed_kb.py                # Example: force re-seed KB
```

**Rule of placement:**
- HTTP / CLI / UI handlers → `api.py`, `core/datascanpipeline.py`, `frontend/`.
- Pure transformation logic → `core/`.
- Reading from external data sources → `data/`.
- Writing to Mongo → `db/`.
- LLM-facing code → `llm/`.
- Graph wiring → `orchestration/`.

If a new file does not fit one of these, the directory is wrong — propose a new top-level dir in chat before creating.

---

## 5. Coding Conventions

### 5.1 Function design
- One responsibility per function. If you write "and" in the docstring, split it.
- Inputs explicit, outputs explicit. Never mutate caller-owned dicts; return new dicts.
- Prefer `dict[str, Any]` over `Any` for state-shaped objects; use `TypedDict` for graph state (see `DiagnosticState`).
- Default args must be immutable. No `def f(x=[])`.

### 5.2 File layout
- Module header (one line): `# path/file.py — one-line purpose.`
- Imports: stdlib → third-party → local, separated by ONE blank line each group.
- Public API at top, private helpers (prefixed `_`) below.
- Constants UPPER_SNAKE at module top.

### 5.3 Naming
- Functions: `verb_noun` (`build_telemetry_snapshot`, `parse_dtc_records`).
- Booleans: `is_*`, `has_*`.
- Private helpers: `_leading_underscore`.
- Constants: `UPPER_SNAKE_CASE`.

### 5.4 Error handling
- Validate at the boundary: FastAPI route handlers, CLI arg parsing, Streamlit form submit.
- Inside the pipeline, fail fast; do not swallow exceptions to "keep the loop going" except in the per-fault LLM call (already isolated in `llm_node` / `explain_node`).
- Always include the offending code / id in the error message.

### 5.5 Logging
- `logger = logging.getLogger(__name__)`.
- Levels: DEBUG (per-fault detail) · INFO (pipeline milestones) · WARNING (recoverable degradation) · ERROR (failed unit) · CRITICAL (process-aborting).
- Never log secrets, full Mongo URIs with credentials, or full LLM responses at INFO+.

### 5.6 Type checking
- Run `mypy --strict core/ db/ llm/ orchestration/ data/` before opening a PR.
- All new code must pass.

### 5.7 Formatting
- `ruff format` + `ruff check --fix` are authoritative. Line length 100. No tabs.
- No emojis in source code, comments, or docstrings.

### 5.8 Tests
- Every new function in `core/`, `data/`, `db/`, `llm/parsers.py` ships with a unit test.
- Mongo-touching tests live in `tests/integration/` and use `mongomock` or a disposable test DB.
- LLM-touching tests stub the client — never call Ollama in CI.

---

## 6. Production Requirements (Beyond User's List)

Apply these whenever you add or modify code:

1. **Configuration management:** All env access through `config/settings.py` (one Pydantic `BaseSettings` instance imported elsewhere). No scattered `os.getenv` calls in business logic.
2. **Structured logging:** JSON logs in production (`LOG_FORMAT=json`), human-readable in dev. Single config in `core/logging_config.py`, called once in `api.py` startup.
3. **Health checks:** `GET /health` (process up) and `GET /ready` (Mongo + Ollama reachable) on the FastAPI app. Required for any container deploy.
4. **Graceful shutdown:** Close Mongo clients on FastAPI `shutdown` event. Drain in-flight LangGraph invocations before exit.
5. **Request tracing:** Add a `request_id` (UUID4) to every API request, propagate through state, log with every line.
6. **Timeouts everywhere:** Mongo ops, Ollama calls, HTTP fetches must have explicit timeouts. No unbounded waits.
7. **Retry policy:** Retry transient Mongo / Ollama errors with exponential backoff (max 3 attempts). Never retry validation errors.
8. **Input validation:** All Pydantic models with strict types. Reject empty `dtcJson.dtcs`. Reject vehicleId longer than 64 chars.
9. **Rate limiting / auth:** When exposing beyond localhost, add API-key middleware in `api.py` and per-IP rate limit. Document required header in README.
10. **Observability:** Emit pipeline metrics (faults processed, unknown rate, LLM latency, KB hit rate) — even just as logs initially. Reserve Prometheus integration as a follow-up.
11. **Indexes:** Mongo collections must have indexes on lookup keys (`knowledge_base.code` unique, `unknown_faults.code` unique, `diagnostics_output.vehicleId + timestamp`). Define in seed / migration code, not ad-hoc.
12. **Schema versioning:** Every persisted document carries a `schema_version` field. Bump when shape changes; readers must tolerate older versions.
13. **Reproducibility:** Pin versions in `requirements.txt` (use `pip-compile` → `requirements.lock`) when stabilizing for deploy.
14. **Security:** Never echo `.env` contents in logs or responses. Sanitize Mongo error messages before returning to clients.
15. **Docker hygiene:** Multi-stage build, non-root user, `HEALTHCHECK` directive, `.dockerignore` excluding `.venv`, `__pycache__`, `.env`, `tests/`.
16. **CI gates (when added):** ruff, mypy, pytest, `pip-audit` for vulnerabilities, `docker build`. Block merge on failure.

---

## 7. LangGraph State Contract

`DiagnosticState` (see `orchestration/diagnostic_graph.py`):
```python
class DiagnosticState(TypedDict):
    raw_input: dict          # {vehicleId, dtcJson, telemetry}
    parsed_faults: list      # output of dtc_parser, enriched as it flows
    diagnostics: list        # final per-fault diagnostic results
    unknown_codes: list      # codes not found in KB
```
Every node MUST: (a) read only the keys it needs, (b) return `{**state, <updated keys>}`, (c) never delete keys.

When adding a node:
1. Define it in `orchestration/diagnostic_graph.py` only.
2. Call into pure functions in `core/`, `data/`, `db/`, `llm/`. Never inline business logic in the node body beyond ~10 lines of glue.
3. Wire it into the DAG in `build_graph()`. Update the README pipeline diagram.

---

## 8. MongoDB Collections

| Collection | Purpose | Index |
|---|---|---|
| `fault_vehicles` | Staging — every source document containing at least one DTC | `source_id` (unique) |
| `knowledge_base` | Known fault code definitions (seeded + extracted + auto-learned) | `code` (unique) |
| `unknown_faults` | Codes not in KB; review queue | `code` (unique) |
| `diagnostics_output` | Full LLM diagnostic output per request | `vehicleId + timestamp` |

Every write goes through a function in `db/`. Never call `db["x"].insert_*` from `core/`, `llm/`, or `orchestration/`.

---

## 9. LLM Call Conventions

- All prompts live in `llm/prompts.py`. Never inline prompt strings in graph nodes.
- Output is **always** a single-line raw JSON object. Parsing happens in `llm/parsers.py` (extract first `{...}`, normalize whitespace).
- Set `temperature=0.0` for diagnostic / explainability calls — determinism over creativity.
- Cap `num_predict` to 512 unless explicitly justified.
- Per-fault LLM failures are isolated: capture the exception, attach `{"error": str(exc)}` to that fault's record, continue the loop.

---

## 10. When You (Codex) Are Asked to Add a Feature

Default checklist before writing code:
1. Does the change cross directory boundaries (§4)? If yes, plan the split before coding.
2. Is there an existing function within 80% of what you need? Extend / parametrize it instead of duplicating.
3. Does the change require a new env var? Add to `config/settings.py` AND `.env.example` AND README env-var table.
4. Does it touch Mongo? Add / update the index. Add a unit test with `mongomock`.
5. Does it touch the LLM? Update the prompt, add a parser test with a fixture response.
6. Does it touch the graph? Update `DiagnosticState`, the DAG diagram in `README.md`, and the integration test.
7. Does it add a new file? Add a one-line header comment, type hints, tests, ruff-clean.
8. Update `CLAUDE.md` §11 file-by-file scan if the file map changed.

---

## 11. Repository File-by-File Scan

Snapshot of the codebase at the time this file was authored. Update when files are added / removed / substantially restructured.

### 11.1 Root files

#### `api.py` (FastAPI entry point — 90 lines)
- Loads `MONGO_DB` from env, builds a Mongo handle via `db.connection.get_db`.
- Calls `core.knowledge_base.seed_knowledge_base(db)` on import; logs whether KB was seeded.
- Builds the LangGraph app via `orchestration.diagnostic_graph.build_graph(db)`.
- **Endpoints:**
  - `POST /analyze-vehicle/{vehicle_id}` → looks up the latest DTC-bearing source document for the given vehicleId, stages it into `fault_vehicles`, invokes the graph (or returns cached `diagnostics_output` if already analyzed and `reanalyze=false`).
  - `GET /knowledge-base` → returns all KB entries.
  - `GET /unknown-faults` → returns unresolved unknowns sorted by `occurrence_count` desc.
- **Concerns to address:** side-effects at import time (seed + graph build) — move into a FastAPI `startup` event handler. No `/health` or `/ready` endpoints — add per §6.3.

#### `requirements.txt`
Unpinned: `python-dotenv`, `pymongo`, `pandas`, `fastapi`, `uvicorn`, `langchain`, `langgraph`, `langchain-ollama`, `langchain_community`, `streamlit`, `requests`. **Action:** generate a pinned `requirements.lock` for deploy reproducibility.

#### `Dockerfile`
`python:3.11-slim`, `WORKDIR /app`, installs `requirements.txt`, copies all, exposes 8000, runs uvicorn on `0.0.0.0:8000`. **Gaps:** no multi-stage, no non-root user, no `HEALTHCHECK`, no `.dockerignore` referenced. **Action:** harden per §6.15.

#### `.env`
Local config. Keys: `MONGO_URI`, `MONGO_DB`, `SOURCE_MONGO_URI`, `SOURCE_MONGO_DB`, `SOURCE_COLLECTION`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`. **Never commit.**

#### `.env.example`
Template mirroring `.env` shape with safe placeholders. Source URIs use `username:password@host` placeholder pattern.

#### `.gitignore`
Excludes `.env`, `*.env`, Python caches, `venv/.venv/env/`, IDE dirs (`.vscode/.idea/`), logs, OS files (`.DS_Store`, `Thumbs.db`), `.claude/`.

#### `README.md`
Full user-facing docs: architecture diagram, two-agent pattern, prerequisites (Python 3.11+, Mongo, Ollama), setup steps, Docker, API examples (`POST /analyze-fault` request + response, `GET /knowledge-base`, `GET /unknown-faults`), project structure tree, Mongo collection table, env-var table.

#### `CODEBASE_EXPLANATION.md`
Long-form module-by-module walkthrough (~600 lines). Treat as supplementary; this `CLAUDE.md` is the source of truth for AI agents.

---

### 11.2 `core/` — Business logic

#### `core/dtc_parser.py` (75 lines)
- `_extract_fmi(description: str) -> int | None` — regex `FMI\s+(\d+)`, case-insensitive.
- `parse_dtc_records(dtc_records: dict, vehicle_id: str, timestamp: str | None = None) -> list[dict]` — iterates `dtc_records["dtcs"]`, builds fault dicts with keys `code, ecu, fmi, description, timestamp, vehicleId, mil`.
- Pure: no I/O. Defaults `timestamp` to `datetime.now(timezone.utc).isoformat()`.

#### `core/knowledge_base.py` (97 lines)
- `_SEED_PATH` resolves to `../knowledge_base/seed_kb.json` relative to module.
- `seed_knowledge_base(db) -> int` — inserts seed entries if collection empty; sets `first_seen / last_seen / occurrence_count`; creates unique index on `code`. Returns count inserted (0 if already populated).
- `lookup(db, code: str) -> dict | None` — case-insensitive lookup, strips whitespace, projects out `_id`.
- `increment_occurrence(db, code: str) -> None` — `$inc: occurrence_count` and `$set: last_seen`.
- `auto_learn_from_diagnosis(db, fault, diagnostic) -> None` — upsert with `$setOnInsert` only (won't overwrite hand-authored entries); tags `source: "auto_learned"`.

#### `core/telemetry_context.py` (94 lines)
- Constants: `_SENTINEL = -6.128e18`, `_SEVERITY_ORDER = ["Low", "Medium", "High", "Critical"]`, `_COOLANT_TEMP_HIGH_C = 105.0`, `_OIL_PRESSURE_LOW_PSI = 20.0`, `_DEF_LEVEL_LOW_PCT = 5.0`, `_ENGINE_ECUS = {"engine", "exhaust emission", "emission"}`.
- `build_telemetry_snapshot(raw_record: dict) -> dict` — extracts six signals, converts to float, drops sentinel-equivalent values.
- `_escalate(severity)` — moves up one level, capped at `Critical`.
- `_is_engine_related(fault)` — substring match on ECU.
- `adjust_severity(base_severity, fault, telemetry)` — three rules: hot coolant + engine fault → +1; low oil + engine fault → `Critical`; low DEF + DEF/emission fault (codes containing `DEF / 1761 / 4374 / 4375 / 5435`) → +1.

#### `core/datascanpipeline.py` (370 lines)
Batch CLI for scanning the source MongoDB collection and running each found document through the diagnostic graph.
- Constants: `DEFAULT_DTC_RECORDS_PATH = "metaData.dtcRecords"`, `DEFAULT_SOURCE_DB = "driverbookv2_ai"`, `DEFAULT_SOURCE_COLLECTION = "driverbookv2.driverdiagnostics"` (literal collection name containing a dot), `DEFAULT_APP_DB = "diagnostics"`, `DEFAULT_TELEMETRY_FIELDS`.
- Helpers: `get_nested`, `clean_query`, `build_dtc_scan_query`, `_extract_fmi`, `_mil_to_bool`, `_normalize_telemetry`, `extract_telemetry`.
- Builders: `build_raw_input`, `extract_dtc_records`.
- Scanners: `scan_dtc_documents` (cursor-based with skip/limit/batch_size).
- Bootstrap: `get_source_collection`, `get_app_graph`.
- Analysis: `analyze_scanned_record`, `run_data_scan_pipeline`.
- CLI: `_parse_cli_args`, `main` — args `--limit / --skip / --batch-size / --dtc-records-path / --vehicle-id-field / --query`.
- **Refactor target:** this file does fetching + preprocessing + orchestration + CLI. Split into `data/fetch_mongo.py` (cursor + query building), `data/preprocess.py` (dtc + telemetry extraction), `core/datascanpipeline.py` (orchestration only), `scripts/scan_cli.py` (argparse + main). See §4.

---

### 11.3 `db/` — Mongo writes & connection

#### `db/connection.py` (20 lines)
- Loads `.env` at import.
- Module-level `_clients: dict[str, MongoClient]` cache keyed by URI.
- `get_db(database: str = "diagnostics", uri: str | None = None)` — returns DB handle, reuses one client per URI.

#### `db/fault_vehicles.py`
- `_COLLECTION = "fault_vehicles"`.
- `ensure_fault_vehicles_collection(db)` — creates unique index on `source_id`. Idempotent.
- `stage_fault_document(db, source_doc, extracted) -> bool` — upsert keyed on `source_id`; `$setOnInsert` for `vehicleId, tenantId, timestamp, mil, fault_count, faults, raw_input, staged_at, analyzed=False`. Returns `True` on first insert.
- `mark_analyzed(db, source_id)` — sets `analyzed=True` and `analyzed_at` once the graph finishes a doc.

#### `db/unknown_faults.py` (45 lines)
- `save_unknown_fault(db, fault, telemetry_snapshot, diagnostic=None)` — upsert into `unknown_faults`; `$setOnInsert` for first-encounter fields (code, ecu, fmi, raw_description, first_seen, status="unresolved"); `$set` for last_seen + sample_telemetry + latest_diagnostic; `$inc` occurrence_count.

---

### 11.4 `llm/` — Ollama client + prompts

#### `llm/hf_client.py` (22 lines)
- Reads `OLLAMA_MODEL` (default `llama3.1`) and `OLLAMA_BASE_URL` (default `http://localhost:11434`).
- `get_llm()` returns `ChatOllama(model, base_url, temperature=0.0, num_predict=512)`.

#### `llm/prompts.py` (62 lines)
- `SYSTEM_PROMPT` — diagnostic agent instructions; mandates single-line raw JSON output with schema `{purpose, issue, impact, severity (Low|Medium|High|Critical), urgency (Ignore|Monitor|Schedule Maintenance|Immediate Action), confidence (0-100)}`.
- `HUMAN_PROMPT` — formats `{code, ecu, fmi, raw_desc, telemetry, kb_entry}`.
- `EXPLAIN_SYSTEM_PROMPT` — explainability agent; output schema `{explanation, resolution_steps, who_can_fix (Driver only|Fleet maintenance team|Certified technician required), parts_likely_needed}`.
- `EXPLAIN_HUMAN_PROMPT` — formats `{code, ecu, diagnosis, kb_entry, telemetry}`.

---

### 11.5 `orchestration/` — LangGraph DAG

#### `orchestration/diagnostic_graph.py` (216 lines)
- `class DiagnosticState(TypedDict)` — see §7.
- `parse_node(state)` — calls `parse_dtc_records`.
- `kb_lookup_node(state, db)` — annotates each fault with `kb_entry`, sets `is_unknown`, populates `unknown_codes`.
- `telemetry_node(state)` — builds snapshot, computes `adjusted_severity` per fault.
- `llm_node(state, llm)` — formats `HUMAN_PROMPT`, invokes LLM, normalizes whitespace, extracts first JSON object via `re.search(r"\{.*\}")`, overrides severity with `adjusted_severity` when present.
- `explain_node(state, llm)` — feeds diagnosis summary to explainability prompt; merges explanation fields into each diagnostic.
- `store_node(state, db)` — for unknowns: `save_unknown_fault` + `auto_learn_from_diagnosis`; for knowns: `increment_occurrence`. Then `db["diagnostics_output"].insert_many(state["diagnostics"])`.
- `build_graph(db)` — instantiates LLM, wires nodes via `lambda s: node(s, dep)`, sets entry point `parse`, edges `parse→kb_lookup→telemetry→llm→explain→store→END`, returns `graph.compile()`.

---

### 11.6 `knowledge_base/`

#### `knowledge_base/seed_kb.json` (~27 KB, 50 entries)
Seed data for the `knowledge_base` Mongo collection. Each entry shape: `{code, system, component, meaning, causes, severity, urgency, ...}`. Auto-loaded on first API startup.

---

### 11.7 `frontend/streamlit_app/`

#### `frontend/streamlit_app/app.py` (107 lines)
- Chat-style Streamlit UI hitting `http://localhost:8000/analyze-fault`.
- Hardcoded `vehicleId="TRUCK-001"`, fixed payload shape with `SPN 521133`.
- Renders code, severity, urgency, issue, explanation, resolution steps.
- **Concerns:** API URL hardcoded — move to env. Payload shape rigid — add a real form per the README's promise.

#### `frontend/streamlit_app/README.md`
Setup + usage docs. Lists features (interactive form, payload preview, expandable sections) — most NOT yet implemented in `app.py`.

---

## 12. Known Gaps / TODOs (Reference for Codex)

These are the deltas between the current code and the structure / requirements above. Address in order of priority when asked to "harden" or "make production-ready":

1. Move side-effects in `api.py` into FastAPI `@app.on_event("startup")`.
2. Add `/health` and `/ready` endpoints.
3. Create `config/settings.py` and route all `os.getenv` calls through it.
4. Create `core/logging_config.py`; replace all `print(...)` with logger calls.
5. Split `core/datascanpipeline.py` per §11.2 refactor note.
6. Extract `db/diagnostics_output.py` from `store_node`'s direct `insert_many` call.
7. Extract LLM JSON parsing into `llm/parsers.py` (used by `llm_node` and `explain_node` — currently duplicated).
8. Pin dependency versions; add `requirements.lock`.
9. Harden `Dockerfile` (multi-stage, non-root, healthcheck) + add `.dockerignore`.
10. Add `tests/` skeleton with at least one unit test per `core/` module (use `mongomock` for Mongo paths, stubbed LLM for graph tests).
11. Add Mongo indexes for `diagnostics_output (vehicleId, timestamp)`.
12. Replace hardcoded `API_URL = "http://localhost:8000"` in Streamlit with `os.getenv("API_URL", ...)`.
13. Add `schema_version` to all persisted documents.

---

## 13. Quick Reference — Where Does X Go?

| If you're adding... | It goes in... |
|---|---|
| A new fault parser (e.g., proprietary CAN format) | `core/<format>_parser.py` |
| A new external data source (e.g., S3, REST API) | `data/fetch_<source>.py` |
| Cleaning / normalization logic | `data/preprocess.py` |
| A new Mongo write target | `db/<collection>.py` |
| A new LLM agent / prompt | `llm/prompts.py` (template) + `orchestration/` (node) |
| A new graph node | `orchestration/diagnostic_graph.py` (glue) + the corresponding pure function in `core/` |
| A new HTTP endpoint | `api.py` (handler only — calls into `core/` / `db/`) |
| A new env var | `config/settings.py` + `.env.example` + README env table |
| A throwaway script | `scripts/<name>.py` |
| A test | `tests/unit/` or `tests/integration/` mirroring source path |

---

End of CLAUDE.md. Keep this file updated when the file map, conventions, or production rules change.
