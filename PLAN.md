# Plan: AI-Powered Vehicle Diagnostics System (Phase 1)

## Context
The `driverbook/` directory currently contains only sample data files (dtc_records.csv, dtc_codes.csv) extracted from MongoDB. The sibling project `mydriverbook-compliance/` already implements a FastAPI + LanGraph + HuggingFace LLM pattern for driver compliance analysis. The new diagnostics service will be built inside `driverbook/` following the same architectural conventions — extending the platform to interpret vehicle fault codes, ground AI responses in a knowledge base, and auto-capture unknown codes for review.

---

## Project Layout

```
driverbook/
├── api.py                          # FastAPI app entry point
├── requirements.txt
├── .env                            # HF_TOKEN, MONGO_URI
├── core/
│   ├── dtc_parser.py               # Extract structured DTC records from raw dtcJson
│   ├── telemetry_context.py        # Telemetry signal analysis & severity adjustment
│   └── knowledge_base.py          # KB lookup, seed loader, occurrence tracking
├── db/
│   ├── connection.py               # MongoDB client singleton
│   └── unknown_faults.py          # Save/update unknown fault records
├── llm/
│   ├── hf_client.py               # LLM client (mirror of mydriverbook-compliance pattern)
│   └── prompts.py                  # DTC system + human prompt templates
├── orchestration/
│   └── diagnostic_graph.py        # LanGraph DAG: parse → kb_lookup → telemetry → llm → store
└── knowledge_base/
    └── seed_kb.json               # Initial 50-code knowledge base (seeded from dtc_codes.csv)
```

---

## MongoDB Collections

Database: `diagnostics`

| Collection | Purpose |
|---|---|
| `knowledge_base` | Known fault code definitions, causes, severity |
| `faults` | Processed fault records per vehicle |
| `unknown_faults` | Auto-captured codes not in KB |
| `diagnostics_output` | LLM analysis results |

---

## Phase 1 Implementation Steps

### Step 1 — Project Scaffold & Dependencies

Create `requirements.txt` mirroring `mydriverbook-compliance/requirement.txt`, adding:
- `fastapi`, `uvicorn`
- `langchain`, `langgraph`, `langchain-huggingface`
- `pymongo`, `pandas`, `python-dotenv`

Create `.env` with `HF_TOKEN` and `MONGO_URI=mongodb://localhost:27017`.

---

### Step 2 — DTC Parser (`core/dtc_parser.py`)

Parse raw `dtcJson` blob from vehicle records into a list of structured fault objects.

**Input:** Raw `metaData.dtcRecords` dict from MongoDB document + vehicleId + timestamp

**Output schema per fault:**
```json
{
  "code": "SPN 521133",
  "ecu": "Engine #2",
  "fmi": 13,
  "description": "Out of Calibration",
  "timestamp": "ISO_DATE",
  "vehicleId": "..."
}
```

**Key logic:**
- Iterate over `dtcRecords.dtcs` keys (each key is a fault code)
- Extract `.ecu` and `.desc` subfields from each DTC entry
- Normalize code format (strip extra whitespace, uppercase)
- Return list of parsed fault dicts

**Reference:** dtc_records.csv columns `metaData.dtcRecords.dtcs.<CODE>.ecu` and `.desc` show the nested structure.

---

### Step 3 — Knowledge Base Service (`core/knowledge_base.py`)

**Functions:**
- `seed_knowledge_base(db)` — On first run, load `knowledge_base/seed_kb.json` into MongoDB `knowledge_base` collection
- `lookup(db, code)` — Return KB entry for code or `None`
- `increment_occurrence(db, code)` — Update `last_seen` and `occurrence_count`

**KB document schema:**
```json
{
  "code": "SPN 521133",
  "system": "Engine Control",
  "component": "Calibration",
  "meaning": "Calibration mismatch",
  "causes": ["ECU misconfig", "sensor mismatch"],
  "severity": "Medium",
  "first_seen": "timestamp",
  "last_seen": "timestamp",
  "occurrence_count": 0
}
```

**Seed file (`knowledge_base/seed_kb.json`):** Populate the top ~50 codes from `dtc_codes.csv` (SPN 0, SPN 929, SPN 241, etc.) with manually authored meanings/causes/severity. The remaining fields will grow via auto-learning.

---

### Step 4 — Unknown Fault Handler (`db/unknown_faults.py`)

**Function: `save_unknown_fault(db, fault, telemetry_snapshot)`**

- Upserts into `unknown_faults` collection keyed on `code`
- If first occurrence: insert full record with `status: "unresolved"`
- If already exists: increment `occurrence_count`, update `last_seen`

**Unknown fault schema:**
```json
{
  "code": "SPN XXXX",
  "ecu": "...",
  "fmi": "...",
  "raw_description": "...",
  "first_seen": "timestamp",
  "last_seen": "timestamp",
  "occurrence_count": 1,
  "sample_telemetry": {},
  "status": "unresolved"
}
```

---

### Step 5 — Telemetry Context Engine (`core/telemetry_context.py`)

**Function: `build_telemetry_snapshot(raw_record)`**

Extract key signals from vehicle record:
- `engineCoolantTemperature`, `engineOilPressure`, `speed`, `fuelLevel`, `defLevel`, `engineSpeed`

**Function: `adjust_severity(base_severity, fault_code, telemetry)`**

Apply threshold rules to escalate severity:
- Coolant temp > 105°C + engine fault → escalate one level
- Oil pressure < 20 PSI + engine fault → escalate to Critical
- Returns adjusted severity string

