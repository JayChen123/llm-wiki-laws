"""注册 / 登录 / 令牌：bcrypt 存密码 + JWT 放 HttpOnly Cookie。

三条安全约束：
1. **密码只存 bcrypt 哈希**，绝不落明文、不用可逆加密。
2. **令牌放 HttpOnly + SameSite=Lax Cookie**：JS 读不到（防 XSS 窃取），
   且前端与 API 同源托管，天然规避 CSRF 与 CORS 配置。
3. **登录失败不区分"用户不存在/密码错"**，避免账号枚举。

bcrypt 只取密码前 72 字节（算法上限），超长部分忽略——须显式截断，
否则 bcrypt 5.x 会直接抛错而不是静默截断。
"""
from __future__ import annotations

import datetime

import bcrypt
import jwt

from app.config import JWT_ALGORITHM, JWT_EXPIRE_DAYS, JWT_SECRET

COOKIE_NAME = "access_token"
_BCRYPT_MAX_BYTES = 72
MIN_PASSWORD_LEN = 8
MIN_USERNAME_LEN = 3
MAX_USERNAME_LEN = 64


class AuthError(Exception):
    """认证相关错误（对外只暴露笼统原因，不泄露账号是否存在）。"""


def _password_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_password_bytes(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False        # 哈希格式异常视为校验失败，不冒泡到接口层


def validate_credentials(username: str, password: str) -> None:
    """注册时的入参校验；不通过抛 AuthError（消息可直接给用户看）。"""
    if not username or not username.strip():
        raise AuthError("用户名不能为空")
    if len(username) < MIN_USERNAME_LEN or len(username) > MAX_USERNAME_LEN:
        raise AuthError(f"用户名长度需在 {MIN_USERNAME_LEN}-{MAX_USERNAME_LEN} 之间")
    if any(c.isspace() for c in username):
        raise AuthError("用户名不能包含空白字符")
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"密码长度至少 {MIN_PASSWORD_LEN} 位")


def create_token(user_id: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(days=JWT_EXPIRE_DAYS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> str | None:
    """校验令牌并取出 user_id；过期/签名不符/格式错一律返回 None。"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    subject = payload.get("sub")
    return str(subject) if subject else None
