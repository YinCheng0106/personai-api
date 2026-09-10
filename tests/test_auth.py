from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

import personai_api.auth as auth_module
from personai_api.auth import verify_token
from personai_api.config import Settings
from personai_api.main import app

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = PRIVATE_KEY.public_key()
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


def test_jwks_connection_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingJwksClient:
        def get_signing_key_from_jwt(self, _: str):
            raise jwt.PyJWKClientConnectionError("temporary tunnel failure")

    clients = iter((FailingJwksClient(), FakeJwksClient()))
    cache_cleared = False

    def fake_jwks_client(_: str):
        return next(clients)

    def clear_cache() -> None:
        nonlocal cache_cleared
        cache_cleared = True

    fake_jwks_client.cache_clear = clear_cache  # type: ignore[attr-defined]
    monkeypatch.setattr(auth_module, "_jwks_client", fake_jwks_client)

    assert verify_token(_token(), SETTINGS).id == "00000000-0000-4000-8000-000000000001"
    assert cache_cleared is True


def test_rest_endpoint_requires_bearer_token() -> None:
    with TestClient(app) as client:
        response = client.get("/wk/me")
    assert response.status_code == 401
