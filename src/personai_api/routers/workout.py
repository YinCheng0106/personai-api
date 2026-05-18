"""
運動紀錄 API

端點：
  GET  /wk/{user_id}          — 取得使用者所有運動紀錄
  POST /wk/{user_id}/record   — 儲存單次運動紀錄
  GET  /wk/{user_id}/summary  — 運動統計報表（依運動類型分組）
  GET  /wk/{user_id}/daily    — 每日統計摘要（供熱力圖使用）
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Path, status

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
    generate_daily_summary,
    generate_workout_summary,
)

router = APIRouter(prefix="/wk", tags=["運動紀錄"])

# 暫存運動紀錄（後續可替換為資料庫）
workout_db: dict[str, list[WorkoutRecord]] = {}

USER_ID_PATTERN = r"^u\d{3}$"

@router.get(
    "/{user_id}",
    summary="取得運動紀錄",
    description="取得該使用者的所有運動紀錄列表。",
    response_model=list[WorkoutRecordOutput],
)
async def get_workouts(
    user_id: str = Path(..., pattern=USER_ID_PATTERN, description="使用者 ID"),
):
    records = workout_db.get(user_id, [])
    return [
        WorkoutRecordOutput(
            user_id=r.user_id,
            exercise_type=r.exercise_type.value
            if isinstance(r.exercise_type, ExerciseType)
            else r.exercise_type,
            reps=r.reps,
            sets=r.sets,
            duration_sec=r.duration_sec,
            calories_burned=r.calories_burned,
            avg_intensity=r.avg_intensity.value
            if isinstance(r.avg_intensity, ExerciseIntensity)
            else r.avg_intensity,
            errors_count=r.errors_count,
            timestamp=r.timestamp,
        )
        for r in records
    ]


@router.post(
    "/{user_id}/record",
    summary="儲存運動紀錄",
    description="運動結束後，前端將本次運動的次數、時長、卡路里等資訊傳送至後端儲存。",
    response_model=WorkoutRecordOutput,
    status_code=status.HTTP_201_CREATED,
)
async def save_workout(
    data: WorkoutRecordInput,
    user_id: str = Path(..., pattern=USER_ID_PATTERN, description="使用者 ID"),
):
    try:
        exercise_type = ExerciseType(data.exercise_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支援的運動類型: {data.exercise_type}",
        )

    try:
        intensity = ExerciseIntensity(data.avg_intensity)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支援的強度: {data.avg_intensity}",
        )

    record = WorkoutRecord(
        user_id=user_id,
        exercise_type=exercise_type,
        reps=data.reps,
        sets=data.sets,
        duration_sec=data.duration_sec,
        calories_burned=data.calories_burned,
        avg_intensity=intensity,
        errors_count=data.errors_count,
        timestamp=datetime.now().isoformat(),
    )

    workout_db.setdefault(user_id, []).append(record)

    return WorkoutRecordOutput(
        user_id=record.user_id,
        exercise_type=record.exercise_type.value,
        reps=record.reps,
        sets=record.sets,
        duration_sec=record.duration_sec,
        calories_burned=record.calories_burned,
        avg_intensity=record.avg_intensity.value,
        errors_count=record.errors_count,
        timestamp=record.timestamp,
    )


@router.get(
    "/{user_id}/summary",
    summary="運動統計報表",
    description="依運動類型分組統計總次數、總卡路里、每組平均次數、錯誤率等。",
    response_model=list[WorkoutSummaryItem],
)
async def get_workout_summary(
    user_id: str = Path(..., pattern=USER_ID_PATTERN, description="使用者 ID"),
):
    records = workout_db.get(user_id, [])
    df = generate_workout_summary(records)
    return df.to_dict(orient="records")


@router.get(
    "/{user_id}/daily",
    summary="每日統計摘要",
    description="依日期分組的每日運動統計，供前端熱力圖與折線圖使用。",
    response_model=list[DailySummaryItem],
)
async def get_daily_summary(
    user_id: str = Path(..., pattern=USER_ID_PATTERN, description="使用者 ID"),
):
    records = workout_db.get(user_id, [])
    df = generate_daily_summary(records)
    return df.to_dict(orient="records")
