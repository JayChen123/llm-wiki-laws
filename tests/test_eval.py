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
