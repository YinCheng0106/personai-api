"""Better Auth JWT verification shared by REST and WebSocket endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from personai_api.config import Settings, get_settings


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None
    name: str | None = None


bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, lifespan=300)


def verify_token(token: str, settings: Settings | None = None) -> CurrentUser:
    config = settings or get_settings()
    try:
        signing_key = _jwks_client(config.auth_jwks_url).get_signing_key_from_jwt(token)
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["EdDSA", "ES256", "RS256", "PS256"],
            issuer=config.auth_issuer,
            audience=config.auth_audience,
            options={"require": ["sub", "iss", "aud", "exp"]},
        )
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
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
# ==========================================
# 忘記密碼與重設密碼 API
# ==========================================
import os
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
import resend

from personai_api.database import get_db
from personai_api.models.password_reset import PasswordReset
from pydantic import BaseModel, EmailStr

router = APIRouter(prefix="/auth", tags=["auth"])
resend.api_key = os.getenv("RESEND_API_KEY")

class ForgotPasswordSchema(BaseModel):
    email: EmailStr

class ResetPasswordSchema(BaseModel):
    token: str
    new_password: str

def get_user_by_email(db: Session, email: str):
    # 自動搜尋資料庫中包含 'user' 名稱的 Table 並嘗試查詢
    table_names = ["users", "user", '"user"', '"Users"', '"User"']
    for table in table_names:
        try:
            result = db.execute(
                text(f"SELECT id, email FROM {table} WHERE email = :email"),
                {"email": email}
            ).fetchone()
            return result, table
        except Exception:
            db.rollback()
            continue
    return None, None

@router.post("/forgot-password")
def forgot_password(data: ForgotPasswordSchema, db: Session = Depends(get_db)):
    user, _ = get_user_by_email(db, data.email)

    if not user:
        return {"message": "若 Email 存在，重設密碼信件已寄出"}

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

    reset_record = PasswordReset(
        user_id=str(user.id),
        token=token,
        expires_at=expires_at
    )
    db.add(reset_record)
    db.commit()

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    reset_link = f"{frontend_url}/reset-password?token={token}"

    try:
        resend.Emails.send({
            "from": "PersonAI <onboarding@resend.dev>",
            "to": [user.email],
            "subject": "【PersonAI】請重設您的密碼",
            "html": f"""
                <div style="font-family: sans-serif; padding: 20px;">
                  <h2>重設您的 PersonAI 密碼</h2>
                  <p>您好，我們收到了您的密碼重設請求。請點擊下方按鈕進行重設（連結於 15 分鐘內有效）：</p>
                  <p style="margin: 20px 0;">
                    <a href="{reset_link}" style="background-color: #2563eb; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; display: inline-block;">點此重設密碼</a>
                  </p>
                  <p style="color: #666; font-size: 12px;">若無法點擊按鈕，請複製連結至瀏覽器開啟：<br>{reset_link}</p>
                </div>
            """
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail="郵件發送失敗")

    return {"message": "重設密碼信件已寄出，請檢查您的電子信箱"}

@router.post("/reset-password")
def reset_password(data: ResetPasswordSchema, db: Session = Depends(get_db)):
    record = db.query(PasswordReset).filter(
        PasswordReset.token == data.token,
        PasswordReset.used == False
    ).first()

    if not record:
        raise HTTPException(status_code=400, detail="無效或已被使用的重設連結")

    if datetime.now(timezone.utc) > record.expires_at:
        raise HTTPException(status_code=400, detail="重設連結已過期，請重新申請")

    # 自動尋找對應的 Table 並更新密碼
    table_names = ["users", "user", '"user"', '"Users"', '"User"']
    updated = False
    for table in table_names:
        try:
            db.execute(
                text(f"UPDATE {table} SET password_hash = :password WHERE id = :user_id"),
                {"password": data.new_password, "user_id": record.user_id}
            )
            db.commit()
            updated = True
            break
        except Exception:
            db.rollback()
            continue

    if not updated:
        raise HTTPException(status_code=500, detail="更新密碼失敗，找不到對應的使用者資料表")

    record.used = True
    db.commit()

    return {"message": "密碼更新成功，請使用新密碼重新登入"}