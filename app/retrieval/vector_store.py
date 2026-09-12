"""Milvus 向量库：知识点向量的存储与检索。

取代早期的 `wiki/knowledge_vectors.npy` 文件缓存。文件缓存只按"文件是否存在"
判断命中，知识库重建后极易与实际节点集不一致却静默生效（曾出现图谱 1238 节点、
缓存却是 205 行的组合，`VectorRetriever` 不报错，只是永远只在前 205 个节点里排序）。
因此这里的写入采用**全量对齐 + 清理陈旧主键**，使集合内容始终等于当前知识库。
"""
from __future__ import annotations

from app.config import (
    EMBED_DIM,
    MILVUS_COLLECTION,
    MILVUS_HOST,
    MILVUS_INDEX,
    MILVUS_METRIC,
    MILVUS_PORT,
)

# Milvus query/delete 的批量上限；知识点规模远小于此，超过需改分页
_QUERY_LIMIT = 16384


class MilvusVectorStore:
    """pymilvus `MilvusClient` 封装的向量存取（主键为知识点 id）。

    `client` 可注入，供离线单测使用假客户端，避免依赖真实 Milvus 服务。
    """

    def __init__(
            self,
            host: str = MILVUS_HOST,
            port: str | int = MILVUS_PORT,
            collection: str = MILVUS_COLLECTION,
            dim: int = EMBED_DIM,
            client=None,
    ):
        self.collection = collection
        self.dim = dim
        if client is not None:
            self._client = client
        else:
            from pymilvus import MilvusClient  # 延迟导入：单测用假 client 时无需真实依赖

            self._client = MilvusClient(uri=f"http://{host}:{port}")

    # ---------- 集合管理 ----------

    def ensure_collection(self) -> None:
        """集合不存在则创建；已存在则校验向量维度，不一致时重建。"""
        if self._client.has_collection(self.collection):
            if self._vector_dim() == self.dim:
                return
            self._client.drop_collection(self.collection)
        from pymilvus import DataType

        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("kp_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("title", DataType.VARCHAR, max_length=1024)
        schema.add_field("source", DataType.VARCHAR, max_length=1024)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dim)
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type=MILVUS_INDEX,
            metric_type=MILVUS_METRIC,
            params={"M": 16, "efConstruction": 200},
        )
        self._client.create_collection(
            self.collection, schema=schema, index_params=index_params
        )

    def _vector_dim(self) -> int | None:
        """读取已存在集合的向量维度；取不到返回 None（视为需要重建）。"""
        desc = self._client.describe_collection(self.collection)
        for field in desc.get("fields", []):
            if field.get("name") == "vector":
                params = field.get("params") or {}
                dim = params.get("dim")
                return int(dim) if dim is not None else None
        return None

    # ---------- 写入 ----------

    def upsert(self, nodes: list[dict], vectors) -> int:
        """全量对齐写入知识点向量，并删除已不在知识库中的陈旧主键，返回写入条数。

        `nodes` 为图谱节点（需含 id/title/source），`vectors` 为同序向量（n×dim）。
        """
        if len(nodes) != len(vectors):
            # zip 会静默按短的一侧截断；那样后续"清理陈旧主键"会误删缺向量的知识点
            raise ValueError(
                f"节点数({len(nodes)})与向量数({len(vectors)})不一致，拒绝写入"
            )
        rows = [
            {
                "kp_id": str(node["id"]),
                "title": str(node.get("title", ""))[:1024],
                "source": str(node.get("source", ""))[:1024],
                "vector": [float(x) for x in vec],
            }
            for node, vec in zip(nodes, vectors)
        ]
        if not rows:
            return 0
        # 先取"写入前"的主键集合，且顺序不能颠倒：Milvus 的写入**与删除**都要 flush 之后
        # 才能被 query 看到，若在 upsert 之后再查会读成空集，陈旧主键清理会静默失效（已踩过）。
        self._client.flush(self.collection)
        existing = set(self._existing_ids())
        self._client.upsert(collection_name=self.collection, data=rows)
        stale = sorted(existing - {r["kp_id"] for r in rows})
        if stale:
            self._client.delete(collection_name=self.collection, ids=stale)
        self._client.flush(self.collection)   # 让本次写入与清理立即生效、可被检索
        return len(rows)

    def _existing_ids(self) -> list[str]:
        rows = self._client.query(
            collection_name=self.collection,
            filter='kp_id != ""',
            output_fields=["kp_id"],
            limit=_QUERY_LIMIT,
        )
        return [str(r["kp_id"]) for r in rows]

    # ---------- 检索 ----------

    def search(self, query_vector, top_k: int = 5) -> list[tuple[str, float]]:
        """按余弦相似度检索，返回 [(kp_id, score)]，按分数降序。"""
        hits = self._client.search(
            collection_name=self.collection,
            data=[[float(x) for x in query_vector]],
            limit=top_k,
            output_fields=["kp_id"],
            search_params={"metric_type": MILVUS_METRIC, "params": {"ef": 64}},
        )
        if not hits:
            return []
        out: list[tuple[str, float]] = []
        for hit in hits[0]:
            entity = hit.get("entity") or {}
            kp_id = entity.get("kp_id") or hit.get("id")
            if kp_id is not None:
                out.append((str(kp_id), float(hit.get("distance", 0.0))))
        return out

    def count(self) -> int:
        """集合内向量条数（用于校验是否与当前知识库规模一致）。"""
        rows = self._client.query(
            collection_name=self.collection,
            filter='kp_id != ""',
            output_fields=["count(*)"],
        )
        return int(rows[0]["count(*)"]) if rows else 0
