"""API 测试的共享假实现与 TestClient fixture（全部离线，不连 MySQL/Milvus/LLM）。

通过 `app.dependency_overrides` 替换依赖，因此接口层逻辑（认证、越权校验、SSE 事件流）
能被真实覆盖，而外部服务全部是假的。
"""
import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.auth import hash_password
from app.api.main import create_app
from app.generation.intent import Intent, IntentResult


class FakeMemoryStore:
    """内存版 MySQLMemoryStore：实现 API 用到的全部方法。"""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, str] = {}          # session_id -> user_id
        self.messages: list[tuple] = []             # (session_id, user_id, role, content)
        self.facts: dict[str, list[str]] = {}

    # --- 账号 ---
    def create_user(self, user_id, password_hash, display_name=None):
        if user_id in self.users:
            return False
        self.users[user_id] = {"user_id": user_id, "password_hash": password_hash,
                               "display_name": display_name or user_id}
        return True

    def get_user(self, user_id):
        return self.users.get(user_id)

    # --- 会话 ---
    def ensure_session(self, session, user_id):
        self.sessions.setdefault(session, user_id)

    def session_belongs_to(self, session, user_id):
        return self.sessions.get(session) == user_id

    def list_sessions(self, user_id):
        out = []
        for sid, uid in self.sessions.items():
            if uid != user_id:
                continue
            n = sum(1 for m in self.messages if m[0] == sid)
            out.append({"session_id": sid, "created_at": "", "message_count": n})
        return out

    def session_messages(self, session, limit=200):
        return [{"role": m[2], "content": m[3], "created_at": "", "citations": m[4]}
                for m in self.messages if m[0] == session][:limit]

    def delete_session(self, session):
        self.sessions.pop(session, None)
        self.messages = [m for m in self.messages if m[0] != session]

    # --- 消息 ---
    def add(self, session, role, content, user_id="", citations=None):
        self.messages.append((session, user_id, role, content, citations or []))

    def recent(self, session, limit=None):
        msgs = [{"role": m[2], "content": m[3]} for m in self.messages if m[0] == session]
        return msgs[-(limit or 10):]

    def recent_user_queries(self, session, n=2):
        return [m[3] for m in self.messages if m[0] == session and m[2] == "user"][-n:]

    def turn_count(self, session):
        return sum(1 for m in self.messages if m[0] == session and m[2] == "user")

    def clear(self, session):
        self.messages = [m for m in self.messages if m[0] != session]

    def clear_user(self, user_id):
        self.messages = [m for m in self.messages if m[1] != user_id]  # noqa: E501
        self.facts.pop(user_id, None)

    def add_fact(self, user_id, fact, source="auto"):
        if fact in self.facts.get(user_id, []):
            return False
        self.facts.setdefault(user_id, []).append(fact)
        return True

    def facts(self, user_id, limit=10):
        return self.facts.get(user_id, [])[:limit]

    def ensure_user(self, user_id, display_name=None):
        pass

    def ensure_schema(self):
        pass

    def close(self):
        pass


class FakeKB:
    """假知识库：固定返回两条知识点，并记录检索词。"""

    def __init__(self):
        self.queries: list[str] = []
        self.last_trace = {"rounds": 0, "reasons": ["测试轨迹"]}

    def search(self, query, top_k=5):
        self.queries.append(query)
        return [
            {"id": "KP_019", "title": "第二章·第十九条", "score": 0.9,
             "content": "试用期不得超过六个月。", "source": "中华人民共和国劳动合同法.md L1"},
            {"id": "KP_042", "title": "第四章·第四十二条", "score": 0.4,
             "content": "孕期不得解除。", "source": "中华人民共和国劳动合同法.md L2"},
        ]


class FakeGen:
    """假生成器：逐块产出，便于断言 SSE 的 delta 事件序列。

    `last_memory` 记在**实例**上（测试请用 `fake_gen` fixture 取同一个实例，
    不要读类属性——那永远是 None）。
    """

    def __init__(self, pieces=None, fail=False):
        self.pieces = pieces or ["试用期", "最长", "六个月。"]
        self.fail = fail
        self.last_memory = None

    def generate_stream(self, query, contexts, history=None, memory=None):
        self.last_memory = memory
        if self.fail:
            raise RuntimeError("模型挂了")
        for p in self.pieces:
            yield p


class FakeLongTerm:
    def __init__(self):
        self.turns: list[tuple] = []
        self.cleared: list[str] = []

    def recall(self, user_id, query, top_k=3):
        return [{"kind": "profile", "text": "在深圳工作", "score": 1.0}]

    def remember_turn(self, user_id, question, answer):
        self.turns.append((user_id, question))
        return True

    def remember_facts(self, user_id, facts):
        return 0

    def clear(self, user_id):
        self.cleared.append(user_id)


class FakeClassifier:
    def __init__(self, intent=Intent.ANSWERABLE):
        self.intent = intent

    def classify(self, query):
        if self.intent is not Intent.ANSWERABLE:
            return IntentResult(self.intent, "测试")
        if "天气" in query:
            return IntentResult(Intent.OFF_TOPIC, "闲聊")
        return IntentResult(Intent.ANSWERABLE, "")


@pytest.fixture
def fake_store():
    return FakeMemoryStore()


@pytest.fixture
def fake_kb():
    return FakeKB()


@pytest.fixture
def fake_gen():
    return FakeGen()


@pytest.fixture
def fake_long_term():
    return FakeLongTerm()


class NullLogger:
    """吞掉日志的假 logger。

    为什么要这个：API 层用的是模块级真实 logger，若不替换，**跑一次 pytest 就会往
    生产日志 logs/qa.log 里写几十条假问答事件**（曾污染 297 条，占日志 69%），
    既影响真实日志的审计与分析，也会把滚动日志挤出真实记录。
    """

    def info(self, line):
        pass


@pytest.fixture
def client(fake_store, fake_kb, fake_gen, fake_long_term, monkeypatch):
    """注入了假依赖的 TestClient（不连任何外部服务，也不写生产日志）。"""
    from app.api import routes_chat

    monkeypatch.setattr(routes_chat, "_logger", NullLogger())
    app = create_app()
    app.dependency_overrides[deps.get_memory_store] = lambda: fake_store
    app.dependency_overrides[deps.get_kb] = lambda: fake_kb
    app.dependency_overrides[deps.get_generator] = lambda: fake_gen
    app.dependency_overrides[deps.get_long_term] = lambda: fake_long_term
    app.dependency_overrides[deps.get_classifier] = lambda: FakeClassifier()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_client(client, fake_store):
    """已注册并登录的客户端（注册即登录）。"""
    client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    return client


def register(client, username, password="secret123"):
    return client.post("/api/auth/register", json={"username": username, "password": password})


def new_session(client) -> str:
    return client.post("/api/sessions").json()["session_id"]


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 响应体解析成 [(event, data), ...]。"""
    import json

    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, {}
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event:
            events.append((event, data))
    return events


__all__ = ["FakeMemoryStore", "FakeKB", "FakeGen", "FakeLongTerm", "register",
           "new_session", "parse_sse", "hash_password"]
