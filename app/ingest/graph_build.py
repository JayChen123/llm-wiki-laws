"""构建知识点关联图谱（节点 + 带类型边）。

边类型：
- sibling ：同一章节下的一级条款（结构关联）
- keyword ：关键词重合（Jaccard 相似度）
- semantic：bge-m3 语义相似度（余弦）
- crossref：正文中的"第X条"交叉引用（同法条文间，权重高于结构边）
"""
from __future__ import annotations

import json
import re

import numpy as np

from app.config import GRAPH_PATH
from app.ingest.knowledge_extract import CN_NUM_CHARS, KnowledgePoint
from app.retrieval.embedder import Embedder

# 正文交叉引用：如"依照本法第三十九条……"（条文号用中文数字，含"千/零"，
# 否则民法典"第一千一百六十五条"这类跨引用边建不出来）
_ART_REF_RE = re.compile(rf"第[{CN_NUM_CHARS}]+条")
# 交叉引用是直接的法条间链接，权重高于章节结构边，保证图谱扩展优先带到被引用条文；
# 反思式多跳（reflector）给取回的条文计名义分数时也复用它，故对外公开
CROSSREF_WEIGHT = 1.5
_CROSSREF_WEIGHT = CROSSREF_WEIGHT


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def semantic_text(point: KnowledgePoint) -> str:
    """语义编码文本：标题（含编名）+ 正文前 200 字。

    语义边与向量库写入必须共用这一模板，否则图谱语义与检索语义不一致；
    需要编码时统一走此函数，避免两处各写一份模板。
    """
    return f"{point.title}\n{point.content[:200]}"


def crossref_map(nodes: list[dict]) -> dict[str, list[str]]:
    """条文正文里的"第X条"引用 → 本库同法条文 id 列表。

    **图谱构建（建 crossref 边）与反思式多跳（挑候选被引条文）共用此函数**，
    避免同一套引用解析规则写两遍而产生不一致（本项目已在中文数字字符集上踩过
    一次"规则重复导致漏切"的坑）。

    **仅同法内**：跨法引用不解析（与 crossref 边"跨法不建边"的策略一致）。
    返回 `{node_id: [被引 node_id, ...]}`，按正文出现顺序去重，不含自引用。
    """
    by_law: dict[str, list[dict]] = {}
    for node in nodes:
        by_law.setdefault(node["source"].split(" L")[0], []).append(node)

    result: dict[str, list[str]] = {}
    for law_nodes in by_law.values():
        ref_to_id: dict[str, str] = {}
        for node in law_nodes:
            m = re.search(rf"第[{CN_NUM_CHARS}]+条$", node["clause"])
            if m:
                ref_to_id.setdefault(m.group(0), node["id"])
        for node in law_nodes:
            targets: list[str] = []
            for ref in _ART_REF_RE.findall(node["content"]):
                target = ref_to_id.get(ref)
                if target and target != node["id"] and target not in targets:
                    targets.append(target)
            if targets:
                result[node["id"]] = targets
    return result


def build_graph(
        points: list[KnowledgePoint],
        embedder: Embedder | None = None,
        vectors=None,
        keyword_threshold: float = 0.3,
        semantic_threshold: float = 0.75,
) -> dict:
    """构建知识图谱，返回 {nodes, edges}。

    `vectors` 可直接传入已算好的知识点向量（与 `points` 同序），避免重复编码；
    未传且给了 `embedder` 时才自行编码。
    """
    nodes = [
        {
            "id": p.id,
            "title": p.title,
            "chapter": p.chapter,
            "chapter_title": p.chapter_title,
            "part": p.part,
            "part_title": p.part_title,
            "clause": p.clause,
            "keywords": p.keywords,
            "content": p.content,
            "source": p.source,
        }
        for p in points
    ]
    id2idx = {p.id: i for i, p in enumerate(points)}

    edges: dict[tuple[str, str], dict] = {}

    def add_edge(u: str, v: str, etype: str, weight: float) -> None:
        key = (min(u, v), max(u, v))
        entry = edges.setdefault(key, {"types": [], "weight": 0.0})
        if etype not in entry["types"]:
            entry["types"].append(etype)
        entry["weight"] = max(entry["weight"], weight)

    # 1) 结构关联：同章节内的一级条款互为兄弟。
    #    分组键必须是 (编, 章)：民法典 7 编各自从"第一章"重新起算，
    #    只按章号分组会把不同编的同号章并成一个大团（曾产生 3 万余条假边）。
    by_chapter: dict[tuple[str, str], list[KnowledgePoint]] = {}
    for p in points:
        by_chapter.setdefault((p.part, p.chapter), []).append(p)
    for items in by_chapter.values():
        clauses = [p for p in items if p.clause]
        for i in range(len(clauses)):
            for j in range(i + 1, len(clauses)):
                add_edge(clauses[i].id, clauses[j].id, "sibling", 1.0)

    # 2) 关键词重合
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            jac = _jaccard(points[i].keywords, points[j].keywords)
            if jac >= keyword_threshold:
                add_edge(points[i].id, points[j].id, "keyword", round(jac, 4))

    # 3) 语义相似度（可选，需要向量或编码器）
    if vectors is None and embedder is not None:
        vectors = embedder.encode([semantic_text(p) for p in points])
    if vectors is not None:
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                cos = float(np.dot(vectors[i], vectors[j]))
                if cos >= semantic_threshold:
                    add_edge(points[i].id, points[j].id, "semantic", round(cos, 4))

    # 4) 法条交叉引用：正文中"第X条"引用同法其他条文 → crossref 边。
    #    引用是直接的法条间链接（如 65 条引 39 条、46 条引 38 条），跨法不加边。
    #    解析逻辑见 crossref_map()，与反思式多跳共用同一份实现。
    for source_id, targets in crossref_map(nodes).items():
        for target_id in targets:
            add_edge(source_id, target_id, "crossref", _CROSSREF_WEIGHT)

    edge_list = [
        {
            "source": s,
            "target": t,
            "types": sorted(entry["types"]),
            "weight": entry["weight"],
        }
        for (s, t), entry in sorted(edges.items())
    ]
    return {"nodes": nodes, "edges": edge_list}


def save_graph(graph: dict, path=GRAPH_PATH) -> None:
    path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")


def load_graph(path=GRAPH_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
