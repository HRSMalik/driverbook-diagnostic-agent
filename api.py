# api.py
# FastAPI entry point for the AI-powered vehicle diagnostics service.

import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import DefaultDict

import requests as http_client
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import settings
from core.knowledge_base import lookup, reload_knowledge_base, seed_knowledge_base
from orchestration.diagnostic_graph import (
    _diag_from_kb,
    _diag_placeholder,
    enrich_unknown_codes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="DriverBook Diagnostics API", version="1.2.0")

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
                return JSONResponse(status_code=503, content={"detail": "API key not configured on server."})
            if request.headers.get("X-API-Key") != settings.API_KEY:
                return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key."})
        return await call_next(request)


app.add_middleware(APIKeyMiddleware)

_rate_buckets: DefaultDict[str, deque] = defaultdict(deque)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter keyed by X-API-Key.

    Counts requests in a rolling 60-second window per caller.
    Exempt paths (/health, /ready) are not counted or blocked.
    """
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _OPEN_PATHS:
            return await call_next(request)

        key = request.headers.get("X-API-Key", "anonymous")
        now = time.monotonic()
        window_start = now - 60.0
        bucket = _rate_buckets[key]

        while bucket and bucket[0] < window_start:
            bucket.popleft()

        if len(bucket) >= settings.RATE_LIMIT_PER_MINUTE:
            retry_after = int(60 - (now - bucket[0]))
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Try again in {retry_after}s."},
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)
        return await call_next(request)


app.add_middleware(RateLimitMiddleware)

# ── Startup ───────────────────────────────────────────────────────────────────

kb_count = seed_knowledge_base()
logger.info("Knowledge base loaded: %d entries.", kb_count)


# ── Helpers ──────────────────────────────────────────────────────────────────

_OBJECT_ID_RE = re.compile(r"^[a-f0-9]{24}$", re.IGNORECASE)

def _validate_object_id(value: str, label: str) -> str:
    if not _OBJECT_ID_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {value}")
    return value


# ── Health & Readiness ────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    """Liveness check — confirms the process is up and the API is reachable."""
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    """Readiness check — confirms OpenAI is reachable.

    Returns 200 when OpenAI is reachable.
    Returns 503 with detail when it is not.
    """
    result: dict = {"openai": "ok"}

    try:
        resp = http_client.get(
            "https://api.openai.com/v1/models",
            timeout=5,
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY or ''}"},
        )
        if resp.status_code == 401:
            result["openai"] = "invalid API key"
            raise HTTPException(status_code=503, detail=result)
        elif resp.status_code != 200:
            result["openai"] = f"unexpected status {resp.status_code}"
            raise HTTPException(status_code=503, detail=result)
    except HTTPException:
        raise
    except Exception as exc:
        result["openai"] = f"unreachable: {exc}"
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

    kb_entry = lookup(code_norm)
    if kb_entry is None:
        enrich_unknown_codes([fault])
        kb_entry = lookup(code_norm)

    if kb_entry is None:
        return _diag_placeholder(fault)

    return _diag_from_kb(fault, kb_entry, {})


@app.get("/knowledge-base")
def get_knowledge_base() -> dict:
    """List all entries in the knowledge base (admin / inspection endpoint).

    Returns:
        dict: count and list of all KB entries.
    """
    from core.knowledge_base import _KB
    entries = list(_KB.values())
    return {"count": len(entries), "entries": entries}


@app.post("/admin/reload-kb")
def admin_reload_kb() -> dict:
    """Force reload of seed_kb.json into memory without restarting the server.

    Returns:
        dict: count of entries loaded after reload.
    """
    count = reload_knowledge_base()
    logger.info("KB manually reloaded: %d entries.", count)
    return {"reloaded": True, "count": count}
