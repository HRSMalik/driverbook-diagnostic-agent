# api.py
# FastAPI entry point for the AI-powered vehicle diagnostics service.

import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

import requests as http_client
from bson import ObjectId
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.datascanpipeline import run_data_scan_pipeline
from core.knowledge_base import lookup, seed_knowledge_base
from db.connection import get_db
from db.fault_vehicles import ensure_fault_vehicles_collection, mark_analyzed
from orchestration.diagnostic_graph import (
    _diag_from_kb,
    _diag_placeholder,
    build_graph,
    enrich_unknown_codes,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="DriverBook Diagnostics API", version="1.1.0")

_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup ───────────────────────────────────────────────────────────────────

database = os.getenv("MONGO_DB", "diagnostics")
db = get_db(database)
seeded = seed_knowledge_base(db)
if seeded:
    logger.info("Knowledge base seeded with %d entries.", seeded)
else:
    logger.info("Knowledge base already populated — skipping seed.")
ensure_fault_vehicles_collection(db)
graph = build_graph(db)
logger.info("Diagnostic graph compiled.")


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


# ── Health & Readiness ────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    """Liveness check — confirms the process is up and the API is reachable."""
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    """Readiness check — confirms Mongo and OpenAI are reachable.

    Returns 200 with a per-dependency status breakdown when all pass.
    Returns 503 with the breakdown when any dependency is unreachable.
    """
    result: dict = {"mongo": "ok", "openai": "ok"}
    failed = False

    try:
        db.client.admin.command("ping")
    except Exception as exc:
        result["mongo"] = f"unreachable: {exc}"
        failed = True

    try:
        resp = http_client.get("https://api.openai.com/v1/models", timeout=5, headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}"})
        if resp.status_code == 401:
            result["openai"] = "invalid API key"
            failed = True
        elif resp.status_code != 200:
            result["openai"] = f"unexpected status {resp.status_code}"
            failed = True
    except Exception as exc:
        result["openai"] = f"unreachable: {exc}"
        failed = True

    if failed:
        raise HTTPException(status_code=503, detail=result)
    return result


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/tenants/{tenant_id}/vehicles")
def list_tenant_vehicles(tenant_id: str) -> dict:
    """List every staged vehicle under a tenant with its diagnostics inline.

    Args:
        tenant_id: MongoDB ObjectId string for the tenant.

    Returns:
        dict: tenantId, count, and list of vehicles with diagnostics.
    """
    _validate_object_id(tenant_id, "tenantId")

    def _scan():
        try:
            run_data_scan_pipeline(query={"tenantId": tenant_id})
        except Exception as exc:
            logger.warning("Background scan error for tenant %s: %s", tenant_id, exc)

    threading.Thread(target=_scan, daemon=True).start()

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
                "telemetry": {"$first": "$raw_input.telemetry"},
            }
        },
        {"$sort": {"fault_count": -1}},
    ]
    grouped = list(db["fault_vehicles"].aggregate(pipeline))

    # Fetch all diagnostics for the tenant in one query, group by source_id in memory
    source_ids = [row["latest_source_id"] for row in grouped]
    all_diags_cursor = db["diagnostics_output"].find(
        {"source_id": {"$in": source_ids}}, {"_id": 0}
    )
    diags_by_source: dict[str, list] = {}
    for d in all_diags_cursor:
        diags_by_source.setdefault(d["source_id"], []).append(d)

    vehicles = []
    for row in grouped:
        source_id = row["latest_source_id"]
        diagnostics = diags_by_source.get(source_id, [])
        vehicles.append(
            {
                "vehicleId": row["_id"],
                "source_id": source_id,
                "fault_count": row["fault_count"],
                "doc_count": row["doc_count"],
                "staged_at": row["latest_staged_at"],
                "timestamp": row["latest_timestamp"],
                "analyzed": bool(diagnostics),
                "telemetry": row.get("telemetry") or {},
                "diagnostics": diagnostics,
            }
        )

    return {"tenantId": tenant_id, "count": len(vehicles), "vehicles": vehicles}


