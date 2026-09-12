"""检索反思器：判断"是否需要继续沿法条引用关系补充检索"。

反思式多跳的闸门。每轮把「已收集的法条」与「候选被引条文」交给 LLM，由它自主决定：
- `sufficient`：现有法条是否已足以支撑一个有法条依据的回答
- `fetch`：候选里哪几条确实与问题相关、值得补进上下文

**只做路由决策，不回答问题**；知识本体仍是 wiki + 图谱，"非 RAG"约束不受影响。

安全设计：
- **候选集白名单**：候选条文显式编号列出，`fetch` 里的编号必须落在候选集内，
  否则丢弃——绝不允许模型自由生成条号或 KP id（否则会召回不存在的知识点）。
- **fail-closed**：输出解析不出/调用异常/无 API_KEY → 一律"停止继续检索"
  （保留已有种子）。这与意图识别的"保守放行"方向相反：那里放行是为了不误拒，
  这里停下是为了不失控、不把无关条文塞进上下文。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    REFLECT_MODEL,
    REFLECT_TIMEOUT_S,
)

# 每条条文注入反思提示词的最大字数（控制 token 消耗）
_EXCERPT_CHARS = 300
# 已收集条文最多展示多少条，避免提示词过长
_MAX_SHOWN = 12

_SYSTEM_PROMPT = (
    "你是法律问答系统的检索反思器。系统已用混合检索定位到若干法条，"
    "现在判断**是否需要继续沿法条引用关系补充检索**。\n\n"
    "你只做判断，不回答用户问题。只输出一个 JSON：\n"
    '{"sufficient": true 或 false, "fetch": [候选编号...], "reason": "一句话理由"}\n\n'
    "判断规则：\n"
    "1. sufficient：已收集的法条是否**足以支撑一个有法条依据的回答**。"
    "只有当问题涉及的情形必须靠已收集条文之外的法条才能讲全时，才判 false。\n"
    "2. fetch：从【候选条文】里挑出**确实与问题相关、能补全回答**的编号；"
    "与问题无关的不要填。不需要补充就填空数组。\n"
    "3. fetch 只能填【候选条文】里出现过的编号（数字），**不要编造编号或条号**。\n"
    "只输出 JSON，不要 markdown 代码块，不要额外解释。"
)


@dataclass
class ReflectionResult:
    sufficient: bool
    fetch_ids: list[str] = field(default_factory=list)
    reason: str = ""


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _as_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1", "是"):
            return True
        if low in ("false", "no", "0", "否"):
            return False
    return None


def _as_index(value) -> int | None:
    """从 1 / "1" / "C1" / "[1]" 中取出候选编号。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        return int(m.group(0)) if m else None
    return None


def parse_reflection(text: str, candidates: list[dict]) -> ReflectionResult:
    """容错解析反思输出；解析不出则 fail-closed（停止继续检索）。"""
    data = _extract_json(text)
    if data is None:
        return ReflectionResult(True, [], "反思输出无法解析，停止继续检索")

    raw_fetch = data.get("fetch") or []
    if isinstance(raw_fetch, (str, int)):
        raw_fetch = [raw_fetch]
    fetch_ids: list[str] = []
    if isinstance(raw_fetch, list):
        for item in raw_fetch:
            idx = _as_index(item)
            if idx is not None and 1 <= idx <= len(candidates):   # 白名单：越界即丢弃
                cid = candidates[idx - 1]["id"]
                if cid not in fetch_ids:
                    fetch_ids.append(cid)

    sufficient = _as_bool(data.get("sufficient"))
    if sufficient is None:
        # 缺字段时以"有没有要取的条文"为准，避免误继续
        sufficient = not fetch_ids
    return ReflectionResult(sufficient, fetch_ids, str(data.get("reason", ""))[:200])


def _render(collected: list[dict], candidates: list[dict]) -> str:
    """渲染已收集条文与候选条文（候选带编号，供模型引用）。"""
    lines = ["【已收集的法条】"]
    for node in collected[:_MAX_SHOWN]:
        lines.append(f"- {node.get('title', '')}\n  {node.get('content', '')[:_EXCERPT_CHARS]}")
    lines.append("\n【候选条文（这些条文被上面某条正文引用）】")
    for i, node in enumerate(candidates, 1):
        lines.append(f"[{i}] {node.get('title', '')}\n    {node.get('content', '')[:_EXCERPT_CHARS]}")
    return "\n".join(lines)


class Reflector:
    """基于轻量 LLM 的检索反思器；任何失败都停止继续检索（fail-closed）。"""

    def __init__(
            self,
            api_key: str | None = None,
            base_url: str | None = None,
            model: str | None = None,
            timeout: float | None = None,
    ):
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.model = model or REFLECT_MODEL
        self.timeout = timeout or REFLECT_TIMEOUT_S
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
        return self._client

    def reflect(
            self, query: str, collected: list[dict], candidates: list[dict]
    ) -> ReflectionResult:
        """反思本轮是否继续；`candidates` 为空时**不调用 LLM** 直接停止。"""
        if not candidates:
            return ReflectionResult(True, [], "无可跟进的引用关系，停止")
        if not self.api_key:
            return ReflectionResult(True, [], "未配置 API_KEY，停止继续检索")
        try:
            resp = self._get_client().chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"【用户问题】\n{query}\n\n{_render(collected, candidates)}"},
                ],
            )
            content = (resp.choices[0].message.content or "") if resp.choices else ""
            return parse_reflection(content, candidates)
        except Exception as exc:               # 网络/超时/接口异常都停止继续
            return ReflectionResult(True, [], f"反思调用异常，停止继续检索：{exc}"[:200])
