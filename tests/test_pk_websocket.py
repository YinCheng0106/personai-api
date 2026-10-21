import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import personai_api.routers.analyze as analyze_module
import personai_api.routers.pk as pk_module
from personai_api.auth import CurrentUser
from personai_api.main import app


def test_pk_websocket_requires_authentication_and_origin() -> None:
    with TestClient(app) as client:
        try:
            with client.websocket_connect(
                "/ws/pk?room=1234&exercise_type=squat",
                headers={"origin": "http://localhost:3000"},
            ):
                raise AssertionError("connection should not be accepted")
        except WebSocketDisconnect as exc:
            assert exc.code == 4401

        try:
            with client.websocket_connect(
                "/ws/pk?room=1234&exercise_type=squat",
                headers={"origin": "https://attacker.test"},
                subprotocols=["personai.v1", "user-1"],
            ):
                raise AssertionError("connection should not be accepted")
        except WebSocketDisconnect as exc:
            assert exc.code == 4403


def test_pk_room_ready_start_and_score_sync(monkeypatch) -> None:
    monkeypatch.setattr(pk_module, "PREPARE_SECONDS", 0)
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda token, *_: CurrentUser(id=token),
    )
    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws/pk?room=1234&exercise_type=squat",
            headers={"origin": "http://localhost:3000"},
            subprotocols=["personai.v1", "user-1"],
        ) as first:
            assert first.accepted_subprotocol == "personai.v1"
            assert first.receive_json()["player_count"] == 1
            with client.websocket_connect(
                "/ws/pk?room=1234&exercise_type=squat",
                headers={"origin": "http://localhost:3000"},
                subprotocols=["personai.v1", "user-2"],
            ) as second:
                assert first.receive_json()["player_count"] == 2
                assert second.receive_json()["player_count"] == 2

                first.send_json({"type": "READY", "ready": True})
                assert first.receive_json()["opponent_ready"] is False
                assert second.receive_json()["opponent_ready"] is True

                second.send_json({"type": "READY", "ready": True})
                assert first.receive_json()["opponent_ready"] is True
                assert second.receive_json()["opponent_ready"] is True
                start = first.receive_json()
                assert second.receive_json()["type"] == "GAME_START"
                assert start["type"] == "GAME_START"
                assert start["duration_seconds"] == 60
                assert first.receive_json()["type"] == "GAME_BEGIN"
                assert second.receive_json()["type"] == "GAME_BEGIN"

                first.send_json({"type": "SCORE_UPDATE", "score": 1})
                assert second.receive_json() == {"type": "OPPONENT_SCORE", "score": 1}


def test_pk_room_rejects_third_player_and_mode_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda token, *_: CurrentUser(id=token),
    )
    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws/pk?room=5678&exercise_type=pushup",
            headers={"origin": "http://localhost:3000"},
            subprotocols=["personai.v1", "user-1"],
        ) as first:
            first.receive_json()
            try:
                with client.websocket_connect(
                    "/ws/pk?room=5678&exercise_type=squat",
                    headers={"origin": "http://localhost:3000"},
                    subprotocols=["personai.v1", "user-2"],
                ):
                    raise AssertionError("mismatched mode should be rejected")
            except WebSocketDisconnect as exc:
                assert exc.code == 4400

            with client.websocket_connect(
                "/ws/pk?room=5678&exercise_type=pushup",
                headers={"origin": "http://localhost:3000"},
                subprotocols=["personai.v1", "user-2"],
            ) as second:
                first.receive_json()
                second.receive_json()
                try:
                    with client.websocket_connect(
                        "/ws/pk?room=5678&exercise_type=pushup",
                        headers={"origin": "http://localhost:3000"},
                        subprotocols=["personai.v1", "user-3"],
                    ):
                        raise AssertionError("third player should be rejected")
                except WebSocketDisconnect as exc:
                    assert exc.code == 4409


def test_pk_rejects_score_before_game_start(monkeypatch) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="user-1"),
    )
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/ws/pk?room=9012&exercise_type=squat",
            headers={"origin": "http://localhost:3000"},
            subprotocols=["personai.v1", "valid-token"],
        ) as websocket,
    ):
        websocket.receive_json()
        websocket.send_json({"type": "SCORE_UPDATE", "score": 1})
        try:
            websocket.receive_json()
            raise AssertionError("score before start should close connection")
        except WebSocketDisconnect as exc:
            assert exc.code == 4400


@pytest.fixture
def pk_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        analyze_module, "verify_token", lambda token, *_: CurrentUser(id=token)
    )
    monkeypatch.setattr(pk_module, "manager", pk_module.PKRoomManager())
    with TestClient(app) as client:
        yield client


