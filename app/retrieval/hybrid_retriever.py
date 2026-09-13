"""混合检索：关键词(BM25) + 向量(bge-m3) 通过 RRF 融合，并沿关联图扩展。"""
from __future__ import annotations

from app.retrieval.keyword_retriever import KeywordRetriever
from app.retrieval.vector_retriever import VectorRetriever

RRF_K = 60  # RRF 常数


class HybridRetriever:
    def __init__(
            self,
            nodes: list[dict],
            graph: dict,
            keyword_retriever: KeywordRetriever,
            vector_retriever: VectorRetriever,
            expand_k: int = 3,
            expand_hops: int = 3,
            expand_decay: float = 0.5,
    ):
        self.nodes = nodes
        self.graph = graph
        self.id2node = {n["id"]: n for n in nodes}
        self.keyword_retriever = keyword_retriever
        self.vector_retriever = vector_retriever
        self.expand_k = expand_k
        self.expand_hops = expand_hops
        self.expand_decay = expand_decay
        self._adjacency: dict[str, list[tuple[str, float]]] | None = None

    def _adjacency_map(self) -> dict[str, list[tuple[str, float]]]:
        """邻接表：id -> [(邻居id, 权重)]，按权重降序。"""
        if self._adjacency is not None:
            return self._adjacency
        adj: dict[str, list[tuple[str, float]]] = {n["id"]: [] for n in self.nodes}
        for e in self.graph["edges"]:
            adj[e["source"]].append((e["target"], e["weight"]))
            adj[e["target"]].append((e["source"], e["weight"]))
        for k in adj:
            adj[k].sort(key=lambda x: -x[1])
        self._adjacency = adj
        return adj

    def _bfs_expand(
            self,
            seeds: list[str],
            seed_scores: dict[str, float],
            hops: int,
            per_hop_k: int,
            decay: float,
    ) -> tuple[dict[str, float], dict[str, int]]:
        """从种子节点沿关联边 BFS 扩展多跳候选。

        节点分数 = 上层种子分数 × 边权重 × decay^hop；每跳只保留 Top
        per_hop_k 个候选，避免遍历爆炸。

        返回 `(分数, 跳数)`：跳数用于日志里说明"这条扩展是从第几跳带进来的"。
        """
        adj = self._adjacency_map()
        visited = set(seeds)
        frontier = [(nid, seed_scores[nid]) for nid in seeds]
        expanded: dict[str, float] = {}
        hops_of: dict[str, int] = {}
        for hop in range(1, hops + 1):
            candidates: dict[str, float] = {}
            for nid, score in frontier:
                for nb, w in adj[nid]:
                    if nb in visited:
                        continue
                    s = score * w * (decay ** hop)
                    if nb not in candidates or s > candidates[nb]:
                        candidates[nb] = s
            top = sorted(candidates.items(), key=lambda kv: -kv[1])[:per_hop_k]
            if not top:
                break
            frontier = []
            for nb, s in top:
                expanded[nb] = s
                hops_of[nb] = hop
                visited.add(nb)
                frontier.append((nb, s))
        return expanded, hops_of

    def _brief(self, node_id: str, score: float, hop: int | None = None) -> dict:
        """日志用的节点摘要：只记 id/标题/分数（+跳数），不记正文。"""
        node = self.id2node.get(node_id, {})
        brief = {"id": node_id, "title": node.get("title", ""), "score": round(float(score), 4)}
        if hop is not None:
            brief["hop"] = hop
        return brief

    def search(self, query: str, top_k: int = 5, expand: bool = True) -> list[dict]:
        """返回 [ {id,title,chapter,content,score,source} ]，已排序。

        `expand=False` 只返回 RRF 融合后的种子（供反思式多跳接管后续检索），
        不再做固定深度 BFS 图谱扩展。
        """
        kw = self.keyword_retriever.search(query, top_k=top_k * 3)
        vec = (
            self.vector_retriever.search(query, top_k=top_k * 3)
            if self.vector_retriever is not None
            else []
        )

        rrf: dict[str, float] = {}
        for rank, (node, _) in enumerate(kw):
            rrf[node["id"]] = rrf.get(node["id"], 0.0) + 1.0 / (RRF_K + rank + 1)
        for rank, (node, _) in enumerate(vec):
            rrf[node["id"]] = rrf.get(node["id"], 0.0) + 1.0 / (RRF_K + rank + 1)

        ranked_ids = sorted(rrf, key=lambda k: -rrf[k])[:top_k]
        base = {nid: rrf[nid] for nid in ranked_ids}

        # 图谱多跳扩展：从命中种子沿关联边 BFS 最多 N 跳，分数逐跳衰减
        if expand:
            expanded_scores, expanded_hops = self._bfs_expand(
                ranked_ids, base, self.expand_hops, self.expand_k, self.expand_decay
            )
            extra_ids = sorted(expanded_scores, key=lambda k: -expanded_scores[k])[
                : self.expand_k
            ]
        else:
            expanded_scores, expanded_hops = {}, {}
            extra_ids = []

        # 记录本次检索的构成，供日志区分"检索命中"与"图谱扩展带进来的"知识点
        self.last_trace = {
            "mode": "bfs" if expand else "seeds_only",
            "seeds": [self._brief(nid, base.get(nid, 0.0)) for nid in ranked_ids],
            "expanded": [self._brief(nid, expanded_scores.get(nid, 0.0), expanded_hops.get(nid))
                         for nid in extra_ids],
        }

        ordered = ranked_ids + extra_ids
        results = []
        for i, nid in enumerate(ordered):
            node = self.id2node[nid]
            score = base.get(nid, 0.0) or expanded_scores.get(nid, 0.0)
            results.append(
                {
                    "id": node["id"],
                    "title": node["title"],
                    "chapter": node["chapter"],
                    "content": node["content"],
                    "keywords": node["keywords"],
                    "source": node.get("source", ""),
                    "score": round(score, 4),
                }
            )
        return results
