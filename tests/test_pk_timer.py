import asyncio
from typing import Any, cast

import pytest
from fastapi import WebSocket

import personai_api.routers.pk as pk
from personai_api.auth import CurrentUser


class Socket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.fail = False

    async def accept(self, **kwargs: Any) -> None:
        pass

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("disconnected")
        self.messages.append(message)

    async def close(self, **kwargs: Any) -> None:
        pass


async def setup_match(monkeypatch: pytest.MonkeyPatch):
    manager = pk.PKRoomManager()
    clock = [100.0]
    monkeypatch.setattr(pk, "monotonic", lambda: clock[0])
    waits: asyncio.Queue[tuple[float, asyncio.Future[None]]] = asyncio.Queue()

    async def wait_until(deadline: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await waits.put((deadline, future))
        await future

    monkeypatch.setattr(manager, "_wait_until", wait_until)
    first, second = Socket(), Socket()
    await manager.connect(cast(WebSocket, first), "1234", "squat", CurrentUser(id="a"))
    await manager.connect(cast(WebSocket, second), "1234", "squat", CurrentUser(id="b"))
    await asyncio.gather(
        manager.set_ready("1234", "a", True),
        manager.set_ready("1234", "b", True),
    )
    return manager, clock, waits, first, second


@pytest.mark.parametrize(
    "scores,results",
    [((2, 1), ("win", "lose")), ((1, 2), ("lose", "win")), ((0, 0), ("draw", "draw"))],
)
def test_authoritative_countdown_expiration_and_results(monkeypatch, scores, results):
    async def scenario():
        manager, clock, waits, first, second = await setup_match(monkeypatch)
        room = manager.rooms["1234"]
        task = room.timer_task
        assert task is not None
        deadline, release = await waits.get()
        assert deadline == 110
        assert room.phase == "countdown"
        starts = [m for m in first.messages if m["type"] == "GAME_START"]
        assert len(starts) == 1
        assert starts[0]["countdown_seconds"] == 10
        assert starts[0]["duration_seconds"] == 60
        assert starts[0]["ends_at_ms"] - starts[0]["starts_at_ms"] == 60_000
        await manager.set_ready("1234", "a", True)
        assert room.timer_task is task
        await manager.update_score("1234", "a", 1)
        assert room.players["a"].score == 0
        clock[0] = deadline
        release.set_result(None)
        deadline, release = await waits.get()
        assert deadline == 170
        assert room.phase == "playing"
        assert first.messages[-1]["type"] == second.messages[-1]["type"] == "GAME_BEGIN"
        for user, score in zip(("a", "b"), scores, strict=True):
            for value in range(1, score + 1):
                assert await manager.update_score("1234", user, value)
        clock[0] = deadline
        # Even before the timer resumes, an expired score cannot change the result.
        assert await manager.update_score("1234", "a", scores[0] + 1)
        release.set_result(None)
        await task
        assert room.phase == "finished"
        assert room.timer_task is None
        for socket, score, opponent, result in zip(
            (first, second), scores, reversed(scores), results, strict=True
        ):
            ends = [m for m in socket.messages if m["type"] == "GAME_END"]
            assert ends == [
                {
                    "type": "GAME_END",
                    "score": score,
                    "opponent_score": opponent,
                    "result": result,
                }
            ]
        await manager.update_score("1234", "a", 999)
        assert room.players["a"].score == scores[0]
        await manager.disconnect("1234", "b", second)
        assert room.phase == "finished"
        assert room.players["a"].score == scores[0]
        await manager.disconnect("1234", "a", first)
        assert manager.rooms == {}

    asyncio.run(scenario())


@pytest.mark.parametrize("playing", [False, True])
def test_disconnect_cancels_timer_and_rejoin_requires_readiness(monkeypatch, playing):
    async def scenario():
        manager, clock, waits, first, second = await setup_match(monkeypatch)
        room = manager.rooms["1234"]
        task = room.timer_task
        assert task is not None
        deadline, release = await waits.get()
        if playing:
            clock[0] = deadline
            release.set_result(None)
            await waits.get()
            await manager.update_score("1234", "a", 1)
        await manager.disconnect("1234", "b", second)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert room.timer_task is None
        assert room.phase == "waiting"
        assert first.messages[-1]["phase"] == "waiting"
        assert room.players["a"].score == 0
        assert room.players["a"].ready is False
        assert await manager.update_score("1234", "a", 5)
        assert room.players["a"].score == 0
        replacement = Socket()
        await manager.connect(replacement, "1234", "squat", CurrentUser(id="b"))
        # Old socket cleanup must not disconnect the replacement.
        await manager.disconnect("1234", "b", second)
        assert room.players["b"].websocket is replacement
        assert room.timer_task is None
        await manager.disconnect("1234", "a", first)
        await manager.disconnect("1234", "b", replacement)
        assert not manager.rooms

    asyncio.run(scenario())


def test_failed_start_broadcast_cleans_up_timer_and_notifies_survivor(monkeypatch):
    async def scenario():
        manager, _, waits, first, second = await setup_match(monkeypatch)
        task = manager.rooms["1234"].timer_task
        assert task is not None
        await waits.get()
        first.fail = True
        room = manager.rooms["1234"]
        async with room.lock:
            await manager._room_state("1234", room)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert room.phase == "waiting"
        assert second.messages[-1]["player_count"] == 1
        assert second.messages[-1]["ready"] is False
        await manager.disconnect("1234", "b", second)
        assert not manager.rooms

    asyncio.run(scenario())


def test_failed_final_notification_does_not_hide_survivor_result(monkeypatch):
    async def scenario():
        manager, clock, waits, first, second = await setup_match(monkeypatch)
        task = manager.rooms["1234"].timer_task
        assert task is not None
        deadline, release = await waits.get()
        clock[0] = deadline
        release.set_result(None)
        deadline, release = await waits.get()
        await manager.update_score("1234", "b", 1)
        first.fail = True
        clock[0] = deadline
        release.set_result(None)
        await task
        ends = [m for m in second.messages if m["type"] == "GAME_END"]
        assert ends == [
            {"type": "GAME_END", "score": 1, "opponent_score": 0, "result": "win"}
        ]
        assert manager.rooms["1234"].timer_task is None
        await manager.disconnect("1234", "b", second)
        assert not manager.rooms

    asyncio.run(scenario())
