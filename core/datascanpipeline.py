# datascanpipeline.py
# Fast MongoDB DTC scan pipeline. This module extracts DTC data only; it does
# not call the diagnostic graph or the LLM.

import argparse
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from db.connection import get_db
from db.fault_vehicles import (
    ensure_fault_vehicles_collection,
    mark_analyzed,
    stage_fault_document,
)
from orchestration.diagnostic_graph import build_graph


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


def get_nested(doc: Dict[str, Any], path: Optional[str], default: Any = None) -> Any:
    if not path:
        return default

    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part, default)
    return node


def clean_query(query: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not query or query == {"additionalProp1": {}}:
        return {}
    return dict(query)


def build_dtc_scan_query(query: Optional[Dict[str, Any]], dtc_records_path: str) -> Dict[str, Any]:
    base_query = clean_query(query)
    dtc_exists_query = {f"{dtc_records_path}.dtcs": {"$exists": True, "$ne": {}}}

    if not base_query:
        return dtc_exists_query

    return {"$and": [base_query, dtc_exists_query]}


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


def _normalize_telemetry(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    normalized: Dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, dict) and "value" in value:
            normalized[key] = value["value"]
        else:
            normalized[key] = value
    return normalized


def extract_telemetry(doc: Dict[str, Any]) -> Dict[str, Any]:
    telemetry: Dict[str, Any] = {}
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
    doc: Dict[str, Any],
    dtc_records_path: str = DEFAULT_DTC_RECORDS_PATH,
    vehicle_id_field: str = "vehicleId",
) -> Optional[Dict[str, Any]]:
    dtc_records = get_nested(doc, dtc_records_path)
    if not isinstance(dtc_records, dict):
        return None

    dtcs = dtc_records.get("dtcs", {})
    if not isinstance(dtcs, dict) or not dtcs:
        return None

    vehicle_id = get_nested(doc, vehicle_id_field, doc.get("vehicleId", "UNKNOWN"))
    return {
        "vehicleId": str(vehicle_id),
        "dtcJson": {
            "dtcs": dtcs,
            "mil": _mil_to_bool(dtc_records.get("mil", False)),
        },
        "telemetry": extract_telemetry(doc),
    }


def extract_dtc_records(
    doc: Dict[str, Any],
    dtc_records_path: str = DEFAULT_DTC_RECORDS_PATH,
    vehicle_id_field: str = "vehicleId",
) -> Optional[Dict[str, Any]]:
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

    raw_input = build_raw_input(
        doc,
        dtc_records_path=dtc_records_path,
        vehicle_id_field=vehicle_id_field,
    )
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


def scan_dtc_documents(
    collection: Any,
    query: Optional[Dict[str, Any]] = None,
    skip: int = 0,
    limit: Optional[int] = None,
    batch_size: int = 100,
    dtc_records_path: str = DEFAULT_DTC_RECORDS_PATH,
    vehicle_id_field: str = "vehicleId",
) -> Dict[str, Any]:
    scan_query = build_dtc_scan_query(query, dtc_records_path)
    cursor = collection.find(scan_query).skip(skip).batch_size(batch_size)
    if limit is not None and limit > 0:
        cursor = cursor.limit(limit)

    results = []
    for doc in cursor:
        extracted = extract_dtc_records(
            doc,
            dtc_records_path=dtc_records_path,
            vehicle_id_field=vehicle_id_field,
        )
        if extracted is not None:
            results.append(extracted)

    return {
        "query": scan_query,
        "skip": skip,
        "limit": limit,
        "batch_size": batch_size,
        "scanned": len(results),
        "next_skip": skip + len(results),
        "results": results,
    }


def get_source_collection(
    database: Optional[str] = None,
    collection: Optional[str] = None,
    uri: Optional[str] = None,
):
    load_dotenv()
    source_database = database or os.getenv("SOURCE_MONGO_DB", DEFAULT_SOURCE_DB)
    source_collection = collection or os.getenv("SOURCE_COLLECTION", DEFAULT_SOURCE_COLLECTION)
    source_uri = uri or os.getenv("SOURCE_MONGO_URI")

    db = get_db(source_database, uri=source_uri)
    return db[source_collection], source_database, source_collection


def get_app_graph():
    load_dotenv()
    app_database = os.getenv("MONGO_DB", DEFAULT_APP_DB)
    app_db = get_db(app_database, uri=os.getenv("MONGO_URI"))
    return build_graph(app_db), app_database


