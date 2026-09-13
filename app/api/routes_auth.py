"""认证接口：注册 / 登录 / 登出 / 当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api.auth import (
    COOKIE_NAME,
    AuthError,
    create_token,
    hash_password,
    validate_credentials,
    verify_password,
)
from app.api.deps import current_user, get_memory_store
from app.config import JWT_EXPIRE_DAYS

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 登录失败统一话术：不区分"用户不存在/密码错"，避免账号枚举
_LOGIN_FAILED = "用户名或密码错误"


class Credentials(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")
    display_name: str | None = Field(None, description="显示名（可选，默认同用户名）")


class UserOut(BaseModel):
    user_id: str
    display_name: str


def _set_auth_cookie(response: Response, user_id: str) -> None:
    """把 JWT 写进 HttpOnly Cookie：JS 读不到（防 XSS 窃取）+ SameSite=Lax（防 CSRF）。"""
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_token(user_id),
        max_age=JWT_EXPIRE_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=False,      # 本地 http 开发；生产上 https 时应置 True
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
        payload: Credentials,
        response: Response,
        store=Depends(get_memory_store),
):
    """注册并直接登录（免去注册后还要手动登录一步）。"""
    try:
        validate_credentials(payload.username, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    created = store.create_user(
        payload.username, hash_password(payload.password), payload.display_name
    )
    if not created:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已被占用")

    _set_auth_cookie(response, payload.username)
    user = store.get_user(payload.username)
    return UserOut(user_id=user["user_id"], display_name=user["display_name"] or user["user_id"])


@router.post("/login", response_model=UserOut)
def login(payload: Credentials, response: Response, store=Depends(get_memory_store)):
    user = store.get_user(payload.username)
    if not user or not verify_password(payload.password, user.get("password_hash")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_LOGIN_FAILED)
    _set_auth_cookie(response, user["user_id"])
    return UserOut(user_id=user["user_id"], display_name=user["display_name"] or user["user_id"])


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user_id: str = Depends(current_user), store=Depends(get_memory_store)):
    user = store.get_user(user_id)
    return UserOut(user_id=user["user_id"], display_name=user["display_name"] or user["user_id"])
