"""bge-m3 向量检索：查询向量在 Milvus 中做余弦检索。"""
from __future__ import annotations

from app.retrieval.embedder import Embedder
from app.retrieval.vector_store import MilvusVectorStore


class VectorRetriever:
    """用 bge-m3 编码 query，到 Milvus 检索知识点向量，再映射回图谱节点。"""

    def __init__(self, nodes: list[dict], store: MilvusVectorStore, embedder: Embedder):
        self.nodes = {n["id"]: n for n in nodes}
        self.store = store
        self.embedder = embedder

    def search(self, query: str, top_k: int = 5) -> list[tuple[dict, float]]:
        qv = self.embedder.encode([query])[0]
        results: list[tuple[dict, float]] = []
        for kp_id, score in self.store.search(qv, top_k=top_k):
            node = self.nodes.get(kp_id)
            if node is not None:      # 集合中残留当前图谱已不存在的知识点时跳过
                results.append((node, score))
        return results
