"""回答生成：流式输出与 prompt 组装测试（mock OpenAI 客户端）。"""
from types import SimpleNamespace

import pytest

from app.generation.answer import AnswerGenerator


def _chunk(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class FakeCompletions:
    """模拟 OpenAI 流式接口。"""

    def __init__(self, chunks: list):
        self.chunks = chunks
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        assert kwargs["stream"] is True
        return iter(self.chunks)


@pytest.fixture
def gen():
    g = AnswerGenerator(api_key="fake-key")
    g.client.chat.completions = FakeCompletions(
        [_chunk("加班费按"), _chunk("岗位工资"), _chunk("计算。")]
    )
    return g


def test_generate_stream_yields_chunks(gen):
    ctx = [{"title": "第五章·二、加班及加班费", "content": "加班费计算基数。"}]
    pieces = list(gen.generate_stream("加班费怎么算", ctx, history=[]))
    assert pieces == ["加班费按", "岗位工资", "计算。"]


def test_generate_aggregates_stream(gen):
    ctx = [{"title": "第五章·二、加班及加班费", "content": "加班费计算基数。"}]
    text = gen.generate("加班费怎么算", ctx, history=[])
    assert text == "加班费按岗位工资计算。"


def test_prompt_contains_reference_and_query(gen):
    ctx = [{"title": "T1", "content": "正文内容"}]
    list(gen.generate_stream("问题？", ctx, history=[{"role": "user", "content": "你好"}]))
    messages = gen.client.chat.completions.last_kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "你好"}
    user_prompt = messages[-1]["content"]
    assert "T1" in user_prompt and "正文内容" in user_prompt and "问题？" in user_prompt


def test_context_title_carries_law_name():
    # 两部法律同章同条（如"第九十八条"），上下文标题须带法律名以便生成器正确归属
    ctx = [
        {"title": "第十二章·第九十八条", "content": "责令改正",
         "source": "中华人民共和国劳动法.md L588-592"},
        {"title": "第八章·第九十八条", "content": "施行日期",
         "source": "中华人民共和国劳动合同法.md L593-596"},
    ]
    out = AnswerGenerator._format_contexts(ctx)
    assert "中华人民共和国劳动法·第十二章·第九十八条" in out
    assert "中华人民共和国劳动合同法·第八章·第九十八条" in out
    assert "[第十二章·第九十八条]" not in out  # 不再出现无法律名的裸标题


def test_system_prompt_guides_multi_hop(gen):
    from app.generation.answer import SYSTEM_PROMPT

    assert "多跳" in SYSTEM_PROMPT or "跨知识点" in SYSTEM_PROMPT
    assert "引用" in SYSTEM_PROMPT
    # 实际请求的 system 消息即使用该 prompt
    list(gen.generate_stream("问题？", [{"title": "T", "content": "C"}]))
    messages = gen.client.chat.completions.last_kwargs["messages"]
    assert messages[0]["content"] == SYSTEM_PROMPT
