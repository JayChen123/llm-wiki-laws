"""MySQL 记忆后端（离线）：断言**生成的 SQL 与绑定参数**，重点是 user_id 隔离。

离线测试不连真实 MySQL，因此这里锁的是"我们发出了什么 SQL"（尤其：过滤条件、
排序、LIMIT、绑定值）——真实 SQL 执行由联通性验证脚本覆盖。
"""
import pytest

from app.memory import build_search_query, create_memory_store
from app.memory.mysql_store import MySQLMemoryStore


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows: list[tuple] = []

    def execute(self, sql, params=()):
        self.conn.executed.append((sql, params))
        if self.conn.raise_on and self.conn.raise_on in sql:
            raise self.conn.raise_on_exc
        self._rows = self.conn.next_rows
        self.description = [("col",)] if sql.strip().upper().startswith("SELECT") else None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    """记录执行过的 SQL；`next_rows` 控制 SELECT 返回。"""

    def __init__(self, next_rows=None, raise_on=None, raise_on_exc=None):
        self.executed: list[tuple] = []
        self.next_rows = list(next_rows or [])
        self.raise_on = raise_on
        self.raise_on_exc = raise_on_exc
        self.committed = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed += 1

    def close(self):
        pass

    def sql_log(self) -> list[str]:
        return [sql for sql, _ in self.executed]

    def params_for(self, fragment: str) -> tuple:
        for sql, params in self.executed:
            if fragment in sql:
                return params
        raise AssertionError(f"未执行包含 {fragment!r} 的 SQL；实际：{self.sql_log()}")


def _store(conn=None, **kw):
    return MySQLMemoryStore(conn=conn or FakeConnection(), **kw)


# ---------- 表结构 ----------