---

### Step 6 — LLM Layer (`llm/prompts.py` + `llm/hf_client.py`)

**`hf_client.py`:** Mirror `mydriverbook-compliance/llm/hf_client.py` exactly — same HuggingFace endpoint pattern with `DeepSeek-V3.2`, temperature 0.0, output capped at 512 tokens.

**`prompts.py`:** New DTC-specific prompts.

System prompt: Expert vehicle diagnostics AI. Ground all answers in the provided knowledge base. Do not invent causes. Output must be raw single-line JSON only.

Human prompt template (filled per fault):
```
Fault Code: {code}
ECU: {ecu}
FMI: {fmi}
Raw Description: {raw_desc}
Telemetry: {telemetry}
Knowledge Base Entry: {kb_entry}
```

**Output JSON schema:**
```json
{
  "purpose": "...",
  "issue": "...",
  "impact": "...",
  "severity": "Low|Medium|High|Critical",
  "urgency": "Ignore|Monitor|Schedule Maintenance|Immediate Action",
  "confidence": 0
}
```

---

### Step 7 — LanGraph Orchestration (`orchestration/diagnostic_graph.py`)

**State:** `DiagnosticState(TypedDict)` with fields: `raw_input`, `parsed_faults`, `diagnostics`, `unknown_codes`

**6-node DAG:**

```
parse_node → kb_lookup_node → telemetry_node → llm_node → explain_node → store_node → END
```

1. **parse_node** — Calls `dtc_parser.parse_dtc_records()` → populates `parsed_faults`
2. **kb_lookup_node** — For each fault: lookup in KB. Annotates each fault with `kb_entry` (or `None`) and marks `is_unknown`
3. **telemetry_node** — Builds telemetry snapshot, runs `adjust_severity()` per fault
4. **llm_node** — Diagnostic agent: calls LLM per fault; outputs `purpose`, `issue`, `impact`, `severity`, `urgency`, `confidence`
5. **explain_node** — Explainability agent: second LLM call per fault; outputs `explanation` (root cause in plain language), `resolution_steps` (numbered list), `who_can_fix`, `parts_likely_needed`, `estimated_downtime`
6. **store_node** — Saves unknown faults via `save_unknown_fault()`; writes full diagnostic+explanation to `diagnostics_output`; increments KB occurrence counts

---

### Step 8 — API Layer (`api.py`)

Mirror the structure of `mydriverbook-compliance/api.py`.

**Pydantic models:**
```python
class FaultRequest(BaseModel):
    vehicleId: str
    dtcJson: dict
    telemetry: dict

class FaultResponse(BaseModel):
    vehicleId: str
    diagnostics: list[dict]
```

**Endpoints:**
- `POST /analyze-fault` — Main diagnostic endpoint; runs LanGraph; returns diagnostics list
- `GET /knowledge-base` — List all KB entries (for inspection/admin)
- `GET /unknown-faults` — List unresolved unknown fault codes (for review queue)

**Startup:** Seed KB if empty (`seed_knowledge_base(db)`), build graph.

---

## Data Flow Summary

```
POST /analyze-fault
    ↓ dtcJson + telemetry
parse_node → list of structured fault dicts
    ↓
kb_lookup_node → each fault annotated with KB entry or marked unknown
    ↓
telemetry_node → severity adjusted by live signals
    ↓
llm_node → each fault gets AI interpretation JSON
    ↓
store_node → unknowns → unknown_faults collection
             knowns  → diagnostics_output collection
    ↓
Response: [{code, severity, urgency, issue, impact, confidence}, ...]
```

---

## Reuse from Existing Codebase

| New File | Pattern Reused From |
|---|---|
| `llm/hf_client.py` | `mydriverbook-compliance/llm/hf_client.py` (near-identical) |
| `orchestration/diagnostic_graph.py` | `mydriverbook-compliance/orchestration/driver_graph.py` (same LanGraph DAG structure) |
| `api.py` | `mydriverbook-compliance/api.py` (same FastAPI + startup + Pydantic pattern) |
| `db/connection.py` | `mydriverbook-compliance/parse_data_db/db_connection.py` |

---

## Seed Knowledge Base Strategy

Use `driverbook/dtc_codes.csv` as the source for the top 50 codes by occurrence count. For each code, author a KB entry with:
- `system` and `component` derived from ECU name and code type
- `meaning`, `causes`, `severity` hand-authored for the most frequent codes (SPN 0, SPN 929, SPN 241, SPN 242, P0128, P0299, etc.)

This avoids empty-KB cold-start and gives the LLM grounding data immediately.

---

## Verification

1. **Unit test DTC parser:** Feed a raw row from `dtc_records.csv` into `dtc_parser.py`, assert structured output matches expected schema.
2. **KB lookup:** Insert a test code into MongoDB, call `lookup()`, confirm return.
3. **Unknown fault capture:** Send a request with a code not in KB, confirm it appears in `unknown_faults` collection.
4. **End-to-end API test:**
   ```bash
   curl -X POST http://localhost:8000/analyze-fault \
     -H "Content-Type: application/json" \
     -d '{"vehicleId": "test-001", "dtcJson": {...}, "telemetry": {...}}'
   ```
   Assert response contains `diagnostics` array with `severity` and `urgency` fields.
5. **Telemetry escalation:** Inject a fault with high coolant temp in telemetry; confirm severity is escalated.
6. **GET /unknown-faults:** Confirm unknown codes are queryable for the review pipeline.
