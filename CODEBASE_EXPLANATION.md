# DriverBook Diagnostics Agent - Complete Code Explanation

## Table of Contents
1. [System Overview](#system-overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Module-by-Module Breakdown](#module-by-module-breakdown)
5. [Data Flow Example](#data-flow-example)
6. [How Everything Works](#how-everything-works)
7. [API Reference](#api-reference)
8. [Setup & Deployment](#setup--deployment)

---

## System Overview

**DriverBook Diagnostics Agent** is an AI-powered vehicle fault code analysis service for commercial fleet management. It analyzes vehicle Diagnostic Trouble Codes (DTCs) and Suspect Parameter Numbers (SPNs) and produces intelligent, plain-language diagnostics and resolution steps for fleet managers.

### Key Features
- **Automated Fault Code Parsing** - Extracts J1939 SPN and OBD-II DTC codes from vehicle data
- **Knowledge Base Lookup** - Grounds diagnoses in MongoDB knowledge base to prevent hallucination
- **Telemetry Context** - Adjusts severity based on real-time vehicle signals (temperature, pressure, etc.)
- **Two-Agent LLM Pipeline** - One agent diagnoses, another explains with resolution steps
- **Auto-Learning** - Automatically learns and stores unknown fault codes for future use
- **REST API** - FastAPI endpoints for easy fleet management integration

### Tech Stack
- **Backend**: Python 3.11+, FastAPI, Uvicorn
- **Database**: MongoDB (knowledge base, unknown faults, diagnostics history)
- **LLM**: Ollama with Llama 3.1 (local inference)
- **Orchestration**: LanGraph (DAG workflow engine)
- **Libraries**: LangChain, Pydantic, PyMongo

---

## Architecture

### Pipeline Overview

```
REQUEST (vehicleId, dtcJson, telemetry)
    ↓
PARSE NODE → Extract structured faults from dtcJson
    ↓
KB LOOKUP NODE → Look up each code in MongoDB
    ↓
TELEMETRY NODE → Adjust severity using live signals
    ↓
LLM DIAGNOSTIC NODE → Interpret fault (what's wrong, urgency)
    ↓
LLM EXPLAIN NODE → Explain root cause + resolution steps
    ↓
STORE NODE → Persist results + auto-learn unknowns
    ↓
RESPONSE (diagnostics with reasoning)
```

### Two-Agent LLM Pattern

**Agent 1 - Diagnostic Agent**:
- Interprets the fault
- Output: `{purpose, issue, impact, severity, urgency, confidence}`
- Determines what's wrong and how urgent

**Agent 2 - Explainability Agent**:
- Explains root cause and provides fix instructions
- Output: `{explanation, resolution_steps, who_can_fix, parts_needed, estimated_downtime}`
- Gives fleet managers actionable steps

Both agents use the knowledge base as ground truth to prevent hallucination.

---

## Project Structure

```
driverbook-diagnostic-agent/
├── api.py                           # FastAPI REST endpoints
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Container configuration
│
├── core/                            # Business logic
│   ├── dtc_parser.py               # Parse fault codes from JSON
│   ├── knowledge_base.py           # KB seeding, lookup, auto-learning
│   └── telemetry_context.py        # Telemetry signals & severity adjustment
│
├── db/                              # Database layer
│   ├── connection.py               # MongoDB singleton client
│   └── unknown_faults.py           # Track unknown codes
│
├── llm/                             # LLM integration
│   ├── hf_client.py                # Ollama client wrapper
│   └── prompts.py                  # System & user prompts (2 agents)
│
├── orchestration/                   # Workflow
│   └── diagnostic_graph.py         # LanGraph DAG with 6 pipeline nodes
│
├── knowledge_base/                  # Initial data
│   └── seed_kb.json                # Pre-loaded fault definitions
│
└── README.md                        # Original setup guide
```

---

## Module-by-Module Breakdown

### 1. api.py - FastAPI Application

**Purpose**: REST API entry point and HTTP request handling.

**Key Components**:
```python
FaultRequest:
    vehicleId: str
    dtcJson: dict          # {dtcs: {...}, mil: true/false}
    telemetry: dict = {}   # {engineCoolantTemperature, engineOilPressure, ...}

FaultResponse:
    vehicleId: str
    diagnostics: list[dict]  # Per-fault diagnostic results
```

**Endpoints**:
- `POST /analyze-fault` - Run full diagnostic pipeline
- `GET /knowledge-base` - List all KB entries (admin endpoint)
- `GET /unknown-faults` - List unresolved unknown codes

**Startup Flow**:
1. Initialize MongoDB connection
2. Seed knowledge base from `seed_kb.json` (if collection is empty)
3. Build and compile LanGraph diagnostic DAG
4. Print startup confirmation

---

### 2. core/dtc_parser.py - Fault Code Extraction

**Purpose**: Parse raw vehicle fault payload into structured records.

**Input Schema**:
```json
{
  "dtcs": {
    "SPN 521133": {"ecu": "Engine #2", "desc": "FMI 13 Out of Calibration"},
    "SPN 241": {"ecu": "Tire Pressure", "desc": "Low pressure"}
  },
  "mil": true
}
```

**Output Schema** (per fault):
```json
{
  "code": "SPN 521133",
  "ecu": "Engine #2",
  "fmi": 13,                    // Parsed from description using regex
  "description": "FMI 13 Out of Calibration",
  "timestamp": "2026-04-29T10:30:45.123456+00:00",
  "vehicleId": "TRUCK-001",
  "mil": true
}
```

**Key Functions**:
- `_extract_fmi(description)` - Parse FMI value using regex `r"FMI\s+(\d+)"`
- `parse_dtc_records(dtc_records, vehicle_id, timestamp)` - Main extraction function

---

### 3. core/knowledge_base.py - Knowledge Base Management

**Purpose**: Manage the fault code knowledge base (seed, lookup, learning).

**MongoDB Collection**: `knowledge_base`

**Document Schema**:
```json
{
  "code": "SPN 521133",
  "system": "Engine",
  "component": "Fuel Pressure",
  "meaning": "Fuel pressure is out of calibration",
  "causes": ["Faulty fuel pump", "Pressure sensor error"],
  "severity": "High",
  "urgency": "Immediate Action",
  "source": "seed" | "auto_learned",
  "first_seen": "2026-04-29T10:00:00+00:00",
  "last_seen": "2026-04-29T11:30:00+00:00",
  "occurrence_count": 5
}
```

**Key Functions**:
- `seed_knowledge_base(db)` - Load seed_kb.json on first startup (returns count inserted)
- `lookup(db, code)` - Case-insensitive code lookup (returns dict or None)
- `increment_occurrence(db, code)` - Update last_seen and occurrence_count for known codes
- `auto_learn_from_diagnosis(db, fault, diagnostic)` - Create KB entry from LLM output (tagged "auto_learned")

**Auto-Learning Logic**:
- When an unknown code is processed, the LLM diagnostic output is saved as a KB entry
- Tagged with `"source": "auto_learned"` for later review
- Prevents overwriting manually-authored seed entries
- Next time the same code appears, it's found in KB immediately

---

### 4. core/telemetry_context.py - Signal Processing & Severity

**Purpose**: Extract vehicle telemetry signals and adjust fault severity based on thresholds.

**Supported Signals**:
- `engineCoolantTemperature` (°C)
- `engineOilPressure` (PSI)
- `speed` (km/h or mph)
- `fuelLevel` (%)
- `defLevel` (%)
- `engineSpeed` (RPM)

**Severity Levels** (escalation order):
```
Low → Medium → High → Critical
```

**Severity Adjustment Rules**:
1. **Coolant Overheating** (>105°C + engine-related fault) → Escalate one level
2. **Low Oil Pressure** (<20 PSI + engine-related fault) → Escalate to Critical
3. **Low DEF Level** (<5% + DEF/emission fault) → Escalate one level

**Key Functions**:
- `build_telemetry_snapshot(raw_record)` - Extract and sanitize signals
- `adjust_severity(base_severity, fault, telemetry)` - Apply severity escalation rules

**Example**:
```python
# Base severity from KB: "High"
# Engine coolant: 108°C (>105°C) + engine-related fault
# Result: Escalate to "Critical"
```

---

### 5. db/connection.py - MongoDB Connection

**Purpose**: Singleton MongoDB client management.

**Pattern**: Global singleton with lazy initialization.

**Default Configuration**:
- URI: `mongodb://localhost:27017` (or via `MONGO_URI` env var)
- Database: `diagnostics`

**Usage**:
```python
from db.connection import get_db
db = get_db()
collection = db["knowledge_base"]
```

---

### 6. db/unknown_faults.py - Auto-Capture Unknown Codes

**Purpose**: Track and persist fault codes not found in the knowledge base.

**MongoDB Collection**: `unknown_faults`

**Document Schema**:
```json
{
  "code": "SPN 999999",
  "ecu": "New ECU",
  "fmi": 7,
  "raw_description": "Unknown fault",
  "first_seen": "2026-04-29T10:30:45+00:00",
  "last_seen": "2026-04-29T11:30:45+00:00",
  "occurrence_count": 3,
  "status": "unresolved",
  "sample_telemetry": {"engineCoolantTemperature": 92, ...}
}
```

**Key Function**:
- `save_unknown_fault(db, fault, telemetry_snapshot)` - Upsert logic:
  - On first encounter: Insert full document with `status: "unresolved"`
  - On subsequent encounters: Increment occurrence_count, update last_seen & telemetry

---

### 7. llm/hf_client.py - LLM Client

**Purpose**: Wrapper for local Ollama LLM inference.

**Configuration**:
- Model: `llama3.1` (default, configurable via `OLLAMA_MODEL` env var)
- Base URL: `http://localhost:11434` (or via `OLLAMA_BASE_URL` env var)
- Temperature: `0.0` (deterministic output)
- Max tokens: `512`

**Key Function**:
```python
def get_llm():
    return ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "llama3.1"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0.0,
        num_predict=512,
    )
```

---

### 8. llm/prompts.py - LLM Prompts

**Purpose**: Define system and user prompts for the two LLM agents.

**Diagnostic Agent Output Schema**:
```json
{
  "purpose": "What the affected system does",
  "issue": "What is wrong right now",
  "impact": "What happens if ignored",
  "severity": "Low|Medium|High|Critical",
  "urgency": "Ignore|Monitor|Schedule Maintenance|Immediate Action",
  "confidence": 0-100
}
```

**Explainability Agent Output Schema**:
```json
{
  "explanation": "Plain-language root cause (why)",
  "resolution_steps": ["Step 1", "Step 2", "..."],
  "who_can_fix": "Driver only|Fleet maintenance team|Certified technician required",
  "parts_likely_needed": ["Part 1", "Part 2"],
  "estimated_downtime": "Time estimate (e.g., '2-4 hours')"
}
```

**Key Design Patterns**:
- Output MUST be single-line JSON (no markdown, no code blocks)
- Use knowledge base as ground truth
- Write for fleet managers (non-technical language)
- No hallucination
- Avoid jargon

---

### 9. orchestration/diagnostic_graph.py - LanGraph DAG

**Purpose**: Orchestrate the 6-node diagnostic workflow.

**State Model**:
```python
class DiagnosticState(TypedDict):
    raw_input: dict        # {vehicleId, dtcJson, telemetry}
    parsed_faults: list    # Structured fault records
    diagnostics: list      # Final diagnostic results
    unknown_codes: list    # Unknown codes encountered
```

**Pipeline Nodes** (in order):

1. **parse_node** → Call `parse_dtc_records()`, populate `parsed_faults`
2. **kb_lookup_node** → Call `lookup()` for each fault, collect unknowns
3. **telemetry_node** → Extract signals, call `adjust_severity()`
4. **llm_node** → Call diagnostic agent LLM, parse JSON response
5. **explain_node** → Call explainability agent LLM, add resolution steps
6. **store_node** → Persist to MongoDB, auto-learn unknowns

**Key Logic in store_node**:
```python
for fault in parsed_faults:
    if fault["is_unknown"]:
        # Unknown code processing
        save_unknown_fault(db, fault, telemetry_snapshot)
        auto_learn_from_diagnosis(db, fault, diagnostic)
    else:
        # Known code processing
        increment_occurrence(db, fault["code"])
```

---

## Data Flow Example

### Scenario: Fuel Pressure Fault

**1. Request Received**:
```json
{
  "vehicleId": "TRUCK-001",
  "dtcJson": {
    "dtcs": {"SPN 521133": {"ecu": "Engine #2", "desc": "FMI 13 Out of Calibration"}},
    "mil": true
  },
  "telemetry": {
    "engineCoolantTemperature": 108,
    "engineOilPressure": 18,
    "speed": 0,
    "fuelLevel": 72
  }
}
```

**2. Parse Node**:
- Extract: `{code: "SPN 521133", ecu: "Engine #2", fmi: 13, ...}`

**3. KB Lookup Node**:
- Query: `db.knowledge_base.find_one({"code": "SPN 521133"})`
- Found: Attach KB entry with causes, base severity "High"

**4. Telemetry Node**:
- Build snapshot from telemetry
- Apply rules:
  - Coolant 108°C > 105°C + engine fault → Escalate
  - Oil pressure 18 PSI < 20 PSI + engine fault → Escalate to Critical
- Result: Adjusted severity = "Critical"

**5. LLM Diagnostic Node**:
- Prompt: Include code, KB entry, telemetry
- LLM Response: `{purpose: "Fuel pressure", issue: "Sensor drifted", severity: "Critical", urgency: "Immediate Action", confidence: 92}`
- Override severity with telemetry-adjusted value

**6. LLM Explain Node**:
- Prompt: Include diagnosis + KB + telemetry
- LLM Response: `{explanation: "Sensor aging...", resolution_steps: ["Pull over", "Check connector", ...], who_can_fix: "Certified technician required", parts_likely_needed: ["Fuel pump"], estimated_downtime: "2-4 hours"}`

**7. Store Node**:
- Known code: `db.knowledge_base.update_one(increment_occurrence_count)`
- Insert diagnostic to `db.diagnostics_output`

**8. Response to Client**:
```json
{
  "vehicleId": "TRUCK-001",
  "diagnostics": [
    {
      "code": "SPN 521133",
      "issue": "Fuel pressure sensor reading is out of range",
      "severity": "Critical",
      "urgency": "Immediate Action",
      "explanation": "Your fuel pressure sensor is sending readings that don't match...",
      "resolution_steps": ["Immediately pull over...", "Check fuel pump connector...", ...],
      "who_can_fix": "Certified technician required",
      "parts_likely_needed": ["Fuel pump", "Fuel filter"],
      "estimated_downtime": "2-4 hours"
    }
  ]
}
```

---

## How Everything Works

### Complete Request-to-Response Journey

```
1. HTTP POST /analyze-fault
   ↓ Pydantic validation
2. Initialize DiagnosticState with empty lists
   ↓ Invoke LanGraph
3. Parse Node
   - dtcJson → structured faults
   - Add: code, ecu, fmi, description, timestamp, vehicleId, mil
4. KB Lookup Node
   - Query MongoDB for each code
   - Add: kb_entry, is_unknown flag
   - Collect unknown_codes list
5. Telemetry Node
   - Extract signals from telemetry
   - Apply severity adjustment rules
   - Add: telemetry_snapshot, adjusted_severity
6. LLM Diagnostic Node
   - Build prompt with fault + KB + telemetry
   - Call LLM (Ollama)
   - Parse JSON response (handle markdown)
   - Override severity with adjusted value
   - Create diagnostic record
7. LLM Explain Node
   - Build prompt with diagnostic + KB + telemetry
   - Call LLM
   - Parse JSON response
   - Add explanation, resolution_steps, parts, downtime
8. Store Node
   - For unknown codes: save + auto-learn to KB
   - For known codes: increment occurrence
   - Insert diagnostics to MongoDB
9. Return FaultResponse
   - vehicleId + list of diagnostics
   - HTTP 200 JSON response
```

### Key Concepts

**Fault Code Standards**:
- **J1939 SPN** (Suspect Parameter Number) - Commercial vehicles: `SPN 521133`
- **OBD-II DTC** (Diagnostic Trouble Code) - Light-duty vehicles: `P0420`
- **FMI** (Failure Mode Identifier) - Type of failure (0-31)

**Severity Levels**:
- Low: Non-critical
- Medium: Degraded operation
- High: Major impact
- Critical: Imminent failure

**Urgency Levels**:
- Ignore: No action needed
- Monitor: Watch for issues
- Schedule Maintenance: Plan service
- Immediate Action: Stop vehicle

**Auto-Learning**:
1. Unknown code encountered → LLM generates diagnosis
2. Save to `unknown_faults` for tracking
3. Create KB entry from LLM output (tagged "auto_learned")
4. Next occurrence → Found in KB immediately
5. Fleet manager can review and promote to official KB

---

## API Reference

### POST /analyze-fault

**Request**:
```json
{
  "vehicleId": "TRUCK-001",
  "dtcJson": {
    "dtcs": {
      "SPN 521133": {
        "ecu": "Engine #2",
        "desc": "FMI 13 Out of Calibration"
      }
    },
    "mil": true
  },
  "telemetry": {
    "engineCoolantTemperature": 108,
    "engineOilPressure": 18,
    "speed": 0,
    "fuelLevel": 72
  }
}
```

**Response** (HTTP 200):
```json
{
  "vehicleId": "TRUCK-001",
  "diagnostics": [
    {
      "code": "SPN 521133",
      "ecu": "Engine #2",
      "fmi": 13,
      "purpose": "Fuel pressure regulation",
      "issue": "Fuel pressure sensor reading is out of range",
      "impact": "Engine may lose power",
      "severity": "Critical",
      "urgency": "Immediate Action",
      "confidence": 92,
      "explanation": "Sensor has drifted...",
      "resolution_steps": ["Stop vehicle", "Check connector", "Replace sensor"],
      "who_can_fix": "Certified technician required",
      "parts_likely_needed": ["Fuel pump", "Fuel filter"],
      "estimated_downtime": "2-4 hours"
    }
  ]
}
```

### GET /knowledge-base

Lists all KB entries.

**Response**:
```json
{
  "count": 150,
  "entries": [
    {
      "code": "SPN 521133",
      "system": "Engine",
      "component": "Fuel Pressure",
      "meaning": "Fuel pressure is out of calibration",
      "causes": ["Faulty fuel pump", "Pressure sensor error"],
      "severity": "High",
      "urgency": "Immediate Action",
      "occurrence_count": 12
    }
  ]
}
```

### GET /unknown-faults

Lists unresolved unknown codes.

**Response**:
```json
{
  "count": 3,
  "faults": [
    {
      "code": "SPN 999999",
      "ecu": "New ECU",
      "first_seen": "2026-04-29T10:30:45+00:00",
      "occurrence_count": 5,
      "status": "unresolved",
      "sample_telemetry": {...}
    }
  ]
}
```

---

## Setup & Deployment

### Local Development Setup

**Prerequisites**:
- Python 3.11+
- MongoDB running locally or accessible
- Ollama with llama3.1 pulled

**Step 1: Setup Python Environment**
```bash
cd driverbook-diagnostic-agent
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

{
  "vehicleId": "TRUCK-004",
  "dtcJson": {
    "dtcs": {
      "SPN 521133": {
        "ecu": "Engine #1",
        "desc": "FMI 13 Out of Calibration"
      }
    },
    "mil": true
  },
  "telemetry": {
    "engineCoolantTemperature": 110,
    "engineOilPressure": 35,
    "speed": 0,
    "fuelLevel": 30,
    "defLevel": 20
  }
}
**Step 2: Install MongoDB**
```bash
# Windows/macOS: Download from mongodb.com
# Linux: sudo apt-get install mongodb
# Or Docker: docker run -d -p 27017:27017 mongo:latest
```

**Step 3: Install Ollama**
```bash
# Download from https://ollama.com
ollama pull llama3.1
ollama serve  # Starts on http://localhost:11434
```

**Step 4: Create .env file**
```bash
# .env
MONGO_URI=mongodb://localhost:27017
OLLAMA_MODEL=llama3.1
OLLAMA_BASE_URL=http://localhost:11434
```

**Step 5: Start API**
```bash
uvicorn api:app --reload --port 8000
# API runs on http://localhost:8000
```

### Docker Deployment

**Build Image**:
```bash
docker build -t driverbook-diagnostics:latest .
```

**Run with Docker Compose** (recommended):
```yaml
# docker-compose.yml
version: '3.8'
services:
  mongodb:
    image: mongo:latest
    ports: ["27017:27017"]
    volumes: [mongodb_data:/data/db]

  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes: [ollama_data:/root/.ollama]

  api:
    build: .
    ports: ["8000:8000"]
    depends_on: [mongodb, ollama]
    environment:
      MONGO_URI: mongodb://mongodb:27017
      OLLAMA_BASE_URL: http://ollama:11434
      OLLAMA_MODEL: llama3.1

volumes:
  mongodb_data:
  ollama_data:
```

**Deploy**:
```bash
docker-compose up -d
```

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection |
| `OLLAMA_MODEL` | `llama3.1` | LLM model to use |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |

### Troubleshooting

| Issue | Solution |
|-------|----------|
| MongoDB connection refused | Start MongoDB: `mongosh` or check service status |
| Ollama connection refused | Start Ollama: `ollama serve` |
| Model not found | Download: `ollama pull llama3.1` |
| Port 8000 in use | Change port: `--port 8001` |
| Slow inference | Reduce tokens: Change `num_predict=256` in hf_client.py |

---

## Summary

**DriverBook Diagnostics Agent** provides:

✅ **Automated parsing** of vehicle fault codes
✅ **Knowledge base lookup** to prevent hallucination
✅ **Telemetry context** for intelligent severity adjustment
✅ **Dual-agent LLM** for diagnosis + explanation
✅ **Auto-learning** to improve over time
✅ **REST API** for easy integration
✅ **MongoDB persistence** for audit trail and learning

The system brings **intelligence and automation** to fleet vehicle diagnostics, enabling fleet managers to make quick, informed decisions about maintenance without requiring a team of diagnostic experts.
