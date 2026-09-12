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
    reflect: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """记录一次问答的关键信息。

    knowledge_blocks 建议为 [{"id", "title", "score", "source"}, ...]，
    即本次检索命中的知识块（不含正文，避免日志冗长）。
    intent 为意图识别结果（answerable/legal_uncovered/off_topic），
    未启用或走兜底路径时用于区分"没检索"的原因。
    reflect 为反思式多跳的轨迹（`{"rounds": n, "reasons": [...]}`），
    用于事后审计"为什么停在第几轮"。
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
    if reflect is not None:
        event["reflect"] = reflect
    logger.info(json.dumps(event, ensure_ascii=False))
