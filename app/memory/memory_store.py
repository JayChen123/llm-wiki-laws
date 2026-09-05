"""轻量短期记忆：SQLite 单文件存储多轮会话历史。"""
from __future__ import annotations

import sqlite3
from pathlib import Path


class MemoryStore:
    """会话消息的轻量持久化存储。

    短期记忆 = 最近 N 轮对话，用于：
    - 生成回答时注入 LLM 上下文（多轮连贯）
    - 检索时把最近用户问句并入 query（处理指代/延续）
    """

    def __init__(self, db_path: Path, history_rounds: int = 5):
        self.db_path = Path(db_path)
        self.history_rounds = history_rounds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "session TEXT NOT NULL,"
            "role TEXT NOT NULL,"
            "content TEXT NOT NULL,"
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        self.conn.commit()

    def add(self, session: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages(session, role, content) VALUES(?,?,?)",
            (session, role, content),
        )
        self.conn.commit()

    def recent(self, session: str, limit: int | None = None) -> list[dict]:
        """返回最近消息列表（旧→新）。"""
        limit = limit or self.history_rounds * 2
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE session=? "
            "ORDER BY id DESC LIMIT ?",
            (session, limit),
        ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def recent_user_queries(self, session: str, n: int = 2) -> list[str]:
        """返回最近 n 条用户问题（用于检索注入）。"""
        rows = self.conn.execute(
            "SELECT content FROM messages WHERE session=? AND role='user' "
            "ORDER BY id DESC LIMIT ?",
            (session, n),
        ).fetchall()
        return [c for (c,) in reversed(rows)]

    def clear(self, session: str) -> None:
        self.conn.execute("DELETE FROM messages WHERE session=?", (session,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
