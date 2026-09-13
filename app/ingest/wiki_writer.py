"""把知识点与关联关系落盘为结构化 Wiki 页面集（llm-wiki 风格）。"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

from app.config import CONCEPTS_DIR, WIKI_DIR
from app.ingest.concept_map import CONCEPT_MAP
from app.ingest.knowledge_extract import KnowledgePoint, cn_to_int

# 落盘示例页的白名单：(来源文档关键字, 条文)。
# 知识点页**不参与检索**（query 读 Neo4j + Milvus），它只是"人读视图"，故只保留少量
# 讲课用的样本；其余条文仍完整出现在 index.md 目录中（索引本身仍是全量的）。
# 这里写 (法律, 条文) 而非 KP 编号——编号会随知识源增删重新分配，硬编码会静默指错页。
WIKI_SAMPLE_REFS: list[tuple[str, str]] = [
    ("劳动合同法", "第一条"),          # 无"编"的基础形态
    ("劳动合同法", "第十九条"),        # 试用期（概念导航命中）
    ("劳动合同法", "第三十八条"),      # crossref 被引方
    ("劳动合同法", "第四十六条"),      # crossref 引用方（引第三十八条）
    ("劳动合同法", "第八十五条"),      # 与劳动法同条号的消歧样本
    ("劳动法", "第四十四条"),          # 加班费（跨法概念导航）
    ("民法典", "第一条"),              # 有"编"：title 带"第一编 总则"
    ("民法典", "第五百一十一条"),      # 民法典内部引用链
    ("民法典", "第一千二百五十四条"),  # 千位条号 + 编名（高空抛物）
    ("民法典", "第一千零七十七条"),    # 离婚冷静期（概念导航命中）
]


def write_wiki(
    points: list[KnowledgePoint],
    graph: dict,
    wiki_dir: Path | None = None,
    concepts_dir: Path | None = None,
) -> None:
    """生成 index.md、log.md 与示例页 concepts/*.md。

    目录可注入：单测必须能指向 tmp_path，否则跑一次测试就往真实 `wiki/` 写
    （理由同 `tests/conftest.py` 注入 `NullLogger`——测试不能写生产产物）。
    """
    wiki_dir = WIKI_DIR if wiki_dir is None else wiki_dir
    concepts_dir = CONCEPTS_DIR if concepts_dir is None else concepts_dir
    wiki_dir.mkdir(parents=True, exist_ok=True)
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "index.md").write_text(_index_md(points, graph), encoding="utf-8")
    for law, article in WIKI_SAMPLE_REFS:
        hit = _resolve_concept(law, article, points)
        if hit is None:
            print(f"[wiki] 示例条文未命中，已跳过：《{law}》{article}")
            continue
        p = next((x for x in points if x.id == hit[0]), None)
        if p is not None:
            (concepts_dir / f"{p.id}.md").write_text(_concept_md(p, graph), encoding="utf-8")
    (wiki_dir / "log.md").write_text(
        f"# 操作日志\n\n- {datetime.datetime.now():%Y-%m-%d %H:%M} 构建知识库，共 {len(points)} 个知识点，{len(graph['edges'])} 条关联。\n",
        encoding="utf-8",
    )


def _order_no(label: str) -> int:
    """把"第二编"/"第二十九章"排成整数用于目录排序；空标签（无"编"的法律）排最前。

    旧实现只认"一"~"九十九"的两位数写法，民法典的"第二十九章"会落到兜底值导致章节乱序。
    """
    s = re.sub(r"[第编章节]", "", label)
    return cn_to_int(s) if s else -1


def _resolve_concept(law: str, article: str, points: list[KnowledgePoint]):
    """把(来源文档关键字, 条文)解析为全局编号知识点；未命中返回 None。"""
    for p in points:
        if law in p.source and article in p.clause:
            return p.id, p.clause.rsplit("·", 1)[-1]
    return None


def _concept_index_section(points: list[KnowledgePoint]) -> str:
    lines = ["## 核心概念导航", "",
             "> 概念 → 相关条文知识点，可快速定位具体条款"
             "（跨《劳动合同法》《劳动法》《民法典》）。", ""]
    for concept, refs in CONCEPT_MAP.items():
        links = []
        for law, article in refs:
            hit = _resolve_concept(law, article, points)
            links.append(f"[[{hit[0]}|{hit[1]}]]" if hit else f"《{law}》{article}")
        lines.append(f"- **{concept}**：{'、'.join(links)}")
    lines.append("")
    return "\n".join(lines)


def _index_md(points: list[KnowledgePoint], graph: dict) -> str:
    lines = ["# 知识索引", ""]
    lines.append(_concept_index_section(points))
    # 按源文档分组，组内按章节排序，支持多文档
    by_doc: dict[str, list[KnowledgePoint]] = {}
    for p in points:
        doc = p.source.split(" ", 1)[0]
        by_doc.setdefault(doc, []).append(p)
    for doc in sorted(by_doc):
        lines.append(f"## {doc}")
        # 有"编"的法律（民法典）按 编→章 两级分组；无编的（劳动法/劳动合同法）直接按章，
        # 否则民法典各编同号章会被并成一个目录节
        by_part: dict[tuple[str, str], dict[str, list[KnowledgePoint]]] = {}
        for p in by_doc[doc]:
            by_part.setdefault((p.part, p.part_title), {}).setdefault(p.chapter, []).append(p)
        for part, part_title in sorted(by_part, key=lambda k: _order_no(k[0])):
            if part:
                lines.append(f"### {part} {part_title}".rstrip())
            for chapter in sorted(by_part[(part, part_title)], key=_order_no):
                lines.append(f"{'####' if part else '###'} {chapter}")
                for p in by_part[(part, part_title)][chapter]:
                    lines.append(f"- [[{p.id}|{p.title}]]")
                lines.append("")
    lines.append(f"共 {len(points)} 个知识点，{len(graph['edges'])} 条关联关系。\n")
    return "\n".join(lines)


def _concept_md(p: KnowledgePoint, graph: dict) -> str:
    related = [
        e for e in graph["edges"] if e["source"] == p.id or e["target"] == p.id
    ]
    lines = [
        "---",
        f"id: {p.id}",
        f"title: {p.title}",
        *([f"part: {p.part} {p.part_title}".rstrip()] if p.part else []),
        f"chapter: {p.chapter}",
        f"source: {p.source}",
        f"keywords: {', '.join(p.keywords)}",
        "---",
        "",
        f"# {p.title}",
        "",
        p.content,
        "",
        "## 关联知识点",
        "",
    ]
    if not related:
        lines.append("- 暂无关联")
    for e in sorted(related, key=lambda x: -x["weight"]):
        other = e["target"] if e["source"] == p.id else e["source"]
        types = "/".join(e["types"])
        lines.append(f"- [[{other}]] （{types}，权重 {e['weight']}）")
    lines.append("")
    return "\n".join(lines)
