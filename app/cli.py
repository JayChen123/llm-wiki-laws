"""命令行交互式问答。

流程：用户 query → 意图识别 → 短期记忆按需注入检索词 → 混合检索+反思式多跳定位知识点
→ 召回长期记忆（画像/相关历史）→ qwen-max 综合生成 → 写回短期与长期记忆。
命令：/exit 退出，/clear 清空记忆，/help 帮助。
"""
from __future__ import annotations

import time

from app.config import (
    INTENT_ENABLED,
    MEMORY_INJECT_MODE,
    PROFILE_EXTRACT_EVERY,
    TOP_K,
)
from app.generation.answer import AnswerGenerator
from app.generation.intent import Intent, IntentClassifier, fallback_answer
from app.kb import KnowledgeBase
from app.logger import get_logger, log_qa_event
from app.memory import build_search_query, create_memory_store
from app.memory.long_term import LongTermMemory, UserMemoryStore, memory_summary
from app.memory.profile_extractor import ProfileExtractor


def _build_long_term_memory(kb: KnowledgeBase, store) -> LongTermMemory:
    """构建长期记忆（画像走 MySQL，历史语义索引走 Milvus）。

    记忆编码复用知识库已加载的 bge-m3（避免重复加载 2.2GB 模型）。
    **不做降级**：Milvus/MySQL 不可用时直接抛出，让问题暴露。
    """
    vector_store = UserMemoryStore(embedder=kb.embedder)
    vector_store.ensure_collection()
    return LongTermMemory(store=vector_store, sql_store=store)


def _print_results(results: list[dict]) -> None:
    print("\n[检索到的知识点]")
    for r in results:
        print(f"  · {r['title']}  (score={r['score']})")
    print("[未配置 API_KEY，仅展示检索结果，无法生成自然语言回答]"
          if results else "[未检索到相关知识点]")


def _knowledge_blocks(results: list[dict]) -> list[dict]:
    """从检索结果抽取日志所需的简要知识块信息（不含正文）。"""
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "score": r["score"],
            "source": r.get("source", ""),
        }
        for r in results
    ]


def main() -> None:
    print("加载知识库…")
    kb = KnowledgeBase.load()
    try:
        gen = AnswerGenerator()
    except RuntimeError as e:
        print(f"[提示] {e}")
        gen = None

    store = create_memory_store()  # MySQL（唯一后端）
    logger = get_logger()
    classifier = IntentClassifier() if INTENT_ENABLED else None
    extractor = ProfileExtractor()
    session = input("会话ID（回车默认 default，重开可继续上次对话）: ").strip() or "default"
    # P4 接入注册登录前，user_id 以会话占位（长期记忆按此累积；换会话即换用户）
    user_id = session
    store.ensure_user(user_id)
    store.ensure_session(session, user_id)
    long_term = _build_long_term_memory(kb, store)
    print("已进入问答模式（输入 /exit 退出，/clear 清空记忆，/help 帮助）")

    while True:
        try:
            q = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q == "/exit":
            break
        if q == "/clear":
            store.clear(session)
            print("[已清空当前会话记忆]")
            continue
        if q == "/help":
            print("命令：/exit 退出  /clear 清空记忆  /help 帮助")
            continue

        # 意图识别：非"可答"路径直接兜底，不检索也不生成
        intent_result = classifier.classify(q) if classifier else None
        if intent_result is not None and intent_result.intent is not Intent.ANSWERABLE:
            answer = fallback_answer(intent_result.intent)
            print(f"\nAI: {answer}")
            log_qa_event(
                user_id=user_id,
                query=q,
                knowledge_blocks=[],
                answer=answer,
                intent=intent_result.intent.value,
                intent_reason=intent_result.reason,
                logger=logger,
            )
            store.add(session, "user", q, user_id=user_id)
            store.add(session, "assistant", answer, user_id=user_id)
            continue

        # 短期记忆按需注入检索词：仅在"像追问"时才并入上一问，避免换话题时污染检索
        past = store.recent_user_queries(session, n=1)
        search_query = build_search_query(q, past, mode=MEMORY_INJECT_MODE)
        t0 = time.perf_counter()
        results = kb.search(search_query, top_k=TOP_K)
        retrieval_ms = (time.perf_counter() - t0) * 1000

        # 长期记忆召回：只注入生成，**不并入检索词**（记忆不是法律知识）
        memories = long_term.recall(user_id, q)

        answer = ""
        generation_ms = None
        if gen:
            history = store.recent(session, limit=10)
            try:
                print("\nAI: ", end="", flush=True)
                t0 = time.perf_counter()
                for piece in gen.generate_stream(q, results, history, memory=memories):
                    print(piece, end="", flush=True)
                    answer += piece
                generation_ms = (time.perf_counter() - t0) * 1000
                print()
            except Exception as e:
                print(f"\nAI: 生成失败（{e}），以下为检索定位的知识点：")
                _print_results(results)
                answer = "，".join(r["title"] for r in results)
        else:
            _print_results(results)
            answer = "，".join(r["title"] for r in results)

        # 日志记录：用户ID + 意图 + 检索知识块 + 最终回答 + 耗时
        log_qa_event(
            user_id=user_id,
            query=q,
            knowledge_blocks=_knowledge_blocks(results),
            answer=answer,
            retrieval_ms=retrieval_ms,
            generation_ms=generation_ms,
            intent=intent_result.intent.value if intent_result else None,
            intent_reason=intent_result.reason if intent_result else None,
            trace=kb.last_trace,
            memory=memory_summary(memories),
            logger=logger,
        )

        # 写回记忆：短期（会话消息）+ 长期（历史语义索引，本地 bge-m3 编码零 API 成本）
        store.add(session, "user", q, user_id=user_id)
        store.add(session, "assistant", answer, user_id=user_id)
        long_term.remember_turn(user_id, q, answer)

        # 低频画像抽取：每 PROFILE_EXTRACT_EVERY 轮一次，避免每轮都付 LLM 成本
        if PROFILE_EXTRACT_EVERY > 0 and store.turn_count(session) % PROFILE_EXTRACT_EVERY == 0:
            facts = extractor.extract(store.recent(session, limit=10))
            added = long_term.remember_facts(user_id, facts)
            if added:
                print(f"[记忆] 已更新用户画像 {added} 条")
    store.close()


if __name__ == "__main__":
    main()
