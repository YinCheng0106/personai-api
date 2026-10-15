from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

import personai_api.routers.analyze as analyze_module
from personai_api.auth import CurrentUser
from personai_api.config import Settings
from personai_api.db_models import BodyProfileModel
from personai_api.main import app


def _frame(frame_id: int = 1) -> dict:
    return {
        "frame_id": frame_id,
        "timestamp": 1.0,
        "keypoints": [{"x": 0.5, "y": 0.5, "z": 0, "visibility": 1} for _ in range(33)],
    }


class _SingleRepAnalyzer:
    def __init__(self, exercise_type: str) -> None:
        self.exercise_type = exercise_type

    def analyze(self, landmarks: object, timestamp: float | None) -> SimpleNamespace:
        return SimpleNamespace(
            rep_count=1,
            state="up",
            angles={},
            form_errors=[],
            tracking_hints=[],
            tracking_state="ACTIVE",
            confidence=1.0,
            is_visible=True,
        )

    def pose_missing(self, timestamp: float | None) -> SimpleNamespace:
        raise AssertionError("not used")

    def reset(self) -> None:
        pass


def test_websocket_rejects_missing_token() -> None:
    with TestClient(app) as client:
        try:
            with client.websocket_connect(
                "/ws/analyze/squat", headers={"origin": "http://localhost:3000"}
            ):
                raise AssertionError("connection should not be accepted")
        except WebSocketDisconnect as exc:
            assert exc.code == 4401


def test_websocket_rejects_untrusted_origin() -> None:
    with TestClient(app) as client:
        try:
            with client.websocket_connect(
                "/ws/analyze/squat",
                headers={"origin": "https://attacker.test"},
                subprotocols=["personai.v1", "not-checked"],
            ):
                raise AssertionError("connection should not be accepted")
        except WebSocketDisconnect as exc:
            assert exc.code == 4403


def test_websocket_echoes_frame_id_and_processing_time(
    monkeypatch, client: TestClient
) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000001"),
    )
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
        assert result["calories"] is None


def test_websocket_closes_on_invalid_frame(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000002"),
    )
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


def test_websocket_closes_when_frame_rate_is_exceeded(
    monkeypatch, client: TestClient
) -> None:
    monkeypatch.setattr(
        analyze_module,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0, perf_counter=lambda: 0.0),
    )
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
    with client.websocket_connect(
        "/ws/analyze/squat",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "valid-token"],
    ) as websocket:
        websocket.send_json(_frame(1))
        websocket.receive_json()
        websocket.send_json(_frame(2))
        try:
            websocket.receive_json()
            raise AssertionError("rate limit should close the connection")
        except WebSocketDisconnect as exc:
            assert exc.code == 4429


def test_websocket_rejects_duplicate_frame_id(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000004"),
    )
    with client.websocket_connect(
        "/ws/analyze/squat",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "valid-token"],
    ) as websocket:
        websocket.send_json(_frame(7))
        websocket.receive_json()
        websocket.send_json(_frame(7))
        try:
            websocket.receive_json()
            raise AssertionError("duplicate frame should close the connection")
        except WebSocketDisconnect as exc:
            assert exc.code == 4400


def test_websocket_rejects_non_finite_keypoint(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000005"),
    )
    frame = _frame(1)
    frame["keypoints"][0]["x"] = float("nan")
    with client.websocket_connect(
        "/ws/analyze/squat",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "valid-token"],
    ) as websocket:
        websocket.send_json(frame)
        try:
            websocket.receive_json()
            raise AssertionError("non-finite keypoint should close the connection")
        except WebSocketDisconnect as exc:
            assert exc.code == 4400


def test_websocket_counts_reps_but_calories_are_null_without_profile_weight(
    monkeypatch, client: TestClient
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id="00000000-0000-4000-8000-000000000006"),
    )
    monkeypatch.setattr(analyze_module, "BiomechanicsAnalyzer", _SingleRepAnalyzer)
    monkeypatch.setattr(analyze_module.logger, "warning", warnings.append)

    with client.websocket_connect(
        "/ws/analyze/squat?weight_kg=70",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "valid-token"],
    ) as websocket:
        websocket.send_json(_frame())
        result = websocket.receive_json()

    assert result["rep_count"] == 1
    assert result["form_errors"] == []
    assert result["tracking_state"] == "ACTIVE"
    assert result["calories"] is None
    assert warnings == []


