# DriverBook Diagnostics Agent

AI-powered vehicle fault code analysis service for commercial fleet management. Parses J1939 SPN and OBD-II DTC codes, grounds diagnoses in a knowledge base, and uses a local LLM to produce plain-language diagnostics and resolution steps for fleet managers.

## Architecture

```
POST /analyze-fault
        │
        ▼
   parse_node          ← Extracts structured faults from raw dtcJson
        │
        ▼
  kb_lookup_node       ← Looks up each code in MongoDB knowledge base
        │
        ▼
  telemetry_node       ← Adjusts severity using live vehicle signals
        │
        ▼
    llm_node           ← Diagnostic agent: issue / severity / urgency
        │
        ▼
  explain_node         ← Explainability agent: root cause + resolution steps
        │
        ▼
   store_node          ← Persists results; auto-learns unknown codes into KB
        │
        ▼
     Response
```

**Two-agent LLM pattern:**
- **Diagnostic agent** — interprets the fault: what's wrong, severity, urgency
- **Explainability agent** — explains root cause in plain English and provides step-by-step resolution instructions

**Auto-learning:** Unknown codes are saved to the `unknown_faults` review queue, and the LLM's output is written back to the knowledge base so the same code is grounded on its next occurrence.

## Prerequisites

- Python 3.11+
- [MongoDB](https://www.mongodb.com/docs/manual/installation/) running locally or accessible via URI
- [Ollama](https://ollama.com) with `llama3.1` pulled

```bash
ollama pull llama3.1
ollama serve
```

## Setup

```bash
# 1. Clone and enter the directory
cd diagnostic_agent

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env if your MongoDB URI or Ollama URL differ from defaults

# 5. Start the API
uvicorn api:app --reload --port 8000
```

The knowledge base is seeded automatically on first startup from `knowledge_base/seed_kb.json`.

## Docker

```bash
docker build -t driverbook-diagnostics .
docker run -p 8000:8000 --env-file .env driverbook-diagnostics
```

> Ollama and MongoDB must be accessible from inside the container. Use `host.docker.internal` instead of `localhost` in `.env` when running on Docker Desktop.

## API

### `POST /analyze-fault`

Run the full diagnostic pipeline for a vehicle's fault payload.

**Request**
```json
{
  "vehicleId": "TRUCK-001",
  "dtcJson": {
    "dtcs": {
      "SPN 521133": { "ecu": "Engine #2", "desc": "FMI 13 Out of Calibration" }
    }
  },
  "telemetry": {
    "engineCoolantTemperature": 98,
    "engineOilPressure": 35,
    "speed": 0,
    "fuelLevel": 72,
    "defLevel": 80,
    "engineSpeed": 750
  }
}
```

**Response**
```json
{
  "vehicleId": "TRUCK-001",
  "diagnostics": [
    {
      "code": "SPN 521133",
      "ecu": "Engine #2",
      "fmi": 13,
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
  ]
}
```

### `GET /knowledge-base`

List all knowledge base entries (admin/inspection).

### `GET /unknown-faults`

List unresolved unknown fault codes captured by the auto-learning pipeline, sorted by occurrence count.

## Project Structure

```
diagnostic_agent/
├── api.py                          # FastAPI entry point
├── requirements.txt
├── Dockerfile
├── .env.example
├── core/
│   ├── dtc_parser.py               # Parse raw dtcJson into structured fault dicts
│   ├── knowledge_base.py           # KB lookup, seed, auto-learn, occurrence tracking
│   └── telemetry_context.py        # Telemetry signal extraction and severity escalation
├── db/
│   ├── connection.py               # MongoDB singleton
│   └── unknown_faults.py           # Unknown fault upsert logic
├── llm/
│   ├── hf_client.py                # Ollama LLM client
│   └── prompts.py                  # Diagnostic and explainability prompt templates
├── orchestration/
│   └── diagnostic_graph.py         # LanGraph DAG definition
└── knowledge_base/
    └── seed_kb.json                # 50-code seed knowledge base
```

## MongoDB Collections

| Collection | Purpose |
|---|---|
| `knowledge_base` | Known fault code definitions — seeded + auto-learned |
| `unknown_faults` | Codes not found in KB; review queue for new codes |
| `diagnostics_output` | Full LLM diagnostic + explanation output per request |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | `llama3.1` | Model name to use for both agents |
