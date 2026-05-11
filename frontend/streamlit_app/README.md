# DriverBook Diagnostics Frontend (Streamlit)

Minimal web interface for the DriverBook Diagnostics API. Enter a `vehicleId` and the latest DTC-bearing source document is fetched, staged, and analyzed.

## Setup

```bash
pip install -r ../../requirements.txt          # backend + frontend deps live in the same file
```

## Run

```bash
streamlit run frontend/streamlit_app/app.py
```

Opens at `http://localhost:8501`.

## Configuration

| Env var | Default | Description |
|---|---|---|
| `API_URL` | `http://localhost:8000` | Base URL of the diagnostics API |

## Prerequisites

- API running: `uvicorn api:app --reload --port 8000`
- MongoDB accessible (both local app DB and remote source DB)
- Ollama running on `OLLAMA_BASE_URL` (only needed when KB-miss faults trigger LLM enrichment)

## What the UI does

1. Takes a `vehicleId` (Mongo ObjectId of the vehicle in the source collection).
2. Optional `reanalyze` checkbox to force the LangGraph pipeline to re-run instead of returning cached `diagnostics_output`.
3. POSTs to `/analyze-vehicle/{vehicle_id}`.
4. Renders metrics (`newly_staged`, `reanalyzed`, diagnostic count) and an expandable per-fault breakdown (severity, urgency, confidence, KB origin, issue, explanation, resolution steps, parts, downtime).
