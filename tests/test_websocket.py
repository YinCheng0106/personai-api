from types import SimpleNamespace

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
