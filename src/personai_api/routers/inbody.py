"""InBody profile persistence and calorie estimation API."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from personai_api.database import get_db
from personai_api.db_models import InBodyProfileModel
from personai_api.models.inbody_schema import (
    CalorieRequest,
    CalorieResponse,
    InBodyInput,
    InBodySummary,
)
from personai_api.services.inbody import (
    ExerciseIntensity,
    ExerciseType,
    InBodyProfile,
    InBodyService,
    get_mets,
)

router = APIRouter(prefix="/inbody", tags=["InBody 生理數據"])
USER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def _profile_from_model(row: InBodyProfileModel) -> InBodyProfile:
    return InBodyProfile(
        weight_kg=row.weight_kg,
        height_cm=row.height_cm,
        age=row.age,
        gender=row.gender,
        body_fat_pct=row.body_fat_pct,
        skeletal_muscle_mass_kg=row.skeletal_muscle_mass_kg,
        body_fat_mass_kg=row.body_fat_mass_kg,
        total_body_water_kg=row.total_body_water_kg,
        visceral_fat_level=row.visceral_fat_level,
    )


def _summary(row: InBodyProfileModel) -> InBodySummary:
    values = InBodyService(_profile_from_model(row)).get_profile_summary()
    return InBodySummary(**values, measured_at=row.measured_at)


@router.post(
    "/{user_id}",
    summary="儲存 InBody 資料",
    response_model=InBodySummary,
    status_code=status.HTTP_201_CREATED,
)
def save_inbody(
    data: InBodyInput,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
):
    row = db.get(InBodyProfileModel, user_id)
    values = data.model_dump()
    if row is None:
        row = InBodyProfileModel(user_id=user_id, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
        row.measured_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _summary(row)


@router.get("/{user_id}", response_model=InBodySummary)
def get_inbody(
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
):
    row = db.get(InBodyProfileModel, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="找不到該使用者的 InBody 資料")
    return _summary(row)


@router.post("/{user_id}/calories", response_model=CalorieResponse)
def calculate_exercise_calories(
    data: CalorieRequest,
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
    db: Session = Depends(get_db),
):
    row = db.get(InBodyProfileModel, user_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="找不到該使用者的 InBody 資料，請先建立 InBody 資料",
        )
    try:
        exercise_type = ExerciseType(data.exercise_type)
        intensity = ExerciseIntensity(data.intensity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    service = InBodyService(_profile_from_model(row))
    return CalorieResponse(
        exercise_type=exercise_type.value,
        duration_min=data.duration_min,
        intensity=intensity.value,
        mets=get_mets(exercise_type, intensity),
        calories_burned=service.calculate_exercise_calories(
            exercise_type, data.duration_min, intensity
        ),
    )
