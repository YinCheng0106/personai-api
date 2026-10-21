"""Preferred body-profile API for current basics and measurement history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from personai_api.auth import CurrentUser, get_current_user
from personai_api.database import get_db
from personai_api.db_models import BodyMeasurementModel, BodyProfileModel
from personai_api.models.body_profile_schema import (
    COMPOSITION_FIELDS,
    MEASUREMENT_VALUE_FIELDS,
    BodyMeasurementCreate,
    BodyMeasurementOutput,
    BodyMeasurementPatch,
    BodyProfileOutput,
    BodyProfilePatch,
    BodyProfileState,
)
from personai_api.services.inbody import (
    calculate_bmi,
    calculate_bmr_katch_mcardle,
    calculate_lean_body_mass,
)

router = APIRouter(prefix="/body-profile", tags=["Body Profile"])


def _owned_measurement(
    db: Session, user_id: str, measurement_id: str
) -> BodyMeasurementModel:
    statement = select(BodyMeasurementModel).where(
        BodyMeasurementModel.id == measurement_id,
        BodyMeasurementModel.user_id == user_id,
    )
    row = db.scalar(statement)
    if row is None:
        raise HTTPException(status_code=404, detail="Body measurement not found")
    return row


def _has_measurement(db: Session, user_id: str) -> bool:
    statement = (
        select(BodyMeasurementModel.id)
        .where(BodyMeasurementModel.user_id == user_id)
        .limit(1)
    )
    return db.scalar(statement) is not None


def _profile_output(db: Session, user_id: str) -> BodyProfileOutput:
    row = db.get(BodyProfileModel, user_id)
    has_basic = row is not None and (
        row.height_cm is not None or row.weight_kg is not None
    )
    if _has_measurement(db, user_id):
        profile_state = BodyProfileState.MEASURED
    elif has_basic:
        profile_state = BodyProfileState.BASIC
    else:
        profile_state = BodyProfileState.UNKNOWN

    height_cm = row.height_cm if row is not None else None
    weight_kg = row.weight_kg if row is not None else None
    bmi = (
        calculate_bmi(weight_kg, height_cm)
        if weight_kg is not None and height_cm is not None
        else None
    )
    return BodyProfileOutput(
        state=profile_state,
        height_cm=height_cm,
        weight_kg=weight_kg,
        bmi=bmi,
        updated_at=row.updated_at if row is not None else None,
    )


def _measurement_output(row: BodyMeasurementModel) -> BodyMeasurementOutput:
    bmi = None
    lean_body_mass = None
    bmr = None
    if row.weight_kg is not None and row.height_cm is not None:
        bmi = calculate_bmi(row.weight_kg, row.height_cm)
    if row.weight_kg is not None and row.body_fat_pct is not None:
        lean_body_mass = calculate_lean_body_mass(row.weight_kg, row.body_fat_pct)
        bmr = calculate_bmr_katch_mcardle(lean_body_mass)

    return BodyMeasurementOutput(
        id=row.id,
        source=row.source,
        source_label=row.source_label,
        measured_at=row.measured_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        height_cm=row.height_cm,
        weight_kg=row.weight_kg,
        body_fat_pct=row.body_fat_pct,
        skeletal_muscle_mass_kg=row.skeletal_muscle_mass_kg,
        body_fat_mass_kg=row.body_fat_mass_kg,
        total_body_water_kg=row.total_body_water_kg,
        visceral_fat_level=row.visceral_fat_level,
        bmi=bmi,
        lean_body_mass_kg=round(lean_body_mass, 1)
        if lean_body_mass is not None
        else None,
        bmr_kcal_day=round(bmr, 1) if bmr is not None else None,
        bmr_is_formula_estimate=bmr is not None,
    )


def _validate_merged_measurement(values: dict[str, object]) -> None:
    if values.get("source") is None or values.get("measured_at") is None:
        raise HTTPException(
            status_code=422, detail="source and measured_at cannot be cleared"
        )
    if not any(values.get(field_name) is not None for field_name in COMPOSITION_FIELDS):
        raise HTTPException(
            status_code=422,
            detail="A body measurement requires at least one composition value",
        )
    weight_kg = values.get("weight_kg")
    if isinstance(weight_kg, int | float):
        for field_name in (
            "skeletal_muscle_mass_kg",
            "body_fat_mass_kg",
            "total_body_water_kg",
        ):
            component = values.get(field_name)
            if isinstance(component, int | float) and component > weight_kg:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field_name} must not exceed weight_kg",
                )


@router.get("/me", response_model=BodyProfileOutput)
def get_body_profile(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BodyProfileOutput:
    """Return an explicit state even when the user has no body data."""
    return _profile_output(db, current_user.id)


@router.patch("/me", response_model=BodyProfileOutput)
def patch_body_profile(
    data: BodyProfilePatch,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BodyProfileOutput:
    row = db.get(BodyProfileModel, current_user.id)
    changes = data.model_dump(exclude_unset=True)
    current_height = row.height_cm if row is not None else None
    current_weight = row.weight_kg if row is not None else None
    height_cm = changes.get("height_cm", current_height)
    weight_kg = changes.get("weight_kg", current_weight)

    if height_cm is None and weight_kg is None:
        if row is not None:
            db.delete(row)
            db.commit()
        return _profile_output(db, current_user.id)

    if row is None:
        row = BodyProfileModel(
            user_id=current_user.id,
            height_cm=height_cm,
            weight_kg=weight_kg,
        )
        db.add(row)
    else:
        row.height_cm = height_cm
        row.weight_kg = weight_kg
    db.commit()
    return _profile_output(db, current_user.id)


@router.post(
    "/me/measurements",
    response_model=BodyMeasurementOutput,
    status_code=status.HTTP_201_CREATED,
)
def create_body_measurement(
    data: BodyMeasurementCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BodyMeasurementOutput:
    row = BodyMeasurementModel(user_id=current_user.id, **data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _measurement_output(row)


@router.get("/me/measurements", response_model=list[BodyMeasurementOutput])
def get_body_measurements(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BodyMeasurementOutput]:
    statement = (
        select(BodyMeasurementModel)
        .where(BodyMeasurementModel.user_id == current_user.id)
        .order_by(
            BodyMeasurementModel.measured_at.desc(),
            BodyMeasurementModel.created_at.desc(),
            BodyMeasurementModel.id.desc(),
        )
    )
    return [_measurement_output(row) for row in db.scalars(statement)]


@router.get("/me/measurements/{measurement_id}", response_model=BodyMeasurementOutput)
def get_body_measurement(
    measurement_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BodyMeasurementOutput:
    return _measurement_output(_owned_measurement(db, current_user.id, measurement_id))


@router.patch("/me/measurements/{measurement_id}", response_model=BodyMeasurementOutput)
def correct_body_measurement(
    measurement_id: str,
    data: BodyMeasurementPatch,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BodyMeasurementOutput:
    row = _owned_measurement(db, current_user.id, measurement_id)
    changes = data.model_dump(exclude_unset=True)
    merged: dict[str, object] = {
        "source": row.source,
        "source_label": row.source_label,
        "measured_at": row.measured_at,
        **{
            field_name: getattr(row, field_name)
            for field_name in MEASUREMENT_VALUE_FIELDS
        },
        **changes,
    }
    _validate_merged_measurement(merged)
    for field_name, value in changes.items():
        setattr(row, field_name, value)
    db.commit()
    db.refresh(row)
    return _measurement_output(row)
