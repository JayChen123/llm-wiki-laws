"""Neo4j 图存储：读写形状一致、全量对齐、批量写入、seq 排序（假 driver，离线）。

离线测试不连 Neo4j，因此这里锁的是**我们发出了什么 Cypher、绑定了什么参数**，
以及 load() 能否把结果还原成与 json 完全一致的结构（这是检索质量不变的前提）。
真实执行由 `python -m app.ingest.sync_graph` 与 Cypher 计数核对覆盖。
"""
import pytest

from app.ingest.graph_store import Neo4jGraphStore


class FakeRecord(dict):
    """neo4j 的 Record 支持按字段名取值，用 dict 即可模拟。"""


class FakeResult(list):
    pass


class FakeSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher, **params):
        self.driver.calls.append((cypher, params))
        return FakeResult(self.driver.rows_for(cypher))


class FakeDriver:
    """记录 Cypher 与参数。

    返回行用 `responses`（按 Cypher 片段匹配）指定，`next_rows` 作为兜底。
    必须按片段分派：`load()` 会连发两次查询（节点、边），
    若对每个查询都返回同一批行，第二次就会拿到节点记录而 KeyError。
    """

    def __init__(self, next_rows=None, responses=None):
        self.calls: list[tuple[str, dict]] = []
        self.next_rows = list(next_rows or [])
        self.responses: dict[str, list] = responses or {}
        self.closed = False

    def rows_for(self, cypher: str) -> list:
        for fragment, rows in self.responses.items():
            if fragment in cypher:
                return list(rows)
        return list(self.next_rows)

    def session(self, database=None):
        self.database = database
        return FakeSession(self)

    def close(self):
        self.closed = True

    def find(self, fragment: str) -> tuple[str, dict]:
        for cypher, params in self.calls:
            if fragment in cypher:
                return cypher, params
        raise AssertionError(f"未执行包含 {fragment!r} 的语句；实际：{[c[0][:40] for c in self.calls]}")


def _store(driver=None):
    return Neo4jGraphStore(driver=driver or FakeDriver())


def _graph():
    return {
        "nodes": [
            {"id": "KP_002", "title": "第一章·第二条", "chapter": "第一章",
             "chapter_title": "基本规定", "part": "", "part_title": "",
             "clause": "第二条", "keywords": ["乙"], "content": "正文乙", "source": "a.md L2"},
            {"id": "KP_001", "title": "第一章·第一条", "chapter": "第一章",
             "chapter_title": "基本规定", "part": "", "part_title": "",
             "clause": "第一条", "keywords": [], "content": "正文甲", "source": "a.md L1"},
        ],
        "edges": [
            {"source": "KP_001", "target": "KP_002", "types": ["sibling"], "weight": 1.0},
        ],
    }


# ---------- 写入 ----------

def test_sync_writes_nodes_and_edges():
    driver = FakeDriver()
    result = _store(driver).sync(_graph())

    assert result == {"nodes": 2, "edges": 1, "relationships": 1}
    node_cypher, node_params = driver.find("MERGE (n:KnowledgePoint")
    assert "UNWIND $rows AS row" in node_cypher
    assert [r["id"] for r in node_params["rows"]] == ["KP_002", "KP_001"]
    assert node_params["rows"][0]["content"] == "正文乙"         # 正文必须入库（反思候选要解析）
    assert node_params["rows"][0]["seq"] == 2                    # 数字序号，用于还原 json 顺序

    edge_cypher, edge_params = driver.find("CREATE (a)-[:SIBLING")
    assert edge_params["rows"] == [
        {"source": "KP_001", "target": "KP_002", "weight": 1.0}
    ]
    # 只有 sibling 类型的边，不该建其它三类关系
    assert not any(":KEYWORD" in c or ":SEMANTIC" in c or ":CROSSREF" in c
                   for c, _ in driver.calls)


