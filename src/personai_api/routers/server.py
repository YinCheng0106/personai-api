from fastapi import APIRouter, status

from src.personai_api.models.server_schema import ServerStatus

router = APIRouter(prefix="/server", tags=["伺服器"])

# 伺服器狀態
@router.get(
    path="",
    summary="取得伺服器狀態",
    description="用來讓前端確認後端 API 伺服器是否正在執行中。",
    response_model=ServerStatus,
    status_code=status.HTTP_200_OK,
)
async def server_status():
    return {"status": "success", "detail": "伺服器正常運作"}
