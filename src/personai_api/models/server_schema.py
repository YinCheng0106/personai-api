from pydantic import BaseModel, Field

# 伺服器狀態資料模型
class ServerStatus(BaseModel):
    status: str = Field(..., description="伺服器狀態")
    detail: str = Field(..., description="伺服器詳細訊息")