@app.get("/vehicles/faults/diagnose")
def diagnose_fault(
    vehicle_id: str = Query(..., description="MongoDB ObjectId string for the vehicle."),
    code: str = Query(..., min_length=1, description="Fault code, e.g. 'SPN 521031'."),
    ecu: str = Query("", description="ECU name reporting the fault."),
    desc: str = Query("", description="Raw fault description (used to extract FMI)."),
) -> dict:
    """Diagnose a single fault from caller-supplied query parameters.

    No staged-document lookup. KB-first: returns instantly if the code is in the KB,
    otherwise calls Flow 2 LLM enrichment once, saves to KB, then returns the diagnostic.
    Telemetry-based severity escalation is skipped (no staged telemetry source).
    """
    _validate_object_id(vehicle_id, "vehicle_id")
    code_norm = code.strip().upper()
    if not code_norm:
        raise HTTPException(status_code=400, detail="code is required.")

    fmi_match = re.search(r"FMI\s+(\d+)", desc or "", re.IGNORECASE)
    fault = {
        "code": code_norm,
        "ecu": (ecu or "").strip(),
        "fmi": int(fmi_match.group(1)) if fmi_match else None,
        "description": (desc or "").strip(),
        "vehicleId": vehicle_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mil": False,
    }

    kb_entry = lookup(db, code_norm)
    if kb_entry is None:
        enrich_unknown_codes(db, [fault])
        kb_entry = lookup(db, code_norm)

    if kb_entry is None:
        return _diag_placeholder(fault)

    return _diag_from_kb(fault, kb_entry, {})


@app.post("/vehicles/{vehicle_id}/reanalyze")
def reanalyze_vehicle(vehicle_id: str) -> dict:
    """Force the diagnostic graph to re-run on the latest staged document for a vehicle.

    Args:
        vehicle_id: MongoDB ObjectId string for the vehicle.

    Returns:
        dict: vehicleId, source_id, and fresh diagnostics list.

    Raises:
        HTTPException: 404 if no staged document found for this vehicle.
    """
    _validate_object_id(vehicle_id, "vehicleId")
    staged = _latest_staged(vehicle_id)
    if staged is None:
        raise HTTPException(
            status_code=404,
            detail=f"No staged document for vehicleId {vehicle_id} — run the batch scan first.",
        )

    diagnostics = _run_graph(staged)
    return {"vehicleId": vehicle_id, "source_id": staged["source_id"], "diagnostics": diagnostics}


@app.get("/knowledge-base")
def get_knowledge_base() -> dict:
    """List all entries in the knowledge base (admin / inspection endpoint).

    Returns:
        dict: count and list of all KB entries.
    """
    entries = list(db["knowledge_base"].find({}, {"_id": 0}))
    return {"count": len(entries), "entries": entries}


@app.get("/unknown-faults")
def get_unknown_faults() -> dict:
    """List all unresolved unknown fault codes captured by the auto-learning pipeline.

    Returns:
        dict: count and list of unresolved faults sorted by occurrence_count descending.
    """
    faults = list(
        db["unknown_faults"].find({"status": "unresolved"}, {"_id": 0}).sort("occurrence_count", -1)
    )
    return {"count": len(faults), "faults": faults}


@app.get("/tenants")
def list_tenants() -> dict:
    """Return all staged tenants with names from the tenant_names collection.

    Returns:
        dict: count and list of {tenantId, name} objects sorted by name.
    """
    tenant_ids = db["fault_vehicles"].distinct("tenantId")
    tenant_ids = [t for t in tenant_ids if t]
    name_docs = {
        d["tenantId"]: d.get("name", d["tenantId"])
        for d in db["tenant_names"].find({"tenantId": {"$in": tenant_ids}}, {"_id": 0})
    }
    tenants = sorted(
        [{"tenantId": tid, "name": name_docs.get(tid, tid)} for tid in tenant_ids],
        key=lambda x: x["name"].lower(),
    )
    return {"count": len(tenants), "tenants": tenants}


class ScanRequest(BaseModel):
    limit: int | None = None
    skip: int = 0
    batch_size: int = 100
    reanalyze: bool = False
    query: dict[str, Any] | None = None


@app.post("/scan")
def trigger_full_scan(body: ScanRequest = ScanRequest()) -> dict:
    """Kick off a full source-collection scan in a background thread.

    Scans every document in the configured source collection that contains DTC records,
    stages new documents, runs Flow 1 (KB lookup), then Flow 2 (LLM enrichment for
    unknown codes). Returns immediately — progress is visible on next tenant/vehicle fetch.

    Args:
        body: Optional scan parameters (limit, skip, batch_size, reanalyze, query).

    Returns:
        dict: Confirmation that the scan was started.
    """
    def _scan() -> None:
        try:
            result = run_data_scan_pipeline(
                query=body.query,
                skip=body.skip,
                limit=body.limit,
                batch_size=body.batch_size,
                reanalyze=body.reanalyze,
            )
            logger.info(
                "Full scan complete — scanned=%d staged=%d flow1=%d flow2_enriched=%d",
                result["scanned"], result["staged_new"],
                result["flow1_analyzed"], result["flow2_enriched"],
            )
        except Exception as exc:
            logger.error("Full scan failed: %s", exc)

    threading.Thread(target=_scan, daemon=True).start()
    return {
        "status": "scan_started",
        "message": "Background scan triggered. KB will be enriched as unknown codes are found.",
        "params": {
            "limit": body.limit,
            "skip": body.skip,
            "batch_size": body.batch_size,
            "reanalyze": body.reanalyze,
        },
    }
