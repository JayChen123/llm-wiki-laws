"""长期记忆：用户隔离（安全关键）、画像与历史召回、后端故障直接暴露（假 Milvus，离线）。"""
import numpy as np
import pytest

from app.generation.answer import AnswerGenerator
from app.memory.long_term import LongTermMemory, UserMemoryStore, build_summary
from app.memory.profile_extractor import ProfileExtractor, parse_facts


class FakeProfileStore:
    """假画像存储（替代真实 MySQL）：按用户隔离地保存事实。"""

    def __init__(self):
        self.by_user: dict[str, list[str]] = {}

    def add_fact(self, user_id, fact, source="auto"):
        if fact in self.by_user.get(user_id, []):
            return False
        self.by_user.setdefault(user_id, []).append(fact)
        return True

    def facts(self, user_id, limit=10):
        return self.by_user.get(user_id, [])[:limit]


class FakeEmbedder:
    """按关键词映射的主题向量，让语义召回可预测，且不加载 bge-m3。"""

    def encode(self, texts):
        out = np.zeros((len(texts), 3), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i, 0] = 1.0 if "加班" in t else 0.0
            out[i, 1] = 1.0 if "离婚" in t else 0.0
            out[i, 2] = 1.0 if "继承" in t else 0.0
            norm = np.linalg.norm(out[i]) or 1.0
            out[i] = out[i] / norm
        return out


class FakeMilvusClient:
    """内存版 Milvus：实现 search 的 filter 语义，用于验证 user_id 隔离。

    忠实模拟**写入/删除都要 flush 才可见**（rows=真实数据，visible=可查询到的），
    否则"写完立刻召回不到""删除没生效"这类顺序问题会在单测里蒙混过关。
    """

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.visible: set[str] = set()
        self.last_filter = None
        self.flushed = 0
        self._exists = False

    def has_collection(self, name):
        return self._exists

    def describe_collection(self, name):
        return {"fields": [{"name": "vector", "params": {"dim": 3}}]}

    def create_schema(self, **kwargs):
        class S:
            def add_field(self, *a, **k):
                pass
        return S()

    def prepare_index_params(self):
        class I:
            def add_index(self, **k):
                pass
        return I()

    def create_collection(self, name, **kwargs):
        self._exists = True

    def drop_collection(self, name):
        self._exists = False

    def upsert(self, collection_name, data):
        for row in data:
            self.rows[row["mem_id"]] = row        # 写入但尚未 flush

    def flush(self, collection_name):
        self.flushed += 1
        self.visible = set(self.rows)

    def search(self, collection_name, data, limit=5, filter=None, output_fields=None,
               search_params=None):
        self.last_filter = filter          # 记录以便断言过滤条件
        user = filter.split('"')[1] if filter else None
        hits = []
        for mem_id, row in self.rows.items():
            if mem_id not in self.visible:        # 未 flush 的写入查不到
                continue
            if user and row["user_id"] != user:
                continue
            hits.append({"id": row["mem_id"], "distance": 0.9,
                         "entity": {"user_id": row["user_id"], "kind": row["kind"],
                                    "text": row["text"]}})
        return [hits[:limit]]

    def query(self, collection_name, filter=None, output_fields=None):
        user = filter.split('"')[1] if filter and "user_id" in filter else None
        n = sum(1 for k, r in self.rows.items()
                if k in self.visible and (not user or r["user_id"] == user))
        return [{"count(*)": n}]

    def delete(self, collection_name, filter=None):
        user = filter.split('"')[1]
        for key in [k for k, r in self.rows.items() if r["user_id"] == user]:
            self.rows.pop(key)                    # 删除同样要 flush 才可见


def _store():
    return UserMemoryStore(embedder=FakeEmbedder(), collection="mem", dim=3,
                           client=FakeMilvusClient())


# ---------- 用户隔离（安全关键） ----------

def test_user_memory_isolated_between_users():
    store = _store()
    store.add("alice", "history", "用户问：加班费怎么算")
    store.add("bob", "history", "用户问：离婚冷静期多久")

    a = store.search("alice", "加班", top_k=5)
    b = store.search("bob", "加班", top_k=5)

    assert [m["text"] for m in a] == ["用户问：加班费怎么算"]
    # 关键是"查不到别人的"：bob 只会看到自己的记忆（假 client 不做相似度排序）
    assert [m["text"] for m in b] == ["用户问：离婚冷静期多久"]
    assert store.count("alice") == 1 and store.count("bob") == 1


def test_search_always_filters_by_user_id():
    """红线：检索必须带 user_id 过滤，否则会跨用户泄漏。"""
    store = _store()
    store.add("alice", "history", "内容")
    store.search("alice", "加班")
    assert store._client.last_filter == 'user_id == "alice"'


def test_empty_user_or_query_returns_nothing():
    store = _store()
    store.add("alice", "history", "内容")
    assert store.search("", "加班") == []
    assert store.search("alice", "") == []
    assert store.add("", "history", "内容") is False


