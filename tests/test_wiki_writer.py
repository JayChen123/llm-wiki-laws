"""Wiki 落盘：index 核心概念导航与概念→条文解析测试。"""
from app.ingest.knowledge_extract import KnowledgePoint
from app.ingest.wiki_writer import _concept_index_section, _resolve_concept

LAW1 = "中华人民共和国劳动合同法.md L10"
LAW2 = "中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md L10"


def _kp(i, source, clause, chapter="第四章"):
    return KnowledgePoint(
        id=f"KP_{i:03d}", title=f"{chapter}·{clause}", chapter=chapter,
        chapter_title="", clause=clause, content="正文内容",
        keywords=[], source=source,
    )


def test_resolve_concept_picks_correct_doc():
    pts = [
        _kp(1, LAW1, "第四十四条"),   # 劳动合同法 第四十四条
        _kp(2, LAW2, "第四十四条"),   # 劳动法 第四十四条
    ]
    assert _resolve_concept("劳动法", "第四十四条", pts) == ("KP_002", "第四十四条")
    assert _resolve_concept("劳动合同法", "第四十四条", pts) == ("KP_001", "第四十四条")


def test_concept_index_section_renders_links():
    pts = [
        _kp(1, LAW1, "第十九条", "第二章"),
        _kp(2, LAW2, "第四十四条", "第四章"),
    ]
    out = _concept_index_section(pts)
    assert "核心概念导航" in out
    # 已映射概念 → wiki 链接
    assert "[[KP_001|第十九条]]" in out    # 试用期概念
    assert "[[KP_002|第四十四条]]" in out   # 加班费概念
    # 夹具未覆盖的概念 → 纯文本兜底（《law》第X条）
    assert "《劳动合同法》第三条" in out
