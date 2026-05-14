# api_models.py — Pydantic request models with strict ObjectId validation.

import re

from pydantic import BaseModel, Field, field_validator

_OBJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")


class ObjectIdPath(BaseModel):
    """Reusable mixin for validating 24-char hex ObjectIds passed as path params."""

    @staticmethod
    def validate_object_id(value: str, label: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Invalid {label}: must be a string")
        stripped = value.strip()
        if not _OBJECT_ID_RE.match(stripped):
            raise ValueError(f"Invalid {label}: must be a 24-char hex ObjectId, got {value!r}")
        return stripped.lower()


class TenantIdPath(BaseModel):
    tenant_id: str = Field(min_length=24, max_length=24)

    @field_validator("tenant_id")
    @classmethod
    def _validate(cls, v: str) -> str:
        return ObjectIdPath.validate_object_id(v, "tenantId")


class VehicleIdPath(BaseModel):
    vehicle_id: str = Field(min_length=24, max_length=24)

    @field_validator("vehicle_id")
    @classmethod
    def _validate(cls, v: str) -> str:
        return ObjectIdPath.validate_object_id(v, "vehicleId")
