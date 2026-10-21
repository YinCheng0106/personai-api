"""Better Auth JWT verification shared by REST and WebSocket endpoints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWKClientConnectionError

from personai_api.config import Settings, get_settings

logger = logging.getLogger(__name__)
JWKS_USER_AGENT = "PersonAI-API/1.0"


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None
    name: str | None = None


bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(
        url,
        cache_keys=True,
        lifespan=300,
        headers={"User-Agent": JWKS_USER_AGENT},
        timeout=10,
    )


def _signing_key(token: str, jwks_url: str) -> Any:
    client = _jwks_client(jwks_url)
    try:
        return client.get_signing_key_from_jwt(token)
    except PyJWKClientConnectionError:
        logger.warning("JWKS fetch failed; retrying once")
        return client.get_signing_key_from_jwt(token)


def verify_token(token: str, settings: Settings | None = None) -> CurrentUser:
    config = settings or get_settings()
    try:
        signing_key = _signing_key(token, config.auth_jwks_url)
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["EdDSA", "ES256", "RS256", "PS256"],
            issuer=config.auth_issuer,
            audience=config.auth_audience,
            options={"require": ["sub", "iss", "aud", "exp"]},
        )
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        logger.warning("JWT verification failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登入憑證無效或已過期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登入憑證缺少使用者識別",
        )
    return CurrentUser(
        id=subject,
        email=payload.get("email") if isinstance(payload.get("email"), str) else None,
        name=payload.get("name") if isinstance(payload.get("name"), str) else None,
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="請先登入",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_token(credentials.credentials)
