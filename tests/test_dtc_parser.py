"""
DTC Parser — unit test suite.

Tests _extract_fmi and parse_dtc_records with fixed input/output pairs.
No external dependencies required.

Run from diagnostic_agent/:
    conda run -n driverbook python tests/test_dtc_parser.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.dtc_parser import _extract_fmi, parse_dtc_records

# ============================================================================
# Test cases — (inputs, expected)
# ============================================================================

FMI_CASES: list[tuple] = [
    # (description_string,             expected_fmi)
    ("Out of Calibration FMI 13",      13),
    ("fmi 0 detected in module",       0),
    ("Voltage High FMI 3",             3),
    ("No fault info here",             None),
    ("",                               None),
    ("FMI",                            None),      # no digit after FMI
    ("fmi  9 extra space",             9),
]

PARSE_CASES: list[tuple] = [
    # (dtc_records_dict, vehicle_id, expected_output_shape)
    (
        {
            "mil": True,
            "dtcs": {
                "SPN 521133": {"ecu": "Engine #2", "desc": "Out of Calibration FMI 13"},
                "spn 0":      {"ecu": "Communications Unit", "desc": "No description"},
            },
        },
        "TRUCK-001",
        [
            {"code": "SPN 521133", "ecu": "Engine #2",           "fmi": 13,   "mil": True},
            {"code": "SPN 0",      "ecu": "Communications Unit",  "fmi": None, "mil": True},
        ],
    ),
    (
        {"mil": False, "dtcs": {}},
        "TRUCK-002",
        [],
    ),
    (
        {"dtcs": {"P0128": {"ecu": "ECM", "desc": "Coolant Temp Below Thermostat"}}},
        "TRUCK-003",
        [{"code": "P0128", "ecu": "ECM", "fmi": None, "mil": False}],
    ),
    (
        {"dtcs": {"  spn 929 ": {"ecu": "  Engine  ", "desc": ""}}},
        "TRUCK-004",
        [{"code": "SPN 929", "ecu": "Engine", "fmi": None}],
    ),
]


# ============================================================================
# Runners
# ============================================================================

def run_fmi_cases() -> tuple[int, int]:
    passed = 0
    for desc, expected in FMI_CASES:
        result = _extract_fmi(desc)
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        label = repr(desc)[:40]
        print(f"  [{status}] _extract_fmi({label}) => {result!r}  (expected {expected!r})")
    return passed, len(FMI_CASES)


def _check_parse(result: list[dict], expected_shapes: list[dict]) -> tuple[bool, list[str]]:
    if len(result) != len(expected_shapes):
        return False, [f"length: expected {len(expected_shapes)} got {len(result)}"]
    diffs = []
    result_by_code = {r["code"]: r for r in result}
    for shape in expected_shapes:
        code = shape["code"]
        actual = result_by_code.get(code)
        if actual is None:
            diffs.append(f"missing code {code!r}")
            continue
        for key, exp_val in shape.items():
            act_val = actual.get(key)
            if act_val != exp_val:
                diffs.append(f"[{code}] {key}: expected={exp_val!r} actual={act_val!r}")
    return len(diffs) == 0, diffs


def run_parse_cases() -> tuple[int, int]:
    passed = 0
    for dtc_records, vehicle_id, expected_shapes in PARSE_CASES:
        result = parse_dtc_records(dtc_records, vehicle_id, timestamp="2025-01-01T00:00:00+00:00")
        ok, diffs = _check_parse(result, expected_shapes)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"  [{status}] parse_dtc_records vehicle={vehicle_id!r} faults={len(result)}")
        for d in diffs:
            print(f"         x {d}")
    return passed, len(PARSE_CASES)


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    print("=== _extract_fmi ===")
    fmi_pass, fmi_total = run_fmi_cases()

    print("\n=== parse_dtc_records ===")
    parse_pass, parse_total = run_parse_cases()

    total_pass = fmi_pass + parse_pass
    total = fmi_total + parse_total
    print(f"\n{total_pass}/{total} passed")


if __name__ == "__main__":
    main()
