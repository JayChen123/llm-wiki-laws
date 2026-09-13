"""把 `knowledge_graph.json` 同步到 Neo4j（图数据库副本）。

    uv run python -m app.ingest.sync_graph              # 同步 + 核对计数
    uv run python -m app.ingest.sync_graph --file x.json # 指定源文件
    uv run python -m app.ingest.sync_graph --check-only  # 只对比 json 与 Neo4j 是否一致

用途：`knowledge_graph.json` 是权威源（可 git diff）。任何对它或知识源的改动，
跑一次本命令即可让 Neo4j 与之一致；`pipeline` 在写完 json 后也会自动调用同一段逻辑。
"""
from __future__ import annotations

import argparse

from app.config import GRAPH_PATH
from app.ingest.graph_build import load_graph
from app.ingest.graph_store import Neo4jGraphStore


def sync_from_json(graph_path=GRAPH_PATH, verbose: bool = True) -> dict:
    """读 json 并全量同步到 Neo4j；返回 json 与 Neo4j 双侧计数。

    **不做降级**：Neo4j 连不上直接抛错（避免"以为在用图数据库、实际在读本地文件"）。
    """
    graph = load_graph(graph_path)
    store = Neo4jGraphStore()
    try:
        written = store.sync(graph)
        actual = store.counts()
        by_type = store.type_counts()
    finally:
        store.close()

    result = {
        "json": {"nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", []))},
        "written": written,
        "neo4j": actual,
        "relationships_by_type": by_type,
    }
    if verbose:
        j, n = result["json"], result["neo4j"]
        print(f"[sync] json  : {j['nodes']} 节点 / {j['edges']} 边（节点对）")
        print(f"[sync] Neo4j : {n['nodes']} 节点 / {n['edges']} 边（节点对）"
              f" / {n['relationships']} 条关系")
        # 边与节点对必须一致；关系数更大是正常的（多类型边会拆成多条关系）
        ok = (j["nodes"] == n["nodes"] and j["edges"] == n["edges"])
        print(f"[sync] 四类关系：{by_type}")
        print("[sync] " + ("✓ 节点与边一致（关系数大于边数属正常：多类型边已按四类拆分）"
                           if ok else "✗ 节点或边数不一致，请检查"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="knowledge_graph.json → Neo4j 同步")
    parser.add_argument("--file", default=str(GRAPH_PATH), help="源 json 路径")
    parser.add_argument("--check-only", action="store_true", help="只对比计数，不写入")
    args = parser.parse_args()

    if args.check_only:
        graph = load_graph(args.file)
        store = Neo4jGraphStore()
        try:
            actual = store.counts()
        finally:
            store.close()
        expected = {"nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", []))}
        print(f"[check] json  : {expected['nodes']} 节点 / {expected['edges']} 边")
        print(f"[check] Neo4j : {actual['nodes']} 节点 / {actual['edges']} 边")
        print("[check] " + ("✓ 一致" if expected == actual else "✗ 不一致，需要同步"))
        return

    sync_from_json(args.file)


if __name__ == "__main__":
    main()
