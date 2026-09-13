"""问答日志：结构化记录关键信息（用户ID、检索知识块、最终回答等）。

日志写入 logs/qa.log（滚动文件），每条问答事件为一行 JSON，便于检索分析。
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def get_logger(log_path: Path | None = None) -> logging.Logger:
    """获取 qa 日志器（首次调用时初始化滚动文件 handler）。"""
    logger = logging.getLogger("qa")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        path = Path(log_path) if log_path else LOGS_DIR / "qa.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            str(path), maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def log_qa_event(
    user_id: str,
    query: str,
    knowledge_blocks: list[dict],
    answer: str,
    retrieval_ms: float | None = None,
    generation_ms: float | None = None,
    intent: str | None = None,
    intent_reason: str | None = None,
    trace: dict | None = None,
    memory: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """记录一次问答的关键信息（每个问答一行 JSON，字段按需出现）。

    字段分工：
    - `query` / `answer`：用户问题与最终回答。
    - `knowledge_blocks`：本次送进生成的**全部**知识块（不含正文，避免日志冗长）。
    - `seeds`：RRF 融合后的种子（检索真正"命中"的知识点）。
    - `graph_expanded`：沿关联图 BFS 扩展带进来的知识点（含 hop 跳数）。
    - `reflect_fetched`：反思循环沿引用关系补取的条文（含第几轮）。
    - `reflect`：反思轨迹 `{rounds, reasons}`，用于审计"为什么停在第几轮"。
    - `mode`：检索链路（reflect / bfs），便于两条链路对比。
    - `intent` / `intent_reason`：意图分类结果与理由。
    - `memory_recalled`：长期记忆召回情况（条数与类型，**不含正文**）。

    `trace` 直接传检索器的 `last_trace`（已按上述三类分好组），由本函数拆成
    独立字段，便于 grep 与统计。
    """
    logger = logger or get_logger()
    event = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "user_id": user_id,
        "query": query,
        "knowledge_blocks": knowledge_blocks,
        "answer": answer,
        "retrieval_ms": round(retrieval_ms, 1) if retrieval_ms is not None else None,
        "generation_ms": round(generation_ms, 1) if generation_ms is not None else None,
    }
    if intent is not None:
        event["intent"] = intent
    if intent_reason:
        event["intent_reason"] = intent_reason
    if trace:
        if trace.get("mode"):
            event["mode"] = trace["mode"]
        if trace.get("seeds") is not None:
            event["seeds"] = trace["seeds"]
        if trace.get("expanded") is not None:
            event["graph_expanded"] = trace["expanded"]
        if trace.get("fetched") is not None:
            event["reflect_fetched"] = trace["fetched"]
        event["reflect"] = {"rounds": trace.get("rounds", 0),
                            "reasons": trace.get("reasons", [])}
    if memory is not None:
        event["memory_recalled"] = memory
    logger.info(json.dumps(event, ensure_ascii=False))
