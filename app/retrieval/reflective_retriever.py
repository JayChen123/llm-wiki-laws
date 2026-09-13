"""反思式自主多跳检索：混合召回+基础图谱扩展 → 反思闸门 → 迭代取回被引条文。

链路由 `RETRIEVAL_MODE=reflect`（默认）启用，`bfs` 为对照链路。两者关系是
**超集**：reflect = bfs 的基础扩展结果 + 反思沿引用关系补取的条文。

为什么不是"反思完全替代扩展"：实测过一版纯反思（候选只有引用关系），
recall@8 从 0.9262 掉到 0.8548 —— 掉的全是 sibling/semantic 邻居带来的补充，
而它们不属于"引用关系"。故保留基础扩展，让反思专注它擅长的事：
**在 BFS 因"每跳剪枝 + 只取 expand_k 条"而丢弃的引用条文里，挑出真正相关的**。

四个停止条件：
1. **已收集条文没有可跟进的引用关系** → 不调用 LLM 直接停止（最常见）
2. 反思判定"已充分"或未给出要取的条文
3. 已达最大轮数 `max_rounds`（防失控）
4. 反思要取的条文已全部在上下文中（防死循环）

知识本体仍是 wiki + 图谱：这里只决定"再取哪几条法条进上下文"，
不切块、不把向量当知识载体。
"""
from __future__ import annotations

from app.config import EXPAND_DECAY, REFLECT_MAX_ROUNDS, TOP_K
from app.ingest.graph_build import CROSSREF_WEIGHT, crossref_map
from app.retrieval.hybrid_retriever import HybridRetriever
from app.retrieval.reflector import Reflector


def _as_result(node: dict, score: float) -> dict:
    """把图谱节点转成与 HybridRetriever 一致的检索结果结构。"""
    return {
        "id": node["id"],
        "title": node["title"],
        "chapter": node["chapter"],
        "content": node["content"],
        "keywords": node.get("keywords", []),
        "source": node.get("source", ""),
        "score": round(score, 4),
    }


class ReflectiveRetriever:
    """反思式多跳检索器。`last_trace` 保留本轮决策，供日志与排查使用。"""

    def __init__(
            self,
            nodes: list[dict],
            graph: dict,
            hybrid: HybridRetriever,
            reflector: Reflector,
            max_rounds: int = REFLECT_MAX_ROUNDS,
            crossrefs: dict[str, list[str]] | None = None,
    ):
        self.id2node = {n["id"]: n for n in nodes}
        self.hybrid = hybrid
        self.reflector = reflector
        self.max_rounds = max_rounds
        # 引用关系与图谱 crossref 边同源（crossref_map），避免两处规则不一致
        self.crossrefs = crossrefs if crossrefs is not None else crossref_map(nodes)
        self.last_trace: dict = {"mode": "reflect", "seeds": [], "expanded": [],
                                 "fetched": [], "rounds": 0, "reasons": []}

    def _candidate_ids(self, collected: list[dict], seen: set[str]) -> list[str]:
        """本轮候选被引条文：已收集条文引用过、且尚未在上下文中的条文（保序去重）。"""
        candidates: list[str] = []
        for item in collected:
            for target in self.crossrefs.get(item["id"], []):
                if target not in seen and target not in candidates and target in self.id2node:
                    candidates.append(target)
        return candidates

    def _brief(self, node_id: str, round_no: int) -> dict:
        """日志用的节点摘要：只记 id/标题/分数（+第几轮补取），不记正文。"""
        node = self.id2node.get(node_id, {})
        return {"id": node_id, "title": node.get("title", ""), "round": round_no}

    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        # 基础上下文 = 与 bfs 模式完全相同的"种子 + 图谱扩展"（含 crossref 边），
        # 保证 reflect 的上下文是 bfs 的超集，不会因换链路丢掉 sibling/semantic 补充
        collected: list[dict] = self.hybrid.search(query, top_k=top_k, expand=True)
        seen: set[str] = {r["id"] for r in collected}
        scores: dict[str, float] = {r["id"]: r.get("score", 0.0) for r in collected}
        reasons: list[str] = []
        fetched_brief: list[dict] = []      # 反思补取的条文（日志用）
        rounds = 0

        while rounds < self.max_rounds:
            candidate_ids = self._candidate_ids(collected, seen)
            if not candidate_ids:
                reasons.append("无可跟进的引用关系，停止")     # 不调 LLM，直接停
                break
            candidates = [self.id2node[i] for i in candidate_ids]
            result = self.reflector.reflect(query, collected, candidates)
            reasons.append(f"[第{rounds + 1}轮] {result.reason}")
            if result.sufficient or not result.fetch_ids:
                break
            fetched = [i for i in result.fetch_ids if i not in seen]
            if not fetched:
                reasons.append("反思要取的条文均已在上下文中，停止")
                break
            for cid in fetched:
                fetched_brief.append(self._brief(cid, rounds + 1))
                parent = next(
                    (item for item in collected if cid in self.crossrefs.get(item["id"], [])),
                    None,
                )
                # 名义分数：沿用原 BFS 的公式（父分数 × 引用权重 × 衰减^轮次），
                # 仅供展示与排序参考，不是 RRF 分数
                base = scores.get(parent["id"], 0.0) if parent else 0.0
                score = base * CROSSREF_WEIGHT * (EXPAND_DECAY ** (rounds + 1))
                collected.append(_as_result(self.id2node[cid], score))
                seen.add(cid)
                scores[cid] = score
            rounds += 1

        # seeds / expanded 来自基础扩展那一步（hybrid 记录的），fetched 是反思补取的
        base_trace = getattr(self.hybrid, "last_trace", None) or {}
        self.last_trace = {
            "mode": "reflect",
            "seeds": base_trace.get("seeds", []),
            "expanded": base_trace.get("expanded", []),
            "fetched": fetched_brief,
            "rounds": rounds,
            "reasons": reasons[:4],
        }
        return collected
