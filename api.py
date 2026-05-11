# api.py
# FastAPI entry point for the AI-powered vehicle diagnostics service.

import os

from bson import ObjectId
from fastapi import FastAPI, HTTPException

from core.knowledge_base import seed_knowledge_base
from db.connection import get_db
from db.fault_vehicles import ensure_fault_vehicles_collection, mark_analyzed
from orchestration.diagnostic_graph import build_graph

app = FastAPI(title="DriverBook Diagnostics API", version="1.1.0")

# ── Startup ───────────────────────────────────────────────────────────────────

database = os.getenv("MONGO_DB", "diagnostics")
db = get_db(database)
seeded = seed_knowledge_base(db)
if seeded:
    print(f"[startup] Knowledge base seeded with {seeded} entries.")
else:
    print("[startup] Knowledge base already populated — skipping seed.")
ensure_fault_vehicles_collection(db)
graph = build_graph(db)
print("[startup] Diagnostic graph compiled.")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _validate_object_id(value: str, label: str) -> str:
    try:
        ObjectId(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {value}")
    return value


def _latest_staged(vehicle_id: str) -> dict | None:
    return db["fault_vehicles"].find_one(
        {"vehicleId": vehicle_id},
        sort=[("staged_at", -1)],
    )


def _diagnostics_for_source(source_id: str) -> list[dict]:
    return list(
        db["diagnostics_output"].find({"source_id": source_id}, {"_id": 0})
    )


def _run_graph(staged: dict) -> list[dict]:
    raw_input = staged.get("raw_input") or {}
    raw_input = {**raw_input, "source_id": staged["source_id"]}
    result = graph.invoke(
        {
            "raw_input": raw_input,
            "parsed_faults": [],
            "diagnostics": [],
            "unknown_codes": [],
        }
    )
    mark_analyzed(db, staged["source_id"])
    return result.get("diagnostics", [])


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/tenants/{tenant_id}/vehicles")
def list_tenant_vehicles(tenant_id: str):
    """List every staged vehicle under a tenant with its diagnostics inline.

    - Reads from the local ``fault_vehicles`` collection (data must already be staged
      by the batch scan or a prior call).
    - For each vehicle the latest staged document is used.
    - Cached diagnostics from ``diagnostics_output`` are included inline.
    - For staged vehicles that have not been analyzed yet, the graph is run once
      and the result is included in the same response.
    """
    _validate_object_id(tenant_id, "tenantId")

    pipeline = [
        {"$match": {"tenantId": tenant_id}},
        {"$sort": {"staged_at": -1}},
        {
            "$group": {
                "_id": "$vehicleId",
                "latest_source_id": {"$first": "$source_id"},
                "latest_staged_at": {"$first": "$staged_at"},
                "latest_timestamp": {"$first": "$timestamp"},
                "fault_count": {"$first": "$fault_count"},
                "analyzed": {"$first": "$analyzed"},
                "doc_count": {"$sum": 1},
            }
        },
        {"$sort": {"fault_count": -1}},
    ]
    grouped = list(db["fault_vehicles"].aggregate(pipeline))

    vehicles = []
    for row in grouped:
        vehicle_id = row["_id"]
        source_id = row["latest_source_id"]
        diagnostics = _diagnostics_for_source(source_id)

        if not diagnostics or not row.get("analyzed"):
            staged = _latest_staged(vehicle_id)
            if staged:
                diagnostics = _run_graph(staged)

        vehicles.append(
            {
                "vehicleId": vehicle_id,
                "source_id": source_id,
                "fault_count": row["fault_count"],
                "doc_count": row["doc_count"],
                "staged_at": row["latest_staged_at"],
                "timestamp": row["latest_timestamp"],
                "analyzed": True if diagnostics else False,
                "diagnostics": diagnostics,
            }
        )

    return {
        "tenantId": tenant_id,
        "count": len(vehicles),
        "vehicles": vehicles,
    }


@app.post("/vehicles/{vehicle_id}/reanalyze")
def reanalyze_vehicle(vehicle_id: str):
    """Force the diagnostic graph to re-run on the latest staged document for a vehicle.

    Use this after editing ``seed_kb.json`` or when the cached diagnostics are stale.
    Returns the fresh diagnostics list.
    """
    _validate_object_id(vehicle_id, "vehicleId")
    staged = _latest_staged(vehicle_id)
    if staged is None:
        raise HTTPException(
            status_code=404,
            detail=f"No staged document for vehicleId {vehicle_id} — run the batch scan first.",
        )

    diagnostics = _run_graph(staged)
    return {
        "vehicleId": vehicle_id,
        "source_id": staged["source_id"],
        "diagnostics": diagnostics,
    }


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
