"""Authenticated, single-process two-player PK rooms with authoritative timing."""

from __future__ import annotations

import asyncio
import json
import re
from collections import deque
from dataclasses import dataclass, field
from time import monotonic, time
from typing import Any

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
    phase: str = "waiting"
    interrupted: bool = False
    starts_at: float = 0.0
    ends_at: float = 0.0
    starts_at_ms: float = 0.0
    ends_at_ms: float = 0.0
    timer_task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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
        room = self.rooms.setdefault(room_id, Room(exercise_type=exercise_type))
        async with room.lock:
            if self.rooms.get(room_id) is not room:
                # A concurrent final disconnect removed this room while we waited.
                return await self.connect(websocket, room_id, exercise_type, user)
            if room.exercise_type != exercise_type:
                await websocket.close(
                    code=4400, reason="Exercise type does not match room"
                )
                return False
            if (
                user.id in room.players
                or len(room.players) >= 2
                or room.phase == "finished"
            ):
                await websocket.close(
                    code=4409, reason="Room full or user already joined"
                )
                return False
            try:
                await websocket.accept(subprotocol=PROTOCOL)
            except Exception:
                if not room.players:
                    self.rooms.pop(room_id, None)
                raise
            room.players[user.id] = Player(websocket=websocket)
            await self._room_state(room_id, room)
            return user.id in room.players

    def _remove(self, room_id: str, room: Room, user_id: str) -> None:
        if room.players.pop(user_id, None) is None:
            return
        task, room.timer_task = room.timer_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        # Completed results stay immutable, including when one player leaves.
        if room.phase != "finished":
            room.interrupted = room.interrupted or room.phase in {
                "countdown",
                "playing",
            }
            room.phase = "waiting"
            room.starts_at = room.ends_at = 0.0
            room.starts_at_ms = room.ends_at_ms = 0.0
            for player in room.players.values():
                player.ready = False
                player.score = 0
        if not room.players:
            self.rooms.pop(room_id, None)

    async def disconnect(
        self,
        room_id: str,
        user_id: str,
        websocket: WebSocket | None = None,
    ) -> None:
        room = self.rooms.get(room_id)
        if room is None:
            return
        async with room.lock:
            player = room.players.get(user_id)
            # A delayed old connection must not remove a newly joined socket.
            if player is None or (
                websocket is not None and player.websocket is not websocket
            ):
                return
            self._remove(room_id, room, user_id)
            await self._room_state(room_id, room)

    @staticmethod
    def _timing(room: Room) -> dict[str, float]:
        # Epoch fields are for display. All server decisions use monotonic time.
        return {
            "server_time_ms": (
                room.starts_at_ms + (monotonic() - room.starts_at) * 1000
                if room.starts_at_ms
                else time() * 1000
            ),
            "starts_at_ms": room.starts_at_ms,
            "ends_at_ms": room.ends_at_ms,
        }

    async def _send(
        self,
        room_id: str,
        room: Room,
        messages: dict[str, dict[str, Any]],
    ) -> None:
        stale = []
        for user_id, message in messages.items():
            player = room.players.get(user_id)
            if player is None:
                continue
            try:
                if "server_time_ms" in message:
                    message = {**message, **self._timing(room)}
                await asyncio.wait_for(player.websocket.send_json(message), timeout=5)
            except Exception:
                stale.append(user_id)
        for user_id in stale:
            self._remove(room_id, room, user_id)
        if stale:
            await self._room_state(room_id, room)

    async def _room_state(self, room_id: str, room: Room) -> None:
        messages = {}
        for user_id, player in room.players.items():
            opponent = next(
                (p for uid, p in room.players.items() if uid != user_id), None
            )
            messages[user_id] = {
                "type": "ROOM_STATE",
                "player_count": len(room.players),
                "ready": player.ready,
                "score": player.score,
                "opponent_ready": opponent.ready if opponent else False,
                "opponent_score": opponent.score if opponent else 0,
                "exercise_type": room.exercise_type,
                "phase": room.phase,
                **self._timing(room),
            }
        await self._send(room_id, room, messages)

    async def set_ready(
        self,
        room_id: str,
        user_id: str,
        ready: bool,
        websocket: WebSocket | None = None,
    ) -> None:
        room = self.rooms.get(room_id)
        if room is None:
            return
        async with room.lock:
            if (
                user_id not in room.players
                or room.phase != "waiting"
                or (
                    websocket is not None
                    and room.players[user_id].websocket is not websocket
                )
            ):
                return
            room.players[user_id].ready = ready
            await self._room_state(room_id, room)
            if len(room.players) != 2 or not all(
                p.ready for p in room.players.values()
            ):
                return
            for player in room.players.values():
                player.score = 0
            room.phase = "countdown"
            room.interrupted = False
            room.starts_at = monotonic() + PREPARE_SECONDS
            room.ends_at = room.starts_at + MATCH_SECONDS
            room.starts_at_ms = time() * 1000 + PREPARE_SECONDS * 1000
            room.ends_at_ms = room.starts_at_ms + MATCH_SECONDS * 1000
            # Register before any network await so disconnect can always cancel it.
            room.timer_task = asyncio.create_task(self.run_match_timer(room_id, room))
            message = {
                "type": "GAME_START",
                "countdown_seconds": PREPARE_SECONDS,
                "duration_seconds": MATCH_SECONDS,
                **self._timing(room),
            }
            await self._send(room_id, room, {uid: message for uid in room.players})

    async def _wait_until(self, deadline: float) -> None:
        await asyncio.sleep(max(0, deadline - monotonic()))

    async def run_match_timer(self, room_id: str, room: Room) -> None:
        try:
            await self._wait_until(room.starts_at)
            async with room.lock:
                if (
                    self.rooms.get(room_id) is not room
                    or room.phase != "countdown"
                    or room.timer_task is not asyncio.current_task()
                ):
                    return
                if monotonic() >= room.ends_at:
                    await self._finish(room_id, room)
                    return
                room.phase = "playing"
                message = {
                    "type": "GAME_BEGIN",
                    "duration_seconds": MATCH_SECONDS,
                    **self._timing(room),
                }
                await self._send(room_id, room, {uid: message for uid in room.players})
            await self._wait_until(room.ends_at)
            async with room.lock:
                if (
                    self.rooms.get(room_id) is room
                    and room.phase == "playing"
                    and room.timer_task is asyncio.current_task()
                ):
                    await self._finish(room_id, room)
        finally:
            if room.timer_task is asyncio.current_task():
                room.timer_task = None

    async def _finish(self, room_id: str, room: Room) -> None:
        room.phase = "finished"
        messages = {}
        for user_id, player in room.players.items():
            opponent = next(p for uid, p in room.players.items() if uid != user_id)
            messages[user_id] = {
                "type": "GAME_END",
                "score": player.score,
                "opponent_score": opponent.score,
                "result": "win"
                if player.score > opponent.score
                else "lose"
                if player.score < opponent.score
                else "draw",
            }
        await self._send(room_id, room, messages)

    async def update_score(
        self,
        room_id: str,
        user_id: str,
        score: int,
        websocket: WebSocket | None = None,
    ) -> bool:
        room = self.rooms.get(room_id)
        if room is None:
            return False
        async with room.lock:
            if user_id not in room.players or (
                websocket is not None
                and room.players[user_id].websocket is not websocket
            ):
                return False
            now = monotonic()
            # Ignore well-formed late/in-flight scores; don't disconnect the room.
            if room.phase in {"countdown", "finished"} or room.interrupted:
                return True
            if room.phase != "playing":
                return False
            if now >= room.ends_at:
                await self._finish(room_id, room)
                return True
            if now < room.starts_at:
                return True
            player = room.players[user_id]
            if score != player.score + 1:
                return False
            player.score = score
            await self._send(
                room_id,
                room,
                {
                    uid: {"type": "OPPONENT_SCORE", "score": score}
                    for uid in room.players
                    if uid != user_id
                },
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

            now = monotonic()

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

            if data.get("type") == "READY" and isinstance(data.get("ready"), bool):
                await manager.set_ready(
                    room,
                    current_user.id,
                    data["ready"],
                    websocket,
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
                    websocket,
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
        await manager.disconnect(room, current_user.id, websocket)
