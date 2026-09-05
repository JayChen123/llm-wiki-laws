"""知识点提取（法律法规）：章/节/条解析、清洗、康熙部首归一、关键词。"""
from pathlib import Path

from app.ingest.knowledge_extract import extract_knowledge_points

# 法律文档样例：页眉日期、页眉文件名、页码、URL、页脚噪音、康熙部首变体（第⼀条）、
# 正文折行处的交叉引用（第三十九条和第四十条…，条后无空白）
SAMPLE = """2024/11/15 21:36

中华人民共和国劳动法_中华人民共和国人力资源和社会保障部

第一章　总则
第⼀条　为了保护劳动者的合法权益，制定本法。
第⼆条　在中华⼈⺠共和国境内的企业、个体经济组织和与之形成劳动关系的劳动者，适用本法。

2/8

第三章　劳动合同和集体合同
第⼀节　集体合同
第五十一条　企业职工一方与用人单位通过平等协商，可以就劳动报酬、工作时间、休息休假等事项订立集体合同。
第二节　劳务派遣
第五十七条　劳务派遣单位应当依照公司法有关规定设立，注册资本不得少于二百万元。
第三十九条和第四十条规定的除外。
https://www.example.com/xx
"""


def _extract(tmp_path: Path, text: str = SAMPLE):
    md = tmp_path / "sample.md"
    md.write_text(text, encoding="utf-8")
    return extract_knowledge_points(md)


def test_extract_articles_and_sections(tmp_path: Path):
    points = _extract(tmp_path)
    assert len(points) == 4
    p0 = points[0]
    assert p0.title == "第一章·第一条"
    assert p0.chapter == "第一章" and p0.chapter_title == "总则"
    assert p0.clause == "第一条"
    # 节内条款：clause 为 "第X节 节名·第X条"
    assert points[2].clause == "第一节 集体合同·第五十一条"
    assert points[3].clause == "第二节 劳务派遣·第五十七条"
    assert all(p.id and p.title and p.content for p in points)


def test_kangxi_variants_normalized(tmp_path: Path):
    points = _extract(tmp_path)
    blob = "".join(p.content + p.title for p in points)
    residue = [c for c in blob if 0x2E80 <= ord(c) <= 0x2FFF]
    assert not residue
    assert "中华人民共和国境内" in points[1].content   # ⼈(U+2F08)/⺠(U+2EA0) 已归为 人/民
    assert points[1].clause == "第二条"                # ⼀(U+2F00)/⼆(U+2F06) 已归为 一/二


def test_cross_ref_not_article(tmp_path: Path):
    points = _extract(tmp_path)
    # 交叉引用（条后无空白）不产生新知识点
    assert not any("第三十九条" in p.clause or "第四十条" in p.clause for p in points)
    # 交叉引用按正文并入前一条文
    assert "第三十九条和第四十条规定的除外。" in points[3].content


def test_footer_noise_removed(tmp_path: Path):
    points = _extract(tmp_path)
    blob = "".join(p.content for p in points)
    assert "2024/11/15" not in blob          # 页眉日期
    assert "2/8" not in blob                  # 页码
    assert "https://" not in blob             # URL
    assert "人力资源和社会保障部" not in blob  # 页眉文件名


def test_table_text_extracted(tmp_path: Path):
    text = ("第一章 总则\n第一条 概述\n"
            "| 名称 | 条文 |\n| --- | --- |\n| 工时 | 44小时 |\n")
    points = _extract(tmp_path, text)
    assert len(points) == 1
    assert "工时" in points[0].content and "44小时" in points[0].content


def test_keywords_extracted(tmp_path: Path):
    points = _extract(tmp_path)
    assert points[0].keywords
    assert all(len(k) > 1 for k in points[0].keywords)


def test_real_markdown_extracts(tmp_path: Path):
    from app.config import RAW_DIR

    sources = sorted(RAW_DIR.glob("*.md")) if RAW_DIR.exists() else []
    if not sources:
        return  # 未构建数据时跳过
    ht = [s for s in sources if "劳动合同法" in s.name]
    lbf = [s for s in sources if "劳动法" in s.name and "劳动合同法" not in s.name]

    if ht:
        points = extract_knowledge_points(ht[0])
        assert len(points) == 98
        assert any("劳务派遣" in p.clause for p in points)
        assert any("非全日制用工" in p.clause for p in points)
        assert any(p.clause == "第九十八条" for p in points)
        blob = "".join(p.content + p.title for p in points)
        assert not [c for c in blob if 0x2E80 <= ord(c) <= 0x2FFF]

    if lbf:
        points = extract_knowledge_points(lbf[0])
        assert len(points) == 107
        assert any(p.clause == "第一百零七条" for p in points)
        blob = "".join(p.content + p.title for p in points)
        assert not [c for c in blob if 0x2E80 <= ord(c) <= 0x2FFF]
