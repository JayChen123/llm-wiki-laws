"""Milvus 向量库：集合定义、全量对齐写入与陈旧主键清理、检索映射（假 client，离线）。"""
import pytest

from app.retrieval.vector_store import MilvusVectorStore


class FakeSchema:
    def __init__(self):
        self.fields = []
        self.auto_id = None

    def add_field(self, name, dtype, **kwargs):
        self.fields.append({"name": name, "dtype": dtype, **kwargs})


class FakeIndexParams:
    def __init__(self):
        self.indexes = []

    def add_index(self, **kwargs):
        self.indexes.append(kwargs)


class FakeMilvusClient:
    """记录调用的假 MilvusClient，覆盖 store 用到的全部接口。

    忠实模拟真实 Milvus 的**写入可见性延迟**：`upsert` 写入的数据在 `flush` 之前
    查不到（`rows` 是实际数据，`visible` 是 query 能看到的）。漏掉这一步会让
    "写入后再读"这类顺序错误在单测里蒙混过关。
    """

    def __init__(self, existing_ids=(), dim=4):
        self.rows: dict[str, dict] = {i: {"kp_id": i} for i in existing_ids}
        self.visible: set[str] = set(existing_ids)
        self.dim = dim
        self.upserted = []
        self.deleted = []
        self.flushed = 0
        self.created = None
        self.dropped = []
        self.search_hits = []

    # --- 集合管理 ---
    def has_collection(self, name):
        return self._exists

    def describe_collection(self, name):
        return {"fields": [{"name": "kp_id"}, {"name": "vector", "params": {"dim": self.dim}}]}

    def create_schema(self, **kwargs):
        return FakeSchema()

    def prepare_index_params(self):
        return FakeIndexParams()

    def create_collection(self, name, schema=None, index_params=None):
        self.created = {"name": name, "schema": schema, "index": index_params}
        self._exists = True

    def drop_collection(self, name):
        self.dropped.append(name)
        self._exists = False

    # --- 写入 / 查询 ---
    def upsert(self, collection_name, data):
        self.upserted.append((collection_name, data))
        for row in data:
            self.rows[row["kp_id"]] = row      # 写入但尚不可见，需 flush

    def flush(self, collection_name):
        self.flushed += 1
        self.visible = set(self.rows)

    def delete(self, collection_name, ids):
        self.deleted.append((collection_name, list(ids)))
        for i in ids:
            self.rows.pop(i, None)      # 删除同样要 flush 之后才对 query 生效

    def query(self, collection_name, filter=None, output_fields=None, limit=None):
        if output_fields and output_fields[0].startswith("count"):
            return [{"count(*)": len(self.visible)}]
        return [{"kp_id": i} for i in sorted(self.visible)]

    def search(self, collection_name, data, limit=5, output_fields=None, search_params=None):
        self.search_params = search_params
        return self.search_hits

    _exists = False


def _store(client):
    return MilvusVectorStore(collection="test_kb", dim=4, client=client)


def _nodes(*ids):
    return [{"id": i, "title": f"{i} 标题", "source": f"{i}.md L1"} for i in ids]


def test_ensure_collection_creates_schema_and_index():
    client = FakeMilvusClient()
    _store(client).ensure_collection()
    fields = {f["name"]: f for f in client.created["schema"].fields}
    assert fields["kp_id"]["is_primary"] is True
    assert [i["field_name"] for i in client.created["index"].indexes] == ["vector"]
    assert client.created["index"].indexes[0]["index_type"] == "HNSW"


def test_ensure_collection_rebuilds_on_dim_mismatch():
    """集合维度与当前编码器不符（如换了模型）则重建，避免维度不一致的静默错误。"""
    client = FakeMilvusClient(dim=768)
    client._exists = True
    _store(client).ensure_collection()
    assert client.dropped == ["test_kb"]
    assert client.created["schema"].fields[-1]["dim"] == 4


def test_ensure_collection_keeps_matching_collection():
    client = FakeMilvusClient(dim=4)
    client._exists = True
    _store(client).ensure_collection()
    assert client.dropped == [] and client.created is None


def test_upsert_writes_all_rows_with_kp_id():
    client = FakeMilvusClient()
    vecs = [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]
    n = _store(client).upsert(_nodes("KP_001", "KP_002"), vecs)
    assert n == 2
    _, data = client.upserted[0]
    assert [r["kp_id"] for r in data] == ["KP_001", "KP_002"]
    assert data[0]["vector"] == [0.1, 0.2, 0.3, 0.4]
    assert client.deleted == []          # 无陈旧主键时不发删除


def test_upsert_purges_stale_primary_keys():
    """全量对齐：知识库重建后变少的节点必须从集合中清除，否则检索会召回已下线知识点。"""
    client = FakeMilvusClient()
    _store(client).upsert(_nodes("KP_001", "KP_002", "KP_003"), [[0.0] * 4] * 3)
    _store(client).upsert(_nodes("KP_001", "KP_002"), [[0.0] * 4] * 2)
    assert client.deleted == [("test_kb", ["KP_003"])]
    assert client.visible == {"KP_001", "KP_002"}


def test_upsert_reads_existing_keys_before_writing():
    """先在写入前取旧主键集合。

    真实 Milvus 写入后需 flush 才可 query；若在 upsert 之后再读会读成空集，
    陈旧主键清理会静默失效（此用例靠假 client 的可见性延迟来卡住这个顺序 bug）。
    """
    client = FakeMilvusClient(existing_ids=["KP_001", "KP_002", "KP_OLD"])
    _store(client).upsert(_nodes("KP_001", "KP_002"), [[0.0] * 4] * 2)
    assert client.deleted == [("test_kb", ["KP_OLD"])]
    assert client.flushed >= 2           # 写入前取旧主键 + 写入/删除后生效
    assert client.visible == {"KP_001", "KP_002"}   # 删除已对 query 生效


def test_upsert_writes_are_visible_after_return():
    """upsert 返回后集合内容必须立即可查（否则管线刚写完、CLI 立刻启动会检索为空）。"""
    client = FakeMilvusClient()
    store = _store(client)
    store.upsert(_nodes("KP_001", "KP_002"), [[0.0] * 4] * 2)
    assert store.count() == 2


def test_search_returns_kp_id_and_score():
    client = FakeMilvusClient()
    client.search_hits = [[
        {"id": "KP_009", "distance": 0.91, "entity": {"kp_id": "KP_009"}},
        {"id": "KP_003", "distance": 0.82, "entity": {"kp_id": "KP_003"}},
    ]]
    hits = _store(client).search([0.1] * 4, top_k=2)
    assert hits == [("KP_009", 0.91), ("KP_003", 0.82)]
    assert client.search_params["metric_type"] == "COSINE"


def test_search_empty_result():
    assert _store(FakeMilvusClient()).search([0.1] * 4) == []


def test_count_reports_collection_size():
    client = FakeMilvusClient(existing_ids=["KP_001", "KP_002"])
    assert _store(client).count() == 2


def test_upsert_rejects_count_mismatch():
    """向量数与节点数不一致必须报错：zip 静默截断会连带误删缺向量的知识点。"""
    client = FakeMilvusClient()
    with pytest.raises(ValueError):
        _store(client).upsert(_nodes("KP_001", "KP_002"), [[0.0] * 4])
    assert client.upserted == [] and client.deleted == []
