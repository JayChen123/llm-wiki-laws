"""bge-m3 向量检索（余弦相似度）。"""
from __future__ import annotations

import numpy as np

from app.retrieval.embedder import Embedder


class VectorRetriever:
    """用 bge-m3 编码 query，与预计算的知识点向量算余弦相似度。"""

    def __init__(self, nodes: list[dict], vectors: np.ndarray, embedder: Embedder):
        self.nodes = nodes
        self.vectors = np.asarray(vectors, dtype=np.float32)
        self.embedder = embedder

    def search(self, query: str, top_k: int = 5) -> list[tuple[dict, float]]:
        qv = self.embedder.encode([query])[0]
        cos = self.vectors @ qv
        order = cos.argsort()[::-1][:top_k]
        return [(self.nodes[int(i)], float(cos[int(i)])) for i in order]
