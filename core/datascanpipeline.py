# core/datascanpipeline.py
# Batch MongoDB DTC scan pipeline — extract, stage, and analyze DTC documents.

import argparse
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from dotenv import load_dotenv

from db.connection import get_db
from db.fault_vehicles import (
    ensure_fault_vehicles_collection,
    mark_analyzed,
    stage_fault_document,
)
from orchestration.diagnostic_graph import build_graph, enrich_unknown_codes

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_DTC_RECORDS_PATH = "metaData.dtcRecords"
DEFAULT_SOURCE_DB = "driverbookv2_ai"
DEFAULT_SOURCE_COLLECTION = "driverbookv2.driverdiagnostics"
DEFAULT_APP_DB = "diagnostics"
DEFAULT_TELEMETRY_FIELDS = (
    "engineCoolantTemperature",
    "engineOilPressure",
    "speed",
    "fuelLevel",
    "defLevel",
    "engineSpeed",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_nested(doc: dict[str, Any], path: str | None, default: Any = None) -> Any:
    if not path:
        return default
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part, default)
    return node


def _try_objectid(value: Any) -> Any:
    if isinstance(value, str) and len(value) == 24:
        try:
            return ObjectId(value)
        except Exception:
            pass
    return value


def clean_query(query: dict[str, Any] | None) -> dict[str, Any]:
    if not query or query == {"additionalProp1": {}}:
        return {}
    return {k: _try_objectid(v) for k, v in query.items()}


def build_dtc_scan_query(query: dict[str, Any] | None, dtc_records_path: str) -> dict[str, Any]:
    base_query = clean_query(query)
    dtc_exists_query = {f"{dtc_records_path}.dtcs": {"$exists": True, "$ne": {}}}
    return {"$and": [base_query, dtc_exists_query]} if base_query else dtc_exists_query


