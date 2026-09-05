"""关键词/向量/混合检索测试。"""
import numpy as np

from app.retrieval.embedder import Embedder
from app.retrieval.hybrid_retriever import HybridRetriever
from app.retrieval.keyword_retriever import KeywordRetriever
from app.retrieval.vector_retriever import VectorRetriever


def _nodes():
    return [
        {"id": "KP_001", "title": "第六章·四、休（请）假管理", "chapter": "第六章",
         "content": "员工请假需提前申请，事假、年假、婚假等假期审批流程。",
         "keywords": ["休假", "请假"]},
        {"id": "KP_002", "title": "第五章·二、加班及加班费", "chapter": "第五章",
         "content": "加班费计算基数为岗位工资，工作日加班按 1.5 倍计算。",
         "keywords": ["加班", "加班费"]},
        {"id": "KP_003", "title": "第六章·三、迟到、早退与旷工", "chapter": "第六章",
         "content": "迟到、早退、旷工的处理规定。",
         "keywords": ["迟到", "旷工"]},
    ]


def _graph():
    return {
        "nodes": _nodes(),
        "edges": [
            {"source": "KP_001", "target": "KP_003", "types": ["sibling"], "weight": 1.0},
            {"source": "KP_001", "target": "KP_002", "types": ["keyword"], "weight": 0.4},
        ],
    }


class StubEmbedder(Embedder):
    """按关键词映射的主题向量，使语义检索可预测。"""

    def encode(self, texts):
        out = np.zeros((len(texts), 3), dtype=np.float32)
        for i, t in enumerate(texts):
            for kw, idx in [("请假", 0), ("加班", 1), ("迟到", 2)]:
                if kw in t:
                    out[i, idx] = 1.0
            norm = np.linalg.norm(out[i]) or 1.0
            out[i] = out[i] / norm
        return out


def test_keyword_retrieval():
    kr = KeywordRetriever(_nodes())
    results = kr.search("请假流程", top_k=2)
    assert results and results[0][0]["id"] == "KP_001"


def test_keyword_retrieval_uses_keywords_field():
    # "加班费"仅出现在关键词字段（标题/正文均无），仍应被 BM25 命中
    # 3 篇语料保证 idf>0；"加班费"仅出现在 KP_142 的关键词字段（标题/正文均无）
    nodes = [
        {"id": "KP_142", "title": "第四章·第四十四条", "chapter": "第四章",
         "content": "用人单位应当按照标准支付高于正常工作时间工资的工资报酬。",
         "keywords": ["加班费"]},
        {"id": "KP_148", "title": "第五章·第五十条", "chapter": "第五章",
         "content": "工资应当以货币形式按月支付给劳动者本人。",
         "keywords": ["工资"]},
        {"id": "KP_031", "title": "第三章·第三十一条", "chapter": "第三章",
         "content": "用人单位不得强迫或者变相强迫劳动者加班。",
         "keywords": ["加班"]},
    ]
    kr = KeywordRetriever(nodes)
    results = kr.search("加班费", top_k=2)
    assert results and results[0][0]["id"] == "KP_142"


def test_vector_retrieval():
    nodes = _nodes()
    emb = StubEmbedder()
    vecs = emb.encode([n["title"] + n["content"] for n in nodes])
    vr = VectorRetriever(nodes, vecs, emb)
    results = vr.search("请假流程", top_k=2)
    assert results and results[0][0]["id"] == "KP_001"


def test_hybrid_rrf_and_expansion():
    nodes = _nodes()
    graph = _graph()
    emb = StubEmbedder()
    vecs = emb.encode([n["title"] + n["content"] for n in nodes])
    kr = KeywordRetriever(nodes)
    vr = VectorRetriever(nodes, vecs, emb)
    hr = HybridRetriever(nodes, graph, kr, vr, expand_k=2)

    results = hr.search("请假流程", top_k=2)
    ids = [r["id"] for r in results]
    assert ids[0] == "KP_001"          # 语义+关键词命中
    assert "KP_003" in ids             # 经 sibling 关联扩展召回
    assert all("content" in r for r in results)


def _chain_nodes():
    """链式图：A-B-C 依次相连（C 是 A 的二跳邻居），D 无关联。"""
    return [
        {"id": "KP_A", "title": "第六章·四、请假管理", "chapter": "第六章",
         "content": "请假需要提前申请。", "keywords": ["请假"]},
        {"id": "KP_B", "title": "第六章·三、考勤管理", "chapter": "第六章",
         "content": "考勤需每日打卡。", "keywords": ["考勤"]},
        {"id": "KP_C", "title": "第九章·四、违纪违规行为", "chapter": "第九章",
         "content": "旷工属于违纪行为。", "keywords": ["违纪"]},
        {"id": "KP_D", "title": "第五章·一、薪酬", "chapter": "第五章",
         "content": "工资按月发放。", "keywords": ["薪酬"]},
    ]


def _chain_graph():
    return {
        "nodes": _chain_nodes(),
        "edges": [
            {"source": "KP_A", "target": "KP_B", "types": ["sibling"], "weight": 1.0},
            {"source": "KP_B", "target": "KP_C", "types": ["keyword"], "weight": 1.0},
        ],
    }


def test_hybrid_multi_hop_expansion():
    nodes = _chain_nodes()
    graph = _chain_graph()
    emb = StubEmbedder()
    vecs = emb.encode([n["title"] + n["content"] for n in nodes])
    kr = KeywordRetriever(nodes)
    vr = VectorRetriever(nodes, vecs, emb)
    # 3 跳扩展：C 是 A 的二跳邻居，应被多跳扩展召回
    hr = HybridRetriever(nodes, graph, kr, vr, expand_k=3, expand_hops=3, expand_decay=0.5)

    results = hr.search("请假", top_k=1)
    ids = [r["id"] for r in results]
    assert ids[0] == "KP_A"            # 种子命中
    assert "KP_B" in ids               # 一跳扩展
    assert "KP_C" in ids               # 二跳扩展（多跳核心）
    assert "KP_D" not in ids           # 无关联节点不被带入


def test_multi_hop_fraction_after_seed():
    """多跳扩展节点应排在种子节点之后。"""
    nodes = _chain_nodes()
    graph = _chain_graph()
    emb = StubEmbedder()
    vecs = emb.encode([n["title"] + n["content"] for n in nodes])
    hr = HybridRetriever(nodes, graph, KeywordRetriever(nodes), VectorRetriever(nodes, vecs, emb),
                         expand_k=3, expand_hops=3, expand_decay=0.5)
    results = hr.search("请假", top_k=1)
    assert results[0]["id"] == "KP_A"
