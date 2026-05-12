# orchestration/diagnostic_graph.py
# LangGraph DAG: parse → kb_lookup → telemetry → llm → explain → store → END

import json
from typing import Any, TypedDict

from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END
from langchain_core.messages import SystemMessage, HumanMessage
from pymongo.database import Database

from core.dtc_parser import parse_dtc_records
from core.knowledge_base import (
    auto_learn_from_diagnosis,
    extract_and_insert_from_document,
    increment_occurrence,
    lookup,
)
from core.telemetry_context import build_telemetry_snapshot, adjust_severity
from db.diagnostics_output import save_diagnostics
from db.unknown_faults import save_unknown_fault
from llm.hf_client import get_llm
from llm.parsers import invoke_and_parse
from llm.prompts import SYSTEM_PROMPT, HUMAN_PROMPT, EXPLAIN_SYSTEM_PROMPT, EXPLAIN_HUMAN_PROMPT


class DiagnosticState(TypedDict):
    raw_input: dict[str, Any]     # {vehicleId, dtcJson, telemetry}
    parsed_faults: list[dict]     # output of dtc_parser, enriched as it flows
    diagnostics: list[dict]       # final per-fault diagnostic results
    unknown_codes: list[str]      # codes not found in KB


# ── Node functions ────────────────────────────────────────────────────────────

def parse_node(state: DiagnosticState) -> DiagnosticState:
    """Extract structured fault list from raw dtcJson payload."""
    raw = state["raw_input"]
    faults = parse_dtc_records(
        dtc_records=raw.get("dtcJson", {}),
        vehicle_id=raw.get("vehicleId", ""),
    )
    return {**state, "parsed_faults": faults}


def kb_lookup_node(state: DiagnosticState, db: Database) -> DiagnosticState:
    """Annotate each fault with its KB entry; flag unknowns."""
    enriched = []
    unknown_codes = []
    for fault in state["parsed_faults"]:
        kb_entry = lookup(db, fault["code"])
        needs_enrichment = bool(kb_entry) and kb_entry.get("source") == "extracted_from_doc"
        fault = {
            **fault,
            "kb_entry": kb_entry,
            "is_unknown": kb_entry is None,
            "needs_enrichment": needs_enrichment,
            "skip_llm": kb_entry is not None and not needs_enrichment,
        }
        if kb_entry is None:
            unknown_codes.append(fault["code"])
        enriched.append(fault)
    return {**state, "parsed_faults": enriched, "unknown_codes": unknown_codes}


def telemetry_node(state: DiagnosticState) -> DiagnosticState:
    """Build telemetry snapshot and pre-adjust severity on KB-known faults."""
    raw_telemetry = state["raw_input"].get("telemetry", {})
    snapshot = build_telemetry_snapshot(raw_telemetry)

    enriched = []
    for fault in state["parsed_faults"]:
        kb = fault.get("kb_entry") or {}
        base_severity = kb.get("severity", "Low")
        adjusted = adjust_severity(base_severity, fault, snapshot)
        enriched.append({**fault, "telemetry_snapshot": snapshot, "adjusted_severity": adjusted})
    return {**state, "parsed_faults": enriched}


def _diagnostic_from_kb(fault: dict[str, Any], kb: dict[str, Any]) -> dict[str, Any]:
    """Build a diagnostic record directly from a KB entry — no LLM call."""
    severity = fault.get("adjusted_severity") or kb.get("severity") or "Low"
    return {
        "purpose": kb.get("meaning", ""),
        "issue": kb.get("meaning", ""),
        "impact": ", ".join(kb.get("causes", []) or []),
        "severity": severity,
        "urgency": kb.get("urgency", "Monitor"),
        "confidence": 100,
        "from_kb": True,
    }


def llm_node(state: DiagnosticState, llm: ChatOllama) -> DiagnosticState:
    """Call LLM for each fault and parse the JSON response. KB hits short-circuit."""
    diagnostics = []
    for fault in state["parsed_faults"]:
        kb = fault.get("kb_entry") or {}

        if fault.get("skip_llm") and kb:
            result = _diagnostic_from_kb(fault, kb)
            diagnostics.append(
                {
                    "code": fault["code"],
                    "ecu": fault.get("ecu", ""),
                    "fmi": fault.get("fmi"),
                    "vehicleId": fault.get("vehicleId", ""),
                    "timestamp": fault.get("timestamp", ""),
                    "is_unknown": False,
                    **result,
                }
            )
            continue

        human_text = HUMAN_PROMPT.format(
            code=fault.get("code", ""),
            ecu=fault.get("ecu", ""),
            fmi=fault.get("fmi", ""),
            raw_desc=fault.get("description", ""),
            telemetry=json.dumps(fault.get("telemetry_snapshot", {})),
            kb_entry=json.dumps(kb) if kb else "No entry found in knowledge base.",
        )
        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=human_text)]

        result = invoke_and_parse(llm, messages)

        if "severity" in result and fault.get("adjusted_severity"):
            result["severity"] = fault["adjusted_severity"]

        diagnostics.append(
            {
                "code": fault["code"],
                "ecu": fault.get("ecu", ""),
                "fmi": fault.get("fmi"),
                "vehicleId": fault.get("vehicleId", ""),
                "timestamp": fault.get("timestamp", ""),
                "is_unknown": fault.get("is_unknown", False),
                **result,
            }
        )
    return {**state, "diagnostics": diagnostics}


