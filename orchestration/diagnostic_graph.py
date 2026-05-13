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
from llm.prompts import (
    BATCH_EXPLAIN_HUMAN_PROMPT,
    BATCH_EXPLAIN_SYSTEM_PROMPT,
    BATCH_HUMAN_PROMPT,
    BATCH_SYSTEM_PROMPT,
)
from llm.parsers import invoke_and_parse


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


def _parse_json_array(raw_text: str) -> list:
    """Robustly extract a JSON array from LLM output. Returns [] on total failure."""
    clean = re.sub(r"\s+", " ", raw_text).strip()
    try:
        result = json.loads(clean)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", clean, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    return []


def _build_diag_row(fault: dict, result: dict) -> dict:
    """Assemble a final diagnostic dict from a fault + LLM/KB result."""
    if "severity" in result and fault.get("adjusted_severity"):
        result = {**result, "severity": fault["adjusted_severity"]}
    return {
        "code": fault["code"],
        "ecu": fault.get("ecu", ""),
        "fmi": fault.get("fmi"),
        "vehicleId": fault.get("vehicleId", ""),
        "timestamp": fault.get("timestamp", ""),
        "is_unknown": fault.get("is_unknown", False),
        **{k: v for k, v in result.items() if k != "code"},
    }


def llm_node(state: DiagnosticState, llm) -> DiagnosticState:
    """Diagnostic agent. KB hits short-circuit; remaining faults are batched into ONE LLM call.

    Batching: faults that need the LLM (KB miss or thin extracted_from_doc entry)
    are collected per vehicle and sent as a single JSON-array request. The response
    is parsed as an array and mapped back to faults by code, with per-element
    fallback if the array is missing or malformed.
    """
    diagnostics = []
    faults_needing_llm = []

    for fault in state["parsed_faults"]:
        kb = fault.get("kb_entry") or {}
        if fault.get("skip_llm") and kb:
            diagnostics.append(_build_diag_row(fault, _diagnostic_from_kb(fault, kb)))
        else:
            faults_needing_llm.append(fault)

    if faults_needing_llm:
        telemetry = faults_needing_llm[0].get("telemetry_snapshot", {})
        batch_input = [
            {
                "code": f.get("code", ""),
                "ecu": f.get("ecu", ""),
                "fmi": f.get("fmi"),
                "raw_desc": f.get("description", ""),
                "kb_entry": f.get("kb_entry") or "No entry found in knowledge base.",
            }
            for f in faults_needing_llm
        ]
        human_text = BATCH_HUMAN_PROMPT.format(
            telemetry=json.dumps(telemetry),
            n=len(batch_input),
            faults_json=json.dumps(batch_input),
        )
        messages = [SystemMessage(content=BATCH_SYSTEM_PROMPT), HumanMessage(content=human_text)]

        try:
            response = llm.invoke(messages)
            raw_text = response.content if hasattr(response, "content") else str(response)
            parsed = _parse_json_array(raw_text)
        except Exception as exc:
            parsed = []
            batch_error = str(exc)
        else:
            batch_error = None

        by_code = {}
        for elem in parsed:
            if isinstance(elem, dict) and elem.get("code"):
                by_code[str(elem["code"]).strip().upper()] = elem

        for fault in faults_needing_llm:
            result = by_code.get(fault["code"].strip().upper())
            if result is None:
                result = {"error": batch_error or "Missing from batch response"}
            diagnostics.append(_build_diag_row(fault, result))

    return {**state, "diagnostics": diagnostics}


def explain_node(state: DiagnosticState, llm) -> DiagnosticState:
    """Explainability agent. KB-sourced diagnostics short-circuit and reuse KB fields;
    remaining diagnostics are batched into ONE LLM call.
    """
    fault_by_code = {f.get("code"): f for f in state["parsed_faults"]}
    explained = []
    diags_needing_llm = []

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
        else:
            diags_needing_llm.append(diag)

    if diags_needing_llm:
        first_fault = fault_by_code.get(diags_needing_llm[0].get("code"), {})
        telemetry = first_fault.get("telemetry_snapshot") or {}
        batch_input = []
        for diag in diags_needing_llm:
            fault = fault_by_code.get(diag.get("code"), {})
            diagnosis_summary = {
                k: diag.get(k)
                for k in ("purpose", "issue", "impact", "severity", "urgency")
                if diag.get(k)
            }
            batch_input.append(
                {
                    "code": diag.get("code", ""),
                    "ecu": diag.get("ecu", ""),
                    "diagnosis": diagnosis_summary,
                    "kb_entry": fault.get("kb_entry") or "No entry found in knowledge base.",
                }
            )

        human_text = BATCH_EXPLAIN_HUMAN_PROMPT.format(
            telemetry=json.dumps(telemetry),
            n=len(batch_input),
            faults_json=json.dumps(batch_input),
        )
        messages = [SystemMessage(content=BATCH_EXPLAIN_SYSTEM_PROMPT), HumanMessage(content=human_text)]

        try:
            response = llm.invoke(messages)
            raw_text = response.content if hasattr(response, "content") else str(response)
            parsed = _parse_json_array(raw_text)
        except Exception as exc:
            parsed = []
            batch_error = str(exc)
        else:
            batch_error = None

        by_code = {}
        for elem in parsed:
            if isinstance(elem, dict) and elem.get("code"):
                by_code[str(elem["code"]).strip().upper()] = elem

        for diag in diags_needing_llm:
            explanation = by_code.get(diag["code"].strip().upper())
            if explanation is None:
                explanation = {"error": batch_error or "Missing from batch response"}
            explained.append({**diag, **{k: v for k, v in explanation.items() if k != "code"}})

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