def analyze_scanned_record(graph: Any, scanned_record: Dict[str, Any]) -> Dict[str, Any]:
    raw_input = scanned_record.get("raw_input")
    if not raw_input:
        return {
            **scanned_record,
            "analysis_error": "No raw input built from scanned document",
            "diagnostics": [],
        }

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
        return {
            **scanned_record,
            "analysis_error": str(exc),
            "diagnostics": [],
            "unknown_codes": [],
        }

    return {
        **scanned_record,
        "diagnostics": state.get("diagnostics", []),
        "unknown_codes": state.get("unknown_codes", []),
    }


def scan_dtc_documents_with_source(
    collection: Any,
    query: Optional[Dict[str, Any]] = None,
    skip: int = 0,
    limit: Optional[int] = None,
    batch_size: int = 100,
    dtc_records_path: str = DEFAULT_DTC_RECORDS_PATH,
    vehicle_id_field: str = "vehicleId",
) -> Dict[str, Any]:
    """Variant of scan_dtc_documents that returns the original source doc alongside."""
    scan_query = build_dtc_scan_query(query, dtc_records_path)
    cursor = collection.find(scan_query).skip(skip).batch_size(batch_size)
    if limit is not None and limit > 0:
        cursor = cursor.limit(limit)

    pairs = []
    for doc in cursor:
        extracted = extract_dtc_records(
            doc,
            dtc_records_path=dtc_records_path,
            vehicle_id_field=vehicle_id_field,
        )
        if extracted is not None:
            pairs.append((doc, extracted))

    return {
        "query": scan_query,
        "skip": skip,
        "limit": limit,
        "batch_size": batch_size,
        "scanned": len(pairs),
        "next_skip": skip + len(pairs),
        "pairs": pairs,
    }


def get_app_db():
    load_dotenv()
    app_database = os.getenv("MONGO_DB", DEFAULT_APP_DB)
    return get_db(app_database, uri=os.getenv("MONGO_URI")), app_database


def run_data_scan_pipeline(
    query: Optional[Dict[str, Any]] = None,
    skip: int = 0,
    limit: Optional[int] = None,
    batch_size: int = 100,
    dtc_records_path: str = DEFAULT_DTC_RECORDS_PATH,
    vehicle_id_field: str = "vehicleId",
    database: Optional[str] = None,
    collection: Optional[str] = None,
    uri: Optional[str] = None,
    reanalyze: bool = False,
) -> Dict[str, Any]:
    mongo_collection, source_database, source_collection = get_source_collection(
        database=database,
        collection=collection,
        uri=uri,
    )
    app_db, app_database = get_app_db()
    ensure_fault_vehicles_collection(app_db)

    scan = scan_dtc_documents_with_source(
        collection=mongo_collection,
        query=query,
        skip=skip,
        limit=limit,
        batch_size=batch_size,
        dtc_records_path=dtc_records_path,
        vehicle_id_field=vehicle_id_field,
    )
    graph = build_graph(app_db)

    analyzed_results = []
    staged_new = 0
    skipped_already_staged = 0
    for source_doc, scanned_record in scan["pairs"]:
        newly_staged = stage_fault_document(app_db, source_doc, scanned_record)
        if newly_staged:
            staged_new += 1
        elif not reanalyze:
            skipped_already_staged += 1
            continue

        analyzed = analyze_scanned_record(graph, scanned_record)
        analyzed_results.append(analyzed)
        mark_analyzed(app_db, str(source_doc.get("_id")))

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
        "analyzed": len(analyzed_results),
        "reanalyze": reanalyze,
        "results": analyzed_results,
    }


def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan the configured MongoDB source collection and automatically analyze any found DTC records."
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of matching documents to scan.")
    parser.add_argument("--skip", type=int, default=0, help="Number of matching documents to skip.")
    parser.add_argument("--batch-size", type=int, default=10, help="MongoDB cursor batch size.")
    parser.add_argument(
        "--dtc-records-path",
        default=DEFAULT_DTC_RECORDS_PATH,
        help="Nested document path containing dtcRecords.",
    )
    parser.add_argument(
        "--vehicle-id-field",
        default="vehicleId",
        help="Nested document path for the vehicle identifier.",
    )
    parser.add_argument(
        "--query",
        default="{}",
        help="Optional JSON MongoDB filter to AND with the DTC existence filter.",
    )
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Re-run the diagnostic graph for documents that are already staged in fault_vehicles.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_cli_args()
    try:
        query = json.loads(args.query)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON passed to --query: {exc}") from exc

    try:
        result = run_data_scan_pipeline(
            query=query,
            skip=args.skip,
            limit=args.limit,
            batch_size=args.batch_size,
            dtc_records_path=args.dtc_records_path,
            vehicle_id_field=args.vehicle_id_field,
            reanalyze=args.reanalyze,
        )
    except Exception as exc:
        raise SystemExit(f"Data scan pipeline failed: {exc}") from exc

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
