"""ingest 流水线：增量扫描 PDF→MD、多文档知识点合并测试。"""
from pathlib import Path

from app.ingest.pipeline import (
    collect_points,
    convert_pdf_to_md,
    discover_markdown_sources,
    discover_pdf_sources,
)


def test_discover_pdf_sources(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.pdf").touch()
    (src / "b.pdf").touch()
    (src / "c.txt").touch()
    pdfs = discover_pdf_sources(src)
    assert [p.name for p in pdfs] == ["a.pdf", "b.pdf"]


def test_convert_pdf_to_md_incremental(tmp_path: Path, monkeypatch):
    src = tmp_path / "src"
    raw = tmp_path / "raw"
    src.mkdir()
    (src / "a.pdf").write_text("dummy")
    (src / "b.pdf").write_text("dummy")

    from app.ingest import pdf_to_markdown as pdf_mod

    def fake_convert(pdf, md):
        md.write_text(f"# {pdf.stem} 内容", encoding="utf-8")

    monkeypatch.setattr(pdf_mod, "pdf_to_markdown", fake_convert)

    converted = convert_pdf_to_md(src, raw, verbose=False)
    assert [p.name for p in converted] == ["a.md", "b.md"]
    # 第二次运行：已转换，不重复转换（增量）
    converted_again = convert_pdf_to_md(src, raw, verbose=False)
    assert converted_again == []
    assert (raw / "a.md").exists() and (raw / "b.md").exists()


def test_discover_markdown_sources(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.md").touch()
    (raw / "b.md").touch()
    (raw / "c.txt").touch()
    mds = discover_markdown_sources(raw)
    assert [p.name for p in mds] == ["a.md", "b.md"]


def test_collect_points_global_ids(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "doc1.md").write_text(
        "第一章 总则\n第一条 适用于全体员工。\n", encoding="utf-8"
    )
    (raw / "doc2.md").write_text(
        "第二章 规定\n第一条 考勤\n员工每天打卡。\n", encoding="utf-8"
    )
    points = collect_points(raw, verbose=False)
    assert len(points) == 2
    # 全局编号唯一
    ids = [p.id for p in points]
    assert len(ids) == len(set(ids))
    assert ids == ["KP_001", "KP_002"]
    # source 含各自文档名
    assert "doc1" in points[0].source and "doc2" in points[1].source
