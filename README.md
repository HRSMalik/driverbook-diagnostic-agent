# DriverBook Diagnostics Agent

AI-powered vehicle fault code analysis service for commercial fleet management. Parses J1939 SPN and OBD-II DTC codes, grounds diagnoses in a local knowledge base, and uses a local LLM (Llama 3.1 via Ollama) to enrich unknown codes — producing plain-language diagnostics and resolution steps for fleet managers.

## How It Works

### Two-Flow Pipeline

### Flow 1 — Fast KB Lookup (always runs, no LLM)

```
parse → kb_lookup → diagnose → store
```

Every fault code is checked against the local knowledge base first. If the code is known, a full diagnostic is returned instantly with zero LLM calls. Unknown codes receive a placeholder diagnostic (`"explanation pending"`, confidence 0).

### Flow 2 — LLM Enrichment (runs only for unknown codes)

```
collect unique unknowns → LLM once per code → save to KB → re-diagnose
```

After Flow 1, any unique unknown codes are sent to the LLM — one call per code. The LLM returns a full KB entry (meaning, causes, severity, urgency, explanation, resolution steps, who can fix, parts, downtime). That entry is saved to the knowledge base permanently. The affected vehicles are then re-diagnosed using Flow 1, now with full KB data instead of placeholders.

Once a code is learned, it lives in the KB forever. Every future occurrence is served by Flow 1 with no LLM.

### Query-Driven Scan

The pipeline is triggered by the dashboard. When a tenant is queried via `GET /tenants/{tenant_id}/vehicles`:

1. A background thread fires `datascanpipeline` for that tenant — connects to the source MongoDB, finds new documents with DTC records, stages them, and runs Flow 1 then Flow 2.
2. The API returns immediately from the local database — it does not wait for the scan.

This means the first request returns whatever is already staged. The background scan picks up new data, and it is available on the next request.

### KB Rule

| Situation | What happens |
| --- | --- |
| Code already in KB (seeded or LLM-learned) | Flow 1 returns full diagnostic instantly, no LLM |
| New unknown code | Flow 1 returns placeholder; Flow 2 calls LLM once, saves to KB, re-diagnoses |
| Same unknown code seen again | Already in KB from first Flow 2 run — instant |

## Prerequisites

