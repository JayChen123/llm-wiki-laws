"""知识库加载与统一检索入口。"""
from __future__ import annotations

from app.config import GRAPH_PATH
from app.ingest.graph_build import load_graph
from app.retrieval.embedder import Embedder
from app.retrieval.hybrid_retriever import HybridRetriever
from app.retrieval.keyword_retriever import KeywordRetriever
from app.retrieval.vector_retriever import VectorRetriever
from app.retrieval.vector_store import MilvusVectorStore


class KnowledgeBase:
    """加载编译好的知识图谱与 Milvus 向量库，提供混合检索。"""

    def __init__(self, nodes, graph, hybrid: HybridRetriever):
        self.nodes = nodes
        self.graph = graph
        self.hybrid = hybrid

    @classmethod
    def load(
            cls,
            embedder: Embedder | None = None,
            store: MilvusVectorStore | None = None,
    ) -> "KnowledgeBase":
        graph = load_graph(GRAPH_PATH)
        nodes = graph["nodes"]
        if embedder is None:
            embedder = Embedder()
        kw = KeywordRetriever(nodes)
        vec = None
        try:
            store = store if store is not None else MilvusVectorStore()
            store.count()          # 探活：集合不存在/服务不可达都会在此抛出
            vec = VectorRetriever(nodes, store, embedder)
        except Exception as exc:   # 向量库不可用则退化为纯关键词检索
            print(f"[kb] Milvus 向量库不可用（{exc}），退化为纯关键词检索")
        hybrid = HybridRetriever(nodes, graph, kw, vec)
        return cls(nodes, graph, hybrid)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        return self.hybrid.search(query, top_k=top_k)