def explain_node(state: DiagnosticState, llm: ChatOllama) -> DiagnosticState:
    """Explainability agent: adds root cause explanation and resolution steps to each diagnostic.

    KB-sourced diagnostics short-circuit and reuse stored KB fields when present.
    """
    fault_by_code = {f.get("code"): f for f in state["parsed_faults"]}
    explained = []

    for diag in state["diagnostics"]:
        if diag.get("from_kb"):
            kb = fault_by_code.get(diag.get("code"), {}).get("kb_entry") or {}
            explained.append(
                {
                    **diag,
                    "explanation": kb.get("meaning", ""),
                    "resolution_steps": kb.get("resolution_steps") or kb.get("causes", []) or [],
                    "who_can_fix": kb.get("who_can_fix", "Fleet maintenance team"),
                    "parts_likely_needed": kb.get("parts_likely_needed", []),
                    "estimated_downtime": kb.get("estimated_downtime", "Unknown"),
                }
            )
            continue

        diagnosis_summary = {
            k: diag.get(k)
            for k in ("purpose", "issue", "impact", "severity", "urgency")
            if diag.get(k)
        }
        kb_entry: dict[str, Any] = {}
        telemetry: dict[str, Any] = {}
        for fault in state["parsed_faults"]:
            if fault.get("code") == diag.get("code"):
                kb_entry = fault.get("kb_entry") or {}
                telemetry = fault.get("telemetry_snapshot") or {}
                break

        human_text = EXPLAIN_HUMAN_PROMPT.format(
            code=diag.get("code", ""),
            ecu=diag.get("ecu", ""),
            diagnosis=json.dumps(diagnosis_summary),
            kb_entry=json.dumps(kb_entry) if kb_entry else "No entry found in knowledge base.",
            telemetry=json.dumps(telemetry),
        )
        messages = [SystemMessage(content=EXPLAIN_SYSTEM_PROMPT), HumanMessage(content=human_text)]

        explanation = invoke_and_parse(llm, messages)

        explained.append({**diag, **explanation})

    return {**state, "diagnostics": explained}


def store_node(state: DiagnosticState, db: Database) -> DiagnosticState:
    """Persist unknown faults and diagnostics output to MongoDB.

    For unknown codes:
      1. Upsert into unknown_faults (occurrence tracking).
      2. Auto-learn a KB entry from the LLM diagnostic output so future
         lookups have grounding data.
    For known codes: increment KB occurrence count.
    All diagnostics are written to diagnostics_output.
    """
    diag_by_code = {d.get("code", "").strip().upper(): d for d in state["diagnostics"]}

    for fault in state["parsed_faults"]:
        if fault.get("is_unknown"):
            save_unknown_fault(db, fault, fault.get("telemetry_snapshot", {}))
            diagnostic = diag_by_code.get(fault["code"].strip().upper(), {})
            if diagnostic and "error" not in diagnostic:
                auto_learn_from_diagnosis(db, fault, diagnostic)
        elif fault.get("needs_enrichment"):
            diagnostic = diag_by_code.get(fault["code"].strip().upper(), {})
            if diagnostic and "error" not in diagnostic:
                auto_learn_from_diagnosis(db, fault, diagnostic)
        else:
            increment_occurrence(db, fault["code"])

    save_diagnostics(db, state["diagnostics"], state["raw_input"].get("source_id"))

    return state


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph(db: Database) -> Any:
    """Construct and compile the LangGraph diagnostic workflow.

    Args:
        db: MongoDB database handle (injected so nodes can access storage).

    Returns:
        Compiled LangGraph app ready to invoke with DiagnosticState.
    """
    llm = get_llm()

    graph = StateGraph(DiagnosticState)

    graph.add_node("parse", parse_node)
    graph.add_node("kb_lookup", lambda s: kb_lookup_node(s, db))
    graph.add_node("telemetry", telemetry_node)
    graph.add_node("llm", lambda s: llm_node(s, llm))
    graph.add_node("explain", lambda s: explain_node(s, llm))
    graph.add_node("store", lambda s: store_node(s, db))

    graph.set_entry_point("parse")
    graph.add_edge("parse", "kb_lookup")
    graph.add_edge("kb_lookup", "telemetry")
    graph.add_edge("telemetry", "llm")
    graph.add_edge("llm", "explain")
    graph.add_edge("explain", "store")
    graph.add_edge("store", END)

    return graph.compile()


if __name__ == "__main__":
    from db.connection import get_db as _get_db
    _db = _get_db()
    app = build_graph(_db)
    print("Graph compiled successfully:", type(app))
