"""BM25 关键词检索（jieba 中文分词）。"""
from __future__ import annotations

import jieba
from rank_bm25 import BM25Okapi


class KeywordRetriever:
    """对知识点标题+正文建立 BM25 索引，按关键词相关度召回。"""

    def __init__(self, nodes: list[dict]):
        self.nodes = nodes
        # 语料加入关键词：概念增强后的关键词能弥补法律原文措辞与用户口语的鸿沟
        corpus = [
            jieba.lcut(f"{n['title']} {n['content']} {' '.join(n.get('keywords') or [])}")
            for n in nodes
        ]
        self.bm25 = BM25Okapi(corpus)

    def search(self, query: str, top_k: int = 5) -> list[tuple[dict, float]]:
        tokens = [t for t in jieba.lcut(query) if t.strip()]
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        order = scores.argsort()[::-1]
        results = []
        for idx in order:
            if scores[idx] <= 0 or len(results) >= top_k:
                if len(results) >= top_k:
                    break
                continue
            results.append((self.nodes[idx], float(scores[idx])))
        return results
