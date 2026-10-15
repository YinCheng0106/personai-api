"""Authenticated two-player exercise PK WebSocket rooms."""

from __future__ import annotations

import asyncio
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
PREPARE_SECONDS = 10
MATCH_SECONDS = 60


@dataclass
class Player:
    websocket: WebSocket
    ready: bool = False
    score: int = 0


@dataclass
class Room:
    exercise_type: str
    players: dict[str, Player] = field(default_factory=dict)

    # waiting -> countdown -> playing -> finished
    phase: str = "waiting"

    starts_at: float = 0.0
    ends_at: float = 0.0

    timer_task: asyncio.Task[None] | None = None


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
            await websocket.close(
                code=4400,
                reason="Exercise type does not match room",
            )
            return False

        if room is not None and user.id in room.players:
            await websocket.close(
                code=4409,
                reason="User is already in this room",
            )
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

        # 中途有人離開，取消這場比賽的計時。
        task = room.timer_task
        room.timer_task = None

        if task is not None and not task.done():
            task.cancel()

        room.phase = "waiting"
        room.starts_at = 0.0
        room.ends_at = 0.0

        for player in room.players.values():
            player.ready = False
            player.score = 0

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
                (
                    item
                    for key, item in room.players.items()
                    if key != user_id
                ),
                None,
            )

            try:
                await player.websocket.send_json(
                    {
                        "type": "ROOM_STATE",
                        "player_count": len(room.players),
                        "opponent_ready": (
                            opponent.ready if opponent else False
                        ),
                        "opponent_score": (
                            opponent.score if opponent else 0
                        ),
                        "exercise_type": room.exercise_type,
                        "phase": room.phase,
                    }
                )
            except Exception:
                stale.append(user_id)

        for user_id in stale:
            room.players.pop(user_id, None)

        if not room.players:
            self.rooms.pop(room_id, None)

    async def broadcast(self, room_id: str, message: dict) -> None:
        room = self.rooms.get(room_id)

        if room is None:
            return

        stale: list[str] = []

        for user_id, player in list(room.players.items()):
            try:
                await player.websocket.send_json(message)
            except Exception:
                stale.append(user_id)

        for user_id in stale:
            room.players.pop(user_id, None)

    async def set_ready(
        self,
        room_id: str,
        user_id: str,
        ready: bool,
    ) -> None:
        room = self.rooms.get(room_id)

        if room is None or user_id not in room.players:
            return

        # 倒數或比賽開始後，不接受準備狀態變更。
        if room.phase in {"countdown", "playing", "finished"}:
            return

        room.players[user_id].ready = ready

        await self.send_room_state(room_id)

        room = self.rooms.get(room_id)

        if room is None:
            return

        if (
            room.phase == "waiting"
            and len(room.players) == 2
            and all(player.ready for player in room.players.values())
        ):
            await self.start_match(room_id)

    async def start_match(self, room_id: str) -> None:
        room = self.rooms.get(room_id)

        if (
            room is None
            or len(room.players) != 2
            or room.phase != "waiting"
            or not all(player.ready for player in room.players.values())
        ):
            return

        # 新一場比賽從零分開始。
        for player in room.players.values():
            player.score = 0

        now = time.monotonic()

        room.phase = "countdown"
        room.starts_at = now + PREPARE_SECONDS
        room.ends_at = room.starts_at + MATCH_SECONDS

        # GAME_START 代表開始 10 秒準備倒數。
        await self.broadcast(
            room_id,
            {
                "type": "GAME_START",
                "countdown_seconds": PREPARE_SECONDS,
                "duration_seconds": MATCH_SECONDS,
            },
        )

        room.timer_task = asyncio.create_task(
            self.run_match_timer(
                room_id,
                room,
                room.starts_at,
                room.ends_at,
            )
        )

    async def run_match_timer(
        self,
        room_id: str,
        expected_room: Room,
        starts_at: float,
        ends_at: float,
    ) -> None:
        try:
            # 使用單調時鐘，避免系統時間調整影響計時。
            await asyncio.sleep(
                max(0.0, starts_at - time.monotonic())
            )

            room = self.rooms.get(room_id)

            if (
                room is not expected_room
                or room.phase != "countdown"
                or len(room.players) != 2
            ):
                return

            room.phase = "playing"

            await self.broadcast(
                room_id,
                {
                    "type": "GAME_BEGIN",
                    "duration_seconds": MATCH_SECONDS,
                },
            )

            await asyncio.sleep(
                max(0.0, ends_at - time.monotonic())
            )

            room = self.rooms.get(room_id)

            if room is not expected_room or room.phase != "playing":
                return

            # 先鎖定比賽狀態，再通知玩家，避免時間到後仍能加分。
            room.phase = "finished"

            for user_id, player in list(room.players.items()):
                opponent = next(
                    (
                        item
                        for key, item in room.players.items()
                        if key != user_id
                    ),
                    None,
                )

                await player.websocket.send_json(
                    {
                        "type": "GAME_END",
                        "score": player.score,
                        "opponent_score": (
                            opponent.score if opponent else 0
                        ),
                        "result": (
                            "win"
                            if opponent and player.score > opponent.score
                            else "lose"
                            if opponent and player.score < opponent.score
                            else "draw"
                        ),
                    }
                )

        except asyncio.CancelledError:
            # 玩家離開時，正常取消計時任務。
            raise

        except Exception:
            # 若通知失敗，保留房間狀態，避免重新接受分數。
            room = self.rooms.get(room_id)

            if room is expected_room and room.phase == "playing":
                room.phase = "finished"

        finally:
            room = self.rooms.get(room_id)

            if room is expected_room and room.timer_task is asyncio.current_task():
                room.timer_task = None

    async def update_score(
        self,
        room_id: str,
        user_id: str,
        score: int,
    ) -> bool:
        room = self.rooms.get(room_id)

        if room is None or user_id not in room.players:
            return False

        now = time.monotonic()

        # 只接受正式比賽時間內、依序增加的分數。
        if (
            room.phase != "playing"
            or now < room.starts_at
            or now >= room.ends_at
        ):
            return False

        player = room.players[user_id]

        if score != player.score + 1:
            return False

        player.score = score

        opponent = next(
            (
                item
                for key, item in room.players.items()
                if key != user_id
            ),
            None,
        )

        if opponent is not None:
            try:
                await opponent.websocket.send_json(
                    {
                        "type": "OPPONENT_SCORE",
                        "score": score,
                    }
                )
            except Exception:
                pass

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
        await websocket.close(
            code=4400,
            reason="Unsupported exercise type",
        )
        return

    if not await manager.connect(
        websocket,
        room,
        exercise_type,
        current_user,
    ):
        return

    settings = get_settings()
    received_at: deque[float] = deque()

    try:
        while True:
            raw = await websocket.receive_text()

            if len(raw.encode("utf-8")) > settings.websocket_max_message_bytes:
                await websocket.close(
                    code=4400,
                    reason="Message too large",
                )
                return

            now = time.monotonic()

            while received_at and now - received_at[0] >= 1.0:
                received_at.popleft()

            received_at.append(now)

            if len(received_at) > settings.websocket_max_frames_per_second:
                await websocket.close(
                    code=4429,
                    reason="Message rate limit exceeded",
                )
                return

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.close(code=4400, reason="Invalid JSON")
                return

            if not isinstance(data, dict):
                await websocket.close(code=4400, reason="Invalid message")
                return

            if (
                data.get("type") == "READY"
                and isinstance(data.get("ready"), bool)
            ):
                await manager.set_ready(
                    room,
                    current_user.id,
                    data["ready"],
                )
                continue

            score = data.get("score")

            if (
                data.get("type") == "SCORE_UPDATE"
                and type(score) is int
                and 0 <= score <= MAX_SCORE
                and await manager.update_score(
                    room,
                    current_user.id,
                    score,
                )
            ):
                continue

            await websocket.close(
                code=4400,
                reason="Invalid game message",
            )
            return

    except WebSocketDisconnect:
        pass

    finally:
        await manager.disconnect(room, current_user.id)
