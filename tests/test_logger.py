"""问答日志：结构化 JSON 事件写入测试。"""
import json
import logging
import logging.handlers
from pathlib import Path

from app.logger import log_qa_event


def _make_logger(path: Path) -> logging.Logger:
    logger = logging.getLogger(f"test_qa_{id(path)}")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def test_log_qa_event_records_key_fields(tmp_path: Path):
    log_path = tmp_path / "qa.log"
    logger = _make_logger(log_path)
    blocks = [
        {"id": "KP_010", "title": "第五章·二、加班及加班费", "score": 0.0328, "source": "奥德-员工手册.md L375-381"},
        {"id": "KP_009", "title": "第五章·一、薪酬", "score": 0.0161, "source": "奥德-员工手册.md L316-374"},
    ]
    log_qa_event(
        user_id="user-001",
        query="加班费怎么算",
        knowledge_blocks=blocks,
        answer="加班费按岗位工资计算。",
        retrieval_ms=12.3,
        generation_ms=456.7,
        logger=logger,
    )

    event = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert event["user_id"] == "user-001"
    assert event["query"] == "加班费怎么算"
    assert len(event["knowledge_blocks"]) == 2
    assert event["knowledge_blocks"][0]["id"] == "KP_010"
    assert event["answer"] == "加班费按岗位工资计算。"
    assert event["retrieval_ms"] == 12.3
    assert event["generation_ms"] == 456.7
    assert "time" in event


def test_log_qa_event_handles_missing_timing(tmp_path: Path):
    log_path = tmp_path / "qa.log"
    logger = _make_logger(log_path)
    log_qa_event(
        user_id="u2", query="hello", knowledge_blocks=[], answer="hi", logger=logger
    )
    event = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert event["retrieval_ms"] is None
    assert event["generation_ms"] is None
    assert event["knowledge_blocks"] == []
