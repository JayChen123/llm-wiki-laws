"""知识构建管线入口：自动扫描数据源，增量构建知识点图谱与 Wiki 页面。

数据源约定：
    data/src/*.pdf   —— 原始 PDF 源文件，自动转为 markdown（增量）
    data/raw/*.md    —— markdown 原始文件（PDF 转换产物 + 用户直接放入），逐个提取知识点

流程：PDF→MD（增量）→ 扫描 raw 全部 md → 提取知识点（全局编号）→ 关联图谱 → Wiki 落盘。
"""
from __future__ import annotations

from pathlib import Path

from app.config import GRAPH_PATH, RAW_DIR, SRC_DIR
from app.ingest.concept_map import enrich_keywords
from app.ingest.graph_build import build_graph, save_graph, semantic_text
from app.ingest.knowledge_extract import KnowledgePoint, extract_knowledge_points
from app.ingest.pdf_to_markdown import pdf_to_markdown
from app.ingest.sync_graph import sync_from_json
from app.ingest.wiki_writer import write_wiki
from app.retrieval.embedder import Embedder
from app.retrieval.vector_store import MilvusVectorStore


def discover_pdf_sources(src_dir: Path = SRC_DIR) -> list[Path]:
    """扫描 src 目录下的 PDF 源文件。"""
    return sorted(src_dir.glob("*.pdf")) if src_dir.exists() else []


def convert_pdf_to_md(
        src_dir: Path = SRC_DIR, raw_dir: Path = RAW_DIR, verbose: bool = True
) -> list[Path]:
    """增量转换：仅转换 raw 目录中缺失对应 md 的 PDF，返回新增的 md 文件。"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    converted: list[Path] = []
    for pdf in discover_pdf_sources(src_dir):
        md = raw_dir / f"{pdf.stem}.md"
        if md.exists():
            continue  # 已转换，跳过（增量）
        if verbose:
            print(f"[pipeline] 转换 {pdf.name} → {md.name}")
        pdf_to_markdown(pdf, md)
        converted.append(md)
    return converted


def discover_markdown_sources(raw_dir: Path = RAW_DIR) -> list[Path]:
    """扫描 raw 目录下的全部 markdown（转换产物 + 用户直接放入）。"""
    return sorted(raw_dir.glob("*.md")) if raw_dir.exists() else []


def collect_points(raw_dir: Path = RAW_DIR, verbose: bool = True) -> list[KnowledgePoint]:
    """提取 raw 下全部 md 的知识点，合并后全局统一编号（KP_001…）。"""
    points: list[KnowledgePoint] = []
    for md in discover_markdown_sources(raw_dir):
        pts = extract_knowledge_points(md)
        if verbose:
            print(f"[pipeline] {md.name}: 提取 {len(pts)} 个知识点")
        points.extend(pts)
    for i, p in enumerate(points, 1):
        p.id = f"KP_{i:03d}"
    return points


def run_pipeline(use_semantic: bool = True, verbose: bool = True) -> dict:
    """执行完整构建管线，返回图谱。"""
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    convert_pdf_to_md(SRC_DIR, RAW_DIR, verbose=verbose)

    points = collect_points(RAW_DIR, verbose=verbose)
    enrich_keywords(points)  # 概念→常用查询词 注入关键词，改善法律措辞与口语匹配
    if verbose:
        print(f"[pipeline] 知识点总数 {len(points)}")

    embedder = None
    vectors = None
    if use_semantic:
        if verbose:
            print("[pipeline] 加载 bge-m3 编码语义向量…")
        embedder = Embedder()
        # 只编码一次，结果同时供语义边构建与向量库写入（早期实现编码了两遍）
        vectors = embedder.encode([semantic_text(p) for p in points])

    graph = build_graph(points, embedder=embedder, vectors=vectors)
    save_graph(graph, GRAPH_PATH)          # 权威源（可 git diff）

    # 同步到 Neo4j 图数据库副本（应用检索时从 Neo4j 读图）。
    # json 已落盘，故同步失败不会丢构建结果；但必须**显式失败**而不是静默跳过，
    # 否则会出现"json 已更新、Neo4j 还是旧图"的静默不一致。
    sync_from_json(GRAPH_PATH, verbose=verbose)

    if vectors is not None:
        # 与 Neo4j 同步一致：派生副本写入失败就**显式失败**（json 已落盘，构建结果不会丢）。
        # 静默跳过会留下"json 已更新、向量库仍是旧数据"的不一致。
        store = MilvusVectorStore()
        store.ensure_collection()
        written = store.upsert(graph["nodes"], vectors)
        if verbose:
            print(f"[pipeline] 向量已写入 Milvus 集合 {store.collection}：{written} 条")

    write_wiki(points, graph)
    if verbose:
        n_edges = len(graph["edges"])
        print(f"[pipeline] 关联边 {n_edges} 条，已写入 {GRAPH_PATH.name} 与 wiki/")
    return graph


if __name__ == "__main__":
    run_pipeline()
