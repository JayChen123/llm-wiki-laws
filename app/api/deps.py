"""FastAPI 依赖：当前登录用户、知识库、记忆存储、生成器。

**全部通过依赖注入提供**，测试时用 `app.dependency_overrides` 换成假实现，
即可离线跑接口测试（不连 MySQL / Milvus / LLM）——这也是让 API 层可测的关键。
"""
from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, Request, status

from app.api.auth import COOKIE_NAME, decode_token


def get_kb(request: Request):
    """知识库单例（含 bge-m3 与 Milvus 连接）。"""
    kb = getattr(request.app.state, "kb", None)
    if kb is None:
        from app.kb import KnowledgeBase
        kb = KnowledgeBase.load()
        request.app.state.kb = kb
    return kb


def get_memory_store(request: Request):
    """短期记忆 + 画像（MySQL）。"""
    from app.memory.mysql_store import MySQLMemoryStore
    store = getattr(request.app.state, "memory_store", None)
    if store is None:
        store = MySQLMemoryStore()
        store.ensure_schema()
        request.app.state.memory_store = store
    return store


def get_long_term(request: Request):
    """长期记忆（Milvus 向量库）；编码器复用知识库已加载的 bge-m3。"""
    from app.memory.long_term import LongTermMemory, UserMemoryStore
    long_term = getattr(request.app.state, "long_term", None)
    if long_term is None:
        kb = get_kb(request)
        vector_store = UserMemoryStore(embedder=kb.embedder)
        vector_store.ensure_collection()
        long_term = LongTermMemory(store=vector_store, sql_store=get_memory_store(request))
        request.app.state.long_term = long_term
    return long_term


def get_generator(request: Request):
    """回答生成器；未配置 API_KEY 时为 None（接口层退化为仅返回检索结果）。"""
    if not hasattr(request.app.state, "generator"):
        from app.generation.answer import AnswerGenerator
        try:
            request.app.state.generator = AnswerGenerator()
        except RuntimeError:
            request.app.state.generator = None
    return request.app.state.generator


def get_classifier(request: Request):
    """意图分类器；可通过 app.state.intent_enabled=False 关闭（测试用）。"""
    if not getattr(request.app.state, "intent_enabled", True):
        return None
    if not hasattr(request.app.state, "classifier"):
        from app.generation.intent import IntentClassifier
        request.app.state.classifier = IntentClassifier()
    return request.app.state.classifier


def current_user(
        access_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
        store=Depends(get_memory_store),
) -> str:
    """从 HttpOnly Cookie 里的 JWT 解出当前用户；未登录/令牌无效一律 401。

    记忆存储走 `Depends` 而非直接调用 `get_memory_store()`：这样测试里
    `app.dependency_overrides` 才能把它换成假实现（离线跑接口测试的关键）。
    """
    user_id = decode_token(access_token) if access_token else None
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或登录已过期")
    if store.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user_id
