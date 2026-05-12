from pydantic import BaseModel, Field

# 使用者資料模型
class User(BaseModel):
    id: str = Field(..., description="使用者 ID")
    name: str = Field(..., description="使用者名稱")
    height: float = Field(..., description="身高 (公分)")
    weight: float = Field(..., description="體重 (公斤)")
    bmi: float = Field(..., description="身體質量指數 (計算結果)")
    bmi_msg: str = Field(..., description="BMI 訊息")
