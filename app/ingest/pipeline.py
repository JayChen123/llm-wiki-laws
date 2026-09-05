"""知识构建管线入口：自动扫描数据源，增量构建知识点图谱与 Wiki 页面。

数据源约定：
    data/src/*.pdf   —— 原始 PDF 源文件，自动转为 markdown（增量）
    data/raw/*.md    —— markdown 原始文件（PDF 转换产物 + 用户直接放入），逐个提取知识点

流程：PDF→MD（增量）→ 扫描 raw 全部 md → 提取知识点（全局编号）→ 关联图谱 → Wiki 落盘。
"""
from __future__ import annotations

from pathlib import Path

from app.config import GRAPH_PATH, RAW_DIR, SRC_DIR, VECTOR_CACHE_PATH
from app.ingest.concept_map import enrich_keywords
from app.ingest.graph_build import build_graph, save_graph
from app.ingest.knowledge_extract import KnowledgePoint, extract_knowledge_points
from app.ingest.pdf_to_markdown import pdf_to_markdown
from app.ingest.wiki_writer import write_wiki
from app.retrieval.embedder import Embedder


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
    if use_semantic:
        if verbose:
            print("[pipeline] 加载 bge-m3 编码语义向量…")
        embedder = Embedder()
        embedder.cache([f"{p.title}\n{p.content[:200]}" for p in points], VECTOR_CACHE_PATH)

    graph = build_graph(points, embedder=embedder)
    save_graph(graph, GRAPH_PATH)
    write_wiki(points, graph)
    if verbose:
        n_edges = len(graph["edges"])
        print(f"[pipeline] 关联边 {n_edges} 条，已写入 {GRAPH_PATH.name} 与 wiki/")
    return graph


if __name__ == "__main__":
    run_pipeline()
