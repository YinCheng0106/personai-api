"""Authenticated user identity endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from personai_api.auth import CurrentUser, get_current_user

router = APIRouter(prefix="/user", tags=["使用者"])


class UserMe(BaseModel):
    id: str
    email: str | None
    name: str | None


@router.get("/me", response_model=UserMe)
def get_user(current_user: CurrentUser = Depends(get_current_user)) -> UserMe:
    return UserMe(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
    )
