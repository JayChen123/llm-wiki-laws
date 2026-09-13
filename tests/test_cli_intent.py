"""CLI 意图路由：非"可答"路径必须跳过检索与生成，但仍写记忆与日志（离线）。"""
import pytest

from app.generation.intent import Intent, IntentResult


class FakeKB:
    """假知识库：实现 KnowledgeBase 的检索接口（含 last_trace，日志会读取）。"""

    def __init__(self):
        self.queries = []
        self.last_trace = {"rounds": 0, "reasons": ["测试用轨迹"]}

    def search(self, query, top_k=5):
        self.queries.append(query)
        return [{"id": "KP_019", "title": "第二章·第十九条", "score": 0.9,
                 "content": "试用期不得超过六个月。", "source": "劳动法.md L1"}]


class FakeGen:
    """假生成器：记录收到的长期记忆，便于断言注入行为。"""

    last_memory = None

    def __init__(self, *a, **k):
        pass

    def generate_stream(self, query, contexts, history=None, memory=None):
        FakeGen.last_memory = memory
        yield "试用期最长六个月。"


class FakeStore:
    """假记忆存储：实现 CLI 用到的全部接口（含 P3 新增的用户/轮数方法）。"""

    def __init__(self, *a, **k):
        self.messages = []

    def ensure_user(self, user_id, display_name=None):
        pass

    def ensure_session(self, session, user_id):
        pass

    def add(self, session, role, content, user_id=""):
        self.messages.append((session, role, content, user_id))

    def recent(self, session, limit=None):
        return []

    def recent_user_queries(self, session, n=2):
        return []

    def turn_count(self, session):
        return sum(1 for m in self.messages if m[1] == "user")

    def clear(self, session):
        pass

    def close(self):
        pass


class FakeLongTerm:
    """假长期记忆：不连 Milvus/MySQL。"""

    def __init__(self, *a, **k):
        self.turns = []

    def recall(self, user_id, query, top_k=3):
        return []

    def remember_turn(self, user_id, question, answer):
        self.turns.append((user_id, question))
        return True

    def remember_facts(self, user_id, facts):
        return 0


class FakeExtractor:
    def __init__(self, *a, **k):
        pass

    def extract(self, history):
        return []


class FakeLogger:
    def __init__(self):
        self.events = []

    def info(self, line):
        self.events.append(line)


class FakeClassifier:
    """按问题文本返回预设意图。"""

    def __init__(self, *a, **k):
        pass

    def classify(self, query):
        if "天气" in query:
            return IntentResult(Intent.OFF_TOPIC, "闲聊")
        if "盗窃罪" in query:
            return IntentResult(Intent.LEGAL_UNCOVERED, "刑法")
        return IntentResult(Intent.ANSWERABLE, "")


@pytest.fixture
def cli_env(monkeypatch):
    """把 cli 的外部依赖全部替换为假实现。"""
    import app.cli as cli

    kb, store, logger = FakeKB(), FakeStore(), FakeLogger()
    monkeypatch.setattr(cli, "KnowledgeBase", type("KB", (), {"load": staticmethod(lambda: kb)}))
    monkeypatch.setattr(cli, "AnswerGenerator", FakeGen)
    monkeypatch.setattr(cli, "create_memory_store", lambda *a, **k: store)
    monkeypatch.setattr(cli, "_build_long_term_memory", lambda *a, **k: FakeLongTerm())
    monkeypatch.setattr(cli, "ProfileExtractor", FakeExtractor)
    monkeypatch.setattr(cli, "get_logger", lambda *a, **k: logger)
    monkeypatch.setattr(cli, "IntentClassifier", FakeClassifier)
    monkeypatch.setattr(cli, "INTENT_ENABLED", True)
    return cli, kb, logger, store


def _run(monkeypatch, questions):
    """按脚本喂入会话ID + 问题 + /exit。"""
    inputs = iter(["s1", *questions, "/exit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    import app.cli as cli

    cli.main()


def test_off_topic_skips_retrieval_and_generation(monkeypatch, cli_env, capsys):
    cli, kb, logger, store = cli_env
    _run(monkeypatch, ["今天天气怎么样"])

    assert kb.queries == []                      # 未检索
    out = capsys.readouterr().out
    assert "只能回答法律相关" in out              # 走兜底话术
    assert "试用期最长六个月" not in out           # 未生成


def test_uncovered_law_skips_retrieval(monkeypatch, cli_env, capsys):
    cli, kb, logger, store = cli_env
    _run(monkeypatch, ["盗窃罪量刑标准"])

    assert kb.queries == []
    assert "其他法律领域" in capsys.readouterr().out


def test_answerable_runs_full_pipeline(monkeypatch, cli_env, capsys):
    cli, kb, logger, store = cli_env
    _run(monkeypatch, ["试用期最长多久"])

    assert kb.queries == ["试用期最长多久"]        # 正常检索
    assert "试用期最长六个月" in capsys.readouterr().out


def test_fallback_path_still_logs_and_remembers(monkeypatch, cli_env):
    """兜底路径也要留痕：日志带 intent 且知识块为空，记忆照写（保证多轮上下文连续）。"""
    import json

    cli, kb, logger, store = cli_env
    _run(monkeypatch, ["今天天气怎么样"])

    assert len(logger.events) == 1
    event = json.loads(logger.events[0])
    assert event["intent"] == "off_topic"
    assert event["knowledge_blocks"] == []
    assert event["retrieval_ms"] is None and event["generation_ms"] is None
    assert "reflect" not in event                  # 未检索，自然没有反思轨迹


def test_answerable_log_carries_reflect_trace(monkeypatch, cli_env):
    """可答路径要把反思轨迹写进日志，便于事后审计"为什么停在第几轮"。"""
    import json

    cli, kb, logger, store = cli_env
    _run(monkeypatch, ["试用期最长多久"])

    event = json.loads(logger.events[0])
    assert event["reflect"] == {"rounds": 0, "reasons": ["测试用轨迹"]}


def test_mixed_sequence_only_legal_query_retrieves(monkeypatch, cli_env):
    cli, kb, logger, store = cli_env
    _run(monkeypatch, ["今天天气怎么样", "试用期最长多久", "盗窃罪量刑标准"])

    assert kb.queries == ["试用期最长多久"]        # 只有可答的那条进入检索
    assert len(logger.events) == 3               # 三条都记了日志
