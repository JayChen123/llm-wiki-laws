"""使用微软 markitdown 将 PDF 转换为 Markdown。"""
from markitdown import MarkItDown


def pdf_to_markdown(pdf_path, md_path):
    """转换 PDF 为 Markdown 并写入 md_path，返回输出路径。"""
    converter = MarkItDown()
    result = converter.convert(str(pdf_path))
    md_path.write_text(result.text_content, encoding="utf-8")
    return md_path
