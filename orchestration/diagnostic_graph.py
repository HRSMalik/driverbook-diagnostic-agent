# orchestration/diagnostic_graph.py
# Two-flow diagnostic pipeline:
#   Flow 1 (diagnose_graph): parse → kb_lookup → diagnose → store  — fast, KB-only
#   Flow 2 (enrich_unknown_codes): LLM once per unknown code → save to KB

import json
import re
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from pymongo.database import Database

from core.dtc_parser import parse_dtc_records
from core.knowledge_base import auto_learn_from_diagnosis, increment_occurrence, lookup
from core.telemetry_context import adjust_severity, build_telemetry_snapshot
from db.diagnostics_output import save_diagnostics
from db.unknown_faults import save_unknown_fault
from llm.hf_client import get_llm
from llm.prompts import KB_ENRICH_HUMAN_PROMPT, KB_ENRICH_SYSTEM_PROMPT
from llm.parsers import invoke_and_parse


class DiagnosticState(TypedDict):
    raw_input: dict[str, Any]    # {vehicleId, dtcJson, telemetry, source_id}
    parsed_faults: list[dict]    # structured faults, annotated as they flow
    diagnostics: list[dict]      # final per-fault results
    unknown_codes: list[str]     # codes not in KB after kb_lookup


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_json_obj(raw_text: str) -> dict:
    clean = re.sub(r"\s+", " ", raw_text).strip()
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _diag_from_kb(fault: dict[str, Any], kb: dict[str, Any], telemetry: dict[str, Any]) -> dict[str, Any]:
    """Build a full diagnostic record from a KB entry, escalating severity with live telemetry."""
    base_severity = kb.get("severity", "Low")
    adjusted = adjust_severity(base_severity, fault, telemetry)
    diag = {
        "code": fault["code"],
        "ecu": fault.get("ecu", ""),
        "fmi": fault.get("fmi"),
        "vehicleId": fault.get("vehicleId", ""),
        "timestamp": fault.get("timestamp", ""),
        "is_unknown": False,
        "purpose": kb.get("meaning", ""),
        "issue": kb.get("meaning", ""),
        "impact": ", ".join(kb.get("causes") or []),
        "severity": adjusted,
        "urgency": kb.get("urgency", "Monitor"),
        "confidence": 100,
        "explanation": kb.get("explanation", ""),
        "resolution_steps": kb.get("resolution_steps") or [],
        "who_can_fix": kb.get("who_can_fix", "Fleet maintenance team"),
        "parts_likely_needed": kb.get("parts_likely_needed") or [],
        "estimated_downtime": kb.get("estimated_downtime", "Unknown"),
        "from_kb": True,
    }
    if adjusted != base_severity:
        diag["severity_escalated"] = True
        diag["base_severity"] = base_severity
    return diag


def _diag_placeholder(fault: dict[str, Any]) -> dict[str, Any]:
    """Placeholder diagnostic for an unknown code — shown until Flow 2 enriches it."""
    return {
        "code": fault["code"],
        "ecu": fault.get("ecu", ""),
        "fmi": fault.get("fmi"),
        "vehicleId": fault.get("vehicleId", ""),
        "timestamp": fault.get("timestamp", ""),
        "is_unknown": True,
        "purpose": fault.get("description", ""),
        "issue": "Unknown fault code — explanation pending.",
        "impact": "Unknown",
        "severity": "Low",
        "urgency": "Monitor",
        "confidence": 0,
        "explanation": "",
        "resolution_steps": [],
        "who_can_fix": "Certified technician required",
        "parts_likely_needed": [],
        "estimated_downtime": "Unknown",
        "from_kb": False,
    }


# ── Flow 1 nodes ──────────────────────────────────────────────────────────────

def parse_node(state: DiagnosticState) -> DiagnosticState:
    raw = state["raw_input"]
    faults = parse_dtc_records(
        dtc_records=raw.get("dtcJson", {}),
        vehicle_id=raw.get("vehicleId", ""),
    )
    return {**state, "parsed_faults": faults}


