from fastapi import FastAPI, HTTPException, Path, status
from pydantic import BaseModel, Field

class ServerStatus(BaseModel):
    status: str = Field(..., description="伺服器狀態")
    detail: str = Field(..., description="伺服器詳細訊息")

# 使用者資料模型
class User(BaseModel):
    id: str = Field(..., description="使用者 ID")
    name: str = Field(..., description="使用者名稱")
    height: float = Field(..., description="身高 (公分)")
    weight: float = Field(..., description="體重 (公斤)")
    bmi: float = Field(..., description="身體質量指數 (計算結果)")
    bmi_msg: str = Field(..., description="BMI 訊息")


# 使用者資料 (模擬)
user_db = {
    "u001": {"id": "u001", "name": "Alex", "height": 175.0, "weight": 70.0},
    "u002": {"id": "u002", "name": "Eason", "height": 187.0, "weight": 75.0},
}

# API 標題
app = FastAPI(
    title="PersonAI",
    description="提供即時姿態分析與生理數據運算的後端服務",
    version="0.1.0",
)


# 伺服器狀態
@app.get(
    path="/server",
    summary="取得伺服器狀態",
    description="用來讓前端確認後端 API 伺服器是否正在執行中。",
    tags=["伺服器"],
    response_model=ServerStatus,
    status_code=status.HTTP_200_OK,
)
async def server_status():
    return {"status": "success", "detail": "伺服器正常運作"}


# 取得使用者資料
@app.get(
    path="/user/{user_id}",
    summary="取得使用者資料",
    description="請輸入以小寫 'u' 開頭，後面接 3 位數字的 ID (例如：u001, u002)",
    tags=["使用者"],
    response_model=User,
    status_code=status.HTTP_200_OK,
)
async def get_user(
    user_id: str = Path(
        default=...,
        title="使用者 ID",
        description="請輸入以小寫 'u' 開頭，後面接 3 位數字的 ID (例如：u001, u002)",
        min_length=4,
        max_length=4,
        pattern="^u\\d{3}$",
    ),
):
    user_data = user_db.get(user_id)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="找不到該使用者的資料"
        )
    bmi = user_data["weight"] / (user_data["height"] / 100) ** 2
    user_data["bmi"] = bmi

    bmi_msg = "正常" if bmi < 25 else "過重" if bmi < 30 else "肥胖"

    response_data = {
        **user_data,
        "bmi": round(bmi, 2),
        "bmi_msg": bmi_msg,
    }

    return response_data
