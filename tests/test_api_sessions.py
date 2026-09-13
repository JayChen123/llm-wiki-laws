"""会话接口：CRUD 与**越权防护**（别人的会话一律 404，不暴露存在性）。"""
from tests.conftest import new_session, register


def test_session_crud_flow(auth_client, fake_store):
    sid = new_session(auth_client)
    assert fake_store.session_belongs_to(sid, "alice")

    fake_store.add(sid, "user", "试用期最长多久", user_id="alice")
    fake_store.add(sid, "assistant", "最长六个月", user_id="alice")

    listed = auth_client.get("/api/sessions").json()
    assert [s["session_id"] for s in listed] == [sid]
    assert listed[0]["message_count"] == 2
    assert listed[0]["title"] == "试用期最长多久"        # 标题取首条提问

    msgs = auth_client.get(f"/api/sessions/{sid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]

    assert auth_client.delete(f"/api/sessions/{sid}").status_code == 200
    assert auth_client.get("/api/sessions").json() == []


def test_sessions_require_login(client):
    assert client.get("/api/sessions").status_code == 401
    assert client.post("/api/sessions").status_code == 401
    assert client.get("/api/sessions/x/messages").status_code == 401
    assert client.delete("/api/sessions/x").status_code == 401


def test_cannot_read_other_users_session(client, fake_store):
    """越权红线：A 不能读 B 的会话消息——返回 404 而非 403（不暴露存在性）。"""
    register(client, "alice")
    alice_sid = new_session(client)
    fake_store.add(alice_sid, "user", "alice 的隐私问题", user_id="alice")
    client.post("/api/auth/logout")

    register(client, "bob")
    resp = client.get(f"/api/sessions/{alice_sid}/messages")
    assert resp.status_code == 404
    assert "alice 的隐私问题" not in resp.text


def test_cannot_delete_other_users_session(client, fake_store):
    register(client, "alice")
    alice_sid = new_session(client)
    client.post("/api/auth/logout")

    register(client, "bob")
    assert client.delete(f"/api/sessions/{alice_sid}").status_code == 404
    assert fake_store.session_belongs_to(alice_sid, "alice")     # 未被删除


def test_cannot_chat_into_other_users_session(client, fake_store, fake_kb):
    register(client, "alice")
    alice_sid = new_session(client)
    client.post("/api/auth/logout")

    register(client, "bob")
    resp = client.post("/api/chat", json={"session_id": alice_sid, "question": "试用期"})
    assert resp.status_code == 404
    assert fake_kb.queries == []                                  # 根本没触发检索


def test_session_list_only_shows_own(client, fake_store):
    register(client, "alice")
    alice_sid = new_session(client)
    client.post("/api/auth/logout")

    register(client, "bob")
    bob_sid = new_session(client)

    listed = [s["session_id"] for s in client.get("/api/sessions").json()]
    assert listed == [bob_sid] and alice_sid not in listed


def test_delete_session_clears_long_term_memory(auth_client, fake_long_term):
    """删除会话时连带清该用户的长期记忆（避免已删内容仍被召回）。"""
    sid = new_session(auth_client)
    assert auth_client.delete(f"/api/sessions/{sid}").status_code == 200
    assert fake_long_term.cleared == ["alice"]


def test_unknown_session_returns_404(auth_client):
    assert auth_client.get("/api/sessions/does-not-exist/messages").status_code == 404
