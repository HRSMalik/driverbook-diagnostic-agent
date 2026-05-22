# api.py
# FastAPI entry point for the AI-powered vehicle diagnostics service.

import logging
import re
from datetime import datetime, timezone

import requests as http_client
from bson import ObjectId
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import settings
from core.knowledge_base import lookup, seed_knowledge_base
from db.connection import get_db
from orchestration.diagnostic_graph import (
    _diag_from_kb,
    _diag_placeholder,
    enrich_unknown_codes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="DriverBook Diagnostics API", version="1.1.0")

_allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_OPEN_PATHS = {"/health", "/ready"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Reject requests missing a valid X-API-Key header.

    /health and /ready are exempt so container orchestrators can probe
    without needing the key.
    """
    async def dispatch(self, request: Request, call_next):
        if request.url.path not in _OPEN_PATHS:
            if not settings.API_KEY:
                # Key not configured — block all protected traffic until set
                return JSONResponse(status_code=503, content={"detail": "API key not configured on server."})
            if request.headers.get("X-API-Key") != settings.API_KEY:
                return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key."})
        return await call_next(request)


app.add_middleware(APIKeyMiddleware)

# ── Startup ───────────────────────────────────────────────────────────────────

db = get_db(settings.MONGO_DB)
seeded = seed_knowledge_base(db)
if seeded:
    logger.info("Knowledge base seeded with %d entries.", seeded)
else:
    logger.info("Knowledge base already populated — skipping seed.")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _validate_object_id(value: str, label: str) -> str:
    try:
        ObjectId(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {value}")
    return value


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
        resp = http_client.get(
            "https://api.openai.com/v1/models",
            timeout=5,
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY or ''}"},
        )
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

@app.get("/vehicles/faults/diagnose")
def diagnose_fault(
    vehicle_id: str = Query(..., description="MongoDB ObjectId string for the vehicle."),
    code: str = Query(..., min_length=1, description="Fault code, e.g. 'SPN 521031'."),
    ecu: str = Query("", description="ECU name reporting the fault."),
    desc: str = Query("", description="Raw fault description (used to extract FMI)."),
) -> dict:
    """Diagnose a single fault from caller-supplied query parameters.

    KB-first: returns instantly if the code is in the KB, otherwise calls LLM once,
    saves to KB permanently, then returns the diagnostic. All future calls on the
    same code are instant.
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


@app.get("/knowledge-base")
def get_knowledge_base() -> dict:
    """List all entries in the knowledge base (admin / inspection endpoint).

    Returns:
        dict: count and list of all KB entries.
    """
    entries = list(db["knowledge_base"].find({}, {"_id": 0}))
    return {"count": len(entries), "entries": entries}
