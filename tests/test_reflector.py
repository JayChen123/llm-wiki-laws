"""检索反思器：候选白名单、容错解析、fail-closed、无候选不调 LLM（mock LLM，离线）。"""
import json
from types import SimpleNamespace

import pytest

from app.retrieval.reflector import Reflector, parse_reflection


def _candidates(n=3):
    return [
        {"id": f"KP_{i:03d}", "title": f"第{i}条", "content": f"内容{i}"}
        for i in range(1, n + 1)
    ]


def _resp(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class FakeCompletions:
    def __init__(self, content=None, exc=None):
        self.content = content
        self.exc = exc
        self.calls = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.exc is not None:
            raise self.exc
        return _resp(self.content)


def _reflector(content=None, exc=None):
    r = Reflector(api_key="fake-key")
    r._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(content, exc)))
    return r


# ---------- 解析 ----------

def test_parse_maps_indices_to_candidate_ids():
    text = json.dumps({"sufficient": False, "fetch": [1, 3], "reason": "需补被引条文"})
    r = parse_reflection(text, _candidates())
    assert r.sufficient is False
    assert r.fetch_ids == ["KP_001", "KP_003"]
    assert r.reason == "需补被引条文"


def test_parse_tolerates_fence_noise_and_string_indices():
    text = '```json\n{"sufficient": false, "fetch": ["C1", " 2 "], "reason": "x"}\n```'
    r = parse_reflection(text, _candidates())
    assert r.fetch_ids == ["KP_001", "KP_002"]


def test_parse_drops_out_of_range_and_fabricated_entries():
    """白名单防护：越界编号、编造的条号一律丢弃，不得召回不存在的知识点。"""
    text = json.dumps({"sufficient": False,
                       "fetch": [9, "第八十八条", -1, 2, 0], "reason": "x"})
    r = parse_reflection(text, _candidates())
    assert r.fetch_ids == ["KP_002"]           # 只有合法编号 2 生效


def test_parse_dedupes_fetch():
    text = json.dumps({"sufficient": False, "fetch": [2, 2, "2"], "reason": "x"})
    assert parse_reflection(text, _candidates()).fetch_ids == ["KP_002"]


def test_parse_sufficient_true_with_fetch_is_still_sufficient():
    """sufficient 优先：判为已充分即停，即使同时列了编号。"""
    text = json.dumps({"sufficient": True, "fetch": [1], "reason": "已够"})
    r = parse_reflection(text, _candidates())
    assert r.sufficient is True


def test_parse_missing_sufficient_uses_fetch_as_signal():
    assert parse_reflection('{"fetch": [1]}', _candidates()).sufficient is False
    assert parse_reflection('{"fetch": []}', _candidates()).sufficient is True


def test_parse_unparsable_is_fail_closed():
    r = parse_reflection("我觉得还需要再找找", _candidates())
    assert r.sufficient is True and r.fetch_ids == []
    assert "无法解析" in r.reason


def test_parse_empty_fetch_means_stop():
    r = parse_reflection('{"sufficient": false, "fetch": [], "reason": "无关"}', _candidates())
    assert r.fetch_ids == []


# ---------- 调用 ----------

def test_no_candidates_skips_llm_call():
    """无引用关系时不调用 LLM —— 这正是"没有引用关系就直接停止"。"""
    r = _reflector('{"sufficient": false, "fetch": [1]}')
    result = r.reflect("问题", [{"id": "KP_009", "title": "t", "content": "c"}], [])
    assert result.sufficient is True and result.fetch_ids == []
    assert "无可跟进" in result.reason
    assert r._client.chat.completions.calls == 0


def test_reflect_uses_low_temperature_and_configured_model():
    r = _reflector('{"sufficient": true, "fetch": []}')
    r.reflect("问题", [{"id": "KP_009", "title": "t", "content": "c"}], _candidates())
    kwargs = r._client.chat.completions.last_kwargs
    assert kwargs["temperature"] == 0
    assert kwargs["model"] == r.model


def test_reflect_prompt_lists_candidates_with_indices():
    r = _reflector('{"sufficient": true, "fetch": []}')
    r.reflect("加班费怎么算", [{"id": "KP_009", "title": "第四章·第四十四条", "content": "工资报酬"}],
              _candidates(2))
    user_prompt = r._client.chat.completions.last_kwargs["messages"][-1]["content"]
    assert "加班费怎么算" in user_prompt
    assert "[1]" in user_prompt and "[2]" in user_prompt      # 候选带编号
    assert "第四章·第四十四条" in user_prompt                    # 已收集条文也在提示里


@pytest.mark.parametrize("exc", [TimeoutError("超时"), ConnectionError("网络不可达")])
def test_reflect_failure_is_fail_closed(exc):
    """反思失败必须停止继续检索（与意图识别的保守放行方向相反）。"""
    r = _reflector(exc=exc)
    result = r.reflect("问题", [{"id": "KP_009", "title": "t", "content": "c"}], _candidates())
    assert result.sufficient is True and result.fetch_ids == []
    assert "停止继续检索" in result.reason


def test_reflect_without_api_key_stops():
    r = Reflector()
    r.api_key = ""
    r._client = None
    result = r.reflect("问题", [{"id": "KP_009", "title": "t", "content": "c"}], _candidates())
    assert result.sufficient is True and "API_KEY" in result.reason
