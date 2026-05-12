"""
Telemetry Context — unit test suite.

Tests build_telemetry_snapshot and adjust_severity with fixed input/output pairs.
No external dependencies required.

Run from diagnostic_agent/:
    conda run -n driverbook python tests/test_telemetry_context.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.telemetry_context import build_telemetry_snapshot, adjust_severity

_SENTINEL = -6.128e18

# ============================================================================
# Test cases — build_telemetry_snapshot
# ============================================================================

SNAPSHOT_CASES: list[tuple] = [
    # (raw_record,                                       expected_snapshot_subset)
    (
        {"engineCoolantTemperature": 90.0, "engineOilPressure": 35.0, "speed": 60.0},
        {"engineCoolantTemperature": 90.0, "engineOilPressure": 35.0, "speed": 60.0},
    ),
    (
        {"engineCoolantTemperature": _SENTINEL, "speed": 55.0},
        {"engineCoolantTemperature": None, "speed": 55.0},
    ),
    (
        {"defLevel": "2.5", "fuelLevel": "75"},
        {"defLevel": 2.5, "fuelLevel": 75.0},
    ),
    (
        {"engineOilPressure": "not_a_number"},
        {"engineOilPressure": None},
    ),
    (
        {},
        {
            "engineCoolantTemperature": None,
            "engineOilPressure": None,
            "speed": None,
            "fuelLevel": None,
            "defLevel": None,
            "engineSpeed": None,
        },
    ),
]


# ============================================================================
# Test cases — adjust_severity
# ============================================================================

SEVERITY_CASES: list[tuple] = [
    # (base_severity, fault_dict,                              telemetry,                              expected)
    # Rule 1: hot coolant + engine fault → escalate one level
    ("Low",      {"code": "SPN 110",  "ecu": "Engine Controller"},  {"engineCoolantTemperature": 110.0}, "Medium"),
    ("Medium",   {"code": "SPN 110",  "ecu": "Engine Controller"},  {"engineCoolantTemperature": 110.0}, "High"),
    ("High",     {"code": "SPN 110",  "ecu": "Engine Controller"},  {"engineCoolantTemperature": 110.0}, "Critical"),
    # Rule 1: coolant below threshold — no escalation
    ("Low",      {"code": "SPN 110",  "ecu": "Engine Controller"},  {"engineCoolantTemperature": 95.0},  "Low"),
    # Rule 1: hot coolant but non-engine ECU — no escalation
    ("Low",      {"code": "SPN 110",  "ecu": "Body Controller"},    {"engineCoolantTemperature": 110.0}, "Low"),
    # Rule 2: low oil + engine → Critical regardless of base
    ("Low",      {"code": "SPN 100",  "ecu": "Engine #1"},          {"engineOilPressure": 15.0},         "Critical"),
    ("High",     {"code": "SPN 100",  "ecu": "Engine #1"},          {"engineOilPressure": 15.0},         "Critical"),
    # Rule 3: low DEF + DEF fault code → escalate one level
    ("Low",      {"code": "SPN 1761", "ecu": "Aftertreatment"},     {"defLevel": 3.0},                   "Medium"),
    ("Medium",   {"code": "SPN 4374", "ecu": "Emission Control"},   {"defLevel": 2.0},                   "High"),
    # Rule 3: low DEF but non-DEF code → no escalation
    ("Low",      {"code": "SPN 521",  "ecu": "Aftertreatment"},     {"defLevel": 2.0},                   "Low"),
    # Unknown severity defaults to Low before escalation
    ("Unknown",  {"code": "SPN 110",  "ecu": "Engine Controller"},  {"engineCoolantTemperature": 110.0}, "Medium"),
    # No telemetry → no change
    ("High",     {"code": "SPN 110",  "ecu": "Engine Controller"},  {},                                  "High"),
]


# ============================================================================
# Runners
# ============================================================================

def _check_snapshot(result: dict, expected_subset: dict) -> tuple[bool, list[str]]:
    diffs = []
    for key, exp_val in expected_subset.items():
        act_val = result.get(key)
        if act_val != exp_val:
            diffs.append(f"{key}: expected={exp_val!r} actual={act_val!r}")
    return len(diffs) == 0, diffs


def run_snapshot_cases() -> tuple[int, int]:
    passed = 0
    for raw, expected_subset in SNAPSHOT_CASES:
        result = build_telemetry_snapshot(raw)
        ok, diffs = _check_snapshot(result, expected_subset)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        keys = list(expected_subset.keys())
        print(f"  [{status}] snapshot keys={keys}")
        for d in diffs:
            print(f"         x {d}")
    return passed, len(SNAPSHOT_CASES)


def run_severity_cases() -> tuple[int, int]:
    passed = 0
    for base, fault, telemetry, expected in SEVERITY_CASES:
        result = adjust_severity(base, fault, telemetry)
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        label = f"base={base!r} ecu={fault.get('ecu')!r} code={fault.get('code')!r}"
        print(f"  [{status}] {label} => {result!r}  (expected {expected!r})")
    return passed, len(SEVERITY_CASES)


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    print("=== build_telemetry_snapshot ===")
    snap_pass, snap_total = run_snapshot_cases()

    print("\n=== adjust_severity ===")
    sev_pass, sev_total = run_severity_cases()

    total_pass = snap_pass + sev_pass
    total = snap_total + sev_total
    print(f"\n{total_pass}/{total} passed")


if __name__ == "__main__":
    main()
