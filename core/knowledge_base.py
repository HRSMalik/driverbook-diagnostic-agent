# core/knowledge_base.py
# Knowledge base service: seed, lookup, and occurrence tracking.

import json
import os
from datetime import datetime, timezone

_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "knowledge_base", "seed_kb.json")


def seed_knowledge_base(db) -> int:
    """Load seed_kb.json into the knowledge_base collection if it is empty.

    Returns:
        Number of documents inserted (0 if collection already had data).
    """
    collection = db["knowledge_base"]
    if collection.count_documents({}) > 0:
        return 0

    seed_path = os.path.abspath(_SEED_PATH)
    with open(seed_path, "r") as f:
        entries = json.load(f)

    now = datetime.now(timezone.utc).isoformat()
    for entry in entries:
        entry.setdefault("first_seen", now)
        entry.setdefault("last_seen", now)
        entry.setdefault("occurrence_count", 0)

    collection.insert_many(entries)
    collection.create_index("code", unique=True)
    return len(entries)


def lookup(db, code: str) -> dict | None:
    """Return the KB entry for a fault code, or None if not found.

    Lookup is case-insensitive and ignores leading/trailing whitespace.
    """
    normalized = code.strip().upper()
    return db["knowledge_base"].find_one({"code": normalized}, {"_id": 0})


def increment_occurrence(db, code: str) -> None:
    """Increment occurrence_count and update last_seen for a known code."""
    normalized = code.strip().upper()
    db["knowledge_base"].update_one(
        {"code": normalized},
        {
            "$inc": {"occurrence_count": 1},
            "$set": {"last_seen": datetime.now(timezone.utc).isoformat()},
        },
    )


def auto_learn_from_diagnosis(db, fault: dict, diagnostic: dict) -> None:
    """Create or update a KB entry for a previously unknown fault code using
    the LLM diagnostic output.

    Only inserts if the code does not already exist in the KB. This prevents
    overwriting hand-authored seed entries.  The entry is tagged with
    ``source: "auto_learned"`` so it can be reviewed and promoted later.

    Args:
        db:         MongoDB database handle.
        fault:      Structured fault dict from dtc_parser.
        diagnostic: LLM diagnostic result dict (purpose, issue, severity, etc.).
    """
    code = fault.get("code", "").strip().upper()
    if not code:
        return

    collection = db["knowledge_base"]
    now = datetime.now(timezone.utc).isoformat()

    # Build a KB-shaped document from the LLM output — only set on first insert
    collection.update_one(
        {"code": code},
        {
            "$setOnInsert": {
                "code": code,
                "system": fault.get("ecu", "Unknown"),
                "component": fault.get("ecu", "Unknown"),
                "meaning": diagnostic.get("purpose", ""),
                "causes": [diagnostic.get("issue", "")] if diagnostic.get("issue") else [],
                "severity": diagnostic.get("severity", "Low"),
                "urgency": diagnostic.get("urgency", "Monitor"),
                "source": "auto_learned",
                "first_seen": now,
                "last_seen": now,
                "occurrence_count": 1,
            }
        },
        upsert=True,
    )