def kb_lookup_node(state: DiagnosticState, db: Database) -> DiagnosticState:
    enriched = []
    unknown_codes = []
    for fault in state["parsed_faults"]:
        kb_entry = lookup(db, fault["code"])
        fault = {**fault, "kb_entry": kb_entry, "is_unknown": kb_entry is None}
        if kb_entry is None:
            unknown_codes.append(fault["code"])
        enriched.append(fault)
    return {**state, "parsed_faults": enriched, "unknown_codes": unknown_codes}


def diagnose_node(state: DiagnosticState) -> DiagnosticState:
    """Build diagnostics from KB for known codes; placeholders for unknowns.

    Live telemetry signals from raw_input are used to escalate severity for known codes
    when vehicle conditions exceed safe thresholds (coolant temp, oil pressure, DEF level).
    """
    telemetry = build_telemetry_snapshot(state["raw_input"].get("telemetry") or {})
    diagnostics = []
    for fault in state["parsed_faults"]:
        kb = fault.get("kb_entry")
        diagnostics.append(_diag_from_kb(fault, kb, telemetry) if kb else _diag_placeholder(fault))
    return {**state, "diagnostics": diagnostics}


def store_node(state: DiagnosticState, db: Database) -> DiagnosticState:
    """Persist unknown faults and diagnostics output."""
    for fault in state["parsed_faults"]:
        if fault.get("is_unknown"):
            save_unknown_fault(db, fault, {})
        else:
            increment_occurrence(db, fault["code"])
    save_diagnostics(db, state["diagnostics"], state["raw_input"].get("source_id"))
    return state


# ── Flow 1 graph builder ──────────────────────────────────────────────────────

def build_graph(db: Database) -> Any:
    """Flow 1: fast KB-based diagnosis. Unknown codes get placeholders.

    Args:
        db: MongoDB database handle.

    Returns:
        Compiled LangGraph app (parse → kb_lookup → diagnose → store).
    """
    graph = StateGraph(DiagnosticState)

    graph.add_node("parse", parse_node)
    graph.add_node("kb_lookup", lambda s: kb_lookup_node(s, db))
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("store", lambda s: store_node(s, db))

    graph.set_entry_point("parse")
    graph.add_edge("parse", "kb_lookup")
    graph.add_edge("kb_lookup", "diagnose")
    graph.add_edge("diagnose", "store")
    graph.add_edge("store", END)

    return graph.compile()


# ── Flow 2: enrich unknown codes ──────────────────────────────────────────────

def enrich_unknown_codes(db: Database, unknown_faults: list[dict[str, Any]]) -> list[str]:
    """Flow 2: for each unique unknown fault code call LLM once and save to KB.

    Args:
        db:             MongoDB database handle.
        unknown_faults: List of fault dicts with is_unknown=True (may contain duplicates).

    Returns:
        List of codes that were successfully enriched and saved to KB.
    """
    llm = get_llm()
    seen: set[str] = set()
    enriched_codes: list[str] = []

    for fault in unknown_faults:
        code = fault.get("code", "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)

        human_text = KB_ENRICH_HUMAN_PROMPT.format(
            code=code,
            ecu=fault.get("ecu", ""),
            fmi=fault.get("fmi", ""),
            raw_desc=fault.get("description", ""),
        )
        messages = [SystemMessage(content=KB_ENRICH_SYSTEM_PROMPT), HumanMessage(content=human_text)]
        try:
            response = llm.invoke(messages)
            raw_text = response.content if hasattr(response, "content") else str(response)
            result = _parse_json_obj(raw_text)
            if result:
                auto_learn_from_diagnosis(db, fault, result)
                enriched_codes.append(code)
        except Exception:
            pass

    return enriched_codes


if __name__ == "__main__":
    from db.connection import get_db as _get_db
    _db = _get_db()
    app = build_graph(_db)
    print("Diagnose graph compiled:", type(app))
