from datetime import datetime

from pydantic import BaseModel, Field

# 前端送出的運動紀錄
class WorkoutRecordInput(BaseModel):
    exercise_type: str = Field(..., description="運動類型 (squat / pushup)")
    reps: int = Field(..., ge=0, description="完成次數")
    sets: int = Field(1, ge=1, description="組數")
    duration_sec: float = Field(0.0, ge=0, description="運動時長 (秒)")
    calories_burned: float = Field(0.0, ge=0, description="消耗卡路里 (kcal)")
    avg_intensity: str = Field("moderate", description="平均強度 (light / moderate / vigorous)")
    errors_count: int = Field(0, ge=0, description="姿勢錯誤次數")

# 回傳的運動紀錄（含 user_id 與 timestamp）
class WorkoutRecordOutput(BaseModel):
    id: str = Field(..., description="紀錄 ID")
    user_id: str = Field(..., description="使用者 ID")
    exercise_type: str = Field(..., description="運動類型")
    reps: int = Field(..., description="完成次數")
    sets: int = Field(..., description="組數")
    duration_sec: float = Field(..., description="運動時長 (秒)")
    calories_burned: float = Field(..., description="消耗卡路里 (kcal)")
    avg_intensity: str = Field(..., description="平均強度")
    errors_count: int = Field(..., description="姿勢錯誤次數")
    form_score: int = Field(..., ge=0, le=100, description="姿勢分數")
    timestamp: datetime = Field(..., description="紀錄時間")

# 依運動類型的統計摘要
class WorkoutSummaryItem(BaseModel):
    exercise_type: str = Field(..., description="運動類型")
    total_reps: int = Field(..., description="總次數")
    total_sets: int = Field(..., description="總組數")
    total_calories: float = Field(..., description="總消耗卡路里 (kcal)")
    total_duration_min: float = Field(..., description="總運動時長 (分鐘)")
    avg_reps_per_set: float = Field(..., description="每組平均次數")
    error_rate: float = Field(..., description="錯誤率")
    session_count: int = Field(..., description="訓練場次")
    avg_form_score: float = Field(..., description="平均姿勢分數")

# 每日統計摘要（供熱力圖使用）
class DailySummaryItem(BaseModel):
    date: str = Field(..., description="日期 (YYYY-MM-DD)")
    total_calories: float = Field(..., description="總消耗卡路里 (kcal)")
    total_duration_min: float = Field(..., description="總運動時長 (分鐘)")
    workout_count: int = Field(..., description="運動次數")
    total_reps: int = Field(..., description="總次數")
