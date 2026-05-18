"""
生物力學分析 WebSocket API

端點：
  WS /ws/analyze/{exercise_type} — 即時 keypoints 姿態分析

前端串接流程：
  1. 建立 WebSocket 連線，指定運動類型 (squat / pushup)
  2. 前端每幀將 MediaPipe 偵測到的 33 個 keypoints 以 JSON 送出
  3. 後端驗證 → 平滑濾波 → FSM 判定 → 回傳 JSON 結果
  4. 前端接收 JSON 渲染 HUD（角度、次數、錯誤提示、卡路里）
"""

import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from personai_api.models.biomechanics_schema import (
    FrameInput,
    FrameOutput,
)
from personai_api.services.biomechanics import BiomechanicsAnalyzer, Point
from personai_api.services.inbody import estimate_calories_per_rep

router = APIRouter(tags=["生物力學分析"])

@router.websocket("/ws/analyze/{exercise_type}")
async def analyze_ws(
    websocket: WebSocket,
    exercise_type: str,
    weight_kg: float = Query(default=70.0),
):
    """
    即時 keypoints 姿態分析 WebSocket

    Parameters
    ----------
    exercise_type : str — "squat" 或 "pushup"
    weight_kg : float — 使用者體重（公斤），用於卡路里計算，預設 70kg

    前端送出格式（JSON）：
        {
            "keypoints": [
                {"x": 0.5, "y": 0.3, "z": 0.0, "visibility": 0.99},
                ...  // 共 33 個
            ],
            "timestamp": 1234567890.123  // 選填
        }

    後端回傳格式（JSON）：
        {
            "rep_count": 5,
            "state": "descending",
            "angles": {"left_knee": 95.2, ...},
            "errors": ["膝蓋內扣：..."],
            "confidence": 0.85,
            "is_visible": true,
            "calories": 12.5
        }

    控制指令：
        { "action": "reset" }  — 重置計數器（開始新的一組）
    """
    await websocket.accept()

    # 建立分析引擎
    try:
        analyzer = BiomechanicsAnalyzer(exercise_type=exercise_type)
    except ValueError:
        await websocket.send_json({"error": f"不支援的運動類型: {exercise_type}"})
        await websocket.close()
        return

    last_rep_count = 0
    total_calories = 0.0

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            # 處理控制指令
            if data.get("action") == "reset":
                analyzer.reset()
                last_rep_count = 0
                total_calories = 0.0
                await websocket.send_json({"action": "reset", "status": "ok"})
                continue

            # Pydantic 驗證
            try:
                frame_input = FrameInput(**data)
            except Exception as e:
                await websocket.send_json({"error": f"輸入格式錯誤: {e}"})
                continue

            # 轉換為 list[Point]
            landmarks = [
                Point(x=kp.x, y=kp.y, z=kp.z, visibility=kp.visibility)
                for kp in frame_input.keypoints
            ]

            # 分析
            result = analyzer.analyze(landmarks, frame_input.timestamp)

            # 卡路里累計：每完成一次 rep 加算
            if result.rep_count > last_rep_count:
                new_reps = result.rep_count - last_rep_count
                cal_per_rep = estimate_calories_per_rep(exercise_type, weight_kg)
                total_calories += cal_per_rep * new_reps
                last_rep_count = result.rep_count

            # 組裝回傳
            output = FrameOutput(
                rep_count=result.rep_count,
                state=result.state,
                angles=result.angles,
                errors=result.errors,
                confidence=result.confidence,
                is_visible=result.is_visible,
                calories=round(total_calories, 2),
            )
            await websocket.send_json(output.model_dump())

    except WebSocketDisconnect:
        pass
