"""
InBody 生理數據 API

端點：
  POST /inbody/{user_id}          — 儲存/更新 InBody 資料
  GET  /inbody/{user_id}          — 取得生理數據摘要 (BMI, BMR, LBM)
  POST /inbody/{user_id}/calories — 計算特定運動消耗卡路里
"""

from fastapi import APIRouter, HTTPException, Path, status

from src.personai_api.models.inbody_schema import (
    CalorieRequest,
    CalorieResponse,
    InBodyInput,
    InBodySummary,
)
from src.personai_api.services.inbody import (
    ExerciseIntensity,
    ExerciseType,
    InBodyProfile,
    InBodyService,
    get_mets,
)

router = APIRouter(prefix="/inbody", tags=["InBody 生理數據"])

# 暫存 InBody 資料（後續可替換為資料庫）
inbody_db: dict[str, InBodyProfile] = {}

# 使用者 ID 驗證 pattern
USER_ID_PATTERN = r"^u\d{3}$"


@router.post(
    "/{user_id}",
    summary="儲存 InBody 資料",
    description="儲存或更新使用者的 InBody 身體組成數據。",
    response_model=InBodySummary,
    status_code=status.HTTP_201_CREATED,
)
async def save_inbody(
    data: InBodyInput,
    user_id: str = Path(..., pattern=USER_ID_PATTERN, description="使用者 ID"),
):
    profile = InBodyProfile(
        weight_kg=data.weight_kg,
        height_cm=data.height_cm,
        age=data.age,
        gender=data.gender,
        body_fat_pct=data.body_fat_pct,
        skeletal_muscle_mass_kg=data.skeletal_muscle_mass_kg,
        body_fat_mass_kg=data.body_fat_mass_kg,
        total_body_water_kg=data.total_body_water_kg,
        visceral_fat_level=data.visceral_fat_level,
    )
    inbody_db[user_id] = profile

    service = InBodyService(profile)
    return service.get_profile_summary()


@router.get(
    "/{user_id}",
    summary="取得生理數據摘要",
    description="回傳使用者的 BMI、BMR、淨體重等計算結果。",
    response_model=InBodySummary,
)
async def get_inbody(
    user_id: str = Path(..., pattern=USER_ID_PATTERN, description="使用者 ID"),
):
    profile = inbody_db.get(user_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到該使用者的 InBody 資料",
        )

    service = InBodyService(profile)
    return service.get_profile_summary()


@router.post(
    "/{user_id}/calories",
    summary="計算運動消耗卡路里",
    description="根據使用者體重與運動類型、時長、強度，精算消耗的卡路里。",
    response_model=CalorieResponse,
)
async def calculate_exercise_calories(
    data: CalorieRequest,
    user_id: str = Path(..., pattern=USER_ID_PATTERN, description="使用者 ID"),
):
    profile = inbody_db.get(user_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到該使用者的 InBody 資料，請先建立 InBody 資料",
        )

    try:
        exercise_type = ExerciseType(data.exercise_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支援的運動類型: {data.exercise_type}，請使用 squat 或 pushup",
        )

    try:
        intensity = ExerciseIntensity(data.intensity)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支援的強度: {data.intensity}，請使用 light / moderate / vigorous",
        )

    service = InBodyService(profile)
    mets = get_mets(exercise_type, intensity)
    calories = service.calculate_exercise_calories(
        exercise_type, data.duration_min, intensity
    )

    return CalorieResponse(
        exercise_type=data.exercise_type,
        duration_min=data.duration_min,
        intensity=data.intensity,
        mets=mets,
        calories_burned=calories,
    )
