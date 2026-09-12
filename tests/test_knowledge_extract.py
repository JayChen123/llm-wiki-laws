"""知识点提取（法律法规）：编/章/节/条解析、清洗、康熙部首归一、关键词。"""
from pathlib import Path

from app.ingest.knowledge_extract import cn_to_int, extract_knowledge_points

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


# 民法典风格样例：编→分编→章→条，含千位条号、裸域名 / 斜杠日期页眉、章前言
CIVIL_SAMPLE = (
    "中华人民共和国民法典_中国人大网\n"
    "\n"
    "第一编  总    则\n"
    "\n"
    "第一章  基本规定\n"
    "\n"
    "www.npc.gov.cn/npc/c30834/202006/75ba6483b8344591abd07917e1d25cc8.shtml\n"
    "\n"
    "第一条　为了保护民事主体的合法权益，制定本法。\n"
    "\n"
    "第二编  物    权\n"
    "\n"
    "第一分编  通    则\n"
    "\n"
    "第一章  物权通则\n"
    "\n"
    "本章规定物权的设立、变更、转让和消灭。\n"
    "\n"
    "8/23/2020\n"
    "\n"
    "第一千条　因物的归属和利用产生的民事关系，适用本编规定。\n"
)


def test_cn_to_int_supports_thousand():
    """中文数字含"千"：民法典"第一千二百六十条"等千位条号依赖它。"""
    assert cn_to_int("九十八") == 98
    assert cn_to_int("一百零三") == 103
    assert cn_to_int("九百九十九") == 999
    assert cn_to_int("一千") == 1000
    assert cn_to_int("一千零一") == 1001
    assert cn_to_int("一千二百六十") == 1260


def test_part_and_thousand_articles(tmp_path: Path):
    """编层级 + 千位条号：旧实现数字类缺"千"，民法典第1000条起整段漏切。"""
    points = _extract(tmp_path, CIVIL_SAMPLE)
    assert len(points) == 2                    # 章前言与页眉噪音不单独成点
    p0, p1 = points
    assert (p0.part, p0.part_title) == ("第一编", "总则")
    assert p0.title == "第一编 总则·第一章·第一条"
    assert p0.clause == "第一条"
    assert (p1.part, p1.part_title) == ("第二编", "物权")
    assert p1.clause == "第一千条"
    assert p1.title == "第二编 物权·第一章·第一千条"


def test_part_disambiguates_repeated_chapter_no(tmp_path: Path):
    """民法典各编章号重新起算：同号章须靠 part 区分，不能合并。"""
    points = _extract(tmp_path, CIVIL_SAMPLE)
    assert [(p.part, p.chapter) for p in points] == [("第一编", "第一章"), ("第二编", "第一章")]


def test_subpart_heading_is_boundary_not_content(tmp_path: Path):
    """"第二分编 典型合同"这类分编标题既不产生知识点，也不并入上一条文正文。"""
    points = _extract(tmp_path, CIVIL_SAMPLE)
    blob = "".join(p.content for p in points)
    assert "分编" not in blob and "通则" not in blob
    assert "本章规定物权的设立" not in blob     # 章前言同样不挂到任何条文


def test_noise_filters_bare_domain_and_slash_date(tmp_path: Path):
    """页眉噪音：裸域名（无 http:// 前缀）与"月/日/年"日期。"""
    points = _extract(tmp_path, CIVIL_SAMPLE)
    blob = "".join(p.content + p.title for p in points)
    assert "www.npc.gov.cn" not in blob
    assert "8/23/2020" not in blob


def test_form_feed_keeps_physical_line_numbers(tmp_path: Path):
    """换页符 \\f 不得让 source 行号偏离编辑器实际行号（splitlines 会多算行）。"""
    text = "第一章 总则\n第一条 内容甲。\f2/8\n第二条 内容乙。\n"
    points = _extract(tmp_path, text)
    assert len(points) == 2
    assert points[0].source.endswith("L2-3")   # 第一条 在物理第 2 行
    assert points[1].source.endswith("L3-4")   # 第二条 在物理第 3 行（\f 未多算一行）
    assert "2/8" not in "".join(p.content for p in points)


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

    mfd = [s for s in sources if "民法典" in s.name]
    if mfd:
        # 民法典 7 编 1260 条：千位条号修复前只能抽到 999 条
        points = extract_knowledge_points(mfd[0])
        assert len(points) == 1260
        assert {p.part for p in points} == {f"第{n}编" for n in "一二三四五六七"}
        assert any(p.clause == "第一千二百六十条" for p in points)
        assert all(p.clause for p in points)             # 无章标题之类的空条文知识点
        assert not [p for p in points if "分编" in p.content]
