"""
生物力學分析 WebSocket I/O Schema

定義前端送入的 keypoints 格式與後端回傳的分析結果格式
"""

from __future__ import annotations

from pydantic import BaseModel, Field

class KeypointIn(BaseModel):
    """單個關鍵點（MediaPipe Pose landmark）"""

    x: float
    y: float
    z: float = 0.0
    visibility: float = 0.0

class FrameInput(BaseModel):
    """單幀輸入：33 個 keypoints + 選填 timestamp"""

    keypoints: list[KeypointIn] = Field(..., min_length=33, max_length=33)
    timestamp: float | None = None

class FrameOutput(BaseModel):
    """單幀分析結果"""

    rep_count: int
    state: str
    angles: dict[str, float]
    errors: list[str]
    confidence: float
    is_visible: bool
    calories: float

class ErrorOutput(BaseModel):
    """錯誤回傳"""

    error: str
