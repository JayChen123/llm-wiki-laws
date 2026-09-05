"""从 markdown 源文件提取知识点（面向法律法规文档）。

markitdown 转换产物含较多噪音（页眉日期、孤立页码、目录/元数据、
网页页脚），且部分法律 PDF 的文本使用康熙部首变体字符（如"第⼀条"）。
本模块先做清洗与字符规范化，再按"章（第X章）→ 节（第X节）→ 条（第X条）"
层级切分知识点（粒度：每一条一个知识点），并抽取关键词。

关键判别：
- 章/节/条标题在**原始行**（保留空白）上检测，要求"第X章/节/条"后紧跟空白，
  从而把正文中换行开头的交叉引用（如"第三十九条和第四十条…"，条后无空白）排除。
- TOC/目录：正文第一条出现前视为"目录/元数据"阶段，只更新章/节上下文、丢弃内容行。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import jieba.analyse

# 康熙部首（U+2F00）与 CJK 部首补充（U+2E80）中 NFKC 无法自动归一的变体 → 标准汉字
# str.maketrans 生成整数码点键，供 str.translate 使用
_KANGXI_TABLE = str.maketrans({
    chr(c): unicodedata.normalize("NFKC", chr(c))
    for c in range(0x2F00, 0x2FD6)
} | {"⺠": "民", "⻅": "见", "⻓": "长", "⻔": "门"})

# 章/节/条边界（原始行，要求"第X章/节/条"后跟空白；数字含"零"，否则漏掉第一百零一条等）
_CHAPTER_RE = re.compile(r"^第([一二三四五六七八九十百零]+)章[　\s]+(.+)$")
_SECTION_RE = re.compile(r"^第([一二三四五六七八九十百零]+)节[　\s]+(.+)$")
_ARTICLE_RE = re.compile(r"^第([一二三四五六七八九十百零]+)条[　\s]+(.*)$")

# 法律领域高频通用词：过滤后降低关键词边的重合密度
_LEGAL_STOPWORDS = {
    "用人单位", "劳动者", "劳动合同", "劳动关系", "本法", "应当", "按照", "依照",
    "规定", "有关", "其他", "以及", "不得", "可以", "法律", "法规", "行政法规",
    "条例", "情形", "下列", "当事人", "部门", "人民政府", "工作人员", "单位",
    "人员", "明确", "制定", "建立", "维护", "保证",
}

_CN_DIGITS = "零一二三四五六七八九"


def _cn_num(s: str) -> int:
    """中文数字转整数（支持个/十/百/零，如 九十八→98、一百零三→103）。"""
    total = 0
    num = 0
    for ch in s:
        if ch == "零":
            continue
        elif ch in _CN_DIGITS:
            num = _CN_DIGITS.index(ch)
        elif ch == "十":
            total += (num if num else 1) * 10
            num = 0
        elif ch == "百":
            total += (num if num else 1) * 100
            num = 0
    return total + num


# 页眉/页脚/网页噪音特征
_NOISE_KEYWORDS = (
    "加入收藏", "网站无障碍", "网站声明", "网站管理", "网站地图", "联系我们",
    "网站值班", "主办单位", "京ICP", "网站标识码", "版权所有", "版权",
)
_DATE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}")
_PAGE_MARKER_RE = re.compile(r"^\d+/\d+$")
_URL_RE = re.compile(r"^https?://")


@dataclass
class KnowledgePoint:
    id: str
    title: str                       # 知识点标题，如"第五章·第五十七条"
    chapter: str                     # 章节号，如"第五章"
    chapter_title: str               # 章节标题，如"特别规定"
    clause: str = ""                 # 条文，如"第五十七条"（节内为"第X节 节名·第X条"）
    content: str = ""                # 清洗后的正文
    keywords: list = field(default_factory=list)  # 关键词
    source: str = ""                 # 来源行号，如"中华人民共和国劳动合同法.md L31-34"


def _normalize(text: str) -> str:
    """合并空白、去掉中文字符间空格（PDF 标题字间空格），并归一康熙部首变体。"""
    text = text.translate(_KANGXI_TABLE)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"(?<=[一-龥])\s+(?=[一-龥])", "", text)


def _is_table_separator(line: str) -> bool:
    """markdown 表格分隔行：| --- | --- |"""
    body = line.replace("|", "").replace(" ", "").replace(":", "")
    return body != "" and all(ch == "-" for ch in body)


def _is_footer_row(row: str) -> bool:
    """判断是否为页眉/页码/网页噪音行。"""
    if not row:
        return True
    compact = re.sub(r"[\s\d]+", "", row)
    if compact == "":
        return True                      # 纯数字页码
    if _DATE_RE.match(row) or _PAGE_MARKER_RE.match(row) or _URL_RE.match(row):
        return True
    if "_" in row:                        # 页眉文件名（如"…劳动法_…人力资源和社会保障部"）
        return True
    return any(kw in row for kw in _NOISE_KEYWORDS)


def _process_table(block: str) -> list[str]:
    """提取 markdown 表格中的有效文本，过滤页眉与分隔行。"""
    rows: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or _is_table_separator(line):
            continue
        cells = [re.sub(r"\s+", "", c.strip()) for c in line.strip("|").split("|")]
        cells = [c for c in cells if c]
        row = " ".join(cells).strip()
        if not _is_footer_row(row):
            rows.append(row)
    return rows


def _smart_join(a: str, b: str) -> str:
    """拼接被 PDF 折行的两行文本：CJK 直接相连，数字/字母间加空格。"""
    if not a:
        return b
    if re.search(r"[一-龥]$", a) and re.search(r"^[一-龥]", b):
        return a + b
    if re.search(r"[a-zA-Z0-9%.)、]$", a) and re.search(r"^[a-zA-Z0-9(]", b):
        return a + " " + b
    return a + " " + b


def extract_knowledge_points(md_path: Path) -> list[KnowledgePoint]:
    """从 markdown 文件提取知识点列表（粒度：章→节→条，每一条一个知识点）。"""
    raw_lines = md_path.read_text(encoding="utf-8").splitlines()

    points: list[KnowledgePoint] = []
    chapter = chapter_title = section = clause = ""
    buf: list[str] = []
    start_line = 0
    body = False  # 是否已进入正文（首个"第X条"之后）
    current_article_no = 0  # 当前条文序号，用于兜底识别交叉引用

    def flush() -> None:
        nonlocal buf
        if buf and chapter and body:
            content = "\n".join(buf)
            title = f"{chapter}·{clause}" if clause else f"{chapter} {chapter_title}"
            kp = KnowledgePoint(
                id=f"KP_{len(points) + 1:03d}",
                title=title,
                chapter=chapter,
                chapter_title=chapter_title,
                clause=clause,
                content=content,
                source=f"{md_path.name} L{start_line}-{line_no}",
            )
            kp.keywords = extract_keywords(kp, content)
            points.append(kp)
        buf = []

    def handle_header(stripped: str, this_line: int) -> bool:
        """在保留空白的原始行上检测章/节/条头；命中则更新状态并返回 True。

        先做康熙部首归一化（法律 PDF 常用"第⼀条"变体，原始行不匹配数字字符类），
        再匹配正则；空白判定仍基于归一化前/后一致的空白，正文折行处的交叉引用
        （"第三十九条和第四十条"，条后无空白）依旧被排除。
        """
        nonlocal chapter, chapter_title, section, clause, buf, start_line, body, current_article_no
        stripped = stripped.translate(_KANGXI_TABLE)
        m = _CHAPTER_RE.match(stripped)
        if m:
            flush()
            chapter = f"第{m.group(1)}章"
            chapter_title = _normalize(m.group(2))
            section = ""
            clause = ""
            buf = []
            start_line = this_line
            return True
        sm = _SECTION_RE.match(stripped)
        if sm and chapter:
            flush()
            section = f"第{sm.group(1)}节 {_normalize(sm.group(2))}"
            clause = ""
            buf = []
            start_line = this_line
            return True
        am = _ARTICLE_RE.match(stripped)
        if am and chapter:
            no = _cn_num(am.group(1))
            if no < current_article_no:
                return False             # 交叉引用（条文号带空白），按正文继续
            current_article_no = no
            body = True
            flush()
            article = f"第{am.group(1)}条"
            clause = f"{section}·{article}" if section else article
            rest = _normalize(am.group(2))
            buf = [rest] if rest else []
            start_line = this_line
            return True
        return False

    i = 0
    n = len(raw_lines)
    while i < n:
        raw = raw_lines[i]
        line_no = i + 1
        stripped = raw.lstrip(" \t　")
        if not stripped:
            i += 1
            continue
        if stripped.startswith("|"):       # 表格块（目录或正文表格）
            j = i
            while j < n and raw_lines[j].lstrip().startswith("|"):
                j += 1
            for row in _process_table("\n".join(raw_lines[i:j])):
                if handle_header(row, line_no):
                    continue
                if body and chapter:       # 正文表格：内容并入当前条文
                    norm = _normalize(row)
                    if buf:
                        buf[-1] = _smart_join(buf[-1], norm)
                    else:
                        buf.append(norm)
                        start_line = line_no
            i = j
            continue
        norm = _normalize(stripped)
        if not norm or _is_footer_row(norm):
            i += 1
            continue                        # 页眉/页码/网页噪音
        if re.search(r"\.{3,}", stripped) and re.search(r"\d+\s*$", stripped):
            i += 1
            continue                        # 目录点线行
        if handle_header(stripped, line_no):
            i += 1
            continue
        if body and chapter:                # 正文内容
            if buf:
                buf[-1] = _smart_join(buf[-1], norm)
            else:
                buf.append(norm)
                start_line = line_no
        i += 1
    flush()

    return points


def extract_keywords(kp: KnowledgePoint, content: str, top_k: int = 8) -> list[str]:
    """基于 jieba TF-IDF 抽取正文关键词，并入章节/条文清洗后的主题词，过滤法律停用词。"""
    tags = jieba.analyse.extract_tags(content, topK=top_k)
    extra = []
    for part in (kp.clause, kp.chapter_title):
        cleaned = re.sub(r"第[一二三四五六七八九十百零]+[章节条]", "", part)
        cleaned = re.sub(r"[\s·、，。；：（）()\"'《》]", "", cleaned)
        if cleaned:
            extra.append(cleaned)
    merged: list[str] = []
    for tag in extra + tags:
        if tag and tag not in merged and len(tag) > 1 and tag not in _LEGAL_STOPWORDS:
            merged.append(tag)
    return merged
