"""会话与问答接口：会话 CRUD + SSE 流式问答。

问答链路**完全复用** CLI 的那条：意图识别 → 混合检索+反思多跳 → 长期记忆召回 → qwen-max 流式生成
→ 写短期（MySQL）与长期（Milvus）记忆。差别只在"用户是谁"来自登录态。

安全：所有会话相关接口都先校验会话归属当前用户，别人的会话一律 404（不泄露存在性）。
"""
from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import (
    current_user,
    get_classifier,
    get_generator,
    get_kb,
    get_long_term,
    get_memory_store,
)
from app.config import MEMORY_INJECT_MODE, PROFILE_EXTRACT_EVERY, TOP_K
from app.generation.intent import Intent, fallback_answer
from app.logger import get_logger, log_qa_event
from app.memory import build_search_query
from app.memory.long_term import memory_summary
from app.memory.profile_extractor import ProfileExtractor

router = APIRouter(prefix="/api", tags=["chat"])

_logger = get_logger()
_extractor = ProfileExtractor()


class SessionOut(BaseModel):
    session_id: str
    created_at: str
    message_count: int
    title: str = ""          # 取该会话首条用户提问，前端列表用


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: str = ""
    citations: list[dict] = []      # assistant 消息的引用来源（历史回看时仍可溯源）


class ChatIn(BaseModel):
    session_id: str = Field(..., description="会话 ID（须属于当前用户）")
    question: str = Field(..., min_length=1, description="用户问题")


def _require_session(session_id: str, user_id: str, store) -> None:
    """会话归属校验：不属于当前用户一律 404（连"存在但不是你的"都不暴露）。"""
    if not store.session_belongs_to(session_id, user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(user_id: str = Depends(current_user), store=Depends(get_memory_store)):
    out: list[SessionOut] = []
    for item in store.list_sessions(user_id):
        history = store.session_messages(item["session_id"], limit=200)
        first_user = next((m["content"] for m in history if m["role"] == "user"), "")
        out.append(SessionOut(title=first_user[:30] or "新会话", **item))
    return out


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(user_id: str = Depends(current_user), store=Depends(get_memory_store)):
    session_id = f"s_{uuid.uuid4().hex[:16]}"
    store.ensure_session(session_id, user_id)
    return SessionOut(session_id=session_id, created_at="", message_count=0, title="新会话")


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def get_messages(
        session_id: str,
        user_id: str = Depends(current_user),
        store=Depends(get_memory_store),
):
    _require_session(session_id, user_id, store)
    return [MessageOut(**m) for m in store.session_messages(session_id)]


@router.delete("/sessions/{session_id}")
def delete_session(
        session_id: str,
        user_id: str = Depends(current_user),
        store=Depends(get_memory_store),
        long_term=Depends(get_long_term),
):
    """删除会话及其消息；该会话写入的长期记忆同时清掉（避免残留可被召回）。"""
    _require_session(session_id, user_id, store)
    store.delete_session(session_id)
    long_term.clear(user_id)
    return {"ok": True}


@router.post("/chat")
def chat(
        payload: ChatIn,
        user_id: str = Depends(current_user),
        store=Depends(get_memory_store),
        kb=Depends(get_kb),
        long_term=Depends(get_long_term),
        gen=Depends(get_generator),
        classifier=Depends(get_classifier),
):
    """流式问答（SSE）。事件：meta → delta* → citations → done（或 fallback）。"""
    question = payload.question.strip()
    _require_session(payload.session_id, user_id, store)

    def stream():
        # ① 意图识别：非"可答"直接兜底，不检索不生成（与 CLI 完全一致）
        intent_result = classifier.classify(question) if classifier else None
        if intent_result is not None and intent_result.intent is not Intent.ANSWERABLE:
            answer = fallback_answer(intent_result.intent)
            yield _sse("fallback", {"intent": intent_result.intent.value, "answer": answer})
            log_qa_event(user_id=user_id, query=question, knowledge_blocks=[], answer=answer,
                         intent=intent_result.intent.value,
                         intent_reason=intent_result.reason, logger=_logger)
            store.add(payload.session_id, "user", question, user_id=user_id)
            store.add(payload.session_id, "assistant", answer, user_id=user_id)
            yield _sse("done", {})
            return

        # ② 短期记忆按需注入检索词（只在"像追问"时并入上一问，避免串题）
        past = store.recent_user_queries(payload.session_id, n=1)
        search_query = build_search_query(question, past, mode=MEMORY_INJECT_MODE)
        t0 = time.perf_counter()
        results = kb.search(search_query, top_k=TOP_K)
        retrieval_ms = (time.perf_counter() - t0) * 1000

        # ③ 长期记忆召回（只注入生成，绝不并入检索词）
        memories = long_term.recall(user_id, question)
        citations = [
            {"id": r["id"], "title": r["title"], "source": r.get("source", ""),
             "score": r["score"]}
            for r in results
        ]
        yield _sse("meta", {"citations": citations, "retrieval_ms": round(retrieval_ms, 1),
                            "memory_count": len(memories)})

        # ④ 流式生成
        answer = ""
        generation_ms = None
        if gen is not None:
            history = store.recent(payload.session_id, limit=10)
            try:
                t0 = time.perf_counter()
                for piece in gen.generate_stream(question, results, history, memory=memories):
                    answer += piece
                    yield _sse("delta", {"text": piece})
                generation_ms = (time.perf_counter() - t0) * 1000
            except Exception as exc:                    # 生成失败也要把检索结果给前端
                yield _sse("error", {"message": f"生成失败：{exc}"})
                answer = "，".join(r["title"] for r in results)
        else:
            answer = "，".join(r["title"] for r in results)
            yield _sse("delta", {"text": f"[未配置 API_KEY] 检索到：{answer}"})

        yield _sse("citations", {"citations": citations})
        log_qa_event(
            user_id=user_id, query=question,
            knowledge_blocks=[{"id": r["id"], "title": r["title"], "score": r["score"],
                               "source": r.get("source", "")} for r in results],
            answer=answer, retrieval_ms=retrieval_ms, generation_ms=generation_ms,
            intent=intent_result.intent.value if intent_result else None,
            intent_reason=intent_result.reason if intent_result else None,
            trace=kb.last_trace, memory=memory_summary(memories), logger=_logger,
        )
        store.add(payload.session_id, "user", question, user_id=user_id)
        store.add(payload.session_id, "assistant", answer, user_id=user_id,
                  citations=citations)
        long_term.remember_turn(user_id, question, answer)

        # ⑤ 低频画像抽取（每 N 轮一次，避免每轮都付 LLM 成本）
        if PROFILE_EXTRACT_EVERY > 0 and store.turn_count(payload.session_id) % PROFILE_EXTRACT_EVERY == 0:
            facts = _extractor.extract(store.recent(payload.session_id, limit=10))
            added = long_term.remember_facts(user_id, facts)
            if added:
                yield _sse("memory", {"added": added})
        yield _sse("done", {})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
