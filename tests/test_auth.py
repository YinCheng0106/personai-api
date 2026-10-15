from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jwt import PyJWKClientConnectionError

import personai_api.auth as auth_module
from personai_api.auth import verify_token
from personai_api.config import Settings
from personai_api.main import app

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = PRIVATE_KEY.public_key()
REAL_JWKS_CLIENT_FACTORY = auth_module._jwks_client
SETTINGS = Settings(
    auth_issuer="https://personai.test",
    auth_audience="personai-api",
    auth_jwks_url="https://personai.test/api/auth/jwks",
    cors_origins=("https://personai.test",),
    websocket_max_message_bytes=65536,
    websocket_max_frames_per_second=15,
)


class FakeJwksClient:
    def __init__(self, key=PUBLIC_KEY):
        self.key = key

    def get_signing_key_from_jwt(self, _: str):
        return SimpleNamespace(key=self.key)


class FlakyJwksClient(FakeJwksClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def get_signing_key_from_jwt(self, token: str):
        self.calls += 1
        if self.calls == 1:
            raise PyJWKClientConnectionError("temporary JWKS failure")
        return super().get_signing_key_from_jwt(token)


def _token(**overrides: object) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": "00000000-0000-4000-8000-000000000001",
        "iss": SETTINGS.auth_issuer,
        "aud": SETTINGS.auth_audience,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "email": "test@personai.test",
        **overrides,
    }
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _: FakeJwksClient())


def test_valid_jwt_uses_subject_as_user_id() -> None:
    user = verify_token(_token(), SETTINGS)
    assert user.id == "00000000-0000-4000-8000-000000000001"
    assert user.email == "test@personai.test"


def test_jwks_client_uses_server_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def client_factory(url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return object()

    REAL_JWKS_CLIENT_FACTORY.cache_clear()
    monkeypatch.setattr(auth_module, "PyJWKClient", client_factory)
    REAL_JWKS_CLIENT_FACTORY(SETTINGS.auth_jwks_url)
    REAL_JWKS_CLIENT_FACTORY.cache_clear()

    assert calls == [
        (
            SETTINGS.auth_jwks_url,
            {
                "cache_keys": True,
                "lifespan": 300,
                "headers": {"User-Agent": "PersonAI-API/1.0"},
                "timeout": 10,
            },
        )
    ]


def test_transient_jwks_failure_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FlakyJwksClient()
    monkeypatch.setattr(auth_module, "_jwks_client", lambda _: client)

    user = verify_token(_token(), SETTINGS)

    assert user.id == "00000000-0000-4000-8000-000000000001"
    assert client.calls == 2


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},
        {"iss": "https://attacker.test"},
        {"aud": "wrong-api"},
    ],
)
def test_invalid_claims_are_rejected(claims: dict[str, object]) -> None:
    with pytest.raises(HTTPException) as exc:
        verify_token(_token(**claims), SETTINGS)
    assert exc.value.status_code == 401


def test_forged_signature_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {
            "sub": "attacker",
            "iss": SETTINGS.auth_issuer,
            "aud": SETTINGS.auth_audience,
            "exp": datetime.now(UTC) + timedelta(minutes=15),
        },
        other_key,
        algorithm="RS256",
    )
    with pytest.raises(HTTPException):
        verify_token(forged, SETTINGS)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/user/me", None),
        ("get", "/wk/me", None),
        ("get", "/wk/me/summary", None),
        ("get", "/wk/me/daily", None),
        ("get", "/inbody/me", None),
        ("post", "/inbody/me", {}),
        ("post", "/inbody/me/calories", {}),
        ("get", "/body-profile/me", None),
        ("patch", "/body-profile/me", {}),
        ("get", "/body-profile/me/measurements", None),
        ("post", "/body-profile/me/measurements", {}),
        ("post", "/wk/me/record", {}),
    ],
)
def test_rest_endpoints_require_bearer_token(
    method: str, path: str, body: dict | None
) -> None:
    with TestClient(app) as client:
        response = client.request(method, path, json=body)
    assert response.status_code == 401
