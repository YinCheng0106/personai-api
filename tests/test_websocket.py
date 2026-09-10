from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import personai_api.routers.analyze as analyze_module
from personai_api.auth import CurrentUser
from personai_api.config import Settings
from personai_api.main import app


def _frame(frame_id: int = 1) -> dict:
    return {
        "frame_id": frame_id,
        "timestamp": 1.0,
        "keypoints": [{"x": 0.5, "y": 0.5, "z": 0, "visibility": 1} for _ in range(33)],
    }


def test_websocket_rejects_missing_token() -> None:
    with TestClient(app) as client:
        try:
            with client.websocket_connect(
                "/ws/analyze/squat", headers={"origin": "http://localhost:3000"}
            ):
                raise AssertionError("connection should not be accepted")
        except WebSocketDisconnect as exc:
            assert exc.code == 4401


def test_websocket_echoes_frame_id_and_processing_time(monkeypatch) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000001"),
    )
    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws/analyze/squat",
            headers={"origin": "http://localhost:3000"},
            subprotocols=["personai.v1", "valid-token"],
        ) as websocket:
            assert websocket.accepted_subprotocol == "personai.v1"
            websocket.send_json(_frame(42))
            result = websocket.receive_json()
            assert result["frame_id"] == 42
            assert result["processing_ms"] >= 0


def test_websocket_closes_on_invalid_frame(monkeypatch) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000002"),
    )
    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws/analyze/squat",
            headers={"origin": "http://localhost:3000"},
            subprotocols=["personai.v1", "valid-token"],
        ) as websocket:
            websocket.send_json({"frame_id": 1, "keypoints": []})
            try:
                websocket.receive_json()
                raise AssertionError("invalid frame should close the connection")
            except WebSocketDisconnect as exc:
                assert exc.code == 4400


def test_websocket_closes_when_frame_rate_is_exceeded(monkeypatch) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000003"),
    )
    monkeypatch.setattr(
        analyze_module,
        "get_settings",
        lambda: Settings(
            auth_issuer="http://localhost:3000",
            auth_audience="personai-api",
            auth_jwks_url="http://localhost:3000/api/auth/jwks",
            cors_origins=("http://localhost:3000",),
            websocket_max_message_bytes=65536,
            websocket_max_frames_per_second=1,
        ),
    )
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/ws/analyze/squat",
            headers={"origin": "http://localhost:3000"},
            subprotocols=["personai.v1", "valid-token"],
        ) as websocket,
    ):
        websocket.send_json(_frame(1))
        websocket.receive_json()
        websocket.send_json(_frame(2))
        try:
            websocket.receive_json()
            raise AssertionError("rate limit should close the connection")
        except WebSocketDisconnect as exc:
            assert exc.code == 4429


def test_websocket_rejects_duplicate_frame_id(monkeypatch) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000004"),
    )
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/ws/analyze/squat",
            headers={"origin": "http://localhost:3000"},
            subprotocols=["personai.v1", "valid-token"],
        ) as websocket,
    ):
        websocket.send_json(_frame(7))
        websocket.receive_json()
        websocket.send_json(_frame(7))
        try:
            websocket.receive_json()
            raise AssertionError("duplicate frame should close the connection")
        except WebSocketDisconnect as exc:
            assert exc.code == 4400


def test_websocket_rejects_non_finite_keypoint(monkeypatch) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000005"),
    )
    frame = _frame(1)
    frame["keypoints"][0]["x"] = float("nan")
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/ws/analyze/squat",
            headers={"origin": "http://localhost:3000"},
            subprotocols=["personai.v1", "valid-token"],
        ) as websocket,
    ):
        websocket.send_json(frame)
        try:
            websocket.receive_json()
            raise AssertionError("non-finite keypoint should close the connection")
        except WebSocketDisconnect as exc:
            assert exc.code == 4400


def test_websocket_accepts_pose_missing_and_preserves_state_integrity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000006"),
    )
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/ws/analyze/squat",
            headers={"origin": "http://localhost:3000"},
            subprotocols=["personai.v1", "valid-token"],
        ) as websocket,
    ):
        for frame_id in range(1, 4):
            frame = _frame(frame_id)
            frame["timestamp"] = frame_id / 10
            websocket.send_json(frame)
            active = websocket.receive_json()

        websocket.send_json({"kind": "pose_missing", "frame_id": 4, "timestamp": 0.4})
        missing = websocket.receive_json()

        assert active["tracking_state"] == "ACTIVE"
        assert missing["tracking_state"] == "PAUSED"
        assert missing["tracking_hints"] == ["POSE_NOT_FOUND"]
        assert missing["form_errors"] == []
        assert missing["errors"] == []
        assert missing["rep_count"] == active["rep_count"]
        assert missing["calories"] == active["calories"]
        assert all(value is None for value in missing["angles"].values())
