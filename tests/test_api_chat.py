"""问答接口：SSE 事件流、意图兜底、反思轨迹与记忆注入、失败降级、用户隔离（离线）。"""
from tests.conftest import FakeClassifier, FakeGen, new_session, parse_sse

from app.api import deps
from app.generation.intent import Intent


def chat(client, session_id, question):
    resp = client.post("/api/chat", json={"session_id": session_id, "question": question})
    assert resp.status_code == 200
    return resp.text


def test_chat_streams_meta_deltas_citations_done(auth_client):
    sid = new_session(auth_client)
    events = parse_sse(chat(auth_client, sid, "试用期最长多久"))
    kinds = [e for e, _ in events]

    assert kinds[0] == "meta"
    assert kinds.count("delta") == 3                       # 逐块产出
    assert kinds[-2:] == ["citations", "done"]

    assert "".join(d["text"] for e, d in events if e == "delta") == "试用期最长六个月。"
    citations = dict(events)["citations"]["citations"]
    assert [c["id"] for c in citations] == ["KP_019", "KP_042"]
    assert citations[0]["source"].startswith("中华人民共和国劳动合同法")   # 带法律名便于溯源


def test_chat_writes_short_and_long_term_memory(auth_client, fake_store, fake_long_term):
    sid = new_session(auth_client)
    chat(auth_client, sid, "试用期最长多久")

    assert [m[2] for m in fake_store.messages] == ["user", "assistant"]
    assert fake_store.messages[0][3] == "试用期最长多久"
    assert fake_long_term.turns == [("alice", "试用期最长多久")]


def test_chat_injects_long_term_memory_into_generation(auth_client, fake_gen):
    """长期记忆要送到生成器（作为"用户背景"），但不是法律依据。"""
    sid = new_session(auth_client)
    chat(auth_client, sid, "试用期最长多久")
    assert fake_gen.last_memory == [{"kind": "profile", "text": "在深圳工作", "score": 1.0}]


def test_off_topic_uses_fallback_and_skips_retrieval(auth_client, fake_kb, fake_store):
    sid = new_session(auth_client)
    events = parse_sse(chat(auth_client, sid, "今天天气怎么样"))
    kinds = [e for e, _ in events]

    assert kinds[0] == "fallback"
    assert fake_kb.queries == []                            # 未检索
    answer = dict(events)["fallback"]["answer"]
    assert "只能回答法律相关" in answer
    assert "仅能依据【参考资料】" not in answer               # 没走生成
    # 兜底也要留痕：写记忆、写日志（否则多轮上下文断裂）
    assert [m[2] for m in fake_store.messages] == ["user", "assistant"]


def test_uncovered_law_fallback(auth_client, fake_kb):
    sid = new_session(auth_client)
    app = auth_client.app
    app.dependency_overrides[deps.get_classifier] = lambda: FakeClassifier(Intent.LEGAL_UNCOVERED)

    events = parse_sse(chat(auth_client, sid, "盗窃罪量刑标准"))
    assert dict(events)["fallback"]["intent"] == "legal_uncovered"
    assert fake_kb.queries == []


def test_generation_failure_still_returns_citations(auth_client, fake_store):
    """生成失败不能让整轮问答白跑：仍要把检索结果给前端，并写入记忆。"""
    sid = new_session(auth_client)
    auth_client.app.dependency_overrides[deps.get_generator] = lambda: FakeGen(fail=True)

    events = parse_sse(chat(auth_client, sid, "试用期最长多久"))
    kinds = [e for e, _ in events]
    assert "error" in kinds and "citations" in kinds
    assert dict(events)["citations"]["citations"]
    assert fake_store.messages[1][2] == "assistant"          # 兜底回答也入库


def test_citations_persist_for_history_review(auth_client, fake_store):
    """引用要能随历史消息取回：否则刷新页面后"溯源"就失效了。"""
    sid = new_session(auth_client)
    chat(auth_client, sid, "试用期最长多久")

    messages = auth_client.get(f"/api/sessions/{sid}/messages").json()
    assistant = messages[-1]
    assert assistant["role"] == "assistant"
    assert [c["id"] for c in assistant["citations"]] == ["KP_019", "KP_042"]
    assert assistant["citations"][0]["source"].startswith("中华人民共和国劳动合同法")
    assert messages[0]["citations"] == []          # 用户提问没有引用


def test_api_tests_do_not_write_production_log(auth_client):
    """回归：API 测试曾把假事件写进生产日志 logs/qa.log（297 条、占当时 69%），
    污染真实审计与日志分析。夹具替换了 API 层 logger 后，增量必须为 0。
    """
    from app.logger import LOGS_DIR

    log = LOGS_DIR / "qa.log"
    before = log.read_text(encoding="utf-8").count("\n") if log.exists() else 0
    sid = new_session(auth_client)
    chat(auth_client, sid, "试用期最长多久")
    after = log.read_text(encoding="utf-8").count("\n") if log.exists() else 0
    assert after == before, "测试往生产日志写了事件"


def test_chat_requires_login(client):
    assert client.post("/api/chat", json={"session_id": "s", "question": "q"}).status_code == 401


def test_chat_rejects_empty_question(auth_client):
    sid = new_session(auth_client)
    resp = auth_client.post("/api/chat", json={"session_id": sid, "question": ""})
    assert resp.status_code == 422                          # pydantic 校验拦截


def test_reflect_trace_is_logged(auth_client, fake_kb):
    """反思轨迹要进日志（审计"为什么停在第几轮"）。"""
    import json

    from app.api import routes_chat

    sid = new_session(auth_client)
    captured: list[dict] = []

    class CapturingLogger:
        def info(self, line):
            captured.append(json.loads(line))

    original = routes_chat._logger
    routes_chat._logger = CapturingLogger()
    try:
        chat(auth_client, sid, "试用期最长多久")
    finally:
        routes_chat._logger = original

    event = captured[-1]
    assert event["user_id"] == "alice"
    assert event["reflect"] == fake_kb.last_trace
    assert event["knowledge_blocks"][0]["id"] == "KP_019"


def test_followup_merges_previous_question_but_topic_switch_does_not(auth_client, fake_kb):
    """短期记忆注入：追问才并入上一问；换话题不并入（修串题污染）。"""
    sid = new_session(auth_client)
    chat(auth_client, sid, "劳务派遣被退回能解除吗")
    fake_kb.queries.clear()

    chat(auth_client, sid, "试用期最长多久")
    assert fake_kb.queries == ["试用期最长多久"]              # 换话题：不并入

    chat(auth_client, sid, "那经济补偿呢")
    assert "试用期最长多久" in fake_kb.queries[-1]            # 追问：并入上一问


def test_health_endpoint(client):
    assert client.get("/api/health").json() == {"ok": True}
