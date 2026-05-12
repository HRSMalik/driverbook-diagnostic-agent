# core/dtc_parser.py
# Extracts structured fault records from raw dtcRecords JSON.

import re
from datetime import datetime, timezone


def _extract_fmi(description: str) -> int | None:
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
        List of fault dicts with keys: code, ecu, fmi, description, timestamp,
        vehicleId, mil.
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
        faults.append(
            {
                "code": code,
                "ecu": ecu,
                "fmi": _extract_fmi(description),
                "description": description,
                "timestamp": timestamp,
                "vehicleId": vehicle_id,
                "mil": mil,
            }
        )

    return faults


if __name__ == "__main__":
    sample = {
        "mil": True,
        "dtcs": {
            "SPN 521133": {"ecu": "Engine #2", "desc": "Out of Calibration FMI 13"},
            "SPN 0": {"ecu": "Communications Unit", "desc": "No description"},
        },
    }
    result = parse_dtc_records(sample, vehicle_id="TEST-001")
    for fault in result:
        print(fault)
    # Expected: two fault dicts; first has fmi=13, second has fmi=None
