# core/knowledge_base.py
# KB seed, lookup, occurrence tracking, and auto-learn from LLM output.

import json
import os
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "knowledge_base", "seed_kb.json")


# ── Seed ──────────────────────────────────────────────────────────────────────

def seed_knowledge_base(db: Database) -> int:
    """Load seed_kb.json into the knowledge_base collection if it is empty.

    Args:
        db: MongoDB database handle.

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


# ── Lookup & Occurrence ───────────────────────────────────────────────────────

def lookup(db: Database, code: str) -> dict[str, Any] | None:
    """Return the KB entry for a fault code, or None if not found.

    Args:
        db:   MongoDB database handle.
        code: Raw fault code string (case-insensitive, whitespace-tolerant).

    Returns:
        KB document dict without _id, or None on miss.
    """
    normalized = code.strip().upper()
    return db["knowledge_base"].find_one({"code": normalized}, {"_id": 0})


def increment_occurrence(db: Database, code: str) -> None:
    """Increment occurrence_count and update last_seen for a known code.

    Args:
        db:   MongoDB database handle.
        code: Fault code string.
    """
    normalized = code.strip().upper()
    db["knowledge_base"].update_one(
        {"code": normalized},
        {
            "$inc": {"occurrence_count": 1},
            "$set": {"last_seen": datetime.now(timezone.utc).isoformat()},
        },
    )


# ── Auto-learn ────────────────────────────────────────────────────────────────

def extract_and_insert_from_document(db: Database, fault: dict[str, Any]) -> bool:
    """Insert a minimal KB entry built from the source document only — no LLM.

    Used on KB miss to grow the knowledge base monotonically with the cheap
    information available in the source record (code, ecu, fmi, raw description).
    Tagged source: "extracted_from_doc" so it can later be enriched by
    auto_learn_from_diagnosis without being overwritten (both helpers use
    $setOnInsert).

    Args:
        db:    MongoDB database handle.
        fault: Structured fault dict from dtc_parser.

    Returns:
        True if a new KB row was inserted, False if the code already existed.
    """
    code = (fault.get("code") or "").strip().upper()
    if not code:
        return False

    now = datetime.now(timezone.utc).isoformat()
    ecu = (fault.get("ecu") or "Unknown").strip() or "Unknown"
    description = (fault.get("description") or "").strip()

    result = db["knowledge_base"].update_one(
        {"code": code},
        {
            "$setOnInsert": {
                "code": code,
                "system": ecu,
                "component": ecu,
                "fmi": fault.get("fmi"),
                "meaning": description,
                "raw_description": description,
                "causes": [],
                "severity": "Low",
                "urgency": "Monitor",
                "source": "extracted_from_doc",
                "first_seen": now,
                "last_seen": now,
                "occurrence_count": 1,
            }
        },
        upsert=True,
    )
    return result.upserted_id is not None


def auto_learn_from_diagnosis(db: Database, fault: dict[str, Any], diagnostic: dict[str, Any]) -> None:
    """Create or upgrade a KB entry using the LLM diagnostic output.

    Behavior:
    - If the code does not exist yet: insert a fully-populated entry tagged
      source: "auto_learned".
    - If a cheap source: "extracted_from_doc" row exists: fill in the
      missing LLM-derived fields and promote it to source: "auto_learned".
    - Seed entries (no source field) and existing auto_learned entries
      are NEVER touched, preserving curated data.

    Args:
        db:         MongoDB database handle.
        fault:      Structured fault dict from dtc_parser.
        diagnostic: Merged LLM output (diagnose + explain fields).
    """
    code = fault.get("code", "").strip().upper()
    if not code:
        return

    collection = db["knowledge_base"]
    now = datetime.now(timezone.utc).isoformat()
    ecu = fault.get("ecu", "Unknown") or "Unknown"

    meaning = diagnostic.get("meaning") or ""
    causes = diagnostic.get("causes") or []

    enrichment: dict[str, Any] = {
        "meaning": meaning,
        "causes": causes,
        "severity": diagnostic.get("severity", "Low"),
        "urgency": diagnostic.get("urgency", "Monitor"),
        "explanation": diagnostic.get("explanation", ""),
        "resolution_steps": diagnostic.get("resolution_steps", []),
        "who_can_fix": diagnostic.get("who_can_fix", ""),
        "parts_likely_needed": diagnostic.get("parts_likely_needed", []),
        "system": diagnostic.get("system") or ecu,
        "component": diagnostic.get("component") or ecu,
    }

    # Path A — first-time insert (no row exists for this code yet)
    collection.update_one(
        {"code": code},
        {
            "$setOnInsert": {
                "code": code,
                "system": ecu,
                "component": ecu,
                **enrichment,
                "source": "auto_learned",
                "first_seen": now,
                "last_seen": now,
                "occurrence_count": 1,
            }
        },
        upsert=True,
    )

    # Path B — upgrade a cheap extracted_from_doc row in-place
    set_fields = {k: v for k, v in enrichment.items() if v}
    if set_fields:
        collection.update_one(
            {"code": code, "source": "extracted_from_doc"},
            {"$set": {**set_fields, "source": "auto_learned", "last_seen": now}},
        )


if __name__ == "__main__":
    from db.connection import get_db
    _db = get_db()
    count = seed_knowledge_base(_db)
    print(f"Seeded: {count} entries")
    entry = lookup(_db, "spn 0")
    print(f"lookup('spn 0'): {entry}")
