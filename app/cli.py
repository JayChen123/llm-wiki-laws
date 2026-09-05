"""命令行交互式问答。

流程：用户 query → 短期记忆注入检索 → 混合检索定位知识点 →
qwen-max 综合生成回答 → 写入短期记忆（SQLite）。
命令：/exit 退出，/clear 清空记忆，/help 帮助。
"""
from __future__ import annotations

import time

from app.config import MEMORY_DB_PATH, TOP_K
from app.generation.answer import AnswerGenerator
from app.kb import KnowledgeBase
from app.logger import get_logger, log_qa_event
from app.memory.memory_store import MemoryStore


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

    store = MemoryStore(MEMORY_DB_PATH)
    logger = get_logger()
    session = input("会话ID（回车默认 default，重开可继续上次对话）: ").strip() or "default"
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

        # 短期记忆注入检索：结合最近一问，处理"那/具体/继续"等指代延续
        past = store.recent_user_queries(session, n=1)
        search_query = " ".join(past + [q])
        t0 = time.perf_counter()
        results = kb.search(search_query, top_k=TOP_K)
        retrieval_ms = (time.perf_counter() - t0) * 1000

        answer = ""
        generation_ms = None
        if gen:
            history = store.recent(session, limit=10)
            try:
                print("\nAI: ", end="", flush=True)
                t0 = time.perf_counter()
                for piece in gen.generate_stream(q, results, history):
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

        # 日志记录：用户ID + 检索知识块 + 最终回答 + 耗时
        log_qa_event(
            user_id=session,
            query=q,
            knowledge_blocks=_knowledge_blocks(results),
            answer=answer,
            retrieval_ms=retrieval_ms,
            generation_ms=generation_ms,
            logger=logger,
        )
        store.add(session, "user", q)
        store.add(session, "assistant", answer)
    store.close()


if __name__ == "__main__":
    main()
