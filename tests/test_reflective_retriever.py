"""反思式多跳检索：停止条件、迭代取回、防死循环（假混合检索 + 假反思器，离线）。"""
import pytest

from app.retrieval.reflective_retriever import ReflectiveRetriever
from app.retrieval.reflector import ReflectionResult


def _node(kp_id, title, content=""):
    return {"id": kp_id, "title": title, "chapter": "第四章", "clause": title,
            "content": content or f"{title} 正文", "keywords": [], "source": "test.md L1"}


class FakeHybrid:
    """假混合检索：返回预设种子，并记录 expand 取值。

    同时**按真实实现的样子维护 `last_trace`**（seeds/expanded/mode）——
    反思链路会读它来拼自己的轨迹；假替身缺这个字段会让轨迹断言失真。
    """

    def __init__(self, seeds):
        self.seeds = seeds
        self.expand_args = []
        self.queries = []
        self.last_trace = {"mode": "bfs", "seeds": [], "expanded": []}

    def search(self, query, top_k=5, expand=True):
        self.queries.append(query)
        self.expand_args.append(expand)
        self.last_trace = {
            "mode": "bfs" if expand else "seeds_only",
            "seeds": [{"id": s["id"], "title": s["title"], "score": s.get("score", 0.0)}
                      for s in self.seeds],
            "expanded": [],
        }
        return [dict(s) for s in self.seeds]


class ScriptedReflector:
    """按脚本依次返回反思结论，并记录每轮看到的候选。"""

    def __init__(self, results):
        self.results = list(results)
        self.seen_candidates = []
        self.calls = 0

    def reflect(self, query, collected, candidates):
        self.calls += 1
        self.seen_candidates.append([c["id"] for c in candidates])
        if self.results:
            return self.results.pop(0)
        return ReflectionResult(True, [], "脚本用尽")


def _seed(kp_id, title, score=0.03):
    return {"id": kp_id, "title": title, "chapter": "第四章", "content": f"{title} 正文",
            "keywords": [], "source": "test.md L1", "score": score}


# 引用关系：单链 A→B，以及链条 A→B→C→D（用于测轮数上限）、分叉 A→B/C
SINGLE = {"KP_001": ["KP_002"]}
CHAIN = {"KP_001": ["KP_002"], "KP_002": ["KP_003"], "KP_003": ["KP_004"]}
BRANCH = {"KP_001": ["KP_002", "KP_003"]}
NODES = [_node("KP_001", "第五章·第六十五条"), _node("KP_002", "第四章·第三十九条"),
         _node("KP_003", "第四章·第四十六条"), _node("KP_004", "第一章·第一条")]


def _retriever(seeds, results, max_rounds=3, crossrefs=SINGLE):
    hybrid = FakeHybrid(seeds)
    reflector = ScriptedReflector(results)
    r = ReflectiveRetriever(NODES, {"edges": []}, hybrid, reflector,
                            max_rounds=max_rounds, crossrefs=crossrefs)
    return r, hybrid, reflector


def test_trace_records_seeds_expanded_and_fetched():
    """反思链路轨迹要能区分三类来源：种子 / 图谱扩展 / 反思补取（各带来源信息）。"""
    r, _, _ = _retriever(
        [_seed("KP_001", "第五章·第六十五条")],
        [ReflectionResult(False, ["KP_002"], "需补被引条文")],
    )
    r.search("派遣工被退回", top_k=5)
    trace = r.last_trace

    assert trace["mode"] == "reflect"
    assert [n["id"] for n in trace["seeds"]] == ["KP_001"]
    assert [n["id"] for n in trace["fetched"]] == ["KP_002"]
    assert trace["fetched"][0]["round"] == 1          # 第几轮补取的
    assert trace["rounds"] == 1
    assert trace["reasons"]


def test_no_reflection_when_no_crossrefs():
    """种子无引用关系 → 停止反思补取，且**不调用反思器**（基础扩展仍在）。"""
    r, hybrid, reflector = _retriever([_seed("KP_004", "第一章·第一条")], [])
    results = r.search("试用期", top_k=5)

    assert [x["id"] for x in results] == ["KP_004"]
    assert reflector.calls == 0                       # 未触发反思
    assert hybrid.expand_args == [True]               # 基础扩展保留（与 bfs 一致）
    assert "无可跟进的引用关系" in r.last_trace["reasons"][0]
    assert r.last_trace["rounds"] == 0


