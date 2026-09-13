"""知识图谱的 Neo4j 存储（图数据库副本）。

**权威源仍是 `knowledge_graph.json`**：`pipeline` 先写 json（可 git diff、可审计），
再由 `sync()` 把它同步进 Neo4j；应用（`kb.py`）从 Neo4j 读图。

为什么这样分工：json 承担"可读可审计、可版本化"，Neo4j 承担"图查询与遍历"。
json 被改动后重跑同步即可 —— `uv run python -m app.ingest.sync_graph`。

建模取舍：
- 节点标签 `KnowledgePoint`，属性与 json 节点字段一一对应（含条文正文 `content`，
  因为反思候选 `crossref_map()` 要解析正文里的"第X条"引用）。
- 边按 json 的**四类边拆成四种关系类型**：`:SIBLING` / `:KEYWORD` / `:SEMANTIC` / `:CROSSREF`，
  便于用 Cypher 直接按类型遍历（如只走引用链 `-[:CROSSREF]->`）。
  **一条 json 边含 N 个类型时拆成 N 条关系**，故关系数（30569）大于边数（21252），
  其中 7097 条边是多类型边。
- **`:CROSSREF` 的方向在同步时派生**：json 的 `source/target` 是 id 字典序（97 条 crossref 边全部
  source<target），**不是"谁引用谁"**。故同步时用 `crossref_map(nodes)`（与反思候选同一份实现）从
  正文解析真实方向，按 `引用方 -[:CROSSREF]-> 被引方` 建关系，权重取 json 边的合并权重；
  且只取 json 里确实存在的 crossref 节点对，保证 Neo4j 仍是 json 的忠实副本。
  这样可直接做**有向引用链遍历**（`MATCH (a)-[:CROSSREF*1..3]->(b)`）而无需改动 json 格式。
  方向只对 `:CROSSREF` 有意义；其余三类语义上无向，仍按 json 的 min/max 建。
- **节点对归一化**：因为 `:CROSSREF` 有向，同一对节点可能双向各有一条关系，
  而 json 一条边只对应一个 `(min, max)` 对。故 `load()` 与 `counts()` 都按 `(min(id), max(id))`
  归一化后再聚合/去重，保证边数与 json 恒等（21252），recall 不受影响。
- ⚠️ **权重口径**：json 每条边只存一个 `weight`（`graph_build` 对各类型取 max），
  **per-type 权重在源里就不存在**，因此每种类型的关系都携带这个合并权重。
  `load()` 按 (source, target) 分组、types 取并集、weight 取 max，**与 json 1:1 还原**
  （已用脚本验证逐条一致），这是检索质量不变的前提。
  若日后需要真正的 per-type 权重，得改 `graph_build` 的 json 格式（属独立的格式变更）。
- `seq` 存 id 的数字序号：id 形如 KP_1000 与 KP_999，字典序与数值序不一致，
  用 seq 排序才能还原 json 的原始节点顺序。
"""
from __future__ import annotations

from app.config import NEO4J_DATABASE, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from app.ingest.graph_build import CROSSREF_WEIGHT, crossref_map

# 批量提交大小：一次 UNWIND 不要塞太多，避免单事务过大
_BATCH = 2000

# 节点属性（与 json 节点字段一致）
_NODE_FIELDS = ("title", "part", "part_title", "chapter", "chapter_title",
                "clause", "content", "source", "keywords")

# json 里的边类型 → Neo4j 关系类型（关系类型不能用参数传入，只能拼进 Cypher；
# 这里取自**固定常量**而非用户数据，无注入风险）
_JSON_TYPE_TO_REL = {
    "sibling": "SIBLING",
    "keyword": "KEYWORD",
    "semantic": "SEMANTIC",
    "crossref": "CROSSREF",
}

_CONSTRAINT = (
    "CREATE CONSTRAINT kp_id_unique IF NOT EXISTS "
    "FOR (n:KnowledgePoint) REQUIRE n.id IS UNIQUE"
)


def _seq_of(kp_id: str) -> int:
    """从 KP_001 这类 id 取出数字序号（用于还原 json 的节点顺序）。"""
    digits = "".join(c for c in kp_id if c.isdigit())
    return int(digits) if digits else 0


