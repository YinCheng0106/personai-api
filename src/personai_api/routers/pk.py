"""1v1 Real-time PK WebSocket Handler."""

from __future__ import annotations

import json
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["1v1 PK 對戰"])


class PKConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections[websocket] = str(id(websocket))

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.pop(websocket, None)

    async def broadcast_to_others(self, sender_ws: WebSocket, message: dict) -> None:
        for connection in list(self.active_connections.keys()):
            if connection != sender_ws:
                try:
                    await connection.send_json(message)
                except Exception:
                    self.disconnect(connection)


manager = PKConnectionManager()


@router.websocket("/ws/pk")
async def pk_ws(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(data, dict):
                continue

            if data.get("type") == "SCORE_UPDATE":
                opponent_msg = {
                    "type": "OPPONENT_SCORE",
                    "score": data.get("score", 0),
                }
                await manager.broadcast_to_others(websocket, opponent_msg)

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)