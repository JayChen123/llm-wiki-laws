"""从 markdown 源文件提取知识点（面向法律法规文档）。

markitdown 转换产物含较多噪音（页眉日期、孤立页码、目录/元数据、
网页页脚），且部分法律 PDF 的文本使用康熙部首变体字符（如"第⼀条"）。
本模块先做清洗与字符规范化，再按"编（第X编）→ 章（第X章）→ 节（第X节）→ 条（第X条）"
层级切分知识点（粒度：每一条一个知识点），并抽取关键词。编为可选层级（民法典有，
劳动法/劳动合同法没有）；民法典各编章号重新起算，故编必须参与去重与分组。

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

# 中文数字字符集（编/章/节/条编号通用）。
# 含"千"：民法典"第一千二百六十条"等千位条号；含"零"：第一百零一条等；
# 缺任一个都会整段漏切（民法典曾因缺"千"丢失第1000-1260条共261条）。
CN_NUM_CHARS = "一二三四五六七八九十百千零"

# 编/章/节/条边界（原始行，要求"第X编/章/节/条"后跟空白）
_PART_RE = re.compile(rf"^第([{CN_NUM_CHARS}]+)编[　\s]+(.+)$")
# "分编"（民法典物权编/合同编内部的分组）不单独建模，只作结构边界
_SUBPART_RE = re.compile(rf"^第([{CN_NUM_CHARS}]+)分编[　\s]+(.+)$")
_CHAPTER_RE = re.compile(rf"^第([{CN_NUM_CHARS}]+)章[　\s]+(.+)$")
_SECTION_RE = re.compile(rf"^第([{CN_NUM_CHARS}]+)节[　\s]+(.+)$")
_ARTICLE_RE = re.compile(rf"^第([{CN_NUM_CHARS}]+)条[　\s]+(.*)$")

# 法律领域高频通用词：过滤后降低关键词边的重合密度
_LEGAL_STOPWORDS = {
    "用人单位", "劳动者", "劳动合同", "劳动关系", "本法", "应当", "按照", "依照",
    "规定", "有关", "其他", "以及", "不得", "可以", "法律", "法规", "行政法规",
    "条例", "情形", "下列", "当事人", "部门", "人民政府", "工作人员", "单位",
    "人员", "明确", "制定", "建立", "维护", "保证",
}

_CN_DIGITS = "零一二三四五六七八九"


def cn_to_int(s: str) -> int:
    """中文数字转整数（支持个/十/百/千/零，如 九十八→98、一百零三→103、一千二百六十→1260）。"""
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
        elif ch == "千":
            total += (num if num else 1) * 1000
            num = 0
    return total + num


# 页眉/页脚/网页噪音特征
_NOISE_KEYWORDS = (
    "加入收藏", "网站无障碍", "网站声明", "网站管理", "网站地图", "联系我们",
    "网站值班", "主办单位", "京ICP", "网站标识码", "版权所有", "版权",
)
# 页眉日期两种写法：2024/11/15（转换日，可带时间）、8/23/2020（网页日期）
_DATE_RE = re.compile(r"^(?:\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4})(?!\d)")
_PAGE_MARKER_RE = re.compile(r"^\d+/\d+$")
# 裸域名也要过滤：民法典页眉是 www.npc.gov.cn/... （无协议前缀）
_URL_RE = re.compile(r"^(?:https?://|www\.)")


@dataclass
class KnowledgePoint:
    id: str
    title: str                       # 知识点标题，如"第五章·第五十七条"（有编时为"第二编 物权·第二章·第十三条"）
    chapter: str                     # 章节号，如"第五章"
    chapter_title: str               # 章节标题，如"特别规定"
    part: str = ""                   # 编号，如"第二编"（无"编"的法律为空）
    part_title: str = ""             # 编标题，如"物权"；民法典 7 编各自重排章号，靠它区分同号章
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
    """从 markdown 文件提取知识点列表（粒度：编→章→节→条，每一条一个知识点）。"""
    # 按物理行（仅 \n）拆分：splitlines() 会把换页符 \f 也当换行，
    # 使 source 行号比编辑器实际行号大（民法典 130 处 \f，漂移 129 行，溯源对不上）。
    raw_lines = md_path.read_text(encoding="utf-8").split("\n")
    # 一物理行内的 \f 是页分隔符，拆成多段但共用同一物理行号：
    # 行号与编辑器一致，且页眉噪音仍能逐段过滤
    segments: list[tuple[int, str]] = [
        (line_no, seg)
        for line_no, line in enumerate(raw_lines, 1)
        for seg in line.split("\f")
    ]

    points: list[KnowledgePoint] = []
    part = part_title = chapter = chapter_title = section = clause = ""
    buf: list[str] = []
    start_line = 0
    body = False  # 是否已进入正文（首个"第X条"之后）
    current_article_no = 0  # 当前条文序号，用于兜底识别交叉引用

    def flush() -> None:
        nonlocal buf
        # 必须有 clause：章/节标题本身不是知识点（民法典页眉噪音曾让 34 个章标题单独成点）
        if buf and chapter and clause and body:
            content = "\n".join(buf)
            # 有"编"时标题带编名（如"第二编 物权·第二章·第十三条"）：
            # 编名（物权/婚姻家庭/继承/侵权责任）参与 BM25 索引与语义编码，且便于 LLM 区分同号章
            head = f"{part} {part_title}·{chapter}" if part else chapter
            kp = KnowledgePoint(
                id=f"KP_{len(points) + 1:03d}",
                title=f"{head}·{clause}",
                chapter=chapter,
                chapter_title=chapter_title,
                part=part,
                part_title=part_title,
                clause=clause,
                content=content,
                source=f"{md_path.name} L{start_line}-{line_no}",
            )
            kp.keywords = extract_keywords(kp, content)
            points.append(kp)
        buf = []

    def handle_header(stripped: str, this_line: int) -> bool:
        """在保留空白的原始行上检测编/章/节/条头；命中则更新状态并返回 True。

        先做康熙部首归一化（法律 PDF 常用"第⼀条"变体，原始行不匹配数字字符类），
        再匹配正则；空白判定仍基于归一化前/后一致的空白，正文折行处的交叉引用
        （"第三十九条和第四十条"，条后无空白）依旧被排除。正则均以 ^ 锚定行首，
        故正文中"依照本法第一编第六章…"这类跨引用不会被误判为层级标题。
        """
        nonlocal part, part_title, chapter, chapter_title, section, clause, buf, start_line, body, current_article_no
        stripped = stripped.translate(_KANGXI_TABLE)
        pm = _PART_RE.match(stripped)
        if pm:
            flush()
            part = f"第{pm.group(1)}编"
            part_title = _normalize(pm.group(2))
            # 编内章号重新起算，章上下文必须一并清空，避免章标题前的内容挂到上一编
            chapter = chapter_title = section = clause = ""
            buf = []
            start_line = this_line
            return True
        sbm = _SUBPART_RE.match(stripped)
        if sbm:
            # 分编标题（如"第二分编 典型合同"）不产生知识点，但必须是结构边界：
            # 否则标题文字会被并入上一条文正文（民法典曾有 6 条正文被污染）。
            # 所属"编"不变，仅重置其下的章上下文。
            flush()
            chapter = chapter_title = section = clause = ""
            buf = []
            start_line = this_line
            return True
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
            no = cn_to_int(am.group(1))
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
    n = len(segments)
    while i < n:
        line_no, raw = segments[i]
        stripped = raw.lstrip(" \t　")
        if not stripped:
            i += 1
            continue
        if stripped.startswith("|"):       # 表格块（目录或正文表格）
            j = i
            while j < n and segments[j][1].lstrip().startswith("|"):
                j += 1
            for row in _process_table("\n".join(s for _, s in segments[i:j])):
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
    """基于 jieba TF-IDF 抽取正文关键词，并入编/章节/条文清洗后的主题词，过滤法律停用词。"""
    tags = jieba.analyse.extract_tags(content, topK=top_k)
    extra = []
    for field in (kp.clause, kp.chapter_title, kp.part_title):
        cleaned = re.sub(rf"第[{CN_NUM_CHARS}]+[编章节条]", "", field)
        cleaned = re.sub(r"[\s·、，。；：（）()\"'《》]", "", cleaned)
        if cleaned:
            extra.append(cleaned)
    merged: list[str] = []
    for tag in extra + tags:
        if tag and tag not in merged and len(tag) > 1 and tag not in _LEGAL_STOPWORDS:
            merged.append(tag)
    return merged