class Neo4jGraphStore:
    """图谱的 Neo4j 读写。`driver` 可注入，便于离线单测用假 driver。"""

    def __init__(
            self,
            uri: str = NEO4J_URI,
            user: str = NEO4J_USER,
            password: str = NEO4J_PASSWORD,
            database: str = NEO4J_DATABASE,
            driver=None,
    ):
        self.database = database
        if driver is not None:
            self.driver = driver
        else:
            from neo4j import GraphDatabase
            self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self.driver.close()

    def _run(self, cypher: str, **params):
        with self.driver.session(database=self.database) as session:
            result = session.run(cypher, **params)
            return list(result)

    # ---------- 结构 ----------

    def ensure_schema(self) -> None:
        """建唯一约束：既保证 id 唯一，也让 MERGE 走索引（否则 1465 次 MERGE 会退化成全表扫）。"""
        self._run(_CONSTRAINT)

    # ---------- 写入（全量对齐） ----------

    def sync(self, graph: dict) -> dict:
        """把 json 图谱全量同步到 Neo4j，返回写入计数。

        全量对齐：先清空旧关系、按新图重建，最后删除已不在新图中的节点
        （知识库缩减时不会留下陈旧节点）。这是**离线批处理**，不做事务级原子性。
        """
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        self.ensure_schema()
        # 关系全量重建：比逐条 diff 简单且保证与 json 完全一致。
        # 不带类型过滤，顺带清掉旧模型遗留的 :RELATED 关系，便于平滑迁移。
        self._run("MATCH (:KnowledgePoint)-[r]->(:KnowledgePoint) DELETE r")

        node_rows = [
            {
                "id": n["id"],
                "seq": _seq_of(n["id"]),
                # Neo4j 不允许空列表作为属性值：空关键词存 None，读回时再还原成 []
                "keywords": list(n.get("keywords") or []) or None,
                **{f: n.get(f) for f in _NODE_FIELDS if f != "keywords"},
            }
            for n in nodes
        ]
        for batch in _chunks(node_rows, _BATCH):
            self._run(
                "UNWIND $rows AS row "
                "MERGE (n:KnowledgePoint {id: row.id}) "
                "SET n.seq = row.seq, n.title = row.title, n.part = row.part, "
                "    n.part_title = row.part_title, n.chapter = row.chapter, "
                "    n.chapter_title = row.chapter_title, n.clause = row.clause, "
                "    n.content = row.content, n.source = row.source, n.keywords = row.keywords",
                rows=batch,
            )

        # CROSSREF 的方向从正文解析（json 侧的 source/target 是 id 字典序，方向无意义）。
        # 只取 json 里确实存在 crossref 的节点对，保证 Neo4j 仍是 json 的忠实副本。
        crossref_pairs = {
            (min(e["source"], e["target"]), max(e["source"], e["target"])): float(e.get("weight", 0.0))
            for e in edges if "crossref" in (e.get("types") or [])
        }
        crossref_rows = [
            {"source": src, "target": tgt,
             "weight": crossref_pairs.get((min(src, tgt), max(src, tgt)), CROSSREF_WEIGHT)}
            for src, targets in crossref_map(nodes).items()
            for tgt in targets
            if (min(src, tgt), max(src, tgt)) in crossref_pairs
        ]

        # 按四类边分别建关系：一条多类型边会建出多条关系
        edge_count = 0
        for json_type, rel_type in _JSON_TYPE_TO_REL.items():
            if json_type == "crossref":
                rows = crossref_rows          # 有向：引用方 → 被引方
            else:
                rows = [
                    {"source": e["source"], "target": e["target"],
                     "weight": float(e.get("weight", 0.0))}
                    for e in edges if json_type in (e.get("types") or [])
                ]
            for batch in _chunks(rows, _BATCH):
                self._run(
                    "UNWIND $rows AS row "
                    "MATCH (a:KnowledgePoint {id: row.source}) "
                    "MATCH (b:KnowledgePoint {id: row.target}) "
                    f"CREATE (a)-[:{rel_type} {{weight: row.weight}}]->(b)",
                    rows=batch,
                )
            edge_count += len(rows)

        # 清理陈旧节点（DETACH 会一并删掉它的关系）
        self._run(
            "MATCH (n:KnowledgePoint) WHERE NOT n.id IN $ids DETACH DELETE n",
            ids=[n["id"] for n in nodes],
        )
        return {"nodes": len(node_rows), "edges": len(edges),
                "relationships": edge_count}

    # ---------- 读取 ----------

    def load(self) -> dict:
        """读回 `{"nodes": [...], "edges": [...]}`，字段与 json 完全一致。

        节点按 seq 排序、边按 (source, target) 排序 —— 与 json 里的原始顺序一致，
        避免检索时的并列打破顺序发生变化而影响 recall 对比。
        """
        node_rows = self._run(
            "MATCH (n:KnowledgePoint) RETURN n.id AS id, n.seq AS seq, n.title AS title, "
            "  n.part AS part, n.part_title AS part_title, n.chapter AS chapter, "
            "  n.chapter_title AS chapter_title, n.clause AS clause, n.content AS content, "
            "  n.source AS source, n.keywords AS keywords ORDER BY n.seq"
        )
        nodes = [
            {
                "id": r["id"],
                "title": r["title"] or "",
                "chapter": r["chapter"] or "",
                "chapter_title": r["chapter_title"] or "",
                "part": r["part"] or "",
                "part_title": r["part_title"] or "",
                "clause": r["clause"] or "",
                "keywords": list(r["keywords"] or []),      # None → []
                "content": r["content"] or "",
                "source": r["source"] or "",
            }
            for r in node_rows
        ]

        # 四类关系分别读回，再按 (source, target) 聚合成 json 的"一条带类型边"
        rel_rows = self._run(
            "MATCH (a:KnowledgePoint)-[r]->(b:KnowledgePoint) "
            "RETURN a.id AS source, b.id AS target, type(r) AS rel_type, r.weight AS weight"
        )
        grouped: dict[tuple[str, str], dict] = {}
        for r in rel_rows:
            # **归一化节点对**：:CROSSREF 是有向的，同一对节点可能双向各有一条关系，
            # 而 json 只有一条（min/max 归一）。不归一化会让边数变多、与 json 不再等价。
            key = (min(r["source"], r["target"]), max(r["source"], r["target"]))
            entry = grouped.setdefault(key, {"types": set(), "weight": 0.0})
            entry["types"].add(str(r["rel_type"]).lower())      # SIBLING → sibling
            entry["weight"] = max(entry["weight"], float(r["weight"] or 0.0))
        # 按 (source, target) 字典序输出，与 graph_build 写 json 时的 sorted(edges) 一致
        edges = [
            {"source": s, "target": t,
             "types": sorted(v["types"]),                       # json 里的 types 也是排序过的
             "weight": v["weight"]}
            for (s, t), v in sorted(grouped.items())
        ]
        return {"nodes": nodes, "edges": edges}

    def counts(self) -> dict:
        """当前图规模：`edges` 为去重后的节点对数（对齐 json），`relationships` 为关系总数。

        拆分四类关系后，一条多类型边对应多条关系，故 relationships ≥ edges。
        """
        rows = self._run(
            "MATCH (n:KnowledgePoint) WITH count(n) AS nodes "
            "MATCH (:KnowledgePoint)-[r]->(:KnowledgePoint) "
            "RETURN nodes, count(r) AS relationships, "
            # 节点对去重前先归一化方向（startNode/endNode 谁大谁小不确定）
            "  count(DISTINCT CASE WHEN startNode(r).id < endNode(r).id "
            "         THEN [startNode(r).id, endNode(r).id] "
            "         ELSE [endNode(r).id, startNode(r).id] END) AS edges"
        )
        if not rows:
            return {"nodes": 0, "edges": 0, "relationships": 0}
        row = rows[0]
        return {"nodes": int(row["nodes"]), "edges": int(row["edges"]),
                "relationships": int(row["relationships"])}

    def type_counts(self) -> dict[str, int]:
        """按关系类型统计（用于核对四类边分布）。"""
        rows = self._run(
            "MATCH (:KnowledgePoint)-[r]->(:KnowledgePoint) "
            "RETURN type(r) AS rel_type, count(r) AS n"
        )
        return {str(r["rel_type"]).lower(): int(r["n"]) for r in rows}


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]