def _connect(client: TestClient, user: str):
    return client.websocket_connect(
        "/ws/pk?room=2468&exercise_type=squat",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", user],
    )


@pytest.mark.parametrize("invalid_score", [1, 3, True, -1, 10_001])
def test_started_game_rejects_duplicate_skipped_and_invalid_scores(
    pk_client: TestClient,
    invalid_score: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pk_module, "PREPARE_SECONDS", 0)
    with _connect(pk_client, "first") as first, _connect(pk_client, "second") as second:
        first.receive_json()  # initial single-player state
        first.receive_json()
        second.receive_json()
        first.send_json({"type": "READY", "ready": True})
        first.receive_json()
        second.receive_json()
        second.send_json({"type": "READY", "ready": True})
        first.receive_json()
        second.receive_json()
        assert (
            first.receive_json()["type"]
            == second.receive_json()["type"]
            == "GAME_START"
        )
        assert (
            first.receive_json()["type"]
            == second.receive_json()["type"]
            == "GAME_BEGIN"
        )
        first.send_json({"type": "SCORE_UPDATE", "score": 1})
        assert second.receive_json() == {"type": "OPPONENT_SCORE", "score": 1}
        # Duplicate READY must not enqueue a second GAME_START.
        first.send_json({"type": "READY", "ready": True})
        # No repeated start/state events are emitted during the active match.
        first.send_json({"type": "SCORE_UPDATE", "score": invalid_score})
        with pytest.raises(WebSocketDisconnect) as exc:
            first.receive_json()
        assert exc.value.code == 4400
        state = second.receive_json()
        assert state["type"] == "ROOM_STATE"
        assert state["player_count"] == 1
        assert state["opponent_ready"] is False


def test_duplicate_user_rejection_and_disconnect_room_cleanup(
    pk_client: TestClient,
) -> None:
    with _connect(pk_client, "first") as first:
        first.receive_json()
        with pytest.raises(WebSocketDisconnect) as exc:
            with _connect(pk_client, "first"):
                pytest.fail("duplicate user must be rejected")
        assert exc.value.code == 4409
        with _connect(pk_client, "second") as second:
            first.receive_json()
            second.receive_json()
            first.send_json({"type": "READY", "ready": True})
            first.receive_json()
            assert second.receive_json()["opponent_ready"] is True
        assert first.receive_json()["player_count"] == 1
        with _connect(pk_client, "replacement") as replacement:
            first.receive_json()
            state = replacement.receive_json()
            assert state["opponent_ready"] is False
            assert state["opponent_score"] == 0
            assert pk_module.manager.rooms["2468"].phase == "waiting"
    assert pk_module.manager.rooms == {}


def test_pk_scores_stay_in_their_room(
    pk_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pk_module, "PREPARE_SECONDS", 0)

    def connect(room: str, user: str):
        return pk_client.websocket_connect(
            f"/ws/pk?room={room}&exercise_type=squat",
            headers={"origin": "http://localhost:3000"},
            subprotocols=["personai.v1", user],
        )

    with connect("1111", "a1") as a1, connect("1111", "a2") as a2:
        a1.receive_json()
        a1.receive_json()
        a2.receive_json()
        a1.send_json({"type": "READY", "ready": True})
        a1.receive_json()
        a2.receive_json()
        a2.send_json({"type": "READY", "ready": True})
        for event in ("ROOM_STATE", "GAME_START", "GAME_BEGIN"):
            assert a1.receive_json()["type"] == event
            assert a2.receive_json()["type"] == event
        with connect("2222", "b1") as b1, connect("2222", "b2") as b2:
            b1.receive_json()
            b1.receive_json()
            b2.receive_json()
            a1.send_json({"type": "SCORE_UPDATE", "score": 1})
            assert a2.receive_json() == {"type": "OPPONENT_SCORE", "score": 1}
            # A room-state response is a deterministic ordering barrier. Any
            # leaked score would be queued ahead of it on the other room.
            b1.send_json({"type": "READY", "ready": False})
            assert b1.receive_json()["type"] == "ROOM_STATE"
            assert b2.receive_json()["type"] == "ROOM_STATE"
            assert pk_module.manager.rooms["2222"].players["b1"].score == 0
            assert pk_module.manager.rooms["2222"].players["b2"].score == 0


def test_legacy_password_reset_endpoints_are_not_registered(client: TestClient) -> None:
    for endpoint in ("/auth/forgot-password", "/auth/reset-password"):
        assert client.post(endpoint, json={}).status_code == 404
