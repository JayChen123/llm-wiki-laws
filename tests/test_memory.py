"""短期记忆 SQLite 存储测试。"""
from pathlib import Path

from app.memory.memory_store import MemoryStore


def test_memory_roundtrip(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem.db")
    store.add("s1", "user", "你好")
    store.add("s1", "assistant", "你好！")
    store.add("s1", "user", "请假流程")

    recent = store.recent("s1")
    assert [m["role"] for m in recent] == ["user", "assistant", "user"]

    assert store.recent_user_queries("s1", n=2) == ["你好", "请假流程"]
    assert store.recent_user_queries("s1", n=1) == ["请假流程"]

    store.clear("s1")
    assert store.recent("s1") == []


def test_memory_session_isolated(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem.db")
    store.add("a", "user", "q1")
    store.add("b", "user", "q2")
    assert store.recent_user_queries("a") == ["q1"]
    assert store.recent_user_queries("b") == ["q2"]


def test_history_rounds_limit(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem.db", history_rounds=2)
    for i in range(6):
        store.add("s", "user", f"q{i}")
        store.add("s", "assistant", f"a{i}")
    recent = store.recent("s")  # 默认 limit=history_rounds*2=4
    assert len(recent) == 4
    assert recent[-1]["content"] == "a5"
