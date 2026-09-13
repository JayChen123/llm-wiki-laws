"""知识库加载：Milvus 可用时启用向量检索，不可用时退化为纯关键词（不抛异常）。"""
import json

import pytest

from app.kb import KnowledgeBase
from app.retrieval.embedder import Embedder


class DummyEmbedder(Embedder):
    """离线假编码器：不加载 bge-m3。"""

    def __init__(self):
        pass

    def encode(self, texts):
        import numpy as np

        out = np.zeros((len(texts), 4), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i, 0] = 1.0 if "加班" in t else 0.0
            out[i, 1] = 1.0 if "请假" in t else 0.0
        return out


class OkStore:
    """可用向量库：count 探活通过。"""

    def __init__(self):
        self.searched = []

    def count(self):
        return 2

    def search(self, query_vector, top_k=5):
        self.searched.append(top_k)
        return [("KP_002", 0.9), ("KP_001", 0.5)]


class DeadStore:
    """不可用向量库：探活即失败（模拟 Milvus 未启动 / 集合不存在）。"""

    def count(self):
        raise ConnectionError("Milvus 不可达")


@pytest.fixture
def graph_file(tmp_path, monkeypatch):
    graph = {
        "nodes": [
            {"id": "KP_001", "title": "第六章·四、休（请）假管理", "chapter": "第六章",
             "chapter_title": "", "clause": "四、休（请）假管理", "content": "员工请假需提前申请。",
             "keywords": ["请假"], "source": "test.md L1"},
            {"id": "KP_002", "title": "第五章·二、加班及加班费", "chapter": "第五章",
             "chapter_title": "", "clause": "二、加班及加班费", "content": "加班按 1.5 倍计算。",
             "keywords": ["加班"], "source": "test.md L2"},
            # BM25 需 ≥3 篇语料才有 idf>0，故补第三个节点
            {"id": "KP_003", "title": "第六章·三、迟到与旷工", "chapter": "第六章",
             "chapter_title": "", "clause": "三、迟到与旷工", "content": "迟到早退按旷工处理。",
             "keywords": ["迟到", "旷工"], "source": "test.md L3"},
        ],
        "edges": [],
    }
    # kb 现在从 Neo4j 读图：注入假存储，返回与 json 同形状的 {nodes, edges}
    class _FakeGraphStore:
        def load(self):
            return graph

        def close(self):
            pass

    monkeypatch.setattr("app.kb.Neo4jGraphStore", _FakeGraphStore)
    return graph


def test_load_uses_milvus_when_available(graph_file):
    store = OkStore()
    kb = KnowledgeBase.load(embedder=DummyEmbedder(), store=store)
    assert kb.hybrid.vector_retriever is not None
    assert store.searched == []          # 加载阶段只探活，不检索
    ids = [r["id"] for r in kb.search("加班费怎么算", top_k=2)]
    assert store.searched                 # 检索确实走了向量库
    assert "KP_002" in ids


def test_load_degrades_to_keyword_when_milvus_unavailable(graph_file, capsys):
    kb = KnowledgeBase.load(embedder=DummyEmbedder(), store=DeadStore())
    assert kb.hybrid.vector_retriever is None      # 退化为纯关键词，且不抛异常
    assert "Milvus" in capsys.readouterr().out
    ids = [r["id"] for r in kb.search("请假", top_k=2)]   # 关键词链路仍可用
    assert "KP_001" in ids


def test_expand_params_flow_from_config(graph_file, monkeypatch):
    """回归：config 里的 EXPAND_* 必须真的传进检索器。

    曾经 kb 构造 HybridRetriever 时没传这三个参数，回落到构造函数默认 expand_k=3，
    于是 config 里的 EXPAND_K=5 与文档"上下文最多 15 块"长期与实际行为不符
    （实测日志里 graph_expanded 恒为 3 才发现）。
    """
    import app.kb as kb_mod

    monkeypatch.setattr(kb_mod, "EXPAND_K", 7)
    monkeypatch.setattr(kb_mod, "EXPAND_HOPS", 2)
    monkeypatch.setattr(kb_mod, "EXPAND_DECAY", 0.25)
    kb = kb_mod.KnowledgeBase.load(embedder=DummyEmbedder(), store=OkStore())

    assert kb.hybrid.expand_k == 7
    assert kb.hybrid.expand_hops == 2
    assert kb.hybrid.expand_decay == 0.25
