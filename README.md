# DriverBook Diagnostics Agent

AI-powered vehicle fault code analysis service for commercial fleet management. Parses J1939 SPN and OBD-II DTC codes, grounds diagnoses in a local knowledge base, and uses OpenAI to enrich unknown codes — producing plain-language diagnostics and resolution steps for fleet managers.

---

## How It Works

On-click KB-first pipeline. When a fault is clicked in the frontend, the main backend calls this service:

```text
lookup code in KB
    ├── found → return full diagnostic instantly (no LLM)
    └── not found → call OpenAI once → save to KB → return diagnostic
                    (all future clicks on this code are instant)
```

| Situation | What happens |
| --- | --- |
| Code already in KB | Full diagnostic returned instantly, no LLM |
| New unknown code | OpenAI called once (~3–6s), result saved to KB permanently |
| Same unknown code again | Already in KB — instant |

---

## Authentication Flow

This service sits **behind the main backend** — it is never called directly by the frontend.

```text
Tenant (browser)
    │  logs in with their credentials
    ▼
Main Backend  ←── handles all user auth, sessions, tenant scoping
    │  verifies user owns the vehicle
    │  sends X-API-Key header on every request
    ▼
Diagnostic API  ←── this service
```

Key points:

- The `API_KEY` lives only in the main backend's `.env` — tenants never see it
- Tenants authenticate with the main backend using their own credentials (JWT / session)
- The main backend enforces tenant scoping (vehicle belongs to tenant?) before calling here
- This service trusts that any request with a valid `X-API-Key` has already been authorized

**Protected endpoints** require `X-API-Key: <value>` header.

**Exempt endpoints** (no key needed): `GET /health`, `GET /ready`

---

## Setup

### Prerequisites

- Python 3.11+
- MongoDB running locally or accessible via URI
- OpenAI API key

### Install

```bash
cd diagnostic_agent

python3.11 -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt

cp .env.example .env
# Edit .env — set MONGO_URI, OPENAI_API_KEY, API_KEY
```

The knowledge base is seeded automatically on first startup (50 codes from `knowledge_base/seed_kb.json`).

### Run

```bash
uvicorn api:app --port 8000
```

### Docker

```bash
docker build -t driverbook-diagnostics .
docker run -p 8000:8000 --env-file .env driverbook-diagnostics
```

> Use `host.docker.internal` instead of `localhost` in `.env` when running on Docker Desktop.

---

## API

All endpoints except `/health` and `/ready` require:

```text
X-API-Key: <your-api-key>
```

---

### `GET /vehicles/faults/diagnose`

Call when the user clicks the diagnose button on a fault. Returns full plain-language diagnostic.

```text
GET /vehicles/faults/diagnose?vehicle_id={id}&code={fault_code}&ecu={ecu}
```

| Param | Required | Description |
| --- | --- | --- |
| `vehicle_id` | Yes | Vehicle MongoDB ObjectId |
| `code` | Yes | Fault code, e.g. `SPN 520203` (URL-encode spaces) |
| `ecu` | No | ECU name reporting the fault |
| `desc` | No | Raw fault description (used to extract FMI) |

```bash
curl -H "X-API-Key: your-key" \
  "http://localhost:8000/vehicles/faults/diagnose?vehicle_id=69dbf13d3ee6a4f020506c7f&code=SPN%20520203&ecu=Cab%20Controller%20-%20Primary"
```

#### Response — known fault (instant)

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
  "explanation": "The cab controller is detecting an excessively high voltage supply, which could cause damage to the electrical system.",
  "resolution_steps": [
    "Check alternator output voltage with a multimeter",
    "Inspect wiring for any signs of wear or damage"
  ],
  "who_can_fix": "Fleet maintenance team|Certified technician required",
  "parts_likely_needed": ["Alternator"],
  "from_kb": true
}
```

Field notes:

- `severity` — `Low` | `Medium` | `High` | `Critical`
- `urgency` — `Ignore` | `Monitor` | `Schedule Maintenance` | `Immediate Action`
- `is_unknown` — `true` only when LLM enrichment failed; show a retry state
- `from_kb` — always `true` once successfully diagnosed
- First call on a new unknown fault may take 3–6 seconds. Show a loading spinner. All future clicks return instantly.

---

### `GET /knowledge-base`

List all entries in the knowledge base. Admin / inspection use only.

```bash
curl -H "X-API-Key: your-key" http://localhost:8000/knowledge-base
```

---

### `GET /health`

Liveness check. No auth required.

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

### `GET /ready`

Readiness check — confirms MongoDB and OpenAI are reachable. No auth required.

```bash
curl http://localhost:8000/ready
# {"mongo": "ok", "openai": "ok"}
```

---

## Project Structure

```text
diagnostic_agent/
├── api.py                      # FastAPI entry point — endpoints + API key middleware
├── requirements.txt
├── Dockerfile
├── .env.example
├── config/
│   └── settings.py             # Settings singleton — all env vars sourced here
├── core/
│   ├── knowledge_base.py       # KB seed, lookup, auto-learn from LLM output
│   └── telemetry_context.py    # Severity escalation rules
├── db/
│   └── connection.py           # Cached MongoClient per URI
├── llm/
│   ├── llm_client.py           # ChatOpenAI factory
│   ├── prompts.py              # KB enrichment prompt templates
│   └── parsers.py              # JSON extraction helpers
├── orchestration/
│   └── diagnostic_graph.py     # _diag_from_kb, _diag_placeholder, enrich_unknown_codes
└── knowledge_base/
    └── seed_kb.json            # 50-code seed KB (loaded on first startup)
```

---

## MongoDB Collections

| Collection | Purpose |
| --- | --- |
| `knowledge_base` | Known fault code definitions — seeded + OpenAI auto-learned |

---

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used for KB enrichment |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://localhost:3000` | CORS allowed origins |
| `API_KEY` | — | Shared secret; main backend sends this as `X-API-Key` header |
| `RATE_LIMIT_PER_MINUTE` | `60` | Max requests per 60-second window per API key |
