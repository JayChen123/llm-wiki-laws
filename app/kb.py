"""知识库加载与统一检索入口。"""
from __future__ import annotations

import numpy as np

from app.config import GRAPH_PATH, VECTOR_CACHE_PATH
from app.ingest.graph_build import load_graph
from app.retrieval.embedder import Embedder
from app.retrieval.hybrid_retriever import HybridRetriever
from app.retrieval.keyword_retriever import KeywordRetriever
from app.retrieval.vector_retriever import VectorRetriever


class KnowledgeBase:
    """加载编译好的知识图谱与向量缓存，提供混合检索。"""

    def __init__(self, nodes, graph, hybrid: HybridRetriever):
        self.nodes = nodes
        self.graph = graph
        self.hybrid = hybrid

    @classmethod
    def load(cls, embedder: Embedder | None = None) -> "KnowledgeBase":
        graph = load_graph(GRAPH_PATH)
        nodes = graph["nodes"]
        vectors = (
            np.load(VECTOR_CACHE_PATH) if VECTOR_CACHE_PATH.exists() else None
        )
        if embedder is None:
            embedder = Embedder()
        kw = KeywordRetriever(nodes)
        if vectors is not None:
            vec = VectorRetriever(nodes, vectors, embedder)
        else:
            # 无向量缓存时退化为仅关键词检索的混合器（用于未构建语义边的场景）
            vec = None
        hybrid = HybridRetriever(nodes, graph, kw, vec)
        return cls(nodes, graph, hybrid)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        return self.hybrid.search(query, top_k=top_k)
