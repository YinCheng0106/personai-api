"""Authenticated two-player exercise PK WebSocket rooms."""

from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from personai_api.auth import CurrentUser
from personai_api.config import get_settings
from personai_api.routers.analyze import PROTOCOL, authenticate_websocket

router = APIRouter(tags=["1v1 PK 對戰"])
ROOM_PATTERN = re.compile(r"^[0-9]{4}$")
EXERCISE_TYPES = {"squat", "pushup"}
MAX_SCORE = 10_000


@dataclass
class Player:
    websocket: WebSocket
    ready: bool = False
    score: int = 0


@dataclass
class Room:
    exercise_type: str
    players: dict[str, Player] = field(default_factory=dict)
    started: bool = False


class PKRoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    async def connect(
        self,
        websocket: WebSocket,
        room_id: str,
        exercise_type: str,
        user: CurrentUser,
    ) -> bool:
        room = self.rooms.get(room_id)
        if room is not None and room.exercise_type != exercise_type:
            await websocket.close(code=4400, reason="Exercise type does not match room")
            return False
        if room is not None and user.id in room.players:
            await websocket.close(code=4409, reason="User is already in this room")
            return False
        if room is not None and len(room.players) >= 2:
            await websocket.close(code=4409, reason="Room is full")
            return False

        if room is None:
            room = Room(exercise_type=exercise_type)
            self.rooms[room_id] = room
        room.players[user.id] = Player(websocket=websocket)
        await websocket.accept(subprotocol=PROTOCOL)
        await self.send_room_state(room_id)
        return True

    async def disconnect(self, room_id: str, user_id: str) -> None:
        room = self.rooms.get(room_id)
        if room is None:
            return
        room.players.pop(user_id, None)
        room.started = False
        for player in room.players.values():
            player.ready = False
        if not room.players:
            self.rooms.pop(room_id, None)
            return
        await self.send_room_state(room_id)

    async def send_room_state(self, room_id: str) -> None:
        room = self.rooms.get(room_id)
        if room is None:
            return
        stale: list[str] = []
        for user_id, player in list(room.players.items()):
            opponent = next(
                (item for key, item in room.players.items() if key != user_id), None
            )
            try:
                await player.websocket.send_json(
                    {
                        "type": "ROOM_STATE",
                        "player_count": len(room.players),
                        "opponent_ready": opponent.ready if opponent else False,
                        "opponent_score": opponent.score if opponent else 0,
                        "exercise_type": room.exercise_type,
                    }
                )
            except Exception:
                stale.append(user_id)
        for user_id in stale:
            room.players.pop(user_id, None)

    async def set_ready(self, room_id: str, user_id: str, ready: bool) -> None:
        room = self.rooms[room_id]
        room.players[user_id].ready = ready
        if not ready:
            room.started = False
        await self.send_room_state(room_id)
        if (
            not room.started
            and len(room.players) == 2
            and all(p.ready for p in room.players.values())
        ):
            room.started = True
            for player in room.players.values():
                await player.websocket.send_json({"type": "GAME_START"})

    async def update_score(self, room_id: str, user_id: str, score: int) -> bool:
        room = self.rooms[room_id]
        player = room.players[user_id]
        if not room.started or score != player.score + 1:
            return False
        player.score = score
        for other_id, opponent in room.players.items():
            if other_id != user_id:
                await opponent.websocket.send_json(
                    {"type": "OPPONENT_SCORE", "score": score}
                )
        return True


manager = PKRoomManager()


@router.websocket("/ws/pk")
async def pk_ws(
    websocket: WebSocket,
    room: str,
    exercise_type: str,
) -> None:
    current_user = await authenticate_websocket(websocket)
    if current_user is None:
        return
    if not ROOM_PATTERN.fullmatch(room):
        await websocket.close(code=4400, reason="Invalid room code")
        return
    if exercise_type not in EXERCISE_TYPES:
        await websocket.close(code=4400, reason="Unsupported exercise type")
        return
    if not await manager.connect(websocket, room, exercise_type, current_user):
        return

    settings = get_settings()
    received_at: deque[float] = deque()
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
                await websocket.close(code=4429, reason="Message rate limit exceeded")
                return

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.close(code=4400, reason="Invalid JSON")
                return
            if not isinstance(data, dict):
                await websocket.close(code=4400, reason="Invalid message")
                return

            if data.get("type") == "READY" and isinstance(data.get("ready"), bool):
                await manager.set_ready(room, current_user.id, data["ready"])
                continue

            score = data.get("score")
            if (
                data.get("type") == "SCORE_UPDATE"
                and type(score) is int
                and 0 <= score <= MAX_SCORE
                and await manager.update_score(room, current_user.id, score)
            ):
                continue

            await websocket.close(code=4400, reason="Invalid game message")
            return
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(room, current_user.id)
