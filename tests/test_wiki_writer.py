"""Wiki 落盘：index 核心概念导航、目录分组排序与概念→条文解析测试。"""
from app.ingest.knowledge_extract import KnowledgePoint
from app.ingest.wiki_writer import _concept_index_section, _index_md, _order_no, _resolve_concept

LAW1 = "中华人民共和国劳动合同法.md L10"
LAW2 = "中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md L10"
CIVIL = "中华人民共和国民法典.md L10"


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


def _kp_civil(i, part, part_title, chapter, clause):
    return KnowledgePoint(
        id=f"KP_{i:03d}", title=f"{part} {part_title}·{chapter}·{clause}", chapter=chapter,
        chapter_title="", part=part, part_title=part_title, clause=clause,
        content="正文内容", keywords=[], source=CIVIL,
    )


def test_order_no_supports_multi_digit_chinese():
    """旧实现只认"一"~"九十九"的两位数写法，"第二十九章"会落到兜底值。"""
    assert _order_no("第一章") == 1
    assert _order_no("第十章") == 10
    assert _order_no("第二十九章") == 29
    assert _order_no("第二编") == 2
    assert _order_no("") == -1


def test_index_groups_by_part_then_chapter():
    """民法典各编章号重新起算：目录须按 编→章 两级分组，同号章不得并成一个节。"""
    pts = [
        _kp_civil(1, "第一编", "总则", "第一章", "第一条"),
        _kp_civil(2, "第二编", "物权", "第一章", "第一百一十四条"),
        _kp_civil(3, "第二编", "物权", "第二章", "第二百零五条"),
        _kp_civil(4, "第三编", "合同", "第十章", "第五百条"),
    ]
    out = _index_md(pts, {"edges": []})
    assert "### 第一编 总则" in out and "### 第二编 物权" in out
    assert out.count("#### 第一章") == 2       # 两编各自的"第一章"分列不同编下
    assert "#### 第二章" in out and "#### 第十章" in out
    assert out.index("#### 第二章") < out.index("#### 第十章")   # 按章号数值排序


def test_index_keeps_flat_chapters_without_part():
    """无"编"的法律（劳动法/劳动合同法）目录仍是 doc→章 两级。"""
    pts = [_kp(1, LAW1, "第一条", "第一章"), _kp(2, LAW1, "第二条", "第二章")]
    out = _index_md(pts, {"edges": []})
    assert "### 第一章" in out and "### 第二章" in out
    assert "####" not in out
