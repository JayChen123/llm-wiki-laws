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


def test_log_qa_event_records_retrieval_breakdown(tmp_path: Path):
    """日志要能区分：检索命中（seeds）/ 图谱扩展（graph_expanded）/ 反思补取（reflect_fetched）。

    这是排查"某个法条为什么没进上下文"的关键——三者的来源与成因完全不同。
    """
    log_path = tmp_path / "qa.log"
    logger = _make_logger(log_path)
    trace = {
        "mode": "reflect",
        "seeds": [{"id": "KP_831", "title": "第三编 合同·第九章·第六百二十六条", "score": 0.0328}],
        "expanded": [{"id": "KP_715", "title": "第三编 合同·第四章·第五百一十条",
                      "score": 0.0161, "hop": 1}],
        "fetched": [{"id": "KP_716", "title": "第三编 合同·第四章·第五百一十一条", "round": 1}],
        "rounds": 1,
        "reasons": ["[第1轮] 需要补充被引条文"],
    }
    log_qa_event(
        user_id="u1", query="买卖合同价款约定不明怎么办", knowledge_blocks=[],
        answer="适用第五百一十条。", intent="answerable", intent_reason="合同纠纷",
        trace=trace, memory={"count": 2, "kinds": {"profile": 1, "history": 1}},
        logger=logger,
    )

    event = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert event["mode"] == "reflect"
    assert [n["id"] for n in event["seeds"]] == ["KP_831"]
    assert event["graph_expanded"] == [
        {"id": "KP_715", "title": "第三编 合同·第四章·第五百一十条", "score": 0.0161, "hop": 1}]
    assert [n["id"] for n in event["reflect_fetched"]] == ["KP_716"]
    assert event["reflect"] == {"rounds": 1, "reasons": ["[第1轮] 需要补充被引条文"]}
    assert event["intent"] == "answerable" and event["intent_reason"] == "合同纠纷"
    assert event["memory_recalled"] == {"count": 2, "kinds": {"profile": 1, "history": 1}}


def test_log_qa_event_omits_absent_fields(tmp_path: Path):
    """未检索/未启用记忆时不写这些字段，保持日志干净（字段按需出现）。"""
    log_path = tmp_path / "qa.log"
    logger = _make_logger(log_path)
    log_qa_event(user_id="u1", query="今天天气", knowledge_blocks=[], answer="只能答法律问题",
                 intent="off_topic", logger=logger)
    event = json.loads(log_path.read_text(encoding="utf-8").strip())
    for absent in ("seeds", "graph_expanded", "reflect_fetched", "reflect",
                   "mode", "memory_recalled", "intent_reason"):
        assert absent not in event


def test_log_qa_event_trace_without_seeds_still_records_reflect(tmp_path: Path):
    """只给 rounds/reasons 的轨迹（旧调用方）也要能落 reflect 字段。"""
    log_path = tmp_path / "qa.log"
    logger = _make_logger(log_path)
    log_qa_event(user_id="u1", query="q", knowledge_blocks=[], answer="a",
                 trace={"rounds": 0, "reasons": ["测试用轨迹"]}, logger=logger)
    event = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert event["reflect"] == {"rounds": 0, "reasons": ["测试用轨迹"]}
    assert "seeds" not in event and "graph_expanded" not in event


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
