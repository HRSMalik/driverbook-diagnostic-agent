# core/dtc_parser.py
# Extracts structured fault records from raw dtcRecords JSON.
#
# Source shape (from MongoDB / dtc_records.csv):
#   metaData.dtcRecords.dtcs.<CODE>.ecu   → ECU name string
#   metaData.dtcRecords.dtcs.<CODE>.desc  → raw description string
#   metaData.dtcRecords.mil               → MIL lamp status bool

import re
from datetime import datetime, timezone


def _extract_fmi(description: str) -> int | None:
    """Parse FMI value from a raw description string such as 'Out of Calibration (FMI 13)'."""
    if not description:
        return None
    match = re.search(r"FMI\s+(\d+)", description, re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_dtc_records(
    dtc_records: dict,
    vehicle_id: str,
    timestamp: str | None = None,
) -> list[dict]:
    """Extract a list of structured fault dicts from a raw dtcRecords payload.

    Args:
        dtc_records: The value of metaData.dtcRecords from a vehicle document.
                     Expected keys: "dtcs" (dict), optionally "mil".
        vehicle_id:  The vehicle identifier string.
        timestamp:   ISO 8601 timestamp string. Defaults to current UTC time.

    Returns:
        List of fault dicts matching the schema:
        {
            "code":        str,   # e.g. "SPN 521133"
            "ecu":         str,
            "fmi":         int | None,
            "description": str,
            "timestamp":   str,   # ISO 8601
            "vehicleId":   str,
            "mil":         bool,
        }
    """
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()

    mil = bool(dtc_records.get("mil", False))
    dtcs: dict = dtc_records.get("dtcs", {})

    faults = []
    for raw_code, entry in dtcs.items():
        if not isinstance(entry, dict):
            continue

        code = raw_code.strip().upper()
        ecu = (entry.get("ecu") or "").strip()
        description = (entry.get("desc") or "").strip()
        fmi = _extract_fmi(description)

        faults.append(
            {
                "code": code,
                "ecu": ecu,
                "fmi": fmi,
                "description": description,
                "timestamp": timestamp,
                "vehicleId": vehicle_id,
                "mil": mil,
            }
        )

    return faults
