"""从 `logs/qa.log` 汇总问答链路画像（意图 / 检索 / 反思 / 记忆 / 成本 / 耗时）。

用途：回答"反思值不值那次 LLM 调用""意图分类分流了多少""检索与扩展各带进来多少"
这类问题——它们决定要不要调整检索链路与开关，靠单条日志看不出结论。

    uv run python -m scripts.analyze_logs                  # 全量
    uv run python -m scripts.analyze_logs --tail 200       # 只看最近 200 条
    uv run python -m scripts.analyze_logs --since 2026-09-13
    uv run python -m scripts.analyze_logs --json           # 机器可读（便于二次处理）

**兼容性**：历史日志里混有多个代码版本的字段（早期事件没有 seeds/reflect 等）。
本工具对缺字段的事件宽容处理——归入 `unknown` / `none`，不会因此报错或失真统计
（这是能安全分析 300+ 条历史日志的前提）。
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from app.config import REFLECT_MAX_ROUNDS, TOP_K
from app.logger import LOGS_DIR

DEFAULT_LOG = LOGS_DIR / "qa.log"

# 反思停止原因归类（reasons 的最后一条即本轮停止原因）
# 无 LLM 参与的两个停止原因（reflective_retriever 里写死的文案）
_STOP_PATTERNS = (
    ("no_candidates", "无可跟进的引用关系"),
    ("already_collected", "均已在上下文中"),
    ("reflect_error", "停止继续检索"),          # 反思调用异常（fail-closed）
)
# LLM 参与判定的轮次，原因统一以 "[第N轮] " 开头（见 reflective_retriever 的 reasons.append）
_LLM_ROUND_PREFIX = "[第"


def load_events(path: Path = DEFAULT_LOG, tail: int | None = None,
                since: str | None = None, user: str | None = None,
                exclude_user: str | None = None) -> list[dict]:
    """读取日志事件；跳过坏行与非 JSON 行，容忍缺字段。

    `user` / `exclude_user` 按 user_id 过滤——既可用于看单个用户的画像，
    也可用于排除已知噪声（例如跑测试时若污染过日志，可按测试账号排除）。
    """
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or "query" not in event:
            continue
        if since and str(event.get("time", "")) < since:
            continue
        uid = str(event.get("user_id", ""))
        if user and uid != user:
            continue
        if exclude_user and uid == exclude_user:
            continue
        events.append(event)
    return events[-tail:] if tail else events


def classify_stop(event: dict) -> str:
    """把一次问答的反思停止原因归入固定桶（便于统计）。"""
    trace = event.get("reflect")
    if not trace:
        return "no_retrieval" if not event.get("knowledge_blocks") else "no_trace"
    rounds = int(trace.get("rounds", 0) or 0)
    if rounds >= REFLECT_MAX_ROUNDS:
        return "max_rounds"
    reasons = trace.get("reasons") or []
    last = reasons[-1] if reasons else ""
    if not rounds and not reasons:
        return "no_trace"
    for name, pattern in _STOP_PATTERNS:
        if pattern in last:
            return name
    if last.startswith(_LLM_ROUND_PREFIX):
        # 带轮次前缀 = 这一轮 LLM 参与了，按其判定退出（措辞多次变过，别靠关键词猜）
        return "llm_stop"
    # 认不出的原因归 other 并在报告里列出样本——不要默认塞进 llm_stop，
    # 否则"未知"会被误读成"LLM 判定退出"（我在测试污染事件上踩过这个坑）
    return "other"


def reflect_calls(event: dict) -> int:
    """估算一次问答里反思的 LLM 调用次数。

    圈定规则（见 reflective_retriever 的循环）：无候选 → 0 次；
    否则每轮取回时调 1 次，再调 1 次判定"已充分"；因达轮数上限退出时没有最后那次判定。
    """
    stop = classify_stop(event)
    if stop in ("no_candidates", "no_retrieval", "no_trace"):
        return 0
    rounds = int((event.get("reflect") or {}).get("rounds", 0) or 0)
    return rounds if stop == "max_rounds" else rounds + 1


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "avg": None, "median": None, "max": None}
    return {"n": len(values),
            "avg": round(statistics.mean(values), 2),
            "median": round(statistics.median(values), 2),
            "max": round(max(values), 2)}


def summarize(events: list[dict]) -> dict:
    """汇总画像。所有字段都对缺字段的历史事件宽容。"""
    if not events:
        return {"events": 0}

    intents = Counter(e.get("intent") or "unknown" for e in events)
    modes = Counter(e.get("mode") or ("none" if not e.get("knowledge_blocks") else "unknown")
                    for e in events)
    stops = Counter(classify_stop(e) for e in events)

    seeds_n = [len(e["seeds"]) for e in events if e.get("seeds") is not None]
    expanded_n = [len(e["graph_expanded"]) for e in events if e.get("graph_expanded") is not None]
    fetched_n = [len(e["reflect_fetched"]) for e in events if e.get("reflect_fetched") is not None]
    blocks_n = [len(e.get("knowledge_blocks") or []) for e in events]

    other_samples = sorted({(e.get("reflect") or {}).get("reasons", [""])[-1][:60]
                            for e in events if classify_stop(e) == "other"})[:5]
    with_fetch = [n for n in fetched_n if n > 0]
    rounds = [int((e.get("reflect") or {}).get("rounds", 0) or 0)
              for e in events if e.get("reflect")]

    calls = [reflect_calls(e) for e in events]
    # 总调用 ≈ 意图识别 1（有 intent 字段）+ 反思 N + 生成 1（有 generation_ms）
    total_calls = [(1 if e.get("intent") else 0) + c + (1 if e.get("generation_ms") is not None else 0)
                   for e, c in zip(events, calls)]

    retrieval_ms = [e["retrieval_ms"] for e in events if isinstance(e.get("retrieval_ms"), (int, float))]
    generation_ms = [e["generation_ms"] for e in events if isinstance(e.get("generation_ms"), (int, float))]
    memory_n = [e["memory_recalled"].get("count", 0) for e in events
                if isinstance(e.get("memory_recalled"), dict)]

    return {
        "events": len(events),
        "time_range": [events[0].get("time"), events[-1].get("time")],
        "intent": dict(intents),
        "mode": dict(modes),
        "reflect_stop": dict(stops),
        "reflect_stop_other_samples": other_samples,
        "seeds": {"events": len(seeds_n), **_stats([float(n) for n in seeds_n])},
        "graph_expanded": {"events": len(expanded_n), **_stats([float(n) for n in expanded_n])},
        "reflect_fetched": {
            "events": len(fetched_n),
            "events_with_fetch": len(with_fetch),
            "fetch_rate": round(len(with_fetch) / len(fetched_n), 4) if fetched_n else None,
            "nodes": sum(fetched_n),
            **_stats([float(n) for n in fetched_n]),
        },
        "reflect_rounds": {**_stats([float(r) for r in rounds]),
                           "max_rounds_setting": REFLECT_MAX_ROUNDS},
        "context_blocks": _stats([float(n) for n in blocks_n]),
        "llm_calls": {"reflect": _stats([float(c) for c in calls]),
                      "total_per_qa": _stats([float(c) for c in total_calls])},
        "latency_ms": {"retrieval": _stats(retrieval_ms), "generation": _stats(generation_ms)},
        "memory_recalled": {"events": len(memory_n), **_stats([float(n) for n in memory_n])},
        "settings": {"TOP_K": TOP_K},
    }


def render(summary: dict) -> str:
    """渲染成可读报告。"""
    if not summary.get("events"):
        return "（日志为空——先跑几轮问答再看）"
    lines = [f"问答事件：{summary['events']} 条   时间范围：{summary['time_range'][0]} ~ {summary['time_range'][1]}", ""]

    lines.append("【意图分流】")
    for k, v in sorted(summary["intent"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {k:16} {v:5}  {v / summary['events']:6.1%}")
    lines.append("")

    lines.append("【检索链路】")
    for k, v in sorted(summary["mode"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {k:16} {v:5}  {v / summary['events']:6.1%}")
    lines.append("")

    def one(name, s):
        if not s.get("n"):
            return f"  {name:16} （无数据）"
        return f"  {name:16} 平均 {s['avg']:7}  中位 {s['median']:7}  最大 {s['max']:7}  样本 {s['n']}"

    lines.append("【上下文构成】")
    lines.append(one("命中 seeds", summary["seeds"]))
    lines.append(one("图谱扩展", summary["graph_expanded"]))
    lines.append(one("最终上下文块", summary["context_blocks"]))
    lines.append("")

    lines.append("【反思（reflect 链路）】")
    for k, v in sorted(summary["reflect_stop"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  停止原因 {k:18} {v:5}  {v / summary['events']:6.1%}")
    if summary.get("reflect_stop_other_samples"):
        lines.append("  （other 样本：" +
                     " | ".join(summary["reflect_stop_other_samples"]) + "）")
    ff = summary["reflect_fetched"]
    if ff.get("events"):
        lines.append(f"  实际补取到条文的事件：{ff['events_with_fetch']}/{ff['events']}"
                     f"（{ff['fetch_rate']:.1%}），累计补取 {ff['nodes']} 个知识点")
    lines.append(one("反思轮数", summary["reflect_rounds"]))
    lines.append("")

    lines.append("【LLM 调用成本（估算）】")
    lines.append(one("反思调用/次", summary["llm_calls"]["reflect"]))
    lines.append(one("总调用/次", summary["llm_calls"]["total_per_qa"]))
    lines.append("")

    lines.append("【耗时与记忆】")
    lines.append(one("检索 retrieval_ms", summary["latency_ms"]["retrieval"]))
    lines.append(one("生成 generation_ms", summary["latency_ms"]["generation"]))
    mem = summary["memory_recalled"]
    if mem.get("events"):
        lines.append(f"  长期记忆召回条目/次：平均 {mem['avg']}（{mem['events']} 条事件有该字段）")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="问答日志画像汇总")
    parser.add_argument("--log", default=str(DEFAULT_LOG), help="日志路径")
    parser.add_argument("--tail", type=int, default=None, help="只看最近 N 条")
    parser.add_argument("--since", default=None, help="只看该时间之后（如 2026-09-13）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    parser.add_argument("--user", default=None, help="只统计该 user_id")
    parser.add_argument("--exclude-user", default=None, help="排除该 user_id")
    args = parser.parse_args()

    events = load_events(Path(args.log), tail=args.tail, since=args.since,
                         user=args.user, exclude_user=args.exclude_user)
    summary = summarize(events)
    print(json.dumps(summary, ensure_ascii=False, indent=2) if args.json else render(summary))


if __name__ == "__main__":
    main()
