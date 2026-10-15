"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# REST/WebSocket authentication settings are read before database modules are
# necessarily imported, so load the project environment at this boundary.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        item.strip() for item in os.getenv(name, default).split(",") if item.strip()
    )


@dataclass(frozen=True)
class Settings:
    auth_issuer: str
    auth_audience: str
    auth_jwks_url: str
    cors_origins: tuple[str, ...]
    websocket_max_message_bytes: int
    websocket_max_frames_per_second: int


@lru_cache
def get_settings() -> Settings:
    issuer = os.getenv("AUTH_ISSUER", "http://localhost:3000")
    return Settings(
        auth_issuer=issuer,
        auth_audience=os.getenv("AUTH_AUDIENCE", "personai-api"),
        auth_jwks_url=os.getenv("AUTH_JWKS_URL", f"{issuer.rstrip('/')}/api/auth/jwks"),
        cors_origins=_csv_env(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,"
            "https://localhost:3000,https://127.0.0.1:3000",
        ),
        websocket_max_message_bytes=int(
            os.getenv("WEBSOCKET_MAX_MESSAGE_BYTES", "65536")
        ),
        websocket_max_frames_per_second=int(
            os.getenv("WEBSOCKET_MAX_FRAMES_PER_SECOND", "15")
        ),
    )
