from typing import TypedDict

from fastapi import APIRouter, HTTPException, Path

from personai_api.models.user_schema import User

router = APIRouter(prefix="/user", tags=["使用者"])


class UserRecord(TypedDict):
    id: str
    name: str
    height: float
    weight: float


user_db: dict[str, UserRecord] = {
    "u001": {"id": "u001", "name": "Alex", "height": 175.0, "weight": 70.0},
    "u002": {"id": "u002", "name": "Eason", "height": 187.0, "weight": 75.0},
    "u003": {"id": "u003", "name": "Ryan", "height": 170.0, "weight": 165.0},
}

@router.get("/{user_id}", response_model=User)
async def get_user(user_id: str = Path(...)):
    user_data = user_db.get(user_id)
    if not user_data:
        raise HTTPException(status_code=404, detail="找不到該使用者的資料")

    bmi = user_data["weight"] / ((user_data["height"] / 100) ** 2)
    bmi_msg = ""
    if bmi < 18.5:
        bmi_msg = "過輕"
    elif bmi < 24:
        bmi_msg = "正常"
    elif bmi < 27:
        bmi_msg = "過重"
    elif bmi < 30:
        bmi_msg = "輕度肥胖"
    elif bmi < 35:
        bmi_msg = "中度肥胖"
    elif bmi >= 35:
        bmi_msg = "重度肥胖"
    else:
        bmi_msg = "未知"
    return {**user_data, "bmi": round(bmi, 2), "bmi_msg": bmi_msg}
