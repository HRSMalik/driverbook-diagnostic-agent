# DriverBook Diagnostics Agent

AI-powered vehicle fault code analysis service for commercial fleet management. Parses J1939 SPN and OBD-II DTC codes, grounds diagnoses in a local knowledge base, and uses OpenAI to enrich unknown codes — producing plain-language diagnostics and resolution steps for fleet managers.

## How It Works

### Two-Flow Pipeline

### Flow 1 — Fast KB Lookup (always runs, no LLM)

```
parse → kb_lookup → diagnose → store
```

Every fault code is checked against the local knowledge base first. If the code is known, a full diagnostic is returned instantly with zero LLM calls. Unknown codes receive a placeholder diagnostic until Flow 2 enriches them.

### Flow 2 — LLM Enrichment (runs only for unknown codes)

```
collect unique unknowns → OpenAI once per code → save to KB → re-diagnose
```

After Flow 1, any unique unknown codes are sent to OpenAI — one call per code. The result is saved to the knowledge base permanently. Every future occurrence of that code is served by Flow 1 with no LLM call.

### KB Rule

| Situation | What happens |
| --- | --- |
| Code already in KB (seeded or LLM-learned) | Flow 1 returns full diagnostic instantly, no LLM |
| New unknown code | Flow 1 returns placeholder; Flow 2 calls OpenAI once, saves to KB |
| Same unknown code seen again | Already in KB — instant |

## Prerequisites

- Python 3.11+
- MongoDB running locally or accessible via URI
- OpenAI API key

## Setup

```bash
# 1. Enter the directory
cd diagnostic_agent

# 2. Create and activate virtual environment
python3.11 -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — set MONGO_URI, SOURCE_MONGO_URI, OPENAI_API_KEY

# 5. Start the API
uvicorn api:app --port 8000
```

The knowledge base is seeded automatically on first startup from `knowledge_base/seed_kb.json` (50 codes).

## Docker

```bash
docker build -t driverbook-diagnostics .
docker run -p 8000:8000 --env-file .env driverbook-diagnostics
```

> Use `host.docker.internal` instead of `localhost` in `.env` when running on Docker Desktop.

## Running a Full Scan

To pre-populate all tenants before integration:

```bash
# Scan all DTC documents in the source collection
python -m core.datascanpipeline

# Scan with a limit
python -m core.datascanpipeline --limit 1000

# Force re-analyze already-staged documents
python -m core.datascanpipeline --reanalyze
```

## API

### Integration Endpoints (client-facing)

These are the two endpoints the frontend integrates against.

---

#### `GET /vehicles/{vehicle_id}/faults`

Call when a vehicle is opened. Returns the fault list with severity.

```bash
curl http://localhost:8000/vehicles/69dbf13d3ee6a4f020506c7f/faults
```

**Response**
```json
{
  "vehicleId": "69dbf13d3ee6a4f020506c7f",
  "source_id": "69dbf1b16a77ae2898178098",
  "analyzed": true,
  "count": 7,
  "faults": [
    {
      "code": "SPN 520203",
      "severity": "Medium",
      "ecu": "Cab Controller - Primary",
      "is_unknown": false
    }
  ]
}
```

**Field notes:**
- `severity` — `Low` | `Medium` | `High` | `Critical`
- `is_unknown` — `true` means the fault has not been diagnosed yet; show a pending state
- `analyzed` — if `false`, faults are raw with `severity: "Pending"`

---

#### `GET /vehicles/faults/diagnose`

Call when the user clicks the explain button on a fault. Returns full plain-language diagnostic.

```
GET /vehicles/faults/diagnose?vehicle_id={id}&code={fault_code}&ecu={ecu}
```

| Param | Required | Description |
|---|---|---|
| `vehicle_id` | Yes | Vehicle ObjectId |
| `code` | Yes | Fault code from the faults endpoint (URL-encoded) |
| `ecu` | No | ECU name from the faults endpoint |

```bash
curl "http://localhost:8000/vehicles/faults/diagnose?vehicle_id=69dbf13d3ee6a4f020506c7f&code=SPN%20520203&ecu=Cab%20Controller%20-%20Primary"
```

**Response**
```json
{
  "code": "SPN 520203",
  "ecu": "Cab Controller - Primary",
  "fmi": null,
  "vehicleId": "69dbf13d3ee6a4f020506c7f",
  "timestamp": "2026-05-22T07:13:19.795103+00:00",
  "is_unknown": false,
  "severity": "Medium",
  "urgency": "Schedule Maintenance",
  "explanation": "This fault code indicates that the cab controller is detecting an excessively high voltage supply, which could cause damage to the electrical system.",
  "resolution_steps": [
    "Check alternator output voltage with a multimeter",
    "Inspect wiring for any signs of wear or damage"
  ],
  "who_can_fix": "Fleet maintenance team|Certified technician required",
  "parts_likely_needed": [
    "Alternator"
  ],
  "from_kb": true
}
```

**Field notes:**
- `urgency` — `Ignore` | `Monitor` | `Schedule Maintenance` | `Immediate Action`
- `who_can_fix` — `Driver only` | `Fleet maintenance team` | `Certified technician required`
- `from_kb` — always `true` once diagnosed
- First call on a new unknown fault may take 3–6 seconds (OpenAI enrichment). Show a loading spinner. All subsequent calls return instantly.

---

### Admin Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/tenants` | List all tenants with company names |
| `GET` | `/tenants/{tenant_id}/vehicles` | List all vehicles for a tenant with diagnostics inline |
| `POST` | `/vehicles/{vehicle_id}/reanalyze` | Force re-run Flow 1 for a vehicle's latest staged doc |
| `POST` | `/scan` | Kick off a full source-collection scan in background |
| `GET` | `/knowledge-base` | List all KB entries |
| `GET` | `/unknown-faults` | List unresolved unknown codes sorted by occurrence |
| `GET` | `/health` | Liveness check |
| `GET` | `/ready` | Readiness check — confirms MongoDB and OpenAI are reachable |

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
│   ├── llm_client.py               # ChatOpenAI factory
│   ├── prompts.py                  # All prompt templates
│   └── parsers.py                  # JSON extraction helpers
├── orchestration/
│   └── diagnostic_graph.py         # LangGraph DAG (Flow 1) + enrich_unknown_codes (Flow 2)
└── knowledge_base/
    └── seed_kb.json                # 50-code seed KB
```

## MongoDB Collections

| Collection | Purpose |
|---|---|
| `knowledge_base` | Known fault code definitions — seeded + OpenAI auto-learned |
| `fault_vehicles` | Staged source documents containing at least one DTC |
| `unknown_faults` | Codes not in KB — review queue sorted by occurrence |
| `diagnostics_output` | Per-fault diagnostic results |
| `tenant_names` | Tenant ID to company name cache |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | App database connection string |
| `MONGO_DB` | `diagnostics` | App database name |
| `SOURCE_MONGO_URI` | — | Remote source cluster URI (read-only) |
| `SOURCE_MONGO_DB` | `driverbookv2_stage` | Source database name |
| `SOURCE_COLLECTION` | `driverdiagnostics` | Source collection name |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used for KB enrichment |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://localhost:3000` | CORS allowed origins |