def test_multi_type_edge_becomes_multiple_relationships():
    """多类型边拆成多条关系（各类型一条），每条都带 json 里的合并权重。

    json 只存 max 权重，per-type 权重在源里就不存在，故各类型关系携带同一权重；
    load() 再按 (source,target) 取 max 还原，与 json 一致。
    """
    driver = FakeDriver()
    graph = {
        "nodes": _graph()["nodes"],
        "edges": [{"source": "KP_001", "target": "KP_002",
                   "types": ["semantic", "sibling"], "weight": 1.0}],
    }
    result = _store(driver).sync(graph)
    assert result["edges"] == 1 and result["relationships"] == 2      # 一条边 → 两条关系
    for rel in ("SIBLING", "SEMANTIC"):
        assert driver.find(f"CREATE (a)-[:{rel}")[1]["rows"] == [
            {"source": "KP_001", "target": "KP_002", "weight": 1.0}]
    assert not any(":KEYWORD" in c or ":CROSSREF" in c for c, _ in driver.calls)


def test_sync_deletes_all_relationship_types():
    """清空关系时不带类型过滤，顺带清掉旧模型遗留的 :RELATED。"""
    driver = FakeDriver()
    _store(driver).sync(_graph())
    cypher, _ = driver.find("DELETE r")
    assert "MATCH (:KnowledgePoint)-[r]->(:KnowledgePoint) DELETE r" == cypher


def test_crossref_direction_is_derived_from_content():
    """`:CROSSREF` 要按**真实引用方向**建：引用方 → 被引方。

    json 的 source/target 是 id 字典序（本例 KP_001 < KP_002，json 里记为 001→002），
    但正文表明是 002 引用 001，故 Neo4j 里必须是 002→001。
    """
    nodes = [
        {"id": "KP_001", "title": "第一章·第一条", "chapter": "第一章", "chapter_title": "",
         "part": "", "part_title": "", "clause": "第一条", "keywords": [],
         "content": "为了保护合法权益，制定本法。", "source": "a.md L1"},
        {"id": "KP_002", "title": "第一章·第二条", "chapter": "第一章", "chapter_title": "",
         "part": "", "part_title": "", "clause": "第二条", "keywords": [],
         "content": "依照本法第一条的规定办理。", "source": "a.md L2"},
    ]
    graph = {"nodes": nodes,
             "edges": [{"source": "KP_001", "target": "KP_002",
                        "types": ["crossref"], "weight": 1.5}]}
    driver = FakeDriver()
    _store(driver).sync(graph)

    cypher, params = driver.find("CREATE (a)-[:CROSSREF")
    assert params["rows"] == [{"source": "KP_002", "target": "KP_001", "weight": 1.5}]  # 方向反过来了
    assert "KP_001" not in cypher                                   # 方向不来自 json 的 source/target


def test_crossref_without_json_edge_is_not_invented():
    """派生出的引用若不在 json 的 crossref 边里，则不建 —— Neo4j 必须是 json 的忠实副本。"""
    nodes = [
        {"id": "KP_001", "title": "t", "chapter": "c", "chapter_title": "", "part": "",
         "part_title": "", "clause": "第一条", "keywords": [], "content": "正文。", "source": "a.md L1"},
        {"id": "KP_002", "title": "t", "chapter": "c", "chapter_title": "", "part": "",
         "part_title": "", "clause": "第二条", "keywords": [],
         "content": "依照本法第一条办理。", "source": "a.md L2"},
    ]
    # json 里没有这条 crossref 边（例如 json 被手工裁剪过）
    driver = FakeDriver()
    result = _store(driver).sync({"nodes": nodes, "edges": []})
    assert result["relationships"] == 0
    assert not any(":CROSSREF" in c for c, _ in driver.calls)


