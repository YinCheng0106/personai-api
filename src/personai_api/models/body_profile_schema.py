"""Preferred API schemas for basic body data and measurement history."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class BodyProfileState(StrEnum):
    UNKNOWN = "unknown"
    BASIC = "basic"
    MEASURED = "measured"


class BodyProfilePatch(BaseModel):
    """Omitted values are unchanged; explicit null values clear a field."""

    height_cm: float | None = Field(None, gt=0, le=300)
    weight_kg: float | None = Field(None, gt=0, le=500)


class BodyProfileOutput(BaseModel):
    state: BodyProfileState
    height_cm: float | None = None
    weight_kg: float | None = None
    bmi: float | None = None
    updated_at: datetime | None = None


class BodyMeasurementValues(BaseModel):
    height_cm: float | None = Field(None, gt=0, le=300)
    weight_kg: float | None = Field(None, gt=0, le=500)
    body_fat_pct: float | None = Field(None, ge=0, le=80)
    skeletal_muscle_mass_kg: float | None = Field(None, ge=0, le=200)
    body_fat_mass_kg: float | None = Field(None, ge=0, le=300)
    total_body_water_kg: float | None = Field(None, ge=0, le=200)
    visceral_fat_level: int | None = Field(None, ge=1, le=20)


class BodyMeasurementCreate(BodyMeasurementValues):
    source: str = Field(..., min_length=1, max_length=64)
    source_label: str | None = Field(None, max_length=128)
    measured_at: datetime

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source must not be blank")
        return normalized

    @field_validator("source_label")
    @classmethod
    def normalize_source_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_composition_value(self) -> BodyMeasurementCreate:
        if not any(
            getattr(self, field_name) is not None for field_name in COMPOSITION_FIELDS
        ):
            raise ValueError(
                "a body measurement requires at least one composition value"
            )
        if self.weight_kg is not None:
            for field_name in (
                "skeletal_muscle_mass_kg",
                "body_fat_mass_kg",
                "total_body_water_kg",
            ):
                value = getattr(self, field_name)
                if value is not None and value > self.weight_kg:
                    raise ValueError(f"{field_name} must not exceed weight_kg")
        return self


class BodyMeasurementPatch(BodyMeasurementValues):
    source: str | None = Field(None, min_length=1, max_length=64)
    source_label: str | None = Field(None, max_length=128)
    measured_at: datetime | None = None

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("source must not be blank")
        return normalized

    @field_validator("source_label")
    @classmethod
    def normalize_source_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class BodyMeasurementOutput(BodyMeasurementValues):
    id: str
    source: str
    source_label: str | None = None
    measured_at: datetime
    created_at: datetime
    updated_at: datetime
    bmi: float | None = None
    lean_body_mass_kg: float | None = None
    bmr_kcal_day: float | None = None
    bmr_is_formula_estimate: bool = False


COMPOSITION_FIELDS = (
    "body_fat_pct",
    "skeletal_muscle_mass_kg",
    "body_fat_mass_kg",
    "total_body_water_kg",
    "visceral_fat_level",
)

MEASUREMENT_VALUE_FIELDS = (
    "height_cm",
    "weight_kg",
    *COMPOSITION_FIELDS,
)
