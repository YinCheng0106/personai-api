from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import personai_api.routers.analyze as analyze_module
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
                assert first.receive_json() == {"type": "GAME_START"}
                assert second.receive_json() == {"type": "GAME_START"}

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
