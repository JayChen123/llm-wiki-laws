"""知识库加载与统一检索入口。"""
from __future__ import annotations

from app.config import REFLECT_MAX_ROUNDS, RETRIEVAL_MODE
from app.ingest.graph_store import Neo4jGraphStore
from app.retrieval.embedder import Embedder
from app.retrieval.hybrid_retriever import HybridRetriever
from app.retrieval.keyword_retriever import KeywordRetriever
from app.retrieval.reflective_retriever import ReflectiveRetriever
from app.retrieval.reflector import Reflector
from app.retrieval.vector_retriever import VectorRetriever
from app.retrieval.vector_store import MilvusVectorStore


class KnowledgeBase:
    """加载编译好的知识图谱与 Milvus 向量库，提供混合检索。

    检索链路由 `RETRIEVAL_MODE` 决定：`reflect`（默认）走反思式自主多跳，
    `bfs` 走原固定深度图谱扩展。
    """

    def __init__(self, nodes, graph, hybrid: HybridRetriever, retriever=None, embedder=None):
        self.nodes = nodes
        self.graph = graph
        self.hybrid = hybrid
        self._retriever = retriever if retriever is not None else hybrid
        # 暴露编码器：长期记忆编码复用它，避免重复加载 bge-m3（2.2GB）
        self.embedder = embedder

    @property
    def last_trace(self) -> dict | None:
        """最近一次检索的反思轨迹（仅 reflect 模式有值），供日志与排查使用。"""
        return getattr(self._retriever, "last_trace", None)

    @classmethod
    def load(
            cls,
            embedder: Embedder | None = None,
            store: MilvusVectorStore | None = None,
    ) -> "KnowledgeBase":
        # 图谱从 Neo4j 读（权威源是 knowledge_graph.json，由 pipeline/同步命令写入 Neo4j）。
        # 不做降级：Neo4j 不可用即报错，避免"以为在用图数据库、实际读了本地文件"。
        # 注意变量名不要用 store —— 那个名字留给下面注入的 Milvus 向量库（测试会替换它）
        graph_store = Neo4jGraphStore()
        try:
            graph = graph_store.load()
        finally:
            graph_store.close()
        if not graph["nodes"]:
            raise RuntimeError(
                "Neo4j 里没有知识点：请先运行 `uv run python -m app.ingest.sync_graph` 同步图谱"
            )
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
        retriever = hybrid
        if RETRIEVAL_MODE == "reflect":
            try:
                retriever = ReflectiveRetriever(
                    nodes, graph, hybrid, Reflector(), max_rounds=REFLECT_MAX_ROUNDS
                )
            except Exception as exc:   # 反思链路构建失败则回退到图谱扩展
                print(f"[kb] 反思式检索初始化失败（{exc}），回退为图谱多跳扩展")
        return cls(nodes, graph, hybrid, retriever, embedder=embedder)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        return self._retriever.search(query, top_k=top_k)
