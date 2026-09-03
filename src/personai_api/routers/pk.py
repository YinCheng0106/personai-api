"""1v1 Real-time PK WebSocket Handler with Rooms."""

from __future__ import annotations
import json
from typing import Dict, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["1v1 PK 對戰"])

class PKRoomManager:
    def __init__(self) -> None:
        # 儲存結構：room_id -> List[WebSocket]
        self.rooms: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_id: str) -> None:
        await websocket.accept()
        if room_id not in self.rooms:
            self.rooms[room_id] = []
        self.rooms[room_id].append(websocket)

    def disconnect(self, websocket: WebSocket, room_id: str) -> None:
        if room_id in self.rooms:
            if websocket in self.rooms[room_id]:
                self.rooms[room_id].remove(websocket)
            # 當房間完全沒有連線時，釋放記憶體資源
            if not self.rooms[room_id]:
                del self.rooms[room_id]

    async def broadcast_to_room(self, room_id: str, sender_ws: WebSocket, message: dict) -> None:
        """向同一房間內的其餘玩家廣播訊息"""
        if room_id in self.rooms:
            for connection in self.rooms[room_id]:
                if connection != sender_ws:
                    try:
                        await connection.send_json(message)
                    except Exception:
                        self.disconnect(connection, room_id)

manager = PKRoomManager()

@router.websocket("/ws/pk")
async def pk_ws(websocket: WebSocket, room: str = "default") -> None:
    await manager.connect(websocket, room)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if isinstance(data, dict):
                msg_type = data.get("type")
                # 1. 廣播即時分數更新
                if msg_type == "SCORE_UPDATE":
                    await manager.broadcast_to_room(
                        room, 
                        websocket, 
                        {"type": "OPPONENT_SCORE", "score": data.get("score", 0)}
                    )
                # 2. 廣播準備狀態 (READY)
                elif msg_type == "READY":
                    await manager.broadcast_to_room(
                        room, 
                        websocket, 
                        {"type": "OPPONENT_READY", "ready": data.get("ready")}
                    )
                # 3. 廣播開始對戰訊號 (START)
                elif msg_type == "START":
                    await manager.broadcast_to_room(
                        room, 
                        websocket, 
                        {"type": "GAME_START"}
                    )

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, room)