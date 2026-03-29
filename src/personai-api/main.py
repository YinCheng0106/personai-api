import json

from fastapi import FastAPI, HTTPException, Path, status
from pydantic import BaseModel


class User(BaseModel):
    id: str
    name: str
    height: float
    weight: float


app = FastAPI(
    title="PersonAI",
    description="提供即時姿態分析與生理數據運算的後端服務",
    version="0.1.0",
)

user_db = {
    "u001": {"id": "u001", "name": "Alex", "height": 175.0, "weight": 70.0},
    "u002": {"id": "u002", "name": "Eason", "height": 187.0, "weight": 75.0},
}


@app.get(
    path="/server",
    summary="取得伺服器狀態",
    description="用來讓前端確認後端 API 伺服器是否正在執行中。",
    tags=["伺服器"],
    status_code=status.HTTP_200_OK,
)
async def server_status():
    return {"status": "success", "detail": "伺服器正常運作"}


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
    return user_data
