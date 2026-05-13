# db/diagnostics_output.py — Write diagnostics results to MongoDB.

from typing import Any

from pymongo.database import Database

_COLLECTION = "diagnostics_output"


def save_diagnostics(db: Database, diagnostics: list[dict[str, Any]], source_id: str | None) -> None:
    """Persist a batch of diagnostic records, replacing any prior run for the same source.

    Deletes existing records for source_id before inserting so re-runs are idempotent.

    Args:
        db:          MongoDB database handle.
        diagnostics: List of diagnostic dicts produced by the graph.
        source_id:   Source document identifier used as the dedup key.
    """
    if not diagnostics:
        return
    if source_id:
        db[_COLLECTION].delete_many({"source_id": source_id})
    db[_COLLECTION].insert_many([{**d, "source_id": source_id} for d in diagnostics])


if __name__ == "__main__":
    from db.connection import get_db

    _db = get_db()
    print("diagnostics_output count:", _db[_COLLECTION].count_documents({}))
