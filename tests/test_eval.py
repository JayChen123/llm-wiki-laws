"""评估指标与评估流程组装测试。"""
from eval.evaluate import build_rows, load_cases
from eval.metrics import recall_at_k, recall_report


def test_recall_at_k_basic():
    assert recall_at_k(["KP_001", "KP_002", "KP_003"], ["KP_002"], 3) == 1.0
    assert recall_at_k(["KP_001", "KP_002"], ["KP_002"], 1) == 0.0
    assert recall_at_k(["KP_001", "KP_002", "KP_003"], ["KP_003", "KP_004"], 5) == 0.5
    assert recall_at_k([], ["KP_001"], 3) == 0.0


def test_recall_at_k_empty_expected():
    assert recall_at_k(["KP_001"], [], 3) is None


def test_recall_report_keys():
    rep = recall_report(["KP_001", "KP_002"], ["KP_001"], ks=(1, 3, 5))
    assert rep == {"recall@1": 1.0, "recall@3": 1.0, "recall@5": 1.0}


class FakeKB:
    def search(self, query, top_k):
        return [
            {"id": f"KP_{i:03d}", "title": f"标题{i}", "content": f"正文{i}"}
            for i in range(top_k)
        ]


class FakeGen:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def generate(self, query, results, history=None):
        if self.fail:
            raise RuntimeError("LLM 调用失败")
        return "系统生成的参考答案"


def test_case_file_carries_source_law():
    """用例要能标明来源法律，便于按法律分组看检索质量。"""
    cases = load_cases()
    assert all(c.get("source") for c in cases), "有用例缺 source 字段"
    laws = {law for c in cases for law in c["source"]}
    assert {"民法典", "劳动法", "劳动合同法"} <= laws
    assert sum(1 for c in cases if "民法典" in c["source"]) >= 20


def test_recall_detail_keeps_source():
    """recall 明细要带上 source —— 否则评估结果无法按来源法律分组。"""
    cases = load_cases()
    _, recalls = build_rows(cases[:3], FakeKB(), FakeGen(), top_k=5)
    assert all("source" in r and r["source"] for r in recalls)
    assert recalls[0]["source"] == cases[0]["source"]


def test_civil_code_crossref_cases_include_referenced_articles():
    """引用关系型用例必须同时期望"引用方"与"被引方"，否则无法验证引用链是否被带进上下文。"""
    import json
    import re
    from pathlib import Path

    graph = json.loads(Path("knowledge_graph.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in graph["nodes"]}
    cases = load_cases()
    multi = [c for c in cases if "民法典" in c["source"] and len(c["expected_knowledge"]) > 1]
    assert len(multi) >= 10, f"民法典多跳用例偏少：{len(multi)}"

    def article(kp):
        return re.search(r"第[一二三四五六七八九十百千零]+条$", nodes[kp]["clause"]).group(0)

    verified = 0
    for case in multi:
        for src in case["expected_knowledge"]:
            if not re.search(r"第[一二三四五六七八九十百千零]+条$", nodes[src]["clause"]):
                continue
            for tgt in case["expected_knowledge"]:
                if src == tgt:
                    continue
                if article(tgt) in nodes[src]["content"]:
                    verified += 1
    assert verified >= 12, f"引用关系覆盖过少：{verified}"


def test_build_rows_with_gen():
    cases = load_cases()
    rows, recalls = build_rows(cases[:3], FakeKB(), FakeGen(), top_k=5)
    assert len(rows) == 3
    assert rows[0]["user_input"] == cases[0]["question"]
    assert rows[0]["response"] == "系统生成的参考答案"
    assert rows[0]["gen_ok"] is True
    assert len(rows[0]["retrieved_contexts"]) == 5
    assert rows[0]["reference"] == cases[0]["answer"]
    # FakeKB 返回 KP_000，期望 KP_015，未命中
    assert recalls[0]["recall@1"] == 0.0


def test_build_rows_gen_failure(tmp_path):
    cases = load_cases()
    rows, _ = build_rows(cases[:2], FakeKB(), FakeGen(fail=True), top_k=3)
    assert rows[0]["gen_ok"] is False
    assert rows[0]["response"] == ""


def test_build_rows_without_gen():
    cases = load_cases()
    rows, _ = build_rows(cases[:2], FakeKB(), gen=None, top_k=3)
    assert all(r["gen_ok"] is False for r in rows)
