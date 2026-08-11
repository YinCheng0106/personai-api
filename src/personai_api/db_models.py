"""SQLAlchemy persistence models for PersonAI application data."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, Integer, String
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
    calories_burned: Mapped[float] = mapped_column(Float, default=0.0)
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
