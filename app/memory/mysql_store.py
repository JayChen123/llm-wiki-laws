"""短期记忆的 MySQL 后端（**记忆的唯一后端**）。

- 会话消息：`messages`（按 `session_id` 取最近 N 轮）
- 用户与会话：`users` / `sessions`（P4 接入注册登录后 user_id 即真实用户）
- 用户画像：`user_profile`（长期记忆的结构化部分，UNIQUE(user_id, fact) 去重）

设计取舍：**不做 SQLite 降级、不做连接失败回退**——MySQL 不可用时应立即暴露，
而不是悄悄换成本地文件让数据分散在两处。`client` 可注入假连接供离线单测断言
所生成的 SQL（真实 SQL 执行由验证脚本覆盖）。
"""
from __future__ import annotations

from typing import Any

from app.config import (
    MYSQL_DATABASE,
    MYSQL_HOST,
    MYSQL_PASSWORD,
    MYSQL_PORT,
    MYSQL_USER,
)

# 建表语句（utf8mb4；messages 按会话与用户各建索引）
_TABLE_DDL = [
    """CREATE TABLE IF NOT EXISTS users (
            user_id VARCHAR(64) PRIMARY KEY,
            display_name VARCHAR(128),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS sessions (
            session_id VARCHAR(64) PRIMARY KEY,
            user_id VARCHAR(64),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_sessions_user (user_id))""",
    """CREATE TABLE IF NOT EXISTS messages (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            session_id VARCHAR(64),
            user_id VARCHAR(64),
            role VARCHAR(16),
            content MEDIUMTEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_messages_session (session_id, id),
            INDEX idx_messages_user (user_id))""",
    """CREATE TABLE IF NOT EXISTS user_profile (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id VARCHAR(64),
            fact VARCHAR(512),
            source VARCHAR(32),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_user_fact (user_id, fact))""",
]


class MySQLMemoryStore:
    """短期记忆与用户画像的 MySQL 存储。"""

    def __init__(
            self,
            history_rounds: int = 5,
            conn=None,
            **connect_kwargs: Any,
    ):
        self.history_rounds = history_rounds
        self.conn = conn if conn is not None else self._connect(connect_kwargs)

    def _connect(self, kwargs: dict):
        import pymysql
        return pymysql.connect(
            host=kwargs.get("host", MYSQL_HOST),
            port=int(kwargs.get("port", MYSQL_PORT)),
            user=kwargs.get("user", MYSQL_USER),
            password=kwargs.get("password", MYSQL_PASSWORD),
            database=kwargs.get("database", MYSQL_DATABASE),
            charset="utf8mb4",
            autocommit=True,
        )

    def ensure_schema(self) -> None:
        cursor = self.conn.cursor()
        for ddl in _TABLE_DDL:
            cursor.execute(ddl)
        self.conn.commit()

    def _run(self, sql: str, params: tuple = ()) -> list[tuple]:
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall() if cursor.description else []
        self.conn.commit()
        return list(rows)

    # ---------- 用户 / 会话 ----------

    def ensure_user(self, user_id: str, display_name: str | None = None) -> None:
        """登记用户（不存在则建）。P4 接入注册登录前，user_id 由 CLI 传入。"""
        self._run(
            "INSERT IGNORE INTO users(user_id, display_name) VALUES(%s, %s)",
            (user_id, display_name or user_id),
        )

    def ensure_session(self, session: str, user_id: str) -> None:
        self._run(
            "INSERT IGNORE INTO sessions(session_id, user_id) VALUES(%s, %s)",
            (session, user_id),
        )

    # ---------- 消息 ----------

    def add(self, session: str, role: str, content: str, user_id: str = "") -> None:
        self._run(
            "INSERT INTO messages(session_id, user_id, role, content) VALUES(%s, %s, %s, %s)",
            (session, user_id, role, content),
        )

    def recent(self, session: str, limit: int | None = None) -> list[dict]:
        """返回最近消息（旧→新）。"""
        limit = limit or self.history_rounds * 2
        rows = self._run(
            "SELECT role, content FROM ("
            "  SELECT id, role, content FROM messages WHERE session_id=%s"
            f"  ORDER BY id DESC LIMIT {int(limit)}) AS t ORDER BY id",
            (session,),
        )
        return [{"role": r, "content": c} for r, c in rows]

    def recent_user_queries(self, session: str, n: int = 2) -> list[str]:
        """返回最近 n 条用户问题（用于检索注入）。"""
        rows = self._run(
            "SELECT content FROM ("
            "  SELECT id, content FROM messages WHERE session_id=%s AND role='user'"
            f"  ORDER BY id DESC LIMIT {int(n)}) AS t ORDER BY id",
            (session,),
        )
        return [c for (c,) in rows]

    def turn_count(self, session: str) -> int:
        """当前会话的用户轮数（用于判断是否该抽画像）。"""
        rows = self._run(
            "SELECT COUNT(*) FROM messages WHERE session_id=%s AND role='user'",
            (session,),
        )
        return int(rows[0][0]) if rows else 0

    def clear(self, session: str) -> None:
        self._run("DELETE FROM messages WHERE session_id=%s", (session,))

    def clear_user(self, user_id: str) -> None:
        """清空某用户的全部记忆（消息 + 会话 + 画像），供"清除我的数据"使用。"""
        self._run("DELETE FROM messages WHERE user_id=%s", (user_id,))
        self._run("DELETE FROM sessions WHERE user_id=%s", (user_id,))
        self._run("DELETE FROM user_profile WHERE user_id=%s", (user_id,))

    # ---------- 画像（长期记忆的结构化部分） ----------

    def add_fact(self, user_id: str, fact: str, source: str = "auto") -> bool:
        """写入一条用户事实；重复则跳过。返回是否新增。

        只吞"唯一约束冲突"（已有该事实），连接断开等真实故障照常抛出。
        """
        fact = (fact or "").strip()[:512]
        if not user_id or not fact:
            return False
        try:
            self._run(
                "INSERT INTO user_profile(user_id, fact, source) VALUES(%s, %s, %s)",
                (user_id, fact, source),
            )
            return True
        except Exception as exc:
            import pymysql
            if isinstance(exc, pymysql.err.IntegrityError):
                return False
            raise

    def facts(self, user_id: str, limit: int = 10) -> list[str]:
        """按用户取画像事实——**必须过滤 user_id**，否则会跨用户泄漏。"""
        rows = self._run(
            "SELECT fact FROM user_profile WHERE user_id=%s ORDER BY id DESC LIMIT %s",
            (user_id, int(limit)),
        )
        return [f for (f,) in rows]

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
