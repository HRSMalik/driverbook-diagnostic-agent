# DriverBook Diagnostics Frontend (Streamlit)

Interactive web interface to test the DriverBook Diagnostics API.

## Setup

### 1. Install all dependencies (backend + frontend)
From the project root directory:
```bash
pip install -r requirements.txt
```

### 2. Run the Streamlit app
```bash
cd frontend/streamlit_app
streamlit run app.py
```

This opens the app on `http://localhost:8501` in your browser.

## Usage

1. **Enter Vehicle ID** - Give your vehicle a unique identifier (e.g., TRUCK-001)

2. **Add Fault Codes** - Enter SPN or DTC codes with their ECU and description

3. **Set Telemetry** - Use sliders to adjust vehicle signals:
   - Engine coolant temperature
   - Oil pressure
   - Speed
   - Fuel level
   - DEF level
   - Engine speed

4. **Click "Send Diagnostic Request"** - The app sends the payload to the API

5. **View Results** - See the diagnostic output with:
   - Severity & Urgency assessment
   - Root cause explanation
   - Step-by-step resolution steps
   - Parts needed & estimated downtime

## Features

✅ Interactive form for all input parameters
✅ Real-time payload preview in JSON format
✅ Visual display of diagnostic results
✅ Shows API response time
✅ Expandable sections for each fault code
✅ Links to check knowledge base and unknown faults
✅ Error handling and connection validation

## Prerequisites

- API must be running: `uvicorn api:app --reload --port 8000`
- MongoDB must be accessible
- Ollama must be serving on port 11434

## File Structure

```
frontend/
└── streamlit_app/
    ├── app.py                 # Main Streamlit application
    ├── requirements.txt       # Python dependencies
    └── README.md              # This file
```
