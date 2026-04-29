# api.py
# FastAPI entry point for the AI-powered vehicle diagnostics service.

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from db.connection import get_db
from core.knowledge_base import seed_knowledge_base
from orchestration.diagnostic_graph import build_graph

app = FastAPI(title="DriverBook Diagnostics API", version="1.0.0")

# ── Startup ───────────────────────────────────────────────────────────────────

db = get_db()
seeded = seed_knowledge_base(db)
if seeded:
    print(f"[startup] Knowledge base seeded with {seeded} entries.")
else:
    print("[startup] Knowledge base already populated — skipping seed.")

graph = build_graph(db)
print("[startup] Diagnostic graph compiled.")


# ── Request / Response models ─────────────────────────────────────────────────

class FaultRequest(BaseModel):
    vehicleId: str
    dtcJson: dict
    telemetry: dict = {}


class FaultResponse(BaseModel):
    vehicleId: str
    diagnostics: list[dict]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/analyze-fault", response_model=FaultResponse)
def analyze_fault(request: FaultRequest):
    """Run the full diagnostic pipeline for a vehicle's DTC payload.

    - Parses fault codes from dtcJson
    - Looks each code up in the knowledge base
    - Adjusts severity using live telemetry signals
    - Calls the LLM for interpretation
    - Auto-saves any unknown codes for the review queue
    """
    try:
        result = graph.invoke(
            {
                "raw_input": {
                    "vehicleId": request.vehicleId,
                    "dtcJson": request.dtcJson,
                    "telemetry": request.telemetry,
                },
                "parsed_faults": [],
                "diagnostics": [],
                "unknown_codes": [],
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return FaultResponse(
        vehicleId=request.vehicleId,
        diagnostics=result.get("diagnostics", []),
    )


@app.get("/knowledge-base")
def get_knowledge_base():
    """List all entries in the knowledge base (admin / inspection endpoint)."""
    entries = list(db["knowledge_base"].find({}, {"_id": 0}))
    return {"count": len(entries), "entries": entries}


@app.get("/unknown-faults")
def get_unknown_faults():
    """List all unresolved unknown fault codes captured by the auto-learning pipeline."""
    faults = list(
        db["unknown_faults"].find({"status": "unresolved"}, {"_id": 0}).sort("occurrence_count", -1)
    )
    return {"count": len(faults), "faults": faults}
