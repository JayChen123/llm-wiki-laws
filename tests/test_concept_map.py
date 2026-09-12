"""法律概念映射表：概念→条文解析与关键词增强测试。"""
from app.ingest.concept_map import concept_terms_for, enrich_keywords
from app.ingest.knowledge_extract import KnowledgePoint

LAW1 = "中华人民共和国劳动合同法.md L10"
LAW2 = "中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md L10"
MFD = "中华人民共和国民法典.md L10"


def _kp(i, source, clause):
    return KnowledgePoint(
        id=f"KP_{i:03d}", title=f"第X章·{clause}", chapter="第X章",
        chapter_title="", clause=clause, content="正文内容",
        keywords=[], source=source,
    )


def test_concept_terms_are_doc_aware():
    # 试用期→劳动合同法第十九条；同条文在劳动法里不是试用期
    assert "试用期" in concept_terms_for(_kp(1, LAW1, "第十九条"))
    assert "试用期" not in concept_terms_for(_kp(2, LAW2, "第十九条"))
    # 加班费→劳动法第四十四条；同条文在劳动合同法里不是加班费
    assert "加班费" in concept_terms_for(_kp(3, LAW2, "第四十四条"))
    assert "加班费" not in concept_terms_for(_kp(4, LAW1, "第四十四条"))
    # 节内条款（第二节 劳务派遣·第五十七条）也能命中
    assert "劳务派遣" in concept_terms_for(_kp(5, LAW1, "第二节 劳务派遣·第五十七条"))


def test_enrich_keywords_injects_and_dedupes():
    kp = _kp(1, LAW2, "第四十四条")
    kp.keywords = ["工资"]
    enrich_keywords([kp])
    assert "加班费" in kp.keywords and "加班" in kp.keywords
    assert "工资" in kp.keywords             # 原关键词保留
    enrich_keywords([kp])                    # 幂等
    assert kp.keywords.count("加班费") == 1


def test_civil_code_concepts_are_doc_aware():
    """民法典概念映射：含千位条号，且只命中民法典文档。"""
    assert "诉讼时效" in concept_terms_for(_kp(1, MFD, "第一百八十八条"))
    assert "离婚冷静期" in concept_terms_for(_kp(2, MFD, "第一千零七十七条"))
    assert "高空抛物" in concept_terms_for(_kp(3, MFD, "第一千二百五十四条"))
    # 民法典条文不命中劳动法概念；劳动法文档不命中民法典概念
    assert "加班费" not in concept_terms_for(_kp(4, MFD, "第四十四条"))
    assert "离婚冷静期" not in concept_terms_for(_kp(5, LAW2, "第一千零七十七条"))


def test_enrich_skips_unmapped():
    kp = _kp(1, "unmatched.md L1", "第一条")
    before = list(kp.keywords)
    enrich_keywords([kp])
    assert kp.keywords == before
