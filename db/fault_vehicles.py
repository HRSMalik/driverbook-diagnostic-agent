# db/fault_vehicles.py
# Staging collection for source documents that carry at least one DTC.

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

_COLLECTION = "fault_vehicles"


def ensure_fault_vehicles_collection(db: Database) -> None:
    """Create the unique index on source_id. Idempotent."""
    db[_COLLECTION].create_index("source_id", unique=True)


def stage_fault_document(db: Database, source_doc: dict[str, Any], extracted: dict[str, Any]) -> bool:
    """Upsert a source document into fault_vehicles keyed on source_id.

    Args:
        db:         MongoDB database handle.
        source_doc: Original source document from the fleet MongoDB collection.
        extracted:  Output of extract_dtc_records() — structured fault data.

    Returns:
        True if a new row was inserted, False if it already existed.
    """
    source_id = str(source_doc.get("_id"))
    if not source_id or source_id == "None":
        return False

    now = datetime.now(timezone.utc).isoformat()
    result = db[_COLLECTION].update_one(
        {"source_id": source_id},
        {
            "$setOnInsert": {
                "source_id": source_id,
                "vehicleId": extracted.get("vehicleId"),
                "tenantId": extracted.get("tenantId"),
                "timestamp": extracted.get("timestamp"),
                "mil": extracted.get("mil", False),
                "fault_count": extracted.get("fault_count", 0),
                "faults": extracted.get("faults", []),
                "dtcs": extracted.get("dtcs", {}),
                "dtc_records": extracted.get("dtc_records", {}),
                "raw_input": extracted.get("raw_input"),
                "staged_at": now,
                "analyzed": False,
            }
        },
        upsert=True,
    )
    return result.upserted_id is not None


def mark_analyzed(db: Database, source_id: str) -> None:
    """Flip analyzed=True for a staged document after the graph has processed it.

    Args:
        db:        MongoDB database handle.
        source_id: The source_id string of the staged document.
    """
    db[_COLLECTION].update_one(
        {"source_id": str(source_id)},
        {"$set": {"analyzed": True, "analyzed_at": datetime.now(timezone.utc).isoformat()}},
    )


if __name__ == "__main__":
    from db.connection import get_db
    _db = get_db()
    ensure_fault_vehicles_collection(_db)
    print("fault_vehicles collection ready.")
    print("staged count:", _db[_COLLECTION].count_documents({}))
