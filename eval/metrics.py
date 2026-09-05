"""检索质量指标：recall@k（自定义，ragas 无内置）。

recall@k = 期望命中的知识点中，有多少出现在检索 Top-k 结果内。
"""
from __future__ import annotations


def recall_at_k(
    retrieved_ids: list[str],
    expected_ids: list[str],
    k: int,
) -> float | None:
    """计算 Top-k 检索召回率。

    若 expected_ids 为空返回 None（无法评估）。
    """
    if not expected_ids:
        return None
    hit = sum(1 for eid in expected_ids if eid in set(retrieved_ids[:k]))
    return hit / len(expected_ids)


def recall_report(
    retrieved_ids: list[str],
    expected_ids: list[str],
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict[str, float | None]:
    """返回 {f"recall@{k}": 值} 字典。"""
    return {f"recall@{k}": recall_at_k(retrieved_ids, expected_ids, k) for k in ks}
