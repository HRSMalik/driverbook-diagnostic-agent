# db/unknown_faults.py
# Upsert logic for auto-capturing fault codes not found in the knowledge base.

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database


def save_unknown_fault(
    db: Database,
    fault: dict[str, Any],
    telemetry_snapshot: dict[str, Any],
    diagnostic: dict[str, Any] | None = None,
) -> None:
    """Insert or update an unknown fault record.

    On first encounter: full document is inserted with status "unresolved".
    On subsequent encounters: occurrence_count is incremented, last_seen is updated,
    and the telemetry snapshot is refreshed.

    Args:
        db:                MongoDB database handle.
        fault:             Structured fault dict from dtc_parser (must contain "code").
        telemetry_snapshot: Telemetry context dict from telemetry_context.py.
        diagnostic:        Generated diagnostic/explanation payload for this code.
    """
    collection = db["unknown_faults"]
    now = datetime.now(timezone.utc).isoformat()
    code = fault.get("code", "").strip().upper()
    diagnostic = diagnostic or {}

    collection.update_one(
        {"code": code},
        {
            "$setOnInsert": {
                "code": code,
                "ecu": fault.get("ecu", ""),
                "fmi": fault.get("fmi"),
                "raw_description": fault.get("description", ""),
                "first_seen": now,
                "status": "unresolved",
            },
            "$set": {
                "last_seen": now,
                "sample_telemetry": telemetry_snapshot,
                "latest_diagnostic": diagnostic,
            },
            "$inc": {"occurrence_count": 1},
        },
        upsert=True,
    )


if __name__ == "__main__":
    from db.connection import get_db
    _db = get_db()
    print("unknown_faults count:", _db["unknown_faults"].count_documents({}))
    print("unresolved:", _db["unknown_faults"].count_documents({"status": "unresolved"}))
