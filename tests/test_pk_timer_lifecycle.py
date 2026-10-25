"""Exercise real timer transitions with a controlled clock and sleep boundary."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any, cast

import pytest
from fastapi import WebSocket

import personai_api.routers.pk as pk
from personai_api.auth import CurrentUser


class Socket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.closed: list[dict[str, Any]] = []
        self.fail_on: set[str] = set()
        self.blocks: dict[str, tuple[asyncio.Event, asyncio.Event]] = {}

    async def accept(self, **kwargs: Any) -> None:
        pass

    async def close(self, **kwargs: Any) -> None:
        self.closed.append(kwargs)

    async def send_json(self, message: dict[str, Any]) -> None:
        if message["type"] in self.blocks:
            entered, release = self.blocks[message["type"]]
            entered.set()
            await release.wait()
        if message["type"] in self.fail_on:
            raise RuntimeError("test transport closed")
        self.messages.append(message)

    def events(self, event: str) -> list[dict[str, Any]]:
        return [message for message in self.messages if message["type"] == event]


class Match:
    def __init__(self) -> None:
        self.manager = pk.PKRoomManager()
        self.now = 100.0
        self.wall_time = 1_000.0
        self.sleeps: asyncio.Queue[tuple[float, asyncio.Future[None]]] = asyncio.Queue()
        self.first, self.second = Socket(), Socket()

    async def sleep(self, delay: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self.sleeps.put((delay, future))
        await future

    async def connect(self, user: str, socket: Socket) -> None:
        assert await self.manager.connect(
            cast(WebSocket, socket), "1234", "squat", CurrentUser(id=user)
        )

    async def ready(self) -> asyncio.Task[None]:
        await asyncio.gather(
            self.manager.set_ready("1234", "a", True),
            self.manager.set_ready("1234", "b", True),
        )
        task = self.manager.rooms["1234"].timer_task
        assert task is not None
        return task

    async def begin(self) -> asyncio.Task[None]:
        task = await self.ready()
        delay, wake = await self.sleeps.get()
        assert delay == 10
        self.now += delay
        wake.set_result(None)
        return task

    async def assert_timer_stopped(self, task: asyncio.Task[None]) -> None:
        # Race completion against another requested sleep, with no wall-clock wait.
        sleeping = asyncio.create_task(self.sleeps.get())
        try:
            done, _ = await asyncio.wait(
                (task, sleeping), return_when=asyncio.FIRST_COMPLETED
            )
            assert task in done, "an aborted match must not keep waiting"
            await task
        finally:
            sleeping.cancel()
            with suppress(asyncio.CancelledError):
                await sleeping


@asynccontextmanager
async def match(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Match]:
    previous = asyncio.all_tasks()
    game = Match()
    monkeypatch.setattr(pk, "monotonic", lambda: game.now)
    monkeypatch.setattr(pk, "time", lambda: game.wall_time)
    # Only replace the scheduling boundary, never the production timer or waits.
    monkeypatch.setattr(pk.asyncio, "sleep", game.sleep)
    await game.connect("a", game.first)
    await game.connect("b", game.second)
    try:
        yield game
    finally:
        for room_id, room in list(game.manager.rooms.items()):
            for user in list(room.players):
                await game.manager.disconnect(room_id, user)
        remaining = asyncio.all_tasks() - previous
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)


def test_simultaneous_and_repeated_readiness_create_one_timer(monkeypatch):
    async def scenario():
        async with match(monkeypatch) as game:
            task = await game.ready()
            await game.sleeps.get()
            room = game.manager.rooms["1234"]
            await asyncio.gather(
                *(
                    game.manager.set_ready("1234", user, ready)
                    for user in ("a", "b")
                    for ready in (True, False, True)
                )
            )
            assert room.phase == "countdown"
            assert room.timer_task is task
            assert all(player.ready for player in room.players.values())
            for socket in (game.first, game.second):
                assert len(socket.events("GAME_START")) == 1
                assert socket.events("GAME_START")[0]["countdown_seconds"] == 10
                assert socket.events("GAME_START")[0]["duration_seconds"] == 60
            assert await game.manager.update_score("1234", "a", 1)
            assert room.players["a"].score == 0
            assert not game.second.events("OPPONENT_SCORE")

    asyncio.run(scenario())


def test_early_wakeup_rechecks_deadline_and_wall_clock_cannot_start_match(monkeypatch):
    async def scenario():
        async with match(monkeypatch) as game:
            await game.ready()
            _, wake = await game.sleeps.get()
            game.now = 109
            game.wall_time += 100_000
            wake.set_result(None)
            delay, wake = await game.sleeps.get()
            assert delay == 1
            assert game.manager.rooms["1234"].phase == "countdown"
            assert not game.first.events("GAME_BEGIN")
            game.now = 110
            game.wall_time = -100_000
            wake.set_result(None)
            delay, _ = await game.sleeps.get()
            assert delay == 60
            assert game.manager.rooms["1234"].phase == "playing"
            begin = game.first.events("GAME_BEGIN")
            assert len(begin) == 1
            assert begin[0]["duration_seconds"] == 60
            assert begin[0]["server_time_ms"] == begin[0]["starts_at_ms"]

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [0, 1, 3, 10_000])
def test_playing_accepts_only_next_score_without_mutating_rejected_scores(
    monkeypatch, invalid
):
    async def scenario():
        async with match(monkeypatch) as game:
            await game.begin()
            await game.sleeps.get()
            assert await game.manager.update_score("1234", "a", 1)
            assert not await game.manager.update_score("1234", "a", invalid)
            assert game.manager.rooms["1234"].players["a"].score == 1
            assert game.second.events("OPPONENT_SCORE") == [
                {"type": "OPPONENT_SCORE", "score": 1}
            ]
            assert await game.manager.update_score("1234", "a", 2)
            assert await game.manager.update_score("1234", "b", 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("offset", [-0.001, 0, 0.001])
def test_scores_at_deadline_boundary(monkeypatch, offset):
    async def scenario():
        async with match(monkeypatch) as game:
            await game.begin()
            await game.sleeps.get()
            room = game.manager.rooms["1234"]
            game.now = room.ends_at + offset
            assert await game.manager.update_score("1234", "a", 1)
            assert room.players["a"].score == (1 if offset < 0 else 0)
            assert room.phase == ("playing" if offset < 0 else "finished")

    asyncio.run(scenario())


@pytest.mark.parametrize("scores", [(2, 1), (1, 2), (0, 0), (2, 2)])
def test_timer_expires_without_client_messages_and_results_are_immutable(
    monkeypatch, scores
):
    async def scenario():
        async with match(monkeypatch) as game:
            task = await game.begin()
            _, wake = await game.sleeps.get()
            room = game.manager.rooms["1234"]
            # Neither an early nor a repeated lifecycle call can finish a match.
            async with room.lock:
                await game.manager._finish("1234", room)
            assert room.phase == "playing"
            for user, score in zip(("a", "b"), scores, strict=True):
                for value in range(1, score + 1):
                    assert await game.manager.update_score("1234", user, value)
            game.now = room.ends_at
            wake.set_result(None)
            await task
            assert room.phase == "finished"
            assert room.timer_task is None
            async with room.lock:
                await game.manager._finish("1234", room)
            for user in ("a", "b"):
                await game.manager.set_ready("1234", user, True)
                assert await game.manager.update_score("1234", user, 999)
            for socket, score, opponent in zip(
                (game.first, game.second), scores, reversed(scores), strict=True
            ):
                assert socket.events("GAME_END") == [
                    {
                        "type": "GAME_END",
                        "score": score,
                        "opponent_score": opponent,
                        "result": "win"
                        if score > opponent
                        else "lose"
                        if score < opponent
                        else "draw",
                    }
                ]
                assert len(socket.events("GAME_START")) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("playing", [False, True])
def test_direct_timer_cancellation_resets_active_match(monkeypatch, playing):
    async def scenario():
        async with match(monkeypatch) as game:
            task = await (game.begin() if playing else game.ready())
            await game.sleeps.get()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            room = game.manager.rooms["1234"]
            assert room.phase == "waiting"
            assert room.timer_task is None
            assert room.interrupted
            assert room.starts_at == room.ends_at == 0
            assert all(
                not player.ready and player.score == 0
                for player in room.players.values()
            )
            assert game.first.messages[-1]["phase"] == "waiting"

    asyncio.run(scenario())


def test_unexpected_timer_failure_resets_match(monkeypatch):
    async def scenario():
        async with match(monkeypatch) as game:
            task = await game.ready()
            _, wake = await game.sleeps.get()
            wake.set_exception(RuntimeError("test clock unavailable"))
            await task
            assert game.manager.rooms["1234"].phase == "waiting"
            assert game.manager.rooms["1234"].timer_task is None
            assert game.first.messages[-1]["phase"] == "waiting"

    asyncio.run(scenario())


@pytest.mark.parametrize("event", ["GAME_START", "GAME_BEGIN", "GAME_END"])
@pytest.mark.parametrize("failed", [("a",), ("b",), ("a", "b")])
def test_broadcast_failures_leave_no_active_or_duplicate_match(
    monkeypatch, event, failed
):
    async def scenario():
        async with match(monkeypatch) as game:
            room = game.manager.rooms["1234"]
            for user, socket in (("a", game.first), ("b", game.second)):
                if user in failed:
                    socket.fail_on.add(event)
            if event == "GAME_START":
                await game.manager.set_ready("1234", "a", True)
                await game.manager.set_ready("1234", "b", True)
            else:
                task = await game.ready()
                _, wake = await game.sleeps.get()
                game.now = room.starts_at
                wake.set_result(None)
                if event == "GAME_BEGIN":
                    await game.assert_timer_stopped(task)
                else:
                    _, wake = await game.sleeps.get()
                    assert await game.manager.update_score("1234", "b", 1)
                    game.now = room.ends_at
                    wake.set_result(None)
                    await task
            assert room.timer_task is None
            assert room.phase == ("finished" if event == "GAME_END" else "waiting")
            assert set(room.players) == {"a", "b"} - set(failed)
            if len(failed) == 2:
                assert not game.manager.rooms
            for user, socket in (("a", game.first), ("b", game.second)):
                if user in failed:
                    continue
                if event == "GAME_END":
                    assert socket.events("GAME_END") == [
                        {
                            "type": "GAME_END",
                            "score": int(user == "b"),
                            "opponent_score": int(user == "a"),
                            "result": "win" if user == "b" else "lose",
                        }
                    ]
                else:
                    assert socket.messages[-1]["phase"] == "waiting"
                    assert not room.players[user].ready

    asyncio.run(scenario())


@pytest.mark.parametrize("playing", [False, True])
@pytest.mark.parametrize("recreate", [False, True])
def test_disconnect_and_rejoin_cannot_revive_old_timer(monkeypatch, playing, recreate):
    async def scenario():
        async with match(monkeypatch) as game:
            task = await (game.begin() if playing else game.ready())
            await game.sleeps.get()
            old_room = game.manager.rooms["1234"]
            await game.manager.disconnect("1234", "b", cast(WebSocket, game.second))
            if recreate:
                await game.manager.disconnect("1234", "a", cast(WebSocket, game.first))
                assert not game.manager.rooms
                game.first = Socket()
                await game.connect("a", game.first)
            replacement = Socket()
            await game.connect("b", replacement)
            await game.manager.disconnect("1234", "b", cast(WebSocket, game.second))
            await game.manager.set_ready(
                "1234", "b", True, cast(WebSocket, game.second)
            )
            assert not await game.manager.update_score(
                "1234", "b", 1, cast(WebSocket, game.second)
            )
            room = game.manager.rooms["1234"]
            assert room.players["b"].websocket is replacement
            assert not room.players["b"].ready
            new_task = await game.ready()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert room.timer_task is new_task
            assert room.phase == "countdown"
            assert (room is not old_room) == recreate

    asyncio.run(scenario())


@pytest.mark.parametrize("playing", [False, True])
def test_deleted_room_and_reused_id_ignore_old_timer(monkeypatch, playing):
    async def scenario():
        async with match(monkeypatch) as game:
            task = await (game.begin() if playing else game.ready())
            _, wake = await game.sleeps.get()
            old_room = game.manager.rooms.pop("1234")
            old_messages = (list(game.first.messages), list(game.second.messages))
            game.first, game.second = Socket(), Socket()
            await game.connect("a", game.first)
            await game.connect("b", game.second)
            new_task = await game.ready()
            new_room = game.manager.rooms["1234"]
            game.now = old_room.ends_at if playing else old_room.starts_at
            wake.set_result(None)
            await task
            assert new_room is not old_room
            assert game.manager.rooms["1234"] is new_room
            assert new_room.timer_task is new_task
            assert new_room.phase == "countdown"
            assert not game.first.events("GAME_END")
            assert not game.second.events("GAME_END")
            assert old_messages == tuple(
                player.websocket.messages for player in old_room.players.values()
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["ready", "score", "disconnect"])
def test_operations_waiting_on_deleted_room_cannot_touch_replacement(
    monkeypatch, operation
):
    async def scenario():
        async with match(monkeypatch) as game:
            old_room = game.manager.rooms["1234"]
            entered = asyncio.Event()

            async def pending_operation():
                entered.set()
                if operation == "ready":
                    await game.manager.set_ready("1234", "a", True)
                elif operation == "score":
                    await game.manager.update_score("1234", "a", 1)
                else:
                    await game.manager.disconnect("1234", "a")

            async with old_room.lock:
                pending = asyncio.create_task(pending_operation())
                await entered.wait()
                del game.manager.rooms["1234"]
                game.first, game.second = Socket(), Socket()
                await game.connect("a", game.first)
                await game.connect("b", game.second)
            await pending
            assert old_room.players["a"].ready is False
            assert set(old_room.players) == {"a", "b"}
            assert game.manager.rooms["1234"] is not old_room
            assert all(
                player.score == 0 and not player.ready
                for player in game.manager.rooms["1234"].players.values()
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("recreate", [False, True])
def test_deletion_during_begin_broadcast_stops_old_messages(monkeypatch, recreate):
    async def scenario():
        async with match(monkeypatch) as game:
            entered, release = asyncio.Event(), asyncio.Event()
            game.first.blocks["GAME_BEGIN"] = entered, release
            task = await game.ready()
            _, wake = await game.sleeps.get()
            game.now = 110
            wake.set_result(None)
            await entered.wait()
            old_room = game.manager.rooms.pop("1234")
            old_second = game.second
            if recreate:
                game.first, game.second = Socket(), Socket()
                await game.connect("a", game.first)
                await game.connect("b", game.second)
            release.set()
            await game.assert_timer_stopped(task)
            assert not old_second.events("GAME_BEGIN")
            assert old_room.timer_task is None
            if recreate:
                assert game.manager.rooms["1234"].phase == "waiting"
                assert set(game.manager.rooms["1234"].players) == {"a", "b"}
            else:
                assert not game.manager.rooms

    asyncio.run(scenario())


def test_timer_cancellation_during_begin_broadcast_resets_match(monkeypatch):
    async def scenario():
        async with match(monkeypatch) as game:
            entered, release = asyncio.Event(), asyncio.Event()
            game.first.blocks["GAME_BEGIN"] = entered, release
            task = await game.ready()
            _, wake = await game.sleeps.get()
            game.now = 110
            wake.set_result(None)
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            room = game.manager.rooms["1234"]
            assert room.phase == "waiting"
            assert room.timer_task is None
            assert game.second.messages[-1]["phase"] == "waiting"
            assert not game.second.events("GAME_BEGIN")

    asyncio.run(scenario())


def test_score_waiting_for_room_lock_rechecks_deadline(monkeypatch):
    async def scenario():
        async with match(monkeypatch) as game:
            await game.begin()
            await game.sleeps.get()
            room = game.manager.rooms["1234"]
            entered = asyncio.Event()

            async def queued_score():
                entered.set()
                return await game.manager.update_score("1234", "a", 1)

            async with room.lock:
                game.now = room.ends_at - 0.001
                pending = asyncio.create_task(queued_score())
                await entered.wait()
                game.now = room.ends_at
            assert await pending
            assert room.phase == "finished"
            assert room.players["a"].score == 0
            assert not game.second.events("OPPONENT_SCORE")

    asyncio.run(scenario())


def test_delayed_countdown_skips_expired_play_and_finishes_once(monkeypatch):
    async def scenario():
        async with match(monkeypatch) as game:
            task = await game.ready()
            _, wake = await game.sleeps.get()
            game.now = 171
            wake.set_result(None)
            await task
            room = game.manager.rooms["1234"]
            assert room.phase == "finished"
            assert room.timer_task is None
            assert not game.first.events("GAME_BEGIN")
            assert game.first.events("GAME_END") == [
                {"type": "GAME_END", "score": 0, "opponent_score": 0, "result": "draw"}
            ]

    asyncio.run(scenario())


def test_stale_cleanup_and_notifications_cannot_remove_reused_room(monkeypatch):
    async def scenario():
        async with match(monkeypatch) as game:
            old_room = game.manager.rooms.pop("1234")
            game.first, game.second = Socket(), Socket()
            await game.connect("a", game.first)
            await game.connect("b", game.second)
            new_room = game.manager.rooms["1234"]
            game.manager._remove("1234", old_room, "a")
            game.manager._remove("1234", old_room, "b")
            await game.manager._send("1234", old_room, {"a": {"type": "GAME_BEGIN"}})
            game.now = old_room.ends_at
            await game.manager._finish("1234", old_room)
            assert game.manager.rooms["1234"] is new_room
            assert set(old_room.players) == {"a", "b"}
            assert not game.first.events("GAME_BEGIN")
            assert not game.first.events("GAME_END")

    asyncio.run(scenario())


def test_join_waiting_on_deleted_room_retries_current_room(monkeypatch):
    async def scenario():
        async with match(monkeypatch) as game:
            old_room = game.manager.rooms["1234"]
            entered = asyncio.Event()
            joining = Socket()

            async def queued_join():
                entered.set()
                await game.connect("c", joining)

            async with old_room.lock:
                pending = asyncio.create_task(queued_join())
                await entered.wait()
                del game.manager.rooms["1234"]
            await pending
            assert game.manager.rooms["1234"] is not old_room
            assert set(game.manager.rooms["1234"].players) == {"c"}

    asyncio.run(scenario())


def test_repeated_unknown_operations_do_not_cancel_match(monkeypatch):
    async def scenario():
        async with match(monkeypatch) as game:
            task = await game.ready()
            await game.sleeps.get()
            room = game.manager.rooms["1234"]
            async with room.lock:
                game.manager._remove("1234", room, "missing")
                await game.manager._send(
                    "1234", room, {"missing": {"type": "GAME_BEGIN"}}
                )
            await game.manager.disconnect("1234", "missing")
            await game.manager.disconnect("9999", "a")
            await game.manager.set_ready("9999", "a", True)
            assert not await game.manager.update_score("9999", "a", 1)
            assert not await game.manager.update_score("1234", "missing", 1)
            assert room.phase == "countdown"
            assert room.timer_task is task
            assert set(room.players) == {"a", "b"}

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["accepted", "failed", "cancelled"])
@pytest.mark.parametrize("recreate", [False, True])
def test_pending_accept_cannot_join_or_remove_replacement_room(outcome, recreate):
    async def scenario():
        manager = pk.PKRoomManager()
        entered, release = asyncio.Event(), asyncio.Event()

        class PendingSocket(Socket):
            async def accept(self, **kwargs: Any) -> None:
                entered.set()
                await release.wait()
                if outcome == "failed":
                    raise RuntimeError("test handshake failed")

        pending_socket, replacement = PendingSocket(), Socket()
        pending = asyncio.create_task(
            manager.connect(
                cast(WebSocket, pending_socket), "1234", "squat", CurrentUser(id="a")
            )
        )
        await entered.wait()
        old_room = manager.rooms["1234"]
        if recreate or outcome == "accepted":
            del manager.rooms["1234"]
        if recreate:
            assert await manager.connect(
                cast(WebSocket, replacement), "1234", "squat", CurrentUser(id="b")
            )
        if outcome == "cancelled":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            release.set()
            if outcome == "failed":
                with pytest.raises(RuntimeError, match="handshake failed"):
                    await pending
            else:
                assert not await pending
                assert pending_socket.closed[-1]["code"] == 4409
        assert not old_room.players
        if recreate:
            assert manager.rooms["1234"] is not old_room
            assert manager.rooms["1234"].players["b"].websocket is replacement
            await manager.disconnect("1234", "b")
        assert not manager.rooms

    asyncio.run(scenario())