- Python 3.11+
- MongoDB running locally or accessible via URI
- [Ollama](https://ollama.com) with `llama3.1` pulled

```bash
ollama pull llama3.1
ollama serve
```

## Setup

```bash
# 1. Enter the directory
cd diagnostic_agent

# 2. Create and activate environment
conda create -n driverbook python=3.11
conda activate driverbook

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — set MONGO_URI, SOURCE_MONGO_URI, OLLAMA_BASE_URL, OLLAMA_MODEL

# 5. Start the API
uvicorn api:app --port 8000

# 6. Start the frontend
cd frontend/react_app && npm install && npm run dev
```

The knowledge base is seeded automatically on first startup from `knowledge_base/seed_kb.json` (50 codes).

## Docker

```bash
docker build -t driverbook-diagnostics .
docker run -p 8000:8000 --env-file .env driverbook-diagnostics
```

> Use `host.docker.internal` instead of `localhost` in `.env` when running on Docker Desktop.

## Running a Full Scan

To pre-populate all tenants before anyone uses the dashboard:

```bash
# Scan all DTC documents in the source collection
conda run -n driverbook python -m core.datascanpipeline

# Scan with a limit
conda run -n driverbook python -m core.datascanpipeline --limit 1000

# Force re-analyze already-staged documents
conda run -n driverbook python -m core.datascanpipeline --reanalyze
```

## API

### `GET /tenants/{tenant_id}/vehicles`

Returns all staged vehicles for a tenant with diagnostics inline. Fires a background source-DB scan for that tenant on every call.

```bash
curl http://localhost:8000/tenants/68ccb0ebc280d8be9b0f5c4a/vehicles
```

**Response**
```json
{
  "tenantId": "68ccb0ebc280d8be9b0f5c4a",
  "count": 38,
  "vehicles": [
    {
      "vehicleId": "...",
      "source_id": "...",
      "fault_count": 6,
      "staged_at": "2026-05-13T13:13:33Z",
      "analyzed": true,
      "diagnostics": [
        {
          "code": "P22FE",
          "ecu": "Engine Control Module",
          "severity": "High",
          "urgency": "Schedule Maintenance",
          "confidence": 100,
          "from_kb": true,
          "issue": "...",
          "explanation": "...",
          "resolution_steps": ["..."],
          "who_can_fix": "Fleet maintenance team",
          "parts_likely_needed": ["..."],
          "estimated_downtime": "2-4 hours"
        }
      ]
    }
  ]
}
```

### `GET /tenants`

Returns all unique tenant IDs found in staged documents.

```bash
curl http://localhost:8000/tenants
```

### `POST /vehicles/{vehicle_id}/reanalyze`

Force re-run the diagnostic pipeline on the latest staged document for a vehicle.

```bash
curl -X POST http://localhost:8000/vehicles/68e4dc3bb56bc4691e8be3a4/reanalyze
```

### `POST /scan`

Kick off a full source-collection scan in a background thread. Returns immediately.

```bash
curl -X POST http://localhost:8000/scan
curl -X POST http://localhost:8000/scan -H "Content-Type: application/json" -d '{"limit": 500, "reanalyze": true}'
```

### `GET /knowledge-base`

List all knowledge base entries.

### `GET /unknown-faults`

List unresolved unknown fault codes sorted by occurrence count.

### `GET /health`

Liveness check — confirms the process is up.

### `GET /ready`

Readiness check — confirms MongoDB and Ollama are reachable.

## Project Structure

```
diagnostic_agent/
├── api.py                          # FastAPI entry point — endpoints only
├── requirements.txt
├── Dockerfile
├── .env.example
├── core/
│   ├── dtc_parser.py               # Raw dtcJson → structured fault dicts
│   ├── knowledge_base.py           # KB seed, lookup, occurrence tracking, auto-learn
│   ├── telemetry_context.py        # Telemetry snapshot + severity escalation
│   └── datascanpipeline.py         # Batch scan orchestrator + CLI (two-phase)
├── db/
│   ├── connection.py               # Cached MongoClient per URI
│   ├── fault_vehicles.py           # Staging collection writes
│   ├── unknown_faults.py           # Unknown fault upsert
│   └── diagnostics_output.py       # diagnostics_output collection writes
├── llm/
│   ├── hf_client.py                # ChatOllama factory (Llama 3.1)
│   ├── prompts.py                  # All prompt templates
│   └── parsers.py                  # JSON extraction helpers
├── orchestration/
│   └── diagnostic_graph.py         # LangGraph DAG (Flow 1) + enrich_unknown_codes (Flow 2)
├── knowledge_base/
│   └── seed_kb.json                # 50-code seed KB
└── frontend/
    └── react_app/                  # React dashboard (Vite)
```

## MongoDB Collections

| Collection | Purpose |
|---|---|
| `knowledge_base` | Known fault code definitions — seeded + LLM auto-learned |
| `fault_vehicles` | Staged source documents containing at least one DTC |
| `unknown_faults` | Codes not in KB — review queue sorted by occurrence |
| `diagnostics_output` | Per-fault diagnostic results served to the dashboard |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | App database connection string |
| `MONGO_DB` | `diagnostics` | App database name |
| `SOURCE_MONGO_URI` | — | Remote source cluster URI (read-only) |
| `SOURCE_MONGO_DB` | `driverbookv2_stage` | Source database name |
| `SOURCE_COLLECTION` | `driverbookv2.driverdiagnostics` | Source collection name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `llama3.1` | Model used for KB enrichment |