def test_sync_stores_empty_keywords_as_none():
    """Neo4j 不允许空列表作为属性值：空关键词要存 None，否则写入直接失败。"""
    driver = FakeDriver()
    _store(driver).sync(_graph())
    rows = driver.find("MERGE (n:KnowledgePoint")[1]["rows"]
    by_id = {r["id"]: r for r in rows}
    assert by_id["KP_001"]["keywords"] is None      # 空列表 → None
    assert by_id["KP_002"]["keywords"] == ["乙"]


def test_sync_is_full_alignment():
    """全量对齐：先清空旧关系，再删除已不在新图中的节点。"""
    driver = FakeDriver()
    _store(driver).sync(_graph())
    assert any("DELETE r" in c for c, _ in driver.calls)                 # 关系全量重建
    cypher, params = driver.find("DETACH DELETE n")
    assert "NOT n.id IN $ids" in cypher
    assert params["ids"] == ["KP_002", "KP_001"]                         # 保留清单


def test_sync_creates_constraint_first():
    driver = FakeDriver()
    _store(driver).sync(_graph())
    assert "CREATE CONSTRAINT kp_id_unique IF NOT EXISTS" in driver.calls[0][0]


def test_sync_batches_large_graphs():
    """分批提交：避免单事务塞进上万条边。"""
    driver = FakeDriver()
    nodes = [{"id": f"KP_{i:03d}", "title": "t", "chapter": "c", "chapter_title": "",
              "part": "", "part_title": "", "clause": "x", "keywords": [],
              "content": "c", "source": "s"} for i in range(1, 2501)]
    _store(driver).sync({"nodes": nodes, "edges": []})
    node_calls = [c for c, _ in driver.calls if "MERGE (n:KnowledgePoint" in c]
    assert len(node_calls) == 2                                          # 2000 + 500


def test_sync_closes_able():
    driver = FakeDriver()
    store = _store(driver)
    store.close()
    assert driver.closed is True


# ---------- 读取 ----------

def test_load_restores_json_shape():
    driver = FakeDriver(responses={
        "RETURN n.id AS id": [FakeRecord(
            id="KP_001", seq=1, title="第一章·第一条", part=None, part_title=None,
            chapter="第一章", chapter_title="基本规定", clause="第一条",
            content="正文甲", source="a.md L1", keywords=None)],
    })
    graph = _store(driver).load()

    node = graph["nodes"][0]
    assert set(node) == {"id", "title", "chapter", "chapter_title", "part",
                         "part_title", "clause", "keywords", "content", "source"}
    assert node["keywords"] == []                    # None → []，供 BM25/Jaccard 使用
    assert node["part"] == "" and node["part_title"] == ""   # None → ""，下游不拿到 None
    assert graph["edges"] == []                      # 无关系时返回空列表而非报错


def test_load_orders_nodes_by_seq_not_lexicographic():
    """节点必须按 seq 排序：KP_1000 在字典序里排在 KP_999 之前，会打乱 json 顺序。

    边不再依赖 Cypher 排序——四类关系要按 (source,target) 聚合，排序在 Python 里做
    （见 test_load_edges_sorted_like_json）。
    """
    driver = FakeDriver()
    _store(driver).load()
    assert "ORDER BY n.seq" in driver.calls[0][0]


def test_load_regroups_typed_relationships_into_one_edge():
    """四类关系要按 (source,target) 聚合成一条带类型边：types 取并集、weight 取 max。

    这是与 json 1:1 还原的关键（json 里一条边含多类型、只存 max 权重）。
    """
    driver = FakeDriver(responses={
        "RETURN a.id AS source, b.id AS target, type(r) AS rel_type": [
            FakeRecord(source="KP_001", target="KP_002", rel_type="SIBLING", weight=1.5),
            FakeRecord(source="KP_001", target="KP_002", rel_type="KEYWORD", weight=1.0),
            FakeRecord(source="KP_002", target="KP_003", rel_type="SEMANTIC", weight=0.8),
        ],
    })
    edges = _store(driver).load()["edges"]
    assert edges == [
        {"source": "KP_001", "target": "KP_002",
         "types": ["keyword", "sibling"], "weight": 1.5},      # 并集 + max
        {"source": "KP_002", "target": "KP_003",
         "types": ["semantic"], "weight": 0.8},
    ]


