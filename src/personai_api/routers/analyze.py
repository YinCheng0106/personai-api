"""Authenticated real-time biomechanical analysis WebSocket."""

from __future__ import annotations

import json
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from personai_api.auth import CurrentUser, verify_token
from personai_api.config import get_settings
from personai_api.models.biomechanics_schema import (
    FrameInput,
    FrameOutput,
    PoseMissingInput,
)
from personai_api.services.biomechanics import BiomechanicsAnalyzer, Point
from personai_api.services.inbody import estimate_calories_per_rep

router = APIRouter(tags=["生物力學分析"])
PROTOCOL = "personai.v1"
active_users: set[str] = set()


def _protocol_token(websocket: WebSocket) -> str | None:
    protocols = [
        item.strip()
        for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    ]
    if len(protocols) != 2 or protocols[0] != PROTOCOL:
        return None
    return protocols[1]


async def authenticate_websocket(websocket: WebSocket) -> CurrentUser | None:
    settings = get_settings()
    origin = websocket.headers.get("origin")
    if origin not in settings.cors_origins:
        await websocket.close(code=4403, reason="Origin not allowed")
        return None

    token = _protocol_token(websocket)
    if token is None:
        await websocket.close(code=4401, reason="Authentication required")
        return None
    try:
        return verify_token(token, settings)
    except HTTPException:
        await websocket.close(code=4401, reason="Invalid authentication")
        return None


@router.websocket("/ws/analyze/{exercise_type}")
async def analyze_ws(
    websocket: WebSocket,
    exercise_type: str,
    weight_kg: float = Query(default=70.0, gt=0, le=500),
) -> None:
    current_user = await authenticate_websocket(websocket)
    if current_user is None:
        return
    if current_user.id in active_users:
        await websocket.close(code=4409, reason="Only one active connection is allowed")
        return

    try:
        analyzer = BiomechanicsAnalyzer(exercise_type=exercise_type)
    except ValueError:
        await websocket.close(code=4400, reason="Unsupported exercise type")
        return

    active_users.add(current_user.id)
    await websocket.accept(subprotocol=PROTOCOL)
    settings = get_settings()
    received_at: deque[float] = deque()
    last_rep_count = 0
    last_frame_id: int | None = None
    total_calories = 0.0
    last_frame_id = -1

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > settings.websocket_max_message_bytes:
                await websocket.close(code=4400, reason="Message too large")
                return

            now = time.monotonic()
            while received_at and now - received_at[0] >= 1.0:
                received_at.popleft()
            received_at.append(now)
            if len(received_at) > settings.websocket_max_frames_per_second:
                await websocket.close(code=4429, reason="Frame rate limit exceeded")
                return

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.close(code=4400, reason="Invalid JSON")
                return
            if not isinstance(data, dict):
                await websocket.close(code=4400, reason="Invalid message")
                return

            if data.get("action") == "reset":
                analyzer.reset()
                last_rep_count = 0
                total_calories = 0.0
                await websocket.send_json({"action": "reset", "status": "ok"})
                continue

            started = time.perf_counter()
            try:
                if data.get("kind", "landmarks") == "landmarks":
                    frame_input: FrameInput | PoseMissingInput = (
                        FrameInput.model_validate(data)
                    )
                elif data.get("kind") == "pose_missing":
                    frame_input = PoseMissingInput.model_validate(data)
                else:
                    raise ValueError("Unsupported pose message kind")
            except (ValidationError, ValueError):
                await websocket.close(code=4400, reason="Invalid pose frame")
                return
            if frame_input.frame_id <= last_frame_id:
                await websocket.close(code=4400, reason="Frame ID must increase")
                return
            last_frame_id = frame_input.frame_id

            if last_frame_id is not None and frame_input.frame_id <= last_frame_id:
                await websocket.close(code=4400, reason="Frame ID must increase")
                return
            last_frame_id = frame_input.frame_id

            if isinstance(frame_input, PoseMissingInput):
                result = analyzer.pose_missing(frame_input.timestamp)
            else:
                landmarks = [
                    Point(x=kp.x, y=kp.y, z=kp.z, visibility=kp.visibility)
                    for kp in frame_input.keypoints
                ]
                result = analyzer.analyze(landmarks, frame_input.timestamp)

            if result.rep_count > last_rep_count:
                new_reps = result.rep_count - last_rep_count
                total_calories += (
                    estimate_calories_per_rep(exercise_type, weight_kg) * new_reps
                )
                last_rep_count = result.rep_count

            output = FrameOutput(
                frame_id=frame_input.frame_id,
                processing_ms=round((time.perf_counter() - started) * 1000, 3),
                rep_count=result.rep_count,
                state=result.state,
                angles=result.angles,
                errors=result.form_errors,
                form_errors=result.form_errors,
                tracking_hints=result.tracking_hints,
                tracking_state=result.tracking_state,
                confidence=result.confidence,
                is_visible=result.is_visible,
                calories=round(total_calories, 2),
            )
            await websocket.send_json(output.model_dump())
    except WebSocketDisconnect:
        pass
    finally:
        active_users.discard(current_user.id)
