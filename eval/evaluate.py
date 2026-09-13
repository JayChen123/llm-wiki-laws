"""评估主程序：遍历测试用例，运行检索+生成，计算 recall@k 与 ragas 指标。

用法：
    uv run python -m eval.evaluate                     # 全量评估（55 条：劳动法/劳动合同法 35 + 民法典 20）
    uv run python -m eval.evaluate --limit 3           # 快速试跑 3 条
    uv run python -m eval.evaluate --skip-ragas        # 仅计算 recall@k，不跑 ragas
    uv run python -m eval.evaluate --skip-ragas --source 民法典   # 只看某部法律的用例

指标映射（ragas 0.2.x）：
    recall@k            → 自定义，见 eval/metrics.py
    上下文相关性         → context_utilization
    上下文精确率         → context_precision
    上下文召回率         → context_recall
    答案相关性           → answer_relevancy
    忠实度               → faithfulness
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from app.config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, LLM_MODEL
from app.generation.answer import AnswerGenerator
from app.kb import KnowledgeBase
from eval.metrics import recall_report

CASES_PATH = Path(__file__).parent / "qa_test_cases.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_rows(
    cases: list[dict],
    kb: KnowledgeBase,
    gen: AnswerGenerator | None,
    top_k: int,
    intent_classifier=None,
) -> tuple[list[dict], list[dict]]:
    """对每条用例执行检索与生成，返回 (ragas 数据行, recall 明细)。

    `intent_classifier` 传入时额外记录每条用例的意图分类结果，用于统计
    **误判率**（用例全是法律题，被判为非 answerable 即为误判）。分类结果
    只记录、不改变检索流程，recall 口径与历史基线保持可比。
    """
    from tqdm import tqdm

    rows: list[dict] = []
    recalls: list[dict] = []
    pbar = tqdm(cases, desc="检索+生成", unit="条", ncols=100)
    for case in pbar:
        pbar.set_postfix_str(f"{case['id']} 检索…")
        intent = None
        if intent_classifier is not None:
            intent = intent_classifier.classify(case["question"]).intent.value
        results = kb.search(case["question"], top_k=top_k)
        pbar.set_postfix_str(f"{case['id']} 命中{len(results)}块 生成…")
        contexts = [
            f"{r.get('source', '').split('.md')[0].strip()}·{r['title']}\n{r['content']}"
            if ".md" in r.get("source", "")
            else f"{r['title']}\n{r['content']}"
            for r in results
        ]
        retrieved_ids = [r["id"] for r in results]
        # 检索返回 top_k 个种子 + 至多 EXPAND_K 个图谱扩展块，全部交给生成器；
        # recall 仍按 @1/3/5/8 汇报（@8 沿用历史基线口径，非生成器上下文上限）
        recall = recall_report(retrieved_ids, case["expected_knowledge"], ks=(1, 3, 5, 8))

        answer = ""
        gen_ok = False
        if gen is not None:
            try:
                answer = gen.generate(case["question"], results, history=[])
                gen_ok = True
            except Exception as e:
                pbar.write(f"  [!] {case['id']} 生成失败: {e}")
        pbar.set_postfix_str(f"{case['id']} recall@1={recall['recall@1']}")

        rows.append({
            "case_id": case["id"],
            "type": case["type"],
            "user_input": case["question"],
            "response": answer,
            "retrieved_contexts": contexts,
            "reference": case["answer"],
            "gen_ok": gen_ok,
        })
        detail = {"id": case["id"], "type": case["type"],
                  "source": case.get("source", []), **recall}
        if intent is not None:
            detail["intent"] = intent
        recalls.append(detail)
    return rows, recalls


def build_llm():
    from langchain_openai import ChatOpenAI
    from ragas.llms import LangchainLLMWrapper

    return LangchainLLMWrapper(
        ChatOpenAI(
            model=LLM_MODEL,
            base_url=DASHSCOPE_BASE_URL,
            api_key=DASHSCOPE_API_KEY,
            temperature=0,
        )
    )


def build_embeddings():
    """DashScope 原生 embedding（text-embedding-v3），经 langchain 包装适配 ragas。"""
    from langchain_community.embeddings import DashScopeEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper

    return LangchainEmbeddingsWrapper(
        DashScopeEmbeddings(
            model="text-embedding-v3",
            dashscope_api_key=DASHSCOPE_API_KEY,
        )
    )


def run_ragas(rows: list[dict], llm, embeddings) -> dict:
    """运行 5 项 ragas 指标，返回 {指标: {avg, min, max, by_type}}。"""
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import (
        ContextRelevance,
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    metrics = [
        context_precision,
        context_recall,
        ContextRelevance(llm=llm),
        answer_relevancy,
        faithfulness,
    ]
    valid = [r for r in rows if r["gen_ok"]]
    if not valid:
        return {"error": "无成功生成的样本，跳过 ragas 指标"}

    names = [m.name for m in metrics]
    print(f"  [ragas] 待计算指标（{len(names)} 项，样本 {len(valid)} 条）：{', '.join(names)}")
    dataset = Dataset.from_dict({
        "user_input": [r["user_input"] for r in valid],
        "response": [r["response"] for r in valid],
        "retrieved_contexts": [r["retrieved_contexts"] for r in valid],
        "reference": [r["reference"] for r in valid],
    })
    result = evaluate(dataset=dataset, metrics=metrics, llm=llm, embeddings=embeddings)
    df = result.to_pandas()

    import pandas as pd

    metric_names = [m.name for m in metrics]
    summary: dict = {}
    for name in metric_names:
        col = pd.to_numeric(df[name], errors="coerce")
        good = col.dropna()
        by_type: dict[str, float] = {}
        for idx, val in col.items():
            if pd.notna(val):
                t = valid[idx]["type"]
                by_type.setdefault(t, []).append(round(float(val), 4))
        summary[name] = {
            "avg": round(float(good.mean()), 4) if not good.empty else None,
            "min": round(float(good.min()), 4) if not good.empty else None,
            "max": round(float(good.max()), 4) if not good.empty else None,
            "failed_count": int(col.isna().sum()),
            "by_type": {
                t: round(sum(v) / len(v), 4) for t, v in by_type.items()
            },
        }

    per_case = {}
    for idx in range(len(valid)):
        case_vals = {}
        for name in metric_names:
            val = pd.to_numeric(df.loc[idx, name], errors="coerce")
            case_vals[name] = round(float(val), 4) if pd.notna(val) else None
        per_case[valid[idx]["case_id"]] = case_vals
    return {"summary": summary, "per_case": per_case, "n": len(valid)}


def main() -> None:
    parser = argparse.ArgumentParser(description="问答系统评估")
    parser.add_argument("--top-k", type=int, default=5, help="检索返回知识点数")
    parser.add_argument("--limit", type=int, default=None, help="仅评估前 N 条")
    parser.add_argument("--skip-ragas", action="store_true", help="跳过 ragas 指标")
    parser.add_argument("--case-file", default=str(CASES_PATH), help="用例文件")
    parser.add_argument("--intent", action="store_true",
                        help="额外跑意图分类并统计误判率（不改变 recall 口径）")
    parser.add_argument("--source", default=None,
                        help="只评估指定来源法律的用例（如 民法典 / 劳动法 / 劳动合同法）")
    args = parser.parse_args()

    cases = load_cases(Path(args.case_file))
    if args.source:
        cases = [c for c in cases if args.source in (c.get("source") or [])]
    if args.limit:
        cases = cases[: args.limit]
    print(f"[eval] 加载 {len(cases)} 条用例"
          + (f"（来源过滤：{args.source}）" if args.source else ""))

    print("[eval] 加载知识库…")
    kb = KnowledgeBase.load()

    gen = None
    if not args.skip_ragas:
        if not DASHSCOPE_API_KEY:
            print("[eval] 未设置 API_KEY，仅能评估 recall@k")
        else:
            gen = AnswerGenerator()

    intent_classifier = None
    if args.intent:
        from app.generation.intent import IntentClassifier
        intent_classifier = IntentClassifier()
        print("[eval] 已启用意图分类统计（用例全为法律题，非 answerable 即为误判）")

    t0 = time.time()
    rows, recalls = build_rows(cases, kb, gen, args.top_k, intent_classifier)
    print(f"[eval] 检索+生成完成，耗时 {time.time() - t0:.1f}s")

    if intent_classifier is not None:
        from collections import Counter
        dist = Counter(r["intent"] for r in recalls if r.get("intent"))
        misrouted = [r["id"] for r in recalls if r.get("intent") not in (None, "answerable")]
        print(f"[eval] 意图分布: {dict(dist)}")
        print(f"[eval] 误判（法律题被判为不可答）{len(misrouted)} 条: {misrouted}")

    # ---- recall@k 汇总 ----
    recall_summary: dict = {"per_case": recalls}
    for k in ("recall@1", "recall@3", "recall@5", "recall@8"):
        vals = [r[k] for r in recalls if r[k] is not None]
        recall_summary[k] = {
            "avg": round(sum(vals) / len(vals), 4) if vals else None,
            "n": len(vals),
        }
    print("\n===== recall@k 汇总 =====")
    for k, v in recall_summary.items():
        if k.startswith("recall@"):
            print(f"  {k}: avg={v['avg']} (n={v['n']})")

    # ---- 按来源法律分组（看出某部法律/某个新库的检索质量）----
    ks = ("recall@1", "recall@3", "recall@5", "recall@8")
    laws = sorted({law for r in recalls for law in (r.get("source") or [])})
    by_source: dict = {}
    if len(laws) > 1:
        print("\n===== 按来源法律 =====")
        for law in laws:
            subset = [r for r in recalls if law in (r.get("source") or [])]
            entry = {"n": len(subset)}
            for k in ks:
                vals = [r[k] for r in subset if r[k] is not None]
                entry[k] = round(sum(vals) / len(vals), 4) if vals else None
            by_source[law] = entry
            print(f"  {law:8} n={entry['n']:3}  " +
                  "  ".join(f"{k}={entry[k]}" for k in ks))

    # ---- ragas 指标 ----
    ragas_result = None
    if not args.skip_ragas and gen is not None:
        print("\n[eval] 运行 ragas 指标（调用 DashScope，可能需要几分钟）…")
        llm = build_llm()
        embeddings = build_embeddings()
        ragas_result = run_ragas(rows, llm, embeddings)
        print("\n===== ragas 指标汇总 =====")
        for name, s in ragas_result.get("summary", {}).items():
            print(f"  {name}: avg={s['avg']} (min={s['min']}, max={s['max']})")

    # ---- 保存结果 ----
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "top_k": args.top_k,
        "n_cases": len(cases),
        "recall_summary": recall_summary,
        "ragas": ragas_result,
    }
    out_path = RESULTS_DIR / f"evaluation_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[eval] 结果已保存: {out_path}")


if __name__ == "__main__":
    main()