def test_websocket_uses_authenticated_basic_profile_weight_for_calories(
    monkeypatch, client: TestClient, db_session: Session
) -> None:
    user_id = "00000000-0000-4000-8000-000000000007"
    db_session.add(BodyProfileModel(user_id=user_id, weight_kg=60))
    db_session.commit()
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id=user_id),
    )
    monkeypatch.setattr(analyze_module, "BiomechanicsAnalyzer", _SingleRepAnalyzer)

    with client.websocket_connect(
        "/ws/analyze/squat",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "valid-token"],
    ) as websocket:
        websocket.send_json(_frame())
        result = websocket.receive_json()

    assert result["rep_count"] == 1
    assert result["calories"] == 0.3


def test_websocket_continues_when_body_profile_lookup_fails(
    monkeypatch,
    client: TestClient,
    db_session: Session,
) -> None:
    user_id = "00000000-0000-4000-8000-000000000008"
    warnings: list[str] = []

    def fail_lookup(*args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(db_session, "get", fail_lookup)
    monkeypatch.setattr(
        analyze_module,
        "verify_token",
        lambda *_: CurrentUser(id=user_id),
    )
    monkeypatch.setattr(analyze_module, "BiomechanicsAnalyzer", _SingleRepAnalyzer)
    monkeypatch.setattr(analyze_module.logger, "warning", warnings.append)

    with client.websocket_connect(
        "/ws/analyze/squat",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "valid-token"],
    ) as websocket:
        websocket.send_json(_frame())
        result = websocket.receive_json()

    assert result["rep_count"] == 1
    assert result["calories"] is None
    assert warnings == ["Body profile lookup failed; calorie estimation is unavailable"]
    assert "database unavailable" not in warnings[0]
    assert "valid-token" not in warnings[0]
    assert user_id not in warnings[0]


@pytest.fixture
def verified_socket(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> TestClient:
    monkeypatch.setattr(
        analyze_module, "verify_token", lambda *_: CurrentUser(id="socket-test-user")
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
            websocket_max_frames_per_second=15,
        ),
    )
    # Replace this module's clock only; never patch the process/event-loop clock.
    monkeypatch.setattr(
        analyze_module,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0, perf_counter=lambda: 0.0),
    )
    return client


def test_frame_rate_window_expires_at_one_second(
    monkeypatch: pytest.MonkeyPatch,
    verified_socket: TestClient,
) -> None:
    from dataclasses import replace

    settings = replace(analyze_module.get_settings(), websocket_max_frames_per_second=1)
    monkeypatch.setattr(analyze_module, "get_settings", lambda: settings)
    times = iter([0.0, 1.0, 1.1])
    monkeypatch.setattr(
        analyze_module,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(times),
            perf_counter=lambda: 0.0,
        ),
    )
    with verified_socket.websocket_connect(
        "/ws/analyze/squat",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "local-test-token"],
    ) as ws:
        for frame_id in [0, 1]:
            ws.send_json({"kind": "pose_missing", "frame_id": frame_id})
            assert ws.receive_json()["frame_id"] == frame_id
        ws.send_json({"kind": "pose_missing", "frame_id": 2})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4429


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        "null",
        '{"kind":"other","frame_id":1}',
        '{"kind":"pose_missing","frame_id":-1}',
        '{"kind":"pose_missing","frame_id":1,"timestamp":Infinity}',
    ],
)
def test_malformed_messages_close_and_release_user_slot(
    verified_socket: TestClient, raw: str
) -> None:
    with verified_socket.websocket_connect(
        "/ws/analyze/squat",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "local-test-token"],
    ) as ws:
        ws.send_text(raw)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4400
    assert "socket-test-user" not in analyze_module.active_users


def test_message_limit_counts_utf8_bytes(verified_socket: TestClient) -> None:
    # Each character is 3 bytes; character count alone would incorrectly accept it.
    with verified_socket.websocket_connect(
        "/ws/analyze/squat",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "local-test-token"],
    ) as ws:
        ws.send_text('"' + "測" * 22_000 + '"')
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4400
        assert exc.value.reason == "Message too large"


@pytest.mark.parametrize("origin", [None, "http://localhost:3000.attacker.test"])
def test_origin_requires_exact_allowlist_match(
    verified_socket: TestClient, origin: str | None
) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with verified_socket.websocket_connect(
            "/ws/analyze/squat",
            headers={"origin": origin} if origin else {},
            subprotocols=["personai.v1", "local-test-token"],
        ):
            pytest.fail("untrusted origin must be rejected")
    assert exc.value.code == 4403