def test_schema_creates_four_tables():
    conn = FakeConnection()
    _store(conn).ensure_schema()
    ddl = " ".join(conn.sql_log())
    for table in ("users", "sessions", "messages", "user_profile"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    assert "UNIQUE KEY uniq_user_fact (user_id, fact)" in ddl     # 画像去重靠 DB 约束


# ---------- user_id 隔离（安全关键） ----------

def test_facts_filters_by_user_id():
    """画像查询必须带 user_id 过滤，否则跨用户泄漏。"""
    conn = FakeConnection(next_rows=[("在深圳工作",)])
    store = _store(conn)
    assert store.facts("alice", limit=3) == ["在深圳工作"]

    sql = conn.sql_log()[0]
    assert "WHERE user_id=%s" in sql
    assert conn.params_for("WHERE user_id") == ("alice", 3)


def test_clear_user_scopes_every_delete_to_that_user():
    conn = FakeConnection()
    _store(conn).clear_user("alice")
    deletes = [sql for sql in conn.sql_log() if sql.startswith("DELETE")]
    assert len(deletes) == 3                                   # messages / sessions / user_profile
    for sql in deletes:
        assert "WHERE user_id=%s" in sql
    assert all(conn.params_for("DELETE") == ("alice",) for _ in [0])


def test_clear_session_only_touches_that_session():
    conn = FakeConnection()
    _store(conn).clear("s1")
    assert "WHERE session_id=%s" in conn.sql_log()[0]
    assert conn.params_for("DELETE") == ("s1",)


def test_add_binds_user_id_with_message():
    conn = FakeConnection()
    _store(conn).add("s1", "user", "试用期最长多久", user_id="alice")
    assert conn.params_for("INSERT INTO messages") == (
        "s1", "alice", "user", "试用期最长多久", None)      # 无引用时写 NULL


def test_add_serializes_citations():
    """assistant 消息的引用以 JSON 存入，便于历史回看时仍然可溯源。"""
    import json

    conn = FakeConnection()
    citations = [{"id": "KP_019", "title": "第二章·第十九条"}]
    _store(conn).add("s1", "assistant", "最长六个月", user_id="alice", citations=citations)
    payload = conn.params_for("INSERT INTO messages")[4]
    assert json.loads(payload) == citations


def test_ensure_user_and_session_use_insert_ignore():
    conn = FakeConnection()
    store = _store(conn)
    store.ensure_user("alice")
    store.ensure_session("s1", "alice")
    assert all("INSERT IGNORE" in sql for sql in conn.sql_log())
    assert conn.params_for("INTO users") == ("alice", "alice")
    assert conn.params_for("INTO sessions") == ("s1", "alice")


# ---------- 消息读取 ----------

def test_recent_orders_ascending_with_limit():
    conn = FakeConnection(next_rows=[("user", "q1"), ("assistant", "a1")])
    store = _store(conn, history_rounds=1)
    assert store.recent("s1") == [{"role": "user", "content": "q1"},
                                  {"role": "assistant", "content": "a1"}]
    sql = conn.sql_log()[0]
    assert "ORDER BY id DESC LIMIT 2" in sql      # 先取最近 N 条
    assert sql.rstrip().endswith("ORDER BY id")   # 再翻回旧→新
    assert conn.params_for("FROM messages") == ("s1",)


def test_recent_user_queries_filters_role_and_preserves_order():
    conn = FakeConnection(next_rows=[("你好",), ("请假流程",)])
    store = _store(conn)
    assert store.recent_user_queries("s1", n=2) == ["你好", "请假流程"]
    sql = conn.sql_log()[0]
    assert "role='user'" in sql and "ORDER BY id DESC LIMIT 2" in sql


def test_turn_count_counts_user_turns():
    conn = FakeConnection(next_rows=[(3,)])
    assert _store(conn).turn_count("s1") == 3
    assert "role='user'" in conn.sql_log()[0]


def test_clear_and_close_are_safe():
    conn = FakeConnection()
    store = _store(conn)
    store.clear("s1")
    store.close()                                  # 不抛异常即可
    assert conn.committed >= 1


# ---------- 画像去重 ----------

def test_add_fact_rejects_blank():
    conn = FakeConnection()
    store = _store(conn)
    assert store.add_fact("alice", "   ") is False
    assert store.add_fact("", "在深圳工作") is False
    assert conn.executed == []                     # 空白输入不该发 SQL


def test_add_fact_swallows_only_unique_violation():
    """重复事实返回 False；其他异常（如连接断开）必须抛出，不能伪装成"已存在"。"""
    class IntegrityErr(Exception):
        pass

    import pymysql
    duplicate = FakeConnection(raise_on="INSERT INTO user_profile",
                               raise_on_exc=pymysql.err.IntegrityError("dup"))
    assert _store(duplicate).add_fact("alice", "在深圳工作") is False

    broken = FakeConnection(raise_on="INSERT INTO user_profile",
                            raise_on_exc=ConnectionError("连接断开"))
    with pytest.raises(ConnectionError):
        _store(broken).add_fact("alice", "在深圳工作")


def test_add_fact_truncates_to_column_length():
    conn = FakeConnection()
    _store(conn).add_fact("alice", "很" * 800)
    assert len(conn.params_for("INSERT INTO user_profile")[1]) == 512


# ---------- 后端选择 ----------

def test_create_memory_store_builds_mysql_backend(monkeypatch, tmp_path):
    """记忆只有 MySQL 一个后端：不再有 SQLite 降级路径。"""
    import app.memory as memory_pkg

    created = {}

    class Recorder(MySQLMemoryStore):
        def __init__(self, **kw):
            created.update(kw)
            super().__init__(conn=FakeConnection(), **kw)

        def ensure_schema(self):
            created["schema"] = True

    monkeypatch.setattr(memory_pkg, "MySQLMemoryStore", Recorder)
    store = create_memory_store(history_rounds=3)
    assert isinstance(store, Recorder)
    assert created["history_rounds"] == 3 and created["schema"] is True


# ---------- 串题修复 ----------

def test_build_search_query_auto_skips_topic_switch():
    """换话题时不并入上一问（P2 实测的串题污染）。"""
    assert build_search_query("试用期最长多久", ["被派遣劳动者被退回后能解除吗"], "auto") \
        == "试用期最长多久"


def test_build_search_query_auto_keeps_followup():
    """追问（含指代词）仍并入上一问，指代延续不被破坏。"""
    out = build_search_query("那经济补偿呢", ["试用期被解除有补偿吗"], "auto")
    assert "试用期被解除有补偿吗" in out and "那经济补偿呢" in out


def test_build_search_query_short_question_is_followup():
    assert "上一问" in build_search_query("怎么办", ["上一问"], "auto")


def test_build_search_query_modes():
    past = ["上一问内容"]
    assert build_search_query("试用期最长多久", past, "always") == "上一问内容 试用期最长多久"
    assert build_search_query("试用期最长多久", past, "off") == "试用期最长多久"
    assert build_search_query("试用期最长多久", [], "auto") == "试用期最长多久"
