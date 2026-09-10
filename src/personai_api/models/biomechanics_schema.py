"""
生物力學分析 WebSocket I/O Schema

定義前端送入的 keypoints 格式與後端回傳的分析結果格式
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KeypointIn(BaseModel):
    """單個關鍵點（MediaPipe Pose landmark）"""

    x: float = Field(..., allow_inf_nan=False)
    y: float = Field(..., allow_inf_nan=False)
    z: float = Field(0.0, allow_inf_nan=False)
    visibility: float = Field(0.0, ge=0, le=1, allow_inf_nan=False)


class FrameInput(BaseModel):
    """單幀輸入：33 個 keypoints + 選填 timestamp"""

    kind: Literal["landmarks"] = "landmarks"
    frame_id: int = Field(..., ge=0)
    keypoints: list[KeypointIn] = Field(..., min_length=33, max_length=33)
    timestamp: float | None = Field(None, allow_inf_nan=False)

class PoseMissingInput(BaseModel):
    """MediaPipe completed a frame but detected no pose."""

    kind: Literal["pose_missing"]
    frame_id: int = Field(..., ge=0)
    timestamp: float | None = Field(None, allow_inf_nan=False)

class FrameOutput(BaseModel):
    """單幀分析結果"""

    frame_id: int
    processing_ms: float
    rep_count: int
    state: str
    angles: dict[str, float | None]
    # ``errors`` remains as a v1 compatibility alias for form_errors.
    errors: list[str]
    form_errors: list[str]
    tracking_hints: list[str]
    tracking_state: str
    confidence: float
    is_visible: bool
    calories: float


class ErrorOutput(BaseModel):
    """錯誤回傳"""

    error: str
