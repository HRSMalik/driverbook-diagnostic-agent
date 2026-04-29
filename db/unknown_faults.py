# db/unknown_faults.py
# Upsert logic for auto-capturing fault codes not found in the knowledge base.

from datetime import datetime, timezone


def save_unknown_fault(db, fault: dict, telemetry_snapshot: dict) -> None:
    """Insert or update an unknown fault record.

    On first encounter: full document is inserted with status "unresolved".
    On subsequent encounters: occurrence_count is incremented, last_seen is updated,
    and the telemetry snapshot is refreshed.

    Args:
        db:                MongoDB database handle.
        fault:             Structured fault dict from dtc_parser (must contain "code").
        telemetry_snapshot: Telemetry context dict from telemetry_context.py.
    """
    collection = db["unknown_faults"]
    now = datetime.now(timezone.utc).isoformat()
    code = fault.get("code", "").strip().upper()

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
            },
            "$inc": {"occurrence_count": 1},
        },
        upsert=True,
    )
