# orchestration/diagnostic_graph.py
# On-click diagnostic helpers:
#   _diag_from_kb / _diag_placeholder — build response dicts
#   enrich_unknown_codes (Flow 2)    — LLM once per unknown code → save to KB

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from core.knowledge_base import auto_learn_from_diagnosis, lookup
from core.telemetry_context import adjust_severity
from llm.llm_client import get_llm
from llm.prompts import KB_ENRICH_HUMAN_PROMPT, KB_ENRICH_SYSTEM_PROMPT


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
        "severity": adjusted,
        "urgency": kb.get("urgency", "Monitor"),
        "causes": kb.get("causes") or [],
        "explanation": kb.get("explanation", ""),
        "resolution_steps": kb.get("resolution_steps") or [],
        "who_can_fix": kb.get("who_can_fix", "Fleet maintenance team"),
        "parts_likely_needed": kb.get("parts_likely_needed") or [],
        "from_kb": True,
    }
    if adjusted != base_severity:
        diag["severity_escalated"] = True
        diag["base_severity"] = base_severity
    return diag


def _diag_placeholder(fault: dict[str, Any]) -> dict[str, Any]:
    """Placeholder diagnostic for an unknown code — shown when LLM enrichment fails."""
    return {
        "code": fault["code"],
        "ecu": fault.get("ecu", ""),
        "fmi": fault.get("fmi"),
        "vehicleId": fault.get("vehicleId", ""),
        "timestamp": fault.get("timestamp", ""),
        "is_unknown": True,
        "severity": "Low",
        "urgency": "Monitor",
        "explanation": "",
        "resolution_steps": [],
        "who_can_fix": "Certified technician required",
        "parts_likely_needed": [],
        "from_kb": False,
    }


# ── Flow 2: enrich unknown codes ──────────────────────────────────────────────

def enrich_unknown_codes(unknown_faults: list[dict[str, Any]]) -> list[str]:
    """For each unique unknown fault code call LLM once and save to KB.

    Args:
        unknown_faults: List of fault dicts (may contain duplicates by code).

    Returns:
        List of codes successfully enriched and saved to KB.
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
                auto_learn_from_diagnosis(fault, result)
                enriched_codes.append(code)
        except Exception:
            pass

    return enriched_codes


if __name__ == "__main__":
    entry = lookup("SPN 520203")
    print("KB lookup SPN 520203:", entry.get("severity") if entry else "not found")
