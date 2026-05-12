"""
Knowledge Base — unit test suite.

Tests lookup, increment_occurrence, extract_and_insert_from_document,
and auto_learn_from_diagnosis using mongomock (in-memory MongoDB).

Run from diagnostic_agent/:
    conda run -n driverbook python tests/test_knowledge_base.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock

from core.knowledge_base import (
    lookup,
    increment_occurrence,
    extract_and_insert_from_document,
    auto_learn_from_diagnosis,
)


# ============================================================================
# Helpers
# ============================================================================

def _fresh_db():
    """Return a clean in-memory MongoDB database for each test."""
    client = mongomock.MongoClient()
    db = client["test_diagnostics"]
    db["knowledge_base"].create_index("code", unique=True)
    return db


def _seed(db, code: str, source: str = "seed", **kwargs) -> None:
    doc = {"code": code, "source": source, "occurrence_count": 0, **kwargs}
    db["knowledge_base"].insert_one(doc)


# ============================================================================
# Test cases
# ============================================================================

def _run(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition and detail:
        print(f"         x {detail}")
    return condition


# ── lookup ────────────────────────────────────────────────────────────────────

def test_lookup() -> tuple[int, int]:
    print("=== lookup ===")
    results = []

    # hit — exact case
    db = _fresh_db()
    _seed(db, "SPN 521133", meaning="Calibration error")
    entry = lookup(db, "SPN 521133")
    results.append(_run("hit exact", entry is not None and entry.get("meaning") == "Calibration error"))

    # hit — case-insensitive
    db = _fresh_db()
    _seed(db, "SPN 521133")
    entry = lookup(db, "spn 521133")
    results.append(_run("hit case-insensitive", entry is not None))

    # hit — leading/trailing whitespace
    db = _fresh_db()
    _seed(db, "SPN 521133")
    entry = lookup(db, "  SPN 521133  ")
    results.append(_run("hit whitespace stripped", entry is not None))

    # miss
    db = _fresh_db()
    entry = lookup(db, "SPN 9999")
    results.append(_run("miss returns None", entry is None))

    # _id never returned
    db = _fresh_db()
    _seed(db, "SPN 0")
    entry = lookup(db, "SPN 0")
    results.append(_run("_id excluded from result", entry is not None and "_id" not in entry))

    passed = sum(results)
    return passed, len(results)


# ── increment_occurrence ──────────────────────────────────────────────────────

def test_increment_occurrence() -> tuple[int, int]:
    print("\n=== increment_occurrence ===")
    results = []

    db = _fresh_db()
    _seed(db, "SPN 100", occurrence_count=3)
    increment_occurrence(db, "SPN 100")
    doc = db["knowledge_base"].find_one({"code": "SPN 100"})
    results.append(_run("count incremented to 4", doc and doc.get("occurrence_count") == 4,
                        f"got {doc.get('occurrence_count') if doc else None}"))

    increment_occurrence(db, "SPN 100")
    doc = db["knowledge_base"].find_one({"code": "SPN 100"})
    results.append(_run("count incremented to 5 on second call", doc and doc.get("occurrence_count") == 5))

    # case-insensitive key
    db = _fresh_db()
    _seed(db, "SPN 200", occurrence_count=0)
    increment_occurrence(db, "spn 200")
    doc = db["knowledge_base"].find_one({"code": "SPN 200"})
    results.append(_run("increment case-insensitive", doc and doc.get("occurrence_count") == 1))

    passed = sum(results)
    return passed, len(results)


# ── extract_and_insert_from_document ──────────────────────────────────────────

def test_extract_and_insert() -> tuple[int, int]:
    print("\n=== extract_and_insert_from_document ===")
    results = []

    fault = {"code": "SPN 9001", "ecu": "Engine #3", "fmi": 7, "description": "Voltage Low"}

    # first insert returns True
    db = _fresh_db()
    inserted = extract_and_insert_from_document(db, fault)
    results.append(_run("first insert returns True", inserted is True))

    doc = db["knowledge_base"].find_one({"code": "SPN 9001"})
    results.append(_run("document created", doc is not None))
    results.append(_run("source is extracted_from_doc", doc and doc.get("source") == "extracted_from_doc"))
    results.append(_run("meaning from description", doc and doc.get("meaning") == "Voltage Low"))

    # duplicate insert returns False
    inserted_again = extract_and_insert_from_document(db, fault)
    results.append(_run("duplicate insert returns False", inserted_again is False))

    # empty code returns False
    db = _fresh_db()
    results.append(_run("empty code returns False", extract_and_insert_from_document(db, {"code": ""}) is False))

    passed = sum(results)
    return passed, len(results)


# ── auto_learn_from_diagnosis ──────────────────────────────────────────────────

def test_auto_learn() -> tuple[int, int]:
    print("\n=== auto_learn_from_diagnosis ===")
    results = []

    fault = {"code": "SPN 8888", "ecu": "Transmission"}
    diagnostic = {
        "purpose": "Gear ratio error",
        "issue": "Slipping clutch pack",
        "severity": "High",
        "urgency": "Immediate Action",
        "explanation": "Clutch pack worn beyond spec",
        "resolution_steps": ["Inspect clutch", "Replace if worn"],
    }

    # Path A — new code: inserts a full auto_learned entry
    db = _fresh_db()
    auto_learn_from_diagnosis(db, fault, diagnostic)
    doc = db["knowledge_base"].find_one({"code": "SPN 8888"})
    results.append(_run("Path A: document created", doc is not None))
    results.append(_run("Path A: source=auto_learned", doc and doc.get("source") == "auto_learned"))
    results.append(_run("Path A: meaning set", doc and doc.get("meaning") == "Gear ratio error"))
    results.append(_run("Path A: severity=High", doc and doc.get("severity") == "High"))

    # Path A — existing auto_learned entry is NOT overwritten (setOnInsert guard)
    auto_learn_from_diagnosis(db, fault, {**diagnostic, "purpose": "SHOULD NOT OVERWRITE"})
    doc = db["knowledge_base"].find_one({"code": "SPN 8888"})
    results.append(_run("Path A: existing auto_learned not overwritten",
                        doc and doc.get("meaning") == "Gear ratio error"))

    # Path B — upgrade extracted_from_doc row in-place
    db = _fresh_db()
    _seed(db, "SPN 8888", source="extracted_from_doc", meaning="", severity="Low")
    auto_learn_from_diagnosis(db, fault, diagnostic)
    doc = db["knowledge_base"].find_one({"code": "SPN 8888"})
    results.append(_run("Path B: source promoted to auto_learned", doc and doc.get("source") == "auto_learned"))
    results.append(_run("Path B: meaning upgraded", doc and doc.get("meaning") == "Gear ratio error"))

    # Seed entry (no source field) is never touched
    db = _fresh_db()
    _seed(db, "SPN 8888", meaning="Original seed meaning")
    auto_learn_from_diagnosis(db, fault, diagnostic)
    doc = db["knowledge_base"].find_one({"code": "SPN 8888"})
    results.append(_run("seed entry meaning not overwritten",
                        doc and doc.get("meaning") == "Original seed meaning"))

    passed = sum(results)
    return passed, len(results)


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    lu_pass,   lu_total   = test_lookup()
    inc_pass,  inc_total  = test_increment_occurrence()
    ext_pass,  ext_total  = test_extract_and_insert()
    aln_pass,  aln_total  = test_auto_learn()

    total_pass = lu_pass + inc_pass + ext_pass + aln_pass
    total      = lu_total + inc_total + ext_total + aln_total
    print(f"\n{total_pass}/{total} passed")


if __name__ == "__main__":
    main()
