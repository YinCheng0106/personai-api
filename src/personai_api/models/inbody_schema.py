from pydantic import BaseModel, Field

# 前端送出的 InBody 資料
class InBodyInput(BaseModel):
    weight_kg: float = Field(..., description="體重 (公斤)")
    height_cm: float = Field(..., description="身高 (公分)")
    age: int = Field(..., description="年齡")
    gender: str = Field(..., description="性別 (male / female)")
    body_fat_pct: float = Field(..., description="體脂率 (%)")
    skeletal_muscle_mass_kg: float = Field(..., description="骨骼肌重 (公斤)")
    body_fat_mass_kg: float = Field(..., description="體脂肪重 (公斤)")
    total_body_water_kg: float | None = Field(None, description="身體水分 (公斤)")
    visceral_fat_level: int | None = Field(None, description="內臟脂肪等級 (1~20)")

# 回傳的生理數據摘要（含 BMR、LBM 等計算結果）
class InBodySummary(BaseModel):
    weight_kg: float = Field(..., description="體重 (公斤)")
    height_cm: float = Field(..., description="身高 (公分)")
    bmi: float = Field(..., description="BMI 身體質量指數")
    bmi_category: str = Field(..., description="BMI 分類")
    body_fat_pct: float = Field(..., description="體脂率 (%)")
    skeletal_muscle_mass_kg: float = Field(..., description="骨骼肌重 (公斤)")
    lean_body_mass_kg: float = Field(..., description="淨體重 (公斤)")
    bmr_kcal_day: float = Field(..., description="每日基礎代謝率 (kcal)")

# 卡路里計算請求
class CalorieRequest(BaseModel):
    exercise_type: str = Field(..., description="運動類型 (squat / pushup)")
    duration_min: float = Field(..., description="運動時間 (分鐘)")
    intensity: str = Field("moderate", description="運動強度 (light / moderate / vigorous)")

# 卡路里計算回應
class CalorieResponse(BaseModel):
    exercise_type: str = Field(..., description="運動類型")
    duration_min: float = Field(..., description="運動時間 (分鐘)")
    intensity: str = Field(..., description="運動強度")
    mets: float = Field(..., description="METs 代謝當量")
    calories_burned: float = Field(..., description="消耗卡路里 (kcal)")