def test_load_collapses_bidirectional_relationships():
    """有向的 :CROSSREF 可能双向各一条关系，但 json 只有一条边 —— load() 必须归一化节点对。

    否则边数会变多、与 json 不再等价，recall 也会受影响。
    """
    driver = FakeDriver(responses={
        "RETURN a.id AS source, b.id AS target, type(r) AS rel_type": [
            FakeRecord(source="KP_002", target="KP_001", rel_type="CROSSREF", weight=1.5),
            FakeRecord(source="KP_001", target="KP_002", rel_type="SEMANTIC", weight=0.8),
        ],
    })
    edges = _store(driver).load()["edges"]
    assert edges == [{"source": "KP_001", "target": "KP_002",     # 归一化为 min→max
                      "types": ["crossref", "semantic"], "weight": 1.5}]


def test_load_edges_sorted_like_json():
    """边按 (source,target) 字典序输出 —— 与 graph_build 写 json 的顺序一致，避免并列打破顺序漂移。"""
    driver = FakeDriver(responses={
        "RETURN a.id AS source, b.id AS target, type(r) AS rel_type": [
            FakeRecord(source="KP_009", target="KP_010", rel_type="SIBLING", weight=1.0),
            FakeRecord(source="KP_001", target="KP_002", rel_type="SIBLING", weight=1.0),
        ],
    })
    edges = _store(driver).load()["edges"]
    assert [(e["source"], e["target"]) for e in edges] == [("KP_001", "KP_002"),
                                                          ("KP_009", "KP_010")]


def test_counts_reports_edges_and_relationships():
    """edges 是去重节点对（对齐 json 的 21252），relationships 是关系总数（拆分后更大）。"""
    driver = FakeDriver(next_rows=[FakeRecord(nodes=1465, edges=21252, relationships=30569)])
    assert _store(driver).counts() == {"nodes": 1465, "edges": 21252, "relationships": 30569}
    cypher, _ = driver.find("count(DISTINCT")
    assert "startNode(r).id < endNode(r).id" in cypher      # 去重前先归一化方向


def test_type_counts_lowercases_relationship_types():
    driver = FakeDriver(next_rows=[FakeRecord(rel_type="SIBLING", n=15306),
                                   FakeRecord(rel_type="CROSSREF", n=97)])
    assert _store(driver).type_counts() == {"sibling": 15306, "crossref": 97}


def test_counts_on_empty_db_is_zero():
    assert _store(FakeDriver()).counts() == {"nodes": 0, "edges": 0, "relationships": 0}


# ---------- 同步入口 ----------

def test_sync_from_json_reports_both_sides(monkeypatch, tmp_path):
    """sync_from_json 要同时报告 json 与 Neo4j 计数，便于判断是否一致。"""
    import json

    from app.ingest import sync_graph as mod

    path = tmp_path / "g.json"
    path.write_text(json.dumps(_graph(), ensure_ascii=False), encoding="utf-8")

    driver = FakeDriver(
        next_rows=[FakeRecord(nodes=2, edges=1, relationships=1)],
        responses={"type(r) AS rel_type": [FakeRecord(rel_type="SIBLING", n=1)]},
    )
    monkeypatch.setattr(mod, "Neo4jGraphStore", lambda: Neo4jGraphStore(driver=driver))

    report = mod.sync_from_json(path, verbose=False)
    assert report["json"] == {"nodes": 2, "edges": 1}
    assert report["neo4j"] == {"nodes": 2, "edges": 1, "relationships": 1}
    assert report["written"] == {"nodes": 2, "edges": 1, "relationships": 1}
    assert report["relationships_by_type"] == {"sibling": 1}
