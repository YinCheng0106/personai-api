"""SQLAlchemy persistence models for PersonAI application data."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, Float, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from personai_api.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class WorkoutRecordModel(Base):
    __tablename__ = "workout_records"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    exercise_type: Mapped[str] = mapped_column(String(32), index=True)
    reps: Mapped[int] = mapped_column(Integer)
    sets: Mapped[int] = mapped_column(Integer, default=1)
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    calories_burned: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_intensity: Mapped[str] = mapped_column(String(16), default="moderate")
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class InBodyProfileModel(Base):
    __tablename__ = "inbody_profiles"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    weight_kg: Mapped[float] = mapped_column(Float)
    height_cm: Mapped[float] = mapped_column(Float)
    age: Mapped[int] = mapped_column(Integer)
    gender: Mapped[str] = mapped_column(String(16))
    body_fat_pct: Mapped[float] = mapped_column(Float)
    skeletal_muscle_mass_kg: Mapped[float] = mapped_column(Float)
    body_fat_mass_kg: Mapped[float] = mapped_column(Float)
    total_body_water_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    visceral_fat_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class BodyProfileModel(Base):
    """Preferred current basic body information; a row is optional per user."""

    __tablename__ = "body_profiles"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class BodyMeasurementModel(Base):
    """Preferred immutable-by-default history of real composition measurements."""

    __tablename__ = "body_measurements"
    __table_args__ = (
        CheckConstraint(
            "body_fat_pct IS NOT NULL "
            "OR skeletal_muscle_mass_kg IS NOT NULL "
            "OR body_fat_mass_kg IS NOT NULL "
            "OR total_body_water_kg IS NOT NULL "
            "OR visceral_fat_level IS NOT NULL",
            name="ck_bm_has_composition",
        ),
        Index(
            "ix_body_measurements_user_measured_at",
            "user_id",
            "measured_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(128), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_fat_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    skeletal_muscle_mass_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_fat_mass_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_body_water_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    visceral_fat_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Retained only for lossless legacy InBody migration/compatibility.
    legacy_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    legacy_gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
