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
from app.ingest.knowledge_extract import KnowledgePoint
from app.retrieval.embedder import Embedder

# 正文交叉引用：如"依照本法第三十九条……"（条文号用中文数字，含"零"）
_ART_REF_RE = re.compile(r"第[一二三四五六七八九十百零]+条")
# 交叉引用是直接的法条间链接，权重高于章节结构边，保证图谱扩展优先带到被引用条文
_CROSSREF_WEIGHT = 1.5


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def build_graph(
        points: list[KnowledgePoint],
        embedder: Embedder | None = None,
        keyword_threshold: float = 0.3,
        semantic_threshold: float = 0.75,
) -> dict:
    """构建知识图谱，返回 {nodes, edges}。"""
    nodes = [
        {
            "id": p.id,
            "title": p.title,
            "chapter": p.chapter,
            "chapter_title": p.chapter_title,
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

    # 1) 结构关联：同章节内的一级条款互为兄弟
    by_chapter: dict[str, list[KnowledgePoint]] = {}
    for p in points:
        by_chapter.setdefault(p.chapter, []).append(p)
    for chapter, items in by_chapter.items():
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

    # 3) 语义相似度（可选，需要向量编码器）
    if embedder is not None:
        texts = [f"{p.title}\n{p.content[:200]}" for p in points]  # 用知识点的标题和内容的前200字作用语义编码
        vecs = embedder.encode(texts)
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                cos = float(np.dot(vecs[i], vecs[j]))
                if cos >= semantic_threshold:
                    add_edge(points[i].id, points[j].id, "semantic", round(cos, 4))

    # 4) 法条交叉引用：正文中"第X条"引用同法其他条文 → crossref 边。
    #    引用是直接的法条间链接（如 65 条引 39 条、46 条引 38 条），跨法不加边。
    by_law: dict[str, list[KnowledgePoint]] = {}
    for p in points:
        by_law.setdefault(p.source.split(" L")[0], []).append(p)
    for pts in by_law.values():
        ref_to_id: dict[str, str] = {}
        for p in pts:
            m = re.search(r"第[一二三四五六七八九十百零]+条$", p.clause)
            if m:
                ref_to_id.setdefault(m.group(0), p.id)
        for p in pts:
            for ref in _ART_REF_RE.findall(p.content):
                target = ref_to_id.get(ref)
                if target and target != p.id:
                    add_edge(p.id, target, "crossref", _CROSSREF_WEIGHT)

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
