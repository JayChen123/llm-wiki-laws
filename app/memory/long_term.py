"""长期记忆：跨会话保留的「用户画像」与「历史问答语义索引」。

- **画像**（结构化事实）→ MySQL `user_profile`（`MySQLMemoryStore.add_fact/facts`）
- **历史问答**（语义索引）→ Milvus 集合 `llm_wiki_user_memory`（与法律知识
  `llm_wiki_laws` **完全分离**）

两条红线：
1. **强制 user_id 过滤**：Milvus 检索必须带 `filter=user_id == "..."`，
   否则会把别人的历史当上下文（跨用户泄漏）。
2. **记忆不是法律知识**：只注入生成 prompt 的"用户背景"段落，且标注不可作为
   法律依据；**绝不并入检索词**——否则会重演"上一问污染法条检索"的串题问题。

向量用**本地 bge-m3** 编码（零 API 成本）；历史摘要直接取"问题+答案"，
不需要额外 LLM 调用。
"""
from __future__ import annotations

from app.config import (
    EMBED_DIM,
    MEMORY_COLLECTION,
    MILVUS_HOST,
    MILVUS_INDEX,
    MILVUS_METRIC,
    MILVUS_PORT,
    MEMORY_RECALL_K,
    MEMORY_SUMMARY_CHARS,
)

_QUERY_LIMIT = 16384


class UserMemoryStore:
    """用户记忆向量库（Milvus），主键为 `mem_id`，每条记忆归属一个 `user_id`。"""

    def __init__(
            self,
            embedder=None,
            host: str = MILVUS_HOST,
            port: str | int = MILVUS_PORT,
            collection: str = MEMORY_COLLECTION,
            dim: int = EMBED_DIM,
            client=None,
    ):
        self.embedder = embedder
        self.collection = collection
        self.dim = dim
        if client is not None:
            self._client = client
        else:
            from pymilvus import MilvusClient
            self._client = MilvusClient(uri=f"http://{host}:{port}")

    # ---------- 集合 ----------

    def ensure_collection(self) -> None:
        if self._client.has_collection(self.collection):
            if self._vector_dim() == self.dim:
                return
            self._client.drop_collection(self.collection)
        from pymilvus import DataType

        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("mem_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("user_id", DataType.VARCHAR, max_length=64)
        schema.add_field("kind", DataType.VARCHAR, max_length=16)   # history | profile
        schema.add_field("text", DataType.VARCHAR, max_length=4096)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dim)
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type=MILVUS_INDEX,
            metric_type=MILVUS_METRIC,
            params={"M": 16, "efConstruction": 200},
        )
        self._client.create_collection(self.collection, schema=schema, index_params=index_params)

    def _vector_dim(self) -> int | None:
        desc = self._client.describe_collection(self.collection)
        for field in desc.get("fields", []):
            if field.get("name") == "vector":
                dim = (field.get("params") or {}).get("dim")
                return int(dim) if dim is not None else None
        return None

    # ---------- 写入 ----------

    def add(self, user_id: str, kind: str, text: str, mem_id: str | None = None) -> bool:
        """写入一条记忆（自动编码）。返回是否写入。"""
        text = (text or "").strip()
        if not user_id or not text or self.embedder is None:
            return False
        vector = self.embedder.encode([text[:2000]])[0]
        row = {
            "mem_id": mem_id or self._next_id(user_id, text),
            "user_id": user_id,
            "kind": kind,
            "text": text[:4096],
            "vector": [float(x) for x in vector],
        }
        self._client.upsert(collection_name=self.collection, data=[row])
        # Milvus 的写入要 flush 之后才可被 query/search 看到，否则刚写的一轮记忆
        # 在下一次提问时召回不到（同一坑在 KB 向量库上踩过一次）
        self._client.flush(self.collection)
        return True

    @staticmethod
    def _next_id(user_id: str, text: str) -> str:
        """稳定主键：同一用户同一条文本重复写入会覆盖而非堆积。"""
        import hashlib

        digest = hashlib.sha1(f"{user_id}|{text}".encode("utf-8")).hexdigest()[:24]
        return f"{user_id[:24]}_{digest}"

    # ---------- 检索 ----------

    def search(self, user_id: str, query: str, top_k: int = MEMORY_RECALL_K) -> list[dict]:
        """按 user_id 过滤 + 语义召回该用户的相关记忆。"""
        if not user_id or not query or self.embedder is None:
            return []
        vector = self.embedder.encode([query])[0]
        hits = self._client.search(
            collection_name=self.collection,
            data=[[float(x) for x in vector]],
            limit=top_k,
            filter=f'user_id == "{user_id}"',      # 红线：必须按用户隔离
            output_fields=["user_id", "kind", "text"],
            search_params={"metric_type": MILVUS_METRIC, "params": {"ef": 64}},
        )
        out: list[dict] = []
        if not hits:
            return out
        for hit in hits[0]:
            entity = hit.get("entity") or {}
            out.append({
                "kind": entity.get("kind", ""),
                "text": entity.get("text", ""),
                "score": float(hit.get("distance", 0.0)),
            })
        return out

    def clear(self, user_id: str) -> None:
        """清空某用户的全部长期记忆（向量）。

        删除同样要 flush 才对 query/search 生效——否则"清除我的数据"看似成功、
        实际仍能被召回（真实 Milvus 上实测过）。
        """
        self._client.delete(collection_name=self.collection, filter=f'user_id == "{user_id}"')
        self._client.flush(self.collection)

    def count(self, user_id: str | None = None) -> int:
        expr = f'user_id == "{user_id}"' if user_id else 'mem_id != ""'
        rows = self._client.query(
            collection_name=self.collection, filter=expr, output_fields=["count(*)"]
        )
        return int(rows[0]["count(*)"]) if rows else 0


def build_summary(question: str, answer: str, max_chars: int = MEMORY_SUMMARY_CHARS) -> str:
    """把一轮问答压成一条记忆文本（不调 LLM，零成本）。"""
    question = (question or "").strip()
    answer = (answer or "").strip().replace("\n", " ")
    return f"用户问：{question}\n回答要点：{answer[:max_chars]}"


class LongTermMemory:
    """长期记忆门面：画像（MySQL）+ 历史问答语义索引（Milvus）。

    记忆只有这一套后端，**不做降级**：后端不可用时直接抛出，让问题暴露，
    而不是静默返回空、让人误以为"这个用户没有历史"。
    """

    def __init__(self, store, sql_store):
        self.store = store
        self.sql_store = sql_store

    def recall(self, user_id: str, query: str, top_k: int = MEMORY_RECALL_K) -> list[dict]:
        """召回该用户的相关记忆：画像（结构化事实）+ 历史问答（语义相似）。"""
        memories: list[dict] = [
            {"kind": "profile", "text": fact, "score": 1.0}
            for fact in self.sql_store.facts(user_id, limit=top_k)
        ]
        memories.extend(self.store.search(user_id, query, top_k=top_k))
        return memories[: top_k * 2]

    def remember_turn(self, user_id: str, question: str, answer: str) -> bool:
        """把一个问答轮次写入长期记忆（历史语义索引）。"""
        return self.store.add(user_id, "history", build_summary(question, answer))

    def remember_facts(self, user_id: str, facts: list[str]) -> int:
        """写入画像事实（去重由 DB 唯一约束保证）。"""
        return sum(1 for fact in facts if self.sql_store.add_fact(user_id, fact))

    def clear(self, user_id: str) -> None:
        self.store.clear(user_id)
