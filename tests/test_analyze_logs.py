"""日志画像汇总：解析容错、停止原因归类、成本估算、过滤（离线，用临时日志）。"""
import json
from pathlib import Path

from scripts.analyze_logs import (classify_stop, load_events, reflect_calls,
                                  render, summarize)


def _write(tmp_path: Path, events: list[dict], extra_lines=()) -> Path:
    path = tmp_path / "qa.log"
    lines = [f"2026-09-13 10:00:00,000 INFO {json.dumps(e, ensure_ascii=False)}" for e in events]
    lines.extend(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _event(**kw) -> dict:
    base = {"time": "2026-09-13T10:00:00", "user_id": "u1", "query": "q", "answer": "a",
            "knowledge_blocks": []}
    base.update(kw)
    return base


def test_load_events_skips_bad_lines(tmp_path: Path):
    """坏行/非 JSON 行/缺 query 的行都要跳过，不能因此报错。"""
    path = _write(tmp_path, [_event()],
                  extra_lines=["这不是 JSON", "{坏掉的 json", json.dumps({"no_query": 1})])
    events = load_events(path)
    assert len(events) == 1 and events[0]["query"] == "q"


def test_load_events_filters(tmp_path: Path):
    path = _write(tmp_path, [
        _event(user_id="alice"), _event(user_id="bob"),
        _event(user_id="alice", time="2026-09-14T10:00:00"),
    ])
    assert len(load_events(path)) == 3
    assert len(load_events(path, user="alice")) == 2
    assert len(load_events(path, exclude_user="alice")) == 1
    assert len(load_events(path, since="2026-09-14")) == 1
    assert len(load_events(path, tail=1)) == 1


def test_classify_stop_buckets():
    assert classify_stop(_event()) == "no_retrieval"                      # 未检索（兜底路径）
    assert classify_stop(_event(knowledge_blocks=[{"id": "KP_1"}])) == "no_trace"
    assert classify_stop(_event(reflect={"rounds": 0, "reasons": ["无可跟进的引用关系，停止"]})) \
        == "no_candidates"
    # LLM 参与的判定按 "[第N轮] " 前缀识别，措辞怎么变都不影响归类
    assert classify_stop(_event(reflect={"rounds": 1, "reasons": ["[第1轮] 已充分，无需补充"]})) \
        == "llm_stop"
    assert classify_stop(_event(reflect={"rounds": 0,
                                        "reasons": ["[第1轮] 已收集的法条已明确回答该问题。"]})) \
        == "llm_stop"
    assert classify_stop(_event(reflect={"rounds": 0, "reasons": ["反思调用异常，停止继续检索：超时"]})) \
        == "reflect_error"
    assert classify_stop(_event(reflect={"rounds": 3, "reasons": ["[第3轮] 继续"]})) == "max_rounds"


def test_classify_stop_unknown_is_other_not_llm_stop():
    """既非写死文案、也不带轮次前缀的原因归 other —— 曾默认塞进 llm_stop，
    把测试噪声误读成"LLM 判定退出"（占 73.5% 的假象就是这么来的）。"""
    assert classify_stop(_event(reflect={"rounds": 0, "reasons": ["测试轨迹"]})) == "other"
    assert classify_stop(_event(reflect={"rounds": 0, "reasons": ["某个没见过的原因"]})) == "other"


def test_reflect_calls_estimate():
    no_ref = _event(reflect={"rounds": 0, "reasons": ["无可跟进的引用关系，停止"]})
    assert reflect_calls(no_ref) == 0                                     # 无候选不调 LLM
    assert reflect_calls(_event(knowledge_blocks=[{}])) == 0              # 无轨迹
    one = _event(reflect={"rounds": 0, "reasons": ["[第1轮] 已充分"]})
    assert reflect_calls(one) == 1                                        # 只调 1 次判定
    capped = _event(reflect={"rounds": 3, "reasons": ["[第3轮] 继续"]})
    assert reflect_calls(capped) == 3                                     # 达上限，无最后判定


def test_summarize_tolerates_missing_fields(tmp_path: Path):
    """历史日志混有多个代码版本的字段：缺字段也要能统计，不能报错。"""
    events = [_event(), _event(intent="answerable", reflect={"rounds": 0, "reasons": ["已充分"]})]
    summary = summarize(load_events(_write(tmp_path, events)))
    assert summary["events"] == 2
    assert summary["intent"]["unknown"] == 1
    assert summary["reflect_fetched"]["events"] == 0 or summary["reflect_fetched"]["events_with_fetch"] == 0
    assert summary["llm_calls"]["total_per_qa"]["n"] == 2


def test_summarize_counts_indirectly(tmp_path: Path):
    """核心指标：意图分布、三类来源规模、反思补取率、成本。"""
    events = [
        _event(intent="answerable", mode="reflect", seeds=[{"id": "KP_1"}] * 10,
               graph_expanded=[{"id": "KP_2"}] * 3, reflect_fetched=[],
               reflect={"rounds": 0, "reasons": ["无可跟进的引用关系，停止"]},
               generation_ms=100, memory_recalled={"count": 2, "kinds": {"profile": 2}}),
        _event(intent="answerable", mode="reflect", seeds=[{"id": "KP_1"}] * 10,
               graph_expanded=[], reflect_fetched=[{"id": "KP_9", "round": 1}],
               reflect={"rounds": 1, "reasons": ["[第1轮] 需要补充被引条文"]},
               generation_ms=200, memory_recalled={"count": 0, "kinds": {}}),
        _event(intent="off_topic"),
    ]
    s = summarize(load_events(_write(tmp_path, events)))
    assert s["intent"] == {"answerable": 2, "off_topic": 1}
    assert s["seeds"]["avg"] == 10.0 and s["seeds"]["n"] == 2
    # 平均值按"带该字段的事件"算，含那些扩展为 0 条的（(3+0)/2）——空扩展也是有效信息
    assert s["graph_expanded"]["avg"] == 1.5 and s["graph_expanded"]["n"] == 2
    assert s["reflect_fetched"]["events_with_fetch"] == 1
    assert s["reflect_fetched"]["fetch_rate"] == 0.5       # 2 条有该字段，1 条补到
    # 成本按**全部问答事件**平均：兜底路径没有反思调用，记 0 才是"每次问答的真实成本"
    assert s["llm_calls"]["reflect"]["avg"] == 0.67       # (0 + 2 + 0) / 3
    assert s["llm_calls"]["total_per_qa"]["avg"] == 2.33  # (2 + 4 + 1) / 3
    assert s["memory_recalled"]["avg"] == 1.0


def test_render_has_sections_and_other_samples(tmp_path: Path):
    events = [_event(reflect={"rounds": 0, "reasons": ["没见过的原因"]})]
    out = render(summarize(load_events(_write(tmp_path, events))))
    for section in ("【意图分流】", "【检索链路】", "【上下文构成】", "【反思", "【LLM 调用成本"):
        assert section in out
    assert "没见过的原因" in out          # other 样本要露出来，便于诊断


def test_render_empty_log(tmp_path: Path):
    assert "日志为空" in render(summarize([]))
