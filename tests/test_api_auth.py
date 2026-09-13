"""认证接口：注册/登录/登出/当前用户、Cookie 安全属性、密码与账号枚举防护（离线）。"""
from tests.conftest import register

from app.api.auth import COOKIE_NAME, hash_password, verify_password


def test_register_creates_user_and_logs_in(client, fake_store):
    resp = register(client, "alice")
    assert resp.status_code == 201
    assert resp.json()["user_id"] == "alice"

    # 注册即登录：Cookie 已下发
    assert COOKIE_NAME in resp.cookies
    assert client.get("/api/auth/me").json()["user_id"] == "alice"
    # 库里存的是哈希，不是明文
    stored = fake_store.get_user("alice")["password_hash"]
    assert stored != "secret123" and stored.startswith("$2b$")


def test_register_rejects_duplicate_username(client):
    assert register(client, "alice").status_code == 201
    assert register(client, "alice").status_code == 409


def test_register_validates_username_and_password(client):
    assert register(client, "ab").status_code == 400                  # 用户名太短
    assert register(client, "a" * 65).status_code == 400              # 用户名太长
    assert register(client, "with space").status_code == 400          # 含空白
    assert register(client, "alice", password="short").status_code == 400


def test_auth_cookie_is_httponly_and_samesite_lax(client):
    """令牌必须 HttpOnly（防 XSS 窃取）+ SameSite=Lax（防 CSRF）。"""
    resp = register(client, "alice")
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


def test_login_success_and_wrong_password(client):
    register(client, "alice")
    client.post("/api/auth/logout")

    bad = client.post("/api/auth/login", json={"username": "alice", "password": "wrongpass"})
    assert bad.status_code == 401

    ok = client.post("/api/auth/login", json={"username": "alice", "password": "secret123"})
    assert ok.status_code == 200 and ok.json()["user_id"] == "alice"


def test_login_does_not_leak_whether_user_exists(client):
    """账号枚举防护：不存在的用户与密码错误，返回完全相同的提示。"""
    register(client, "alice")
    client.post("/api/auth/logout")

    wrong_pw = client.post("/api/auth/login",
                           json={"username": "alice", "password": "wrongpass"})
    no_user = client.post("/api/auth/login",
                          json={"username": "nobody", "password": "wrongpass"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["detail"] == no_user.json()["detail"]


def test_logout_clears_session(client):
    register(client, "alice")
    assert client.get("/api/auth/me").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_me_requires_login(client):
    assert client.get("/api/auth/me").status_code == 401


def test_invalid_or_forged_token_is_rejected(client):
    client.cookies.set(COOKIE_NAME, "not-a-jwt")
    assert client.get("/api/auth/me").status_code == 401


def test_token_for_deleted_user_is_rejected(client, fake_store):
    """令牌有效但用户已被删除 → 401，避免用旧令牌长期访问。"""
    from app.api.auth import create_token

    client.cookies.set(COOKIE_NAME, create_token("ghost"))
    assert client.get("/api/auth/me").status_code == 401


def test_password_hash_roundtrip_and_edges():
    h = hash_password("secret123")
    assert verify_password("secret123", h) is True
    assert verify_password("secret124", h) is False
    assert verify_password("anything", None) is False
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_long_password_is_truncated_not_crashing():
    """bcrypt 只处理前 72 字节；超长密码必须显式截断而不是抛错。"""
    long_pw = "a" * 200
    h = hash_password(long_pw)
    assert verify_password(long_pw, h) is True
    assert verify_password("a" * 72, h) is True          # 72 字节之后被忽略
