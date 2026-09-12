"""知识点关联图谱构建测试。"""
import numpy as np

from app.ingest.graph_build import build_graph
from app.ingest.knowledge_extract import KnowledgePoint


def _kp(i, title, chapter, clause, keywords):
    return KnowledgePoint(
        id=f"KP_{i:03d}", title=title, chapter=chapter,
        chapter_title="", clause=clause, content=f"{title} 内容",
        keywords=keywords, source="test.md",
    )


class FakeEmbedder:
    """按主题返回固定向量，同主题余弦=1。"""

    def encode(self, texts):
        vecs = np.zeros((len(texts), 4))
        for i, t in enumerate(texts):
            if "请假" in t or "休假" in t:
                vecs[i] = [1, 0, 0, 0]
            elif "加班" in t:
                vecs[i] = [0, 1, 0, 0]
            else:
                vecs[i] = [0, 0, 1, 0]
        return vecs


def test_sibling_and_keyword_edges():
    points = [
        _kp(1, "第六章·一、工作时间", "第六章", "一、工作时间", ["工时", "考勤"]),
        _kp(2, "第六章·二、考勤管理", "第六章", "二、考勤管理", ["考勤", "打卡"]),
        _kp(3, "第五章·一、薪酬", "第五章", "一、薪酬", ["薪酬", "工资"]),
        _kp(4, "第五章·二、加班及加班费", "第五章", "二、加班及加班费", ["加班", "加班费"]),
    ]
    graph = build_graph(points, keyword_threshold=0.5)
    edges = {(e["source"], e["target"]): e for e in graph["edges"]}

    # 同章兄弟边
    assert ("KP_001", "KP_002") in edges and "sibling" in edges[("KP_001", "KP_002")]["types"]
    assert ("KP_003", "KP_004") in edges and "sibling" in edges[("KP_003", "KP_004")]["types"]

    # 关键词边：KP_001(考勤) 与 KP_002(考勤) Jaccard=1/3≈0.33 < 0.5 不加
    # 但 KP_003(薪酬,工资) 与 KP_004(加班,加班费) 无重合
    assert ("KP_001", "KP_002") in edges  # sibling 已覆盖
    kw_edges = [e for e in graph["edges"] if "keyword" in e["types"]]
    assert len(kw_edges) == 0


def test_semantic_edges():
    points = [
        _kp(1, "第六章·四、休（请）假管理", "第六章", "四、休（请）假管理", ["休假"]),
        _kp(2, "第五章·二、加班及加班费", "第五章", "二、加班及加班费", ["加班"]),
    ]
    graph = build_graph(points, embedder=FakeEmbedder(), semantic_threshold=0.9)
    edges = graph["edges"]
    # 两知识点主题不同，不应有 semantic 边（余弦=0）
    assert all("semantic" not in e["types"] for e in edges)


def test_semantic_edge_same_topic():
    points = [
        _kp(1, "第六章·四、请假管理", "第六章", "四、请假管理", ["休假"]),
        _kp(2, "第六章·五、请假审批", "第六章", "五、请假审批", ["审批"]),
    ]
    graph = build_graph(points, embedder=FakeEmbedder(), semantic_threshold=0.9)
    edges = {(e["source"], e["target"]): e for e in graph["edges"]}
    assert "semantic" in edges[("KP_001", "KP_002")]["types"]


def _kp_law(i, title, chapter, clause, content, source):
    return KnowledgePoint(
        id=f"KP_{i:03d}", title=title, chapter=chapter,
        chapter_title="", clause=clause, content=content,
        keywords=[], source=source,
    )


def test_crossref_edges():
    """正文"第X条"引用同法条文 → crossref 高权重边；跨法/缺失引用不加边。"""
    pts = [
        _kp_law(38, "第四章·第三十八条", "第四章", "第三十八条",
                "劳动者有下列情形之一的，可以解除劳动合同。", "test.md"),
        _kp_law(46, "第四章·第四十六条", "第四章", "第四十六条",
                "依照本法第三十八条规定解除劳动合同的，应当支付经济补偿。", "test.md"),
        _kp_law(65, "第五章·第二节 劳务派遣·第六十五条", "第五章", "第二节 劳务派遣·第六十五条",
                "被派遣劳动者有本法第三十九条规定情形的，用工单位可以退回。", "test.md"),
        _kp_law(39, "第四章·第三十九条", "第四章", "第三十九条",
                "劳动者有下列情形之一的，用人单位可以解除劳动合同。", "other.md"),
    ]
    graph = build_graph(pts)
    edges = {(e["source"], e["target"]): e for e in graph["edges"]}
    # 46 引用 38 → crossref 边（权重 1.5）
    assert "crossref" in edges[("KP_038", "KP_046")]["types"]
    assert edges[("KP_038", "KP_046")]["weight"] == 1.5
    # 引用的条文不在同法内（KP_039 属于 other.md）→ 不加 crossref 边
    e_65_39 = edges.get(("KP_039", "KP_065"))
    assert e_65_39 is None or "crossref" not in e_65_39["types"]


def _kp_part(i, part, chapter, clause):
    return KnowledgePoint(
        id=f"KP_{i:03d}", title=f"{part}·{chapter}·{clause}", chapter=chapter,
        chapter_title="", part=part, part_title="", clause=clause,
        content="内容", keywords=[], source="中华人民共和国民法典.md L1",
    )


def test_sibling_edges_scoped_by_part():
    """民法典各编章号重新起算：sibling 边必须按 (编, 章) 分组，跨编同号章不得成团。"""
    pts = [
        _kp_part(1, "第一编", "第一章", "第一条"),
        _kp_part(2, "第一编", "第一章", "第二条"),
        _kp_part(3, "第二编", "第一章", "第一千条"),
    ]
    graph = build_graph(pts)
    edges = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("KP_001", "KP_002") in edges            # 同编同章 → 兄弟
    assert edges == {("KP_001", "KP_002")}          # 跨编同号章不建边
    assert graph["nodes"][2]["part"] == "第二编"     # 节点带编信息


def test_crossref_supports_thousand_article():
    """千位条号的交叉引用也要能建 crossref 边（民法典大量引用第1000条以上条文）。"""
    pts = [
        KnowledgePoint(id="KP_001", title="第一章·第一千条", chapter="第一章",
                       chapter_title="", clause="第一千条",
                       content="参照适用本法第一千二百六十条的规定。", keywords=[],
                       source="中华人民共和国民法典.md L1"),
        KnowledgePoint(id="KP_002", title="第十章·第一千二百六十条", chapter="第十章",
                       chapter_title="", clause="第一千二百六十条",
                       content="本法自2021年1月1日起施行。", keywords=[],
                       source="中华人民共和国民法典.md L2"),
    ]
    graph = build_graph(pts)
    edges = {(e["source"], e["target"]): e for e in graph["edges"]}
    assert "crossref" in edges[("KP_001", "KP_002")]["types"]