def test_clear_removes_only_that_users_memory():
    store = _store()
    store.add("alice", "history", "a")
    store.add("bob", "history", "b")
    store.clear("alice")
    assert store.count("alice") == 0 and store.count("bob") == 1


def test_same_text_not_duplicated():
    """稳定主键：同用户同文本重复写入覆盖而非堆积。"""
    store = _store()
    store.add("alice", "history", "同一段记忆")
    store.add("alice", "history", "同一段记忆")
    assert store.count("alice") == 1


# ---------- 门面：召回与写入 ----------

def test_long_term_recall_merges_profile_and_history():
    store = _store()
    sql = FakeProfileStore()
    sql.add_fact("alice", "在深圳工作", source="auto")
    sql.add_fact("alice", "是HR")

    ltm = LongTermMemory(store=store, sql_store=sql)
    ltm.remember_turn("alice", "加班费怎么算", "按第四十四条计算。")

    memories = ltm.recall("alice", "加班费")
    kinds = {m["kind"] for m in memories}
    assert "profile" in kinds and "history" in kinds
    assert any("在深圳工作" in m["text"] for m in memories)


def test_long_term_recall_isolated_between_users():
    store = _store()
    sql = FakeProfileStore()
    sql.add_fact("alice", "在深圳工作")
    sql.add_fact("bob", "在北京工作")

    ltm = LongTermMemory(store=store, sql_store=sql)
    assert "在深圳工作" in "".join(m["text"] for m in ltm.recall("alice", "加班"))
    assert "在北京工作" not in "".join(m["text"] for m in ltm.recall("alice", "加班"))


def test_backend_failure_surfaces_not_swallowed():
    """记忆不做降级：后端故障必须抛出，不能静默返回空（否则会被误读为"没有历史"）。"""
    class Boom:
        def __getattr__(self, name):
            def _raise(*a, **k):
                raise ConnectionError("后端不可用")
            return _raise

    ltm = LongTermMemory(store=Boom(), sql_store=Boom())
    with pytest.raises(ConnectionError):
        ltm.recall("alice", "加班")
    with pytest.raises(ConnectionError):
        ltm.remember_turn("alice", "q", "a")
    with pytest.raises(ConnectionError):
        ltm.remember_facts("alice", ["事实"])


def test_build_summary_is_llm_free():
    text = build_summary("加班费怎么算", "按第四十四条计算。\n另外注意基数。")
    assert "加班费怎么算" in text and "第四十四条" in text
    assert "\n" not in text.split("回答要点：")[1]        # 换行被压平
    assert len(text) < 500


# ---------- 记忆注入 prompt（不得作为法律依据） ----------

def test_memory_injected_as_separate_non_authoritative_block():
    prompt = AnswerGenerator._format_memory([
        {"kind": "profile", "text": "在深圳工作"},
        {"kind": "history", "text": "用户问：加班费怎么算"},
    ])
    assert "用户背景" in prompt
    assert "不得作为法律依据" in prompt
    assert "在深圳工作" in prompt and "（画像）" in prompt and "（相关历史）" in prompt


def test_no_memory_produces_no_block():
    assert AnswerGenerator._format_memory(None) == ""
    assert AnswerGenerator._format_memory([]) == ""
    assert AnswerGenerator._format_memory([{"kind": "profile", "text": "   "}]) == ""


def test_memory_is_not_in_system_prompt():
    """记忆必须走 user 段落注入，不能写进 system prompt（否则被当成规则）。"""
    from app.generation.answer import SYSTEM_PROMPT

    assert "在深圳工作" not in SYSTEM_PROMPT


# ---------- 画像抽取 ----------

def test_parse_facts_tolerates_noise_and_limits():
    assert parse_facts('{"facts": ["在深圳工作", "是HR"]}') == ["在深圳工作", "是HR"]
    assert parse_facts('```json\n{"facts":["关注加班费"]}\n```') == ["关注加班费"]
    assert parse_facts('{"facts": "在深圳工作"}') == ["在深圳工作"]
    assert parse_facts('{"facts": ["a", "a", "b"]}') == ["a", "b"]      # 去重
    assert parse_facts("模型乱答") == []
    assert parse_facts('{"facts": []}') == []


def test_parse_facts_caps_length_and_count():
    long_fact = "很" * 100
    facts = parse_facts('{"facts": ["%s", "b", "c", "d"]}' % long_fact)
    assert len(facts) == 3                       # 最多 3 条
    assert all(len(f) <= 40 for f in facts)      # 单条截断


def test_extractor_returns_empty_on_failure_or_without_key():
    class Boom:
        def create(self, **kwargs):
            raise TimeoutError("超时")

    ex = ProfileExtractor(api_key="fake")
    ex._client = type("C", (), {"chat": type("Ch", (), {"completions": Boom()})()})()
    assert ex.extract([{"role": "user", "content": "我在深圳"}]) == []

    ex2 = ProfileExtractor()
    ex2.api_key = ""
    assert ex2.extract([{"role": "user", "content": "我在深圳"}]) == []
    assert ex2.extract([]) == []