def test_fetches_referenced_article_when_not_sufficient():
    """有引用关系且反思判"不充分" → 取回被引条文。"""
    r, _, reflector = _retriever(
        [_seed("KP_001", "第五章·第六十五条")],
        [ReflectionResult(False, ["KP_002"], "需补被引条文")],
    )
    results = r.search("派遣工被退回", top_k=5)

    assert [x["id"] for x in results] == ["KP_001", "KP_002"]   # 种子在前，取回的在后
    assert reflector.seen_candidates[0] == ["KP_002"]           # 候选是被引条文
    assert r.last_trace["rounds"] == 1


def test_reflection_not_called_again_when_no_further_candidates():
    """取回条文后若再无引用可跟，则**不再调用反思器**，直接停止（省一次 LLM 调用）。"""
    r, _, reflector = _retriever(
        [_seed("KP_001", "第五章·第六十五条")],
        [ReflectionResult(False, ["KP_002"], "继续"),
         ReflectionResult(False, ["KP_003"], "本不该被调用")],
    )
    results = r.search("派遣工被退回", top_k=5)

    assert [x["id"] for x in results] == ["KP_001", "KP_002"]
    assert reflector.calls == 1                          # 第二轮没有候选，未调用
    assert any("无可跟进的引用关系" in x for x in r.last_trace["reasons"])


def test_stops_when_reflection_says_sufficient():
    r, _, reflector = _retriever(
        [_seed("KP_001", "第五章·第六十五条")],
        [ReflectionResult(True, [], "现有条文已足以回答")],
    )
    results = r.search("派遣工被退回", top_k=5)
    assert [x["id"] for x in results] == ["KP_001"]
    assert reflector.calls == 1 and r.last_trace["rounds"] == 0


def test_stops_when_fetch_empty():
    r, _, _ = _retriever(
        [_seed("KP_001", "第五章·第六十五条")],
        [ReflectionResult(False, [], "被引条文与本问题无关")],
    )
    results = r.search("派遣工被退回", top_k=5)
    assert [x["id"] for x in results] == ["KP_001"]
    assert r.last_trace["rounds"] == 0


def test_respects_max_rounds():
    """反思一直要求继续时，必须被轮数上限卡住（防失控）。"""
    r, _, reflector = _retriever(
        [_seed("KP_001", "第五章·第六十五条")],
        [ReflectionResult(False, ["KP_002"], "继续"),
         ReflectionResult(False, ["KP_003"], "继续"),
         ReflectionResult(False, ["KP_004"], "继续")],
        max_rounds=2, crossrefs=CHAIN,
    )
    results = r.search("派遣工被退回", top_k=5)

    assert [x["id"] for x in results] == ["KP_001", "KP_002", "KP_003"]
    assert "KP_004" not in [x["id"] for x in results]     # 上限外的条文没被取回
    assert r.last_trace["rounds"] == 2
    assert reflector.calls == 2


def test_guard_against_refetching_collected_article():
    """反思返回已在上下文中的条文 → 不重复添加并停下（防死循环）。"""
    r, _, _ = _retriever(
        [_seed("KP_001", "第五章·第六十五条"), _seed("KP_002", "第四章·第三十九条")],
        [ReflectionResult(False, ["KP_002"], "取回已收集的条文")],
        crossrefs=BRANCH,
    )
    results = r.search("派遣工被退回", top_k=5)

    assert [x["id"] for x in results] == ["KP_001", "KP_002"]   # 无重复
    assert r.last_trace["rounds"] == 0
    assert any("均已在上下文中" in x for x in r.last_trace["reasons"])


def test_fetched_article_gets_nominal_score_below_parent():
    r, _, _ = _retriever(
        [_seed("KP_001", "第五章·第六十五条", score=0.03)],
        [ReflectionResult(False, ["KP_002"], "继续")],
    )
    results = r.search("派遣工被退回", top_k=5)
    seed, fetched = results
    assert seed["score"] == 0.03
    assert 0 < fetched["score"] < seed["score"]        # 名义分数递减，便于展示排序
    assert fetched["title"] == "第四章·第三十九条"


def test_search_does_not_mutate_graph_nodes():
    """取回条文时构造新的结果字典，不污染图谱节点。"""
    before = [dict(n) for n in NODES]
    r, _, _ = _retriever(
        [_seed("KP_001", "第五章·第六十五条")],
        [ReflectionResult(False, ["KP_002"], "继续")],
    )
    r.search("派遣工被退回", top_k=5)
    assert NODES == before