@pytest.mark.parametrize(
    "protocols",
    [
        ["local-test-token", "personai.v1"],
        ["personai.v1"],
        ["personai.v1", "local-test-token", "extra"],
    ],
)
def test_websocket_requires_exact_protocol_contract(
    verified_socket: TestClient, protocols: list[str]
) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with verified_socket.websocket_connect(
            "/ws/analyze/squat",
            headers={"origin": "http://localhost:3000"},
            subprotocols=protocols,
        ):
            pytest.fail("invalid subprotocols must be rejected")
    assert exc.value.code == 4401


def test_duplicate_connection_does_not_evict_original_and_disconnect_allows_reconnect(
    verified_socket: TestClient,
) -> None:
    options: dict[str, Any] = {
        "headers": {"origin": "http://localhost:3000"},
        "subprotocols": ["personai.v1", "local-test-token"],
    }
    with verified_socket.websocket_connect("/ws/analyze/squat", **options) as first:
        with pytest.raises(WebSocketDisconnect) as exc:
            with verified_socket.websocket_connect("/ws/analyze/squat", **options):
                pytest.fail("second connection must be rejected")
        assert exc.value.code == 4409
        first.send_json({"kind": "pose_missing", "frame_id": 1})
        assert first.receive_json()["frame_id"] == 1
    with verified_socket.websocket_connect("/ws/analyze/squat", **options) as next_ws:
        next_ws.send_json({"kind": "pose_missing", "frame_id": 0})
        assert next_ws.receive_json()["frame_id"] == 0


def test_pose_missing_reset_and_frame_sequence_use_real_analyzer(
    verified_socket: TestClient,
) -> None:
    frame = _frame()
    # Straight bilateral legs and torso: real geometry, no mocked analyzer/angles.
    for index, x, y in [
        (11, 0.4, 0.1),
        (12, 0.6, 0.1),
        (23, 0.4, 0.35),
        (24, 0.6, 0.35),
        (25, 0.4, 0.6),
        (26, 0.6, 0.6),
        (27, 0.4, 0.9),
        (28, 0.6, 0.9),
    ]:
        frame["keypoints"][index].update(x=x, y=y)
    with verified_socket.websocket_connect(
        "/ws/analyze/squat",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "local-test-token"],
    ) as ws:
        for frame_id, state in enumerate(["ACQUIRING", "ACQUIRING", "ACTIVE"]):
            ws.send_json({**frame, "frame_id": frame_id, "timestamp": frame_id / 10})
            result = ws.receive_json()
            assert result["tracking_state"] == state
        assert result["angles"]["left_knee"] == 180
        for frame_id, timestamp, state in [(3, 0.3, "PAUSED"), (4, 1.4, "LOST")]:
            ws.send_json(
                {"kind": "pose_missing", "frame_id": frame_id, "timestamp": timestamp}
            )
            result = ws.receive_json()
            assert result["tracking_state"] == state
            assert result["is_visible"] is False
            assert result["errors"] == result["form_errors"] == []
            assert all(value is None for value in result["angles"].values())
        for frame_id, state in [(5, "ACQUIRING"), (6, "ACQUIRING"), (7, "ACTIVE")]:
            ws.send_json(
                {**frame, "frame_id": frame_id, "timestamp": 1.5 + frame_id / 10}
            )
            assert ws.receive_json()["tracking_state"] == state
        ws.send_json({"action": "reset"})
        assert ws.receive_json() == {"action": "reset", "status": "ok"}
        ws.send_json({**frame, "frame_id": 8, "timestamp": 3.0})
        reset = ws.receive_json()
        assert reset["tracking_state"] == "ACQUIRING"
        assert reset["rep_count"] == 0
        assert reset["calories"] is None
        # Reset does not permit replaying an already consumed frame ID.
        ws.send_json({"kind": "pose_missing", "frame_id": 7})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4400


def test_other_users_weight_cannot_enable_calorie_estimation(
    verified_socket: TestClient,
    db_session: Session,
) -> None:
    db_session.add(BodyProfileModel(user_id="another-user", weight_kg=80))
    db_session.commit()
    with verified_socket.websocket_connect(
        "/ws/analyze/squat?weight_kg=80",
        headers={"origin": "http://localhost:3000"},
        subprotocols=["personai.v1", "local-test-token"],
    ) as ws:
        ws.send_json(_frame())
        assert ws.receive_json()["calories"] is None
