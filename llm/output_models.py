# llm/output_models.py — Pydantic schemas + helpers for validating LLM responses.

from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

SEVERITY_ORDER = ["Low", "Medium", "High", "Critical"]
URGENCY_VALUES = ("Ignore", "Monitor", "Schedule Maintenance", "Immediate Action")
WHO_CAN_FIX_VALUES = ("Driver only", "Fleet maintenance team", "Certified technician required")

_FIELD_MAX = 500
_LIST_ITEM_MAX = 200
_LIST_MAX_ITEMS = 10


class DiagnosticResult(BaseModel):
    """Schema for one element of the BATCH_SYSTEM_PROMPT JSON array response."""
    code: str = Field(min_length=1, max_length=120)
    purpose: str = Field(default="", max_length=_FIELD_MAX)
    issue: str = Field(default="", max_length=_FIELD_MAX)
    impact: str = Field(default="", max_length=_FIELD_MAX)
    severity: Literal["Low", "Medium", "High", "Critical"] = "Low"
    urgency: Literal["Ignore", "Monitor", "Schedule Maintenance", "Immediate Action"] = "Monitor"
    confidence: int = Field(default=0, ge=0, le=100)

    @field_validator("purpose", "issue", "impact", mode="before")
    @classmethod
    def _coerce_to_str(cls, v):
        if v is None:
            return ""
        return str(v)[:_FIELD_MAX]


class ExplainResult(BaseModel):
    """Schema for one element of the BATCH_EXPLAIN_SYSTEM_PROMPT JSON array response."""
    code: str = Field(min_length=1, max_length=120)
    explanation: str = Field(default="", max_length=_FIELD_MAX)
    resolution_steps: list[str] = Field(default_factory=list)
    who_can_fix: str = Field(default="Fleet maintenance team", max_length=80)
    parts_likely_needed: list[str] = Field(default_factory=list)

    @field_validator("explanation", mode="before")
    @classmethod
    def _coerce_explanation(cls, v):
        if v is None:
            return ""
        return str(v)[:_FIELD_MAX]

    @field_validator("resolution_steps", "parts_likely_needed", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if not isinstance(v, list):
            return []
        cleaned = []
        for item in v[:_LIST_MAX_ITEMS]:
            if item is None:
                continue
            cleaned.append(str(item)[:_LIST_ITEM_MAX])
        return cleaned

    @field_validator("who_can_fix", mode="before")
    @classmethod
    def _coerce_who_can_fix(cls, v):
        s = str(v or "").strip()
        if s in WHO_CAN_FIX_VALUES:
            return s
        return "Fleet maintenance team"


def validate_diagnostic(raw: dict) -> tuple[dict, str | None]:
    """Validate one parsed diagnostic element.

    Returns (validated_dict, error_message). On success error_message is None.
    On schema failure the dict contains {"error": ..., "code": <best-effort>}.
    """
    if not isinstance(raw, dict):
        return ({"error": "schema validation failed: not an object"}, "not a dict")
    try:
        model = DiagnosticResult(**raw)
        return (model.model_dump(), None)
    except ValidationError as exc:
        return (
            {"code": str(raw.get("code", "")), "error": f"schema validation failed: {exc.errors()[0].get('msg', 'invalid')}"},
            str(exc),
        )


def validate_explanation(raw: dict) -> tuple[dict, str | None]:
    """Validate one parsed explanation element. Same contract as validate_diagnostic."""
    if not isinstance(raw, dict):
        return ({"error": "schema validation failed: not an object"}, "not a dict")
    try:
        model = ExplainResult(**raw)
        return (model.model_dump(), None)
    except ValidationError as exc:
        return (
            {"code": str(raw.get("code", "")), "error": f"schema validation failed: {exc.errors()[0].get('msg', 'invalid')}"},
            str(exc),
        )


def clamp_severity(kb_severity: str | None, llm_severity: str) -> str:
    """Allow at most a one-step escalation above KB severity. Prevents random Critical jumps."""
    if not kb_severity or kb_severity not in SEVERITY_ORDER:
        return llm_severity if llm_severity in SEVERITY_ORDER else "Low"
    if llm_severity not in SEVERITY_ORDER:
        return kb_severity
    kb_idx = SEVERITY_ORDER.index(kb_severity)
    llm_idx = SEVERITY_ORDER.index(llm_severity)
    return SEVERITY_ORDER[min(llm_idx, kb_idx + 1)]


def should_auto_learn(diagnostic: dict, confidence_floor: int = 50) -> bool:
    """Decide whether a diagnostic is high-quality enough to write back to the KB."""
    if "error" in diagnostic:
        return False
    confidence = diagnostic.get("confidence", 0)
    try:
        return int(confidence) >= confidence_floor
    except (TypeError, ValueError):
        return False
