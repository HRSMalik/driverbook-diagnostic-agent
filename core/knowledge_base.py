# core/knowledge_base.py
# File-based KB: seed_kb.json (read-only) + learned_kb.json (auto-written).
# Both are merged into an in-memory dict at startup — lookups are O(1).

import json
import os
from datetime import datetime, timezone
from typing import Any

_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
_SEED_PATH = os.path.join(_DIR, "seed_kb.json")

# In-memory KB keyed by normalised code string (uppercase, stripped)
_KB: dict[str, dict[str, Any]] = {}


# ── Load ──────────────────────────────────────────────────────────────────────

def _load() -> None:
    """Load seed_kb.json into _KB. Called once at import time."""
    if not os.path.exists(_SEED_PATH):
        return
    with open(_SEED_PATH, "r") as f:
        try:
            entries = json.load(f)
        except json.JSONDecodeError:
            entries = []
    for entry in entries:
        code = (entry.get("code") or "").strip().upper()
        if code:
            _KB[code] = entry

_load()


# ── Seed ──────────────────────────────────────────────────────────────────────

def seed_knowledge_base() -> int:
    """Return the number of entries currently in the KB.

    Returns:
        Total KB entry count after load.
    """
    return len(_KB)


# ── Lookup ────────────────────────────────────────────────────────────────────

def lookup(code: str) -> dict[str, Any] | None:
    """Return the KB entry for a fault code, or None if not found.

    Args:
        code: Raw fault code string (case-insensitive, whitespace-tolerant).

    Returns:
        KB entry dict, or None on miss.
    """
    return _KB.get(code.strip().upper())


# ── Auto-learn ────────────────────────────────────────────────────────────────

def auto_learn_from_diagnosis(fault: dict[str, Any], diagnostic: dict[str, Any]) -> None:
    """Save an LLM-derived KB entry to memory and learned_kb.json.

    Never overwrites an existing entry — seed data is always preserved.

    Args:
        fault:      Structured fault dict (must contain "code").
        diagnostic: LLM output dict with severity, explanation, etc.
    """
    code = (fault.get("code") or "").strip().upper()
    if not code or code in _KB:
        return

    ecu = fault.get("ecu", "Unknown") or "Unknown"
    now = datetime.now(timezone.utc).isoformat()

    entry: dict[str, Any] = {
        "code": code,
        "system": diagnostic.get("system") or ecu,
        "component": diagnostic.get("component") or ecu,
        "meaning": diagnostic.get("meaning") or "",
        "causes": diagnostic.get("causes") or [],
        "severity": diagnostic.get("severity", "Low"),
        "urgency": diagnostic.get("urgency", "Monitor"),
        "explanation": diagnostic.get("explanation", ""),
        "resolution_steps": diagnostic.get("resolution_steps") or [],
        "who_can_fix": diagnostic.get("who_can_fix", ""),
        "parts_likely_needed": diagnostic.get("parts_likely_needed") or [],
        "source": "auto_learned",
        "first_seen": now,
        "last_seen": now,
    }

    _KB[code] = entry

    with open(_SEED_PATH, "r") as f:
        all_entries = json.load(f)
    all_entries.append(entry)
    with open(_SEED_PATH, "w") as f:
        json.dump(all_entries, f, indent=2)


if __name__ == "__main__":
    print(f"KB loaded: {seed_knowledge_base()} entries")
    entry = lookup("SPN 520203")
    print(f"lookup('SPN 520203'): severity={entry.get('severity') if entry else 'not found'}")
