"""Persistent workout records and aggregate reporting API."""

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from personai_api.database import get_db
from personai_api.db_models import WorkoutRecordModel
from personai_api.models.workout_schema import (
    DailySummaryItem,
    WorkoutRecordInput,
    WorkoutRecordOutput,
    WorkoutSummaryItem,
)
from personai_api.services.inbody import (
    ExerciseIntensity,
    ExerciseType,
    WorkoutRecord,
    calculate_form_score,
    generate_daily_summary,
    generate_workout_summary,
)

router = APIRouter(prefix="/wk", tags=["運動紀錄"])
USER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def _to_service(row: WorkoutRecordModel) -> WorkoutRecord:
    return WorkoutRecord(
        user_id=row.user_id,
        exercise_type=ExerciseType(row.exercise_type),
        reps=row.reps,
        sets=row.sets,
        duration_sec=row.duration_sec,
        calories_burned=row.calories_burned,
        avg_intensity=ExerciseIntensity(row.avg_intensity),
        errors_count=row.errors_count,
        timestamp=row.timestamp.isoformat(),
    )


def _to_output(row: WorkoutRecordModel) -> WorkoutRecordOutput:
    return WorkoutRecordOutput(
        id=row.id,
        user_id=row.user_id,
        exercise_type=row.exercise_type,
        reps=row.reps,
        sets=row.sets,
        duration_sec=row.duration_sec,
        calories_burned=row.calories_burned,
        avg_intensity=row.avg_intensity,
        errors_count=row.errors_count,
        form_score=calculate_form_score(row.reps, row.errors_count),
        timestamp=row.timestamp,
    )


def _records(db: Session, user_id: str) -> list[WorkoutRecordModel]:
    statement = (
        select(WorkoutRecordModel)
        .where(WorkoutRecordModel.user_id == user_id)
        .order_by(WorkoutRecordModel.timestamp.desc())
    )
    return list(db.scalars(statement))


@router.get("/{user_id}", response_model=list[WorkoutRecordOutput])
def get_workouts(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
):
    return [_to_output(row) for row in _records(db, user_id)]


@router.post(
    "/{user_id}/record",
    response_model=WorkoutRecordOutput,
    status_code=status.HTTP_201_CREATED,
)
def save_workout(
    data: WorkoutRecordInput,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
):
    try:
        exercise_type = ExerciseType(data.exercise_type)
        intensity = ExerciseIntensity(data.avg_intensity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row = WorkoutRecordModel(
        user_id=user_id,
        exercise_type=exercise_type.value,
        reps=data.reps,
        sets=data.sets,
        duration_sec=data.duration_sec,
        calories_burned=data.calories_burned,
        avg_intensity=intensity.value,
        errors_count=data.errors_count,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_output(row)


@router.get("/{user_id}/summary", response_model=list[WorkoutSummaryItem])
def get_workout_summary(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
):
    frame = generate_workout_summary([_to_service(row) for row in _records(db, user_id)])
    return frame.to_dict(orient="records")


@router.get("/{user_id}/daily", response_model=list[DailySummaryItem])
def get_daily_summary(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
):
    frame = generate_daily_summary([_to_service(row) for row in _records(db, user_id)])
    return frame.to_dict(orient="records")
