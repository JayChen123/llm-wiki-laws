"""bge-m3 向量编码器，带本地缓存。"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from app.config import BGE_MODEL_PATH


class Embedder:
    """基于 sentence-transformers 的向量编码器（默认 bge-m3）。"""

    def __init__(self, model_path: str | Path = BGE_MODEL_PATH):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(str(model_path))
        self.dim = None

    def encode(self, texts: list[str]) -> np.ndarray:
        """批量编码为 L2 归一化向量，形状 (n, dim)。"""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vecs = self.model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            batch_size=16,
            show_progress_bar=False,
        )
        self.dim = vecs.shape[1]
        return np.asarray(vecs, dtype=np.float32)

    def cache(self, texts: list[str], cache_path: Path) -> np.ndarray:
        """编码并缓存到 numpy 文件；缓存命中则直接加载。"""
        if cache_path.exists():
            return np.load(cache_path)
        vecs = self.encode(texts)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_path, vecs)
        return vecs


# class DummyEmbedder:
#     """测试用假编码器：按文本哈希映射到固定向量，避免加载大模型。"""
#
#     def __init__(self, dim: int = 64):
#         self.dim = dim
#         self._rng = np.random.default_rng(42)
#         self._cache: dict[str, np.ndarray] = {}
#
#     def encode(self, texts: list[str]) -> np.ndarray:
#         out = np.zeros((len(texts), self.dim), dtype=np.float32)
#         for i, t in enumerate(texts):
#             key = t
#             if key not in self._cache:
#                 self._cache[key] = self._rng.normal(size=self.dim)
#                 self._cache[key] /= np.linalg.norm(self._cache[key]) + 1e-8
#             out[i] = self._cache[key]
#         return out
