# core/telemetry_context.py
# Extracts key telemetry signals and applies threshold rules to adjust fault severity.

_SENTINEL = -6.128e18  # sentinel value for missing data in the source CSV/MongoDB docs

_SEVERITY_ORDER = ["Low", "Medium", "High", "Critical"]

# Threshold constants
_COOLANT_TEMP_HIGH_C = 105.0   # °C — engine overheating threshold
_OIL_PRESSURE_LOW_PSI = 20.0   # PSI — minimum safe oil pressure
_DEF_LEVEL_LOW_PCT = 5.0       # % — DEF tank critically low

# ECU prefixes considered "engine-related" for severity escalation
_ENGINE_ECUS = {"engine", "exhaust emission", "emission"}


def build_telemetry_snapshot(raw_record: dict) -> dict:
    """Extract key signals from a raw vehicle telemetry record.

    Handles sentinel values (large negative floats) by returning None for those fields.

    Args:
        raw_record: Flat dict from a vehicle document (CSV row or MongoDB doc).

    Returns:
        Dict with clean numeric values or None for unavailable signals.
    """
    def _safe(key: str):
        val = raw_record.get(key)
        if val is None:
            return None
        try:
            fval = float(val)
        except (ValueError, TypeError):
            return None
        return None if fval < _SENTINEL * 0.5 else fval

    return {
        "engineCoolantTemperature": _safe("engineCoolantTemperature"),
        "engineOilPressure": _safe("engineOilPressure"),
        "speed": _safe("speed"),
        "fuelLevel": _safe("fuelLevel"),
        "defLevel": _safe("defLevel"),
        "engineSpeed": _safe("engineSpeed"),
    }


def _escalate(severity: str) -> str:
    """Move severity up one level, capping at Critical."""
    idx = _SEVERITY_ORDER.index(severity) if severity in _SEVERITY_ORDER else 0
    return _SEVERITY_ORDER[min(idx + 1, len(_SEVERITY_ORDER) - 1)]


def _is_engine_related(fault: dict) -> bool:
    """Return True if the fault's ECU is engine or emissions related."""
    ecu = (fault.get("ecu") or "").lower()
    return any(prefix in ecu for prefix in _ENGINE_ECUS)


def adjust_severity(base_severity: str, fault: dict, telemetry: dict) -> str:
    """Apply telemetry-based rules to escalate a fault's severity.

    Rules (applied in order; each can escalate at most once):
    1. Coolant temp > 105°C + engine-related fault → escalate one level
    2. Oil pressure < 20 PSI + engine-related fault → escalate to Critical
    3. DEF level < 5% + DEF/emission fault → escalate one level

    Args:
        base_severity: Starting severity string from KB or LLM output.
        fault:         Structured fault dict (must contain "ecu" and "code").
        telemetry:     Output of build_telemetry_snapshot().

    Returns:
        Adjusted severity string.
    """
    severity = base_severity if base_severity in _SEVERITY_ORDER else "Low"
    engine_related = _is_engine_related(fault)
    code_upper = (fault.get("code") or "").upper()

    coolant = telemetry.get("engineCoolantTemperature")
    if coolant is not None and coolant > _COOLANT_TEMP_HIGH_C and engine_related:
        severity = _escalate(severity)

    oil_pressure = telemetry.get("engineOilPressure")
    if oil_pressure is not None and oil_pressure < _OIL_PRESSURE_LOW_PSI and engine_related:
        severity = "Critical"

    def_level = telemetry.get("defLevel")
    is_def_fault = "DEF" in code_upper or "1761" in code_upper or "4374" in code_upper or "4375" in code_upper or "5435" in code_upper
    if def_level is not None and def_level < _DEF_LEVEL_LOW_PCT and is_def_fault:
        severity = _escalate(severity)

    return severity
