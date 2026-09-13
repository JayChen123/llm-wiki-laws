"""意图识别：三分类、容错解析、故障保守放行、兜底话术（mock LLM，离线）。"""
from types import SimpleNamespace

import pytest

from app.generation.intent import (
    COVERED_LAWS,
    Intent,
    IntentClassifier,
    fallback_answer,
    parse_intent,
)


def _resp(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class FakeCompletions:
    """模拟 OpenAI 的 chat.completions.create。"""

    def __init__(self, content=None, exc=None):
        self.content = content
        self.exc = exc
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self.exc is not None:
            raise self.exc
        return _resp(self.content)


def _classifier(content=None, exc=None):
    c = IntentClassifier(api_key="fake-key")
    c._client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions(content, exc))
    )
    return c


def test_classify_three_intents():
    assert _classifier('{"intent": "answerable", "reason": "劳动合同纠纷"}') \
        .classify("试用期最长多久").intent is Intent.ANSWERABLE
    assert _classifier('{"intent": "legal_uncovered", "reason": "属刑法"}') \
        .classify("盗窃罪量刑标准").intent is Intent.LEGAL_UNCOVERED
    assert _classifier('{"intent": "off_topic", "reason": "天气"}') \
        .classify("今天天气怎么样").intent is Intent.OFF_TOPIC


def test_classify_reason_is_carried():
    r = _classifier('{"intent": "off_topic", "reason": "与法律无关"}').classify("今天天气")
    assert r.reason == "与法律无关"


def test_classify_uses_low_temperature_and_configured_model():
    c = _classifier('{"intent": "answerable"}')
    c.classify("试用期")
    kwargs = c._client.chat.completions.last_kwargs
    assert kwargs["temperature"] == 0          # 分类要稳定，不要发散
    assert kwargs["model"] == c.model


def test_parse_tolerates_markdown_fence_and_noise():
    noisy = '```json\n{"intent": "off_topic", "reason": "闲聊"}\n```\n以上是分类结果。'
    assert parse_intent(noisy).intent is Intent.OFF_TOPIC
    assert parse_intent('结果：{"intent":"legal_uncovered","reason":"行政法"}').intent \
        is Intent.LEGAL_UNCOVERED


def test_parse_falls_back_on_bare_enum_text():
    assert parse_intent("legal_uncovered").intent is Intent.LEGAL_UNCOVERED


def test_parse_falls_back_when_unparsable():
    r = parse_intent("我不确定这是什么")
    assert r.intent is Intent.ANSWERABLE and "无法解析" in r.reason


def test_parse_falls_back_on_empty_output():
    r = parse_intent("")
    assert r.intent is Intent.ANSWERABLE


@pytest.mark.parametrize("exc", [TimeoutError("超时"), ConnectionError("网络不可达"),
                                 ValueError("返回格式异常")])
def test_classify_falls_back_on_llm_failure(exc):
    """分类器故障必须放行：宁可多答一次，也不要把正常法律问题挡掉。"""
    r = _classifier(exc=exc).classify("试用期最长多久")
    assert r.intent is Intent.ANSWERABLE
    assert "按可答放行" in r.reason


def test_classify_falls_back_without_api_key():
    c = IntentClassifier()
    c.api_key = ""              # 模拟未配置 API_KEY
    c._client = None
    r = c.classify("试用期最长多久")
    assert r.intent is Intent.ANSWERABLE and "分类器不可用" in r.reason


def test_prompt_states_covered_laws():
    """提示词必须写明覆盖范围，否则模型无从判断"未覆盖"。"""
    from app.generation.intent import _SYSTEM_PROMPT

    for law in ("民法典", "劳动法", "劳动合同法"):
        assert law in _SYSTEM_PROMPT
    for field in ("刑法", "行政法", "off_topic"):
        assert field in _SYSTEM_PROMPT


def test_fallback_answer_for_uncovered_mentions_scope():
    text = fallback_answer(Intent.LEGAL_UNCOVERED)
    assert "其他法律领域" in text
    assert COVERED_LAWS.split("》")[0].lstrip("《") in text   # 说明本库覆盖哪些法律


def test_fallback_answer_for_off_topic_gives_examples():
    text = fallback_answer(Intent.OFF_TOPIC)
    assert "只能回答法律相关" in text
    assert "试用期" in text and "加班费" in text               # 给出可问范围示例
