"""记忆模块：短期（会话消息，MySQL）+ 长期（用户画像 MySQL + 历史问答语义索引 Milvus）。

**记忆只有 MySQL 一个后端**：不做 SQLite 降级、不做连接失败回退——连不上就让它
明确报错，避免数据悄悄分散到本地文件、也避免"以为在用 MySQL 实际在写 SQLite"。
"""
from __future__ import annotations

from app.memory.mysql_store import MySQLMemoryStore

# 指代词/追问特征：出现这些词时，当前问题很可能依赖上一问的语境
_FOLLOWUP_MARKERS = (
    "那", "这", "它", "他", "她", "该", "此", "上述", "前面", "刚才",
    "具体", "继续", "还有", "另外", "为什么", "怎么说", "怎么办", "还有呢",
)
# 法律实体词：问题自带这些词时说明语境完整，不该并入上一问
_ENTITY_MARKERS = (
    "劳动法", "劳动合同法", "民法典", "第", "条", "试用期", "加班", "工资", "补偿",
    "解除", "合同", "离婚", "继承", "侵权", "竞业", "派遣", "工伤", "社保", "年假",
)


def create_memory_store(history_rounds: int = 5) -> MySQLMemoryStore:
    """创建短期记忆存储（MySQL）并确保表结构存在。"""
    store = MySQLMemoryStore(history_rounds=history_rounds)
    store.ensure_schema()
    return store


def build_search_query(query: str, past_queries: list[str], mode: str = "auto") -> str:
    """构造实际用于检索的查询串。

    短期记忆注入是为了处理"那/具体/继续"这类**追问**的指代延续；但无条件并入
    上一问会在用户**换话题**时污染检索词（实测：会话里先问"劳务派遣…"再问
    "试用期最长多久"，命中混入劳务派遣条文，并让检索反思顺着错话题补取条文）。

    - `always`：总是并入上一问（旧行为）
    - `off`   ：从不并入，只用当前问题
    - `auto`  ：仅当问题"像追问"时并入——含指代词，或很短且不含法律实体词
    """
    if mode == "off" or not past_queries:
        return query
    if mode == "always":
        return " ".join(past_queries + [query])
    looks_like_followup = _looks_like_followup(query)
    return " ".join(past_queries + [query]) if looks_like_followup else query


def _looks_like_followup(query: str) -> bool:
    text = (query or "").strip()
    if not text:
        return False
    if any(marker in text for marker in _FOLLOWUP_MARKERS):
        return True
    # 很短且没有法律实体词 → 大概率是"那怎么办"这类省略式追问
    return len(text) < 8 and not any(m in text for m in _ENTITY_MARKERS)