def _extract_fmi(description: str) -> int | None:
    if not description:
        return None
    match = re.search(r"FMI\s+(\d+)", description, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _mil_to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _normalize_telemetry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        normalized[key] = value["value"] if isinstance(value, dict) and "value" in value else value
    return normalized


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_telemetry(doc: dict[str, Any]) -> dict[str, Any]:
    telemetry: dict[str, Any] = {}
    candidate_nodes = [
        doc,
        get_nested(doc, "diagnostics", {}),
        get_nested(doc, "sourcePayload", {}),
    ]
    for field in DEFAULT_TELEMETRY_FIELDS:
        for node in candidate_nodes:
            value = get_nested(node, field)
            if value is not None:
                telemetry[field] = value
                break
    return _normalize_telemetry(telemetry)


def build_raw_input(
    doc: dict[str, Any],
    dtc_records_path: str = DEFAULT_DTC_RECORDS_PATH,
    vehicle_id_field: str = "vehicleId",
) -> dict[str, Any] | None:
    dtc_records = get_nested(doc, dtc_records_path)
    if not isinstance(dtc_records, dict):
        return None
    dtcs = dtc_records.get("dtcs", {})
    if not isinstance(dtcs, dict) or not dtcs:
        return None
    vehicle_id = get_nested(doc, vehicle_id_field, doc.get("vehicleId", "UNKNOWN"))
    return {
        "vehicleId": str(vehicle_id),
        "dtcJson": {"dtcs": dtcs, "mil": _mil_to_bool(dtc_records.get("mil", False))},
        "telemetry": extract_telemetry(doc),
    }


def extract_dtc_records(
    doc: dict[str, Any],
    dtc_records_path: str = DEFAULT_DTC_RECORDS_PATH,
    vehicle_id_field: str = "vehicleId",
) -> dict[str, Any] | None:
    dtc_records = get_nested(doc, dtc_records_path)
    if not isinstance(dtc_records, dict):
        return None
    dtcs = dtc_records.get("dtcs", {})
    if not isinstance(dtcs, dict) or not dtcs:
        return None

    vehicle_id = get_nested(doc, vehicle_id_field, doc.get("vehicleId", "UNKNOWN"))
    tenant_id = doc.get("tenantId")
    mil = _mil_to_bool(dtc_records.get("mil", False))
    timestamp = dtc_records.get("timestamp") or datetime.now(timezone.utc).isoformat()

    faults = []
    for raw_code, entry in dtcs.items():
        if not isinstance(entry, dict):
            continue
        description = str(entry.get("desc") or "").strip()
        faults.append(
            {
                "code": str(raw_code).strip().upper(),
                "ecu": str(entry.get("ecu") or "").strip(),
                "fmi": _extract_fmi(description),
                "description": description,
                "vehicleId": str(vehicle_id),
                "mil": mil,
                "timestamp": timestamp,
            }
        )

    if not faults:
        return None

    raw_input = build_raw_input(doc, dtc_records_path=dtc_records_path, vehicle_id_field=vehicle_id_field)
    return {
        "_id": str(doc.get("_id")),
        "tenantId": str(tenant_id) if tenant_id is not None else None,
        "vehicleId": str(vehicle_id),
        "dtc_records_path": dtc_records_path,
        "mil": mil,
        "status": dtc_records.get("status"),
        "timestamp": timestamp,
        "dtc_records": dtc_records,
        "dtcs": dtcs,
        "fault_count": len(faults),
        "faults": faults,
        "raw_input": raw_input,
    }


# ── Scan ──────────────────────────────────────────────────────────────────────

def scan_dtc_documents(
    collection: Any,
    query: dict[str, Any] | None = None,
    skip: int = 0,
    limit: int | None = None,
    batch_size: int = 100,
    dtc_records_path: str = DEFAULT_DTC_RECORDS_PATH,
    vehicle_id_field: str = "vehicleId",
) -> dict[str, Any]:
    scan_query = build_dtc_scan_query(query, dtc_records_path)
    cursor = collection.find(scan_query).skip(skip).batch_size(batch_size)
    if limit is not None and limit > 0:
        cursor = cursor.limit(limit)

    results = []
    for doc in cursor:
        extracted = extract_dtc_records(doc, dtc_records_path=dtc_records_path, vehicle_id_field=vehicle_id_field)
        if extracted is not None:
            results.append(extracted)

    return {
        "query": scan_query, "skip": skip, "limit": limit,
        "batch_size": batch_size, "scanned": len(results),
        "next_skip": skip + len(results), "results": results,
    }


def scan_dtc_documents_with_source(
    collection: Any,
    query: dict[str, Any] | None = None,
    skip: int = 0,
    limit: int | None = None,
    batch_size: int = 100,
    dtc_records_path: str = DEFAULT_DTC_RECORDS_PATH,
    vehicle_id_field: str = "vehicleId",
) -> dict[str, Any]:
    """Variant of scan_dtc_documents that returns the original source doc alongside."""
    scan_query = build_dtc_scan_query(query, dtc_records_path)
    cursor = collection.find(scan_query).skip(skip).batch_size(batch_size)
    if limit is not None and limit > 0:
        cursor = cursor.limit(limit)

    pairs = []
    for doc in cursor:
        extracted = extract_dtc_records(doc, dtc_records_path=dtc_records_path, vehicle_id_field=vehicle_id_field)
        if extracted is not None:
            pairs.append((doc, extracted))

    return {
        "query": scan_query, "skip": skip, "limit": limit,
        "batch_size": batch_size, "scanned": len(pairs),
        "next_skip": skip + len(pairs), "pairs": pairs,
    }


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def get_source_collection(
    database: str | None = None,
    collection: str | None = None,
    uri: str | None = None,
) -> tuple[Any, str, str]:
    source_database = database or os.getenv("SOURCE_MONGO_DB", DEFAULT_SOURCE_DB)
    source_collection = collection or os.getenv("SOURCE_COLLECTION", DEFAULT_SOURCE_COLLECTION)
    source_uri = uri or os.getenv("SOURCE_MONGO_URI")
    db = get_db(source_database, uri=source_uri)
    return db[source_collection], source_database, source_collection


def get_app_db() -> tuple[Any, str]:
    app_database = os.getenv("MONGO_DB", DEFAULT_APP_DB)
    return get_db(app_database, uri=os.getenv("MONGO_URI")), app_database


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze_scanned_record(graph: Any, scanned_record: dict[str, Any]) -> dict[str, Any]:
    raw_input = scanned_record.get("raw_input")
    if not raw_input:
        return {**scanned_record, "analysis_error": "No raw input built from scanned document", "diagnostics": []}

    try:
        state = graph.invoke(
            {
                "raw_input": {**raw_input, "source_id": scanned_record.get("_id")},
                "parsed_faults": [],
                "diagnostics": [],
                "unknown_codes": [],
            }
        )
    except Exception as exc:
        return {**scanned_record, "analysis_error": str(exc), "diagnostics": [], "unknown_codes": []}

    return {
        **scanned_record,
        "diagnostics": state.get("diagnostics", []),
        "unknown_codes": state.get("unknown_codes", []),
        "parsed_faults": state.get("parsed_faults", []),
    }


def run_data_scan_pipeline(
    query: dict[str, Any] | None = None,
    skip: int = 0,
    limit: int | None = None,
    batch_size: int = 100,
    dtc_records_path: str = DEFAULT_DTC_RECORDS_PATH,
    vehicle_id_field: str = "vehicleId",
    database: str | None = None,
    collection: str | None = None,
    uri: str | None = None,
    reanalyze: bool = False,
) -> dict[str, Any]:
    """Scan the source MongoDB collection, stage DTC documents, and run the diagnostic graph.

    Args:
        query:            Optional MongoDB filter applied alongside DTC existence check.
        skip:             Number of matching documents to skip.
        limit:            Maximum number of documents to process (None = all).
        batch_size:       MongoDB cursor batch size.
        dtc_records_path: Dot-separated path to dtcRecords in source documents.
        vehicle_id_field: Dot-separated path to vehicleId in source documents.
        database:         Override SOURCE_MONGO_DB env var.
        collection:       Override SOURCE_COLLECTION env var.
        uri:              Override SOURCE_MONGO_URI env var.
        reanalyze:        Re-run graph for already-staged documents.

    Returns:
        Summary dict with counts and analyzed results.
    """
    mongo_collection, source_database, source_collection = get_source_collection(
        database=database, collection=collection, uri=uri,
    )
    app_db, app_database = get_app_db()
    ensure_fault_vehicles_collection(app_db)

    scan = scan_dtc_documents_with_source(
        collection=mongo_collection, query=query, skip=skip, limit=limit,
        batch_size=batch_size, dtc_records_path=dtc_records_path, vehicle_id_field=vehicle_id_field,
    )
    graph = build_graph(app_db)

    # ── Phase 1: diagnose all new/unanalyzed docs from KB (fast) ─────────────
    phase1_results = []
    staged_new = 0
    skipped_already_staged = 0
    all_unknown_faults: list[dict] = []

    for source_doc, scanned_record in scan["pairs"]:
        newly_staged = stage_fault_document(app_db, source_doc, scanned_record)
        if newly_staged:
            staged_new += 1
        elif reanalyze:
            pass
        else:
            staged_doc = app_db["fault_vehicles"].find_one(
                {"source_id": str(source_doc.get("_id"))}, {"analyzed": 1}
            )
            if staged_doc and staged_doc.get("analyzed"):
                skipped_already_staged += 1
                continue

        result = analyze_scanned_record(graph, scanned_record)
        phase1_results.append(result)
        mark_analyzed(app_db, str(source_doc.get("_id")))
        logger.info("Flow1 source_id=%s vehicle=%s unknowns=%s",
                    source_doc.get("_id"), scanned_record.get("vehicleId"),
                    result.get("unknown_codes", []))

        for fault in result.get("parsed_faults", []):
            if fault.get("is_unknown"):
                all_unknown_faults.append(fault)

    # ── Phase 2: enrich unknown codes via LLM → save to KB ───────────────────
    enriched_codes: list[str] = []
    if all_unknown_faults:
        logger.info("Flow2 enriching %d unique unknown codes via LLM...",
                    len({f["code"] for f in all_unknown_faults}))
        enriched_codes = enrich_unknown_codes(app_db, all_unknown_faults)
        logger.info("Flow2 enriched: %s", enriched_codes)

        # Re-diagnose docs that had unknown codes so they get full KB entries
        if enriched_codes:
            enriched_set = set(enriched_codes)
            for result in phase1_results:
                doc_unknowns = {f["code"] for f in result.get("parsed_faults", []) if f.get("is_unknown")}
                if doc_unknowns & enriched_set:
                    updated = analyze_scanned_record(graph, result)
                    logger.info("Flow2 re-diagnosed source_id=%s", result.get("_id"))
                    # Replace in phase1_results for the summary
                    result.update(updated)

    return {
        "source_database": source_database,
        "source_collection": source_collection,
        "app_database": app_database,
        "dtc_records_path": dtc_records_path,
        "query": scan["query"],
        "skip": scan["skip"],
        "limit": scan["limit"],
        "batch_size": scan["batch_size"],
        "scanned": scan["scanned"],
        "next_skip": scan["next_skip"],
        "staged_new": staged_new,
        "skipped_already_staged": skipped_already_staged,
        "flow1_analyzed": len(phase1_results),
        "flow2_enriched": len(enriched_codes),
        "reanalyze": reanalyze,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan the configured MongoDB source collection and analyze DTC records."
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--dtc-records-path", default=DEFAULT_DTC_RECORDS_PATH)
    parser.add_argument("--vehicle-id-field", default="vehicleId")
    parser.add_argument("--query", default="{}", help="JSON MongoDB filter.")
    parser.add_argument("--reanalyze", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_cli_args()
    try:
        query = json.loads(args.query)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON passed to --query: {exc}") from exc

    try:
        result = run_data_scan_pipeline(
            query=query, skip=args.skip, limit=args.limit,
            batch_size=args.batch_size, dtc_records_path=args.dtc_records_path,
            vehicle_id_field=args.vehicle_id_field, reanalyze=args.reanalyze,
        )
    except Exception as exc:
        raise SystemExit(f"Data scan pipeline failed: {exc}") from exc

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
