"""从对话中抽取「值得长期记住的用户事实」（用户画像）。

**低频调用**（默认每 `PROFILE_EXTRACT_EVERY` 轮或会话退出时抽一次），
用轻量模型 `qwen-turbo`，避免每轮都付一次 LLM 成本。

只抽**与用户自身相关、且跨会话仍有用**的事实（关注领域、身份/职业、所在地区、
已明确的诉求），不抽法律结论（那是知识库的事），不抽敏感信息（身份证号、银行卡等）。
任何失败都返回空列表——画像抽取失败不该影响问答。
"""
from __future__ import annotations

import json
import re

from app.config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, PROFILE_MODEL

_MAX_FACTS = 3

_SYSTEM_PROMPT = (
    "你从用户与法律问答助手的对话中，抽取**值得长期记住的用户事实**，用于后续个性化回答。\n"
    "只输出 JSON：{\"facts\": [\"事实1\", \"事实2\"]}\n\n"
    "抽取标准（宁缺勿滥）：\n"
    "- 用户自身相关且跨会话仍有用的信息：关注的领域/诉求、身份或职业、所在地区、"
    "已明确的条件（如\"在深圳工作\"、\"是HR\"、\"关心竞业限制\"）。\n"
    "- **不要**抽取：法律结论或法条本身、一次性的提问内容、"
    "敏感个人信息（身份证号、银行卡、手机号、住址门牌、健康隐私等）。\n"
    "- 没有值得长期记住的信息就返回空数组。\n"
    "每条事实不超过 40 字，最多 3 条。只输出 JSON，不要解释。"
)


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_facts(text: str) -> list[str]:
    """容错解析画像抽取结果；解析不出返回空列表。"""
    data = _extract_json(text)
    if data is None:
        return []
    raw = data.get("facts") or []
    if isinstance(raw, str):
        raw = [raw]
    facts: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            fact = str(item).strip()[:40]
            if fact and fact not in facts:
                facts.append(fact)
    return facts[:_MAX_FACTS]


class ProfileExtractor:
    """画像抽取器（轻量 LLM）；失败一律返回空列表。"""

    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.model = model or PROFILE_MODEL
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def extract(self, history: list[dict]) -> list[str]:
        """从最近的对话消息里抽取用户事实（history 为 [{role, content}, ...]）。"""
        if not self.api_key or not history:
            return []
        dialogue = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '助手'}：{m.get('content', '')[:300]}"
            for m in history[-10:]
        )
        try:
            resp = self._get_client().chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": dialogue},
                ],
            )
            content = (resp.choices[0].message.content or "") if resp.choices else ""
            return parse_facts(content)
        except Exception:
            return []
