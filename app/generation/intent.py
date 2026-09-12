"""问答意图识别：区分"本库可答的法律问题"与"未覆盖的法律问题 / 无关问题"。

问答前先分类，非"可答"路径直接走兜底话术，**不进检索与生成**：
- ANSWERABLE        法律相关，且能由本库覆盖的法律（民法典/劳动法/劳动合同法）回答
- LEGAL_UNCOVERED   法律相关，但属本库未覆盖的部门法（刑法/行政法/诉讼法/公司法…）
- OFF_TOPIC         与法律无关（天气、编程、闲聊…）

分类只用于**路由**，不参与知识定位；知识本体仍是 wiki + 关联图谱，
"非 RAG"约束不受影响（向量只参与召回与用户记忆，不承载法律知识）。

设计要点：**保守放行**——分类器超时/无 key/输出解析不出，一律归 ANSWERABLE。
宁可多答一次，也不因分类器故障把正常法律问题挡在门外。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

from app.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    INTENT_MODEL,
    INTENT_TIMEOUT_S,
)

# 本库覆盖的法律（提示词与兜底话术共用；新增知识源时同步这里）
COVERED_LAWS = "《中华人民共和国民法典》《中华人民共和国劳动法》《中华人民共和国劳动合同法》"


class Intent(str, Enum):
    ANSWERABLE = "answerable"
    LEGAL_UNCOVERED = "legal_uncovered"
    OFF_TOPIC = "off_topic"


@dataclass
class IntentResult:
    intent: Intent
    reason: str = ""


_SYSTEM_PROMPT = (
    "你是法律问答系统的意图分类器。判断用户问题属于哪一类，只输出一个 JSON，"
    '格式为 {"intent": "answerable|legal_uncovered|off_topic", "reason": "一句话理由"}。\n\n'
    f"- answerable：面向普通用户的法律咨询，且能由本知识库覆盖的法律（{COVERED_LAWS}）回答"
    "（如劳动权益、劳动合同、加班与工资、经济补偿、合同、婚姻家庭、继承、侵权责任、物权等）。\n"
    "- legal_uncovered：是法律问题，但属于本知识库未覆盖的部门法（如刑法、行政法、"
    "民事诉讼法/刑事诉讼法、公司法、证券法、知识产权法、税法等），或询问诉讼流程等程序问题。\n"
    "- off_topic：与法律无关（如天气、编程、生活闲聊、常识问答）。\n\n"
    "只输出 JSON，不要 markdown 代码块，不要任何额外解释。"
)

# 分类器不可用时的放行结果（含原因，便于日志追溯）
_UNAVAILABLE = IntentResult(Intent.ANSWERABLE, "分类器不可用，按可答放行")
_UNPARSABLE = IntentResult(Intent.ANSWERABLE, "分类结果无法解析，按可答放行")

_JSON_RE = re.compile(r"\{.*?\}", re.S)


def parse_intent(text: str) -> IntentResult:
    """容错解析分类器输出：容忍 markdown 围栏与前后多余文字，解析不出则保守放行。"""
    match = _JSON_RE.search(text or "")
    if match:
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            raw = str(data.get("intent", "")).strip().lower()
            for intent in Intent:
                if raw == intent.value:
                    return IntentResult(intent, str(data.get("reason", ""))[:200])
    # 退化：输出里直接出现枚举值（如模型只回了 legal_uncovered）
    lowered = (text or "").lower()
    for intent in Intent:
        if intent.value in lowered:
            return IntentResult(intent, "")
    return _UNPARSABLE


def fallback_answer(intent: Intent) -> str:
    """非可答路径的兜底话术（不检索、不生成）。"""
    if intent is Intent.LEGAL_UNCOVERED:
        return (
            f"抱歉，本知识库目前只覆盖 {COVERED_LAWS}，"
            "你问的属于其他法律领域，我无法据此作答，以免给你错误结论。\n"
            "如果问题涉及合同、婚姻家庭、继承、侵权责任、物权、人格权、"
            "劳动权益、劳动合同、加班与工资、经济补偿等，欢迎换个角度问我。"
        )
    return (
        "抱歉，我是法律问答助手，只能回答法律相关的问题。\n"
        f"当前知识库覆盖 {COVERED_LAWS}，例如你可以问我：\n"
        "· 试用期最长可以约定多久？\n"
        "· 离婚冷静期是多久？\n"
        "· 加班费应该怎么计算？"
    )


class IntentClassifier:
    """基于轻量 LLM 的意图分类器：任何异常都保守放行（ANSWERABLE）。"""

    def __init__(
            self,
            api_key: str | None = None,
            base_url: str | None = None,
            model: str | None = None,
            timeout: float | None = None,
    ):
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.model = model or INTENT_MODEL
        self.timeout = timeout or INTENT_TIMEOUT_S
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
        return self._client

    def classify(self, query: str) -> IntentResult:
        """分类单条问题；失败一律放行，异常信息写进 reason 便于排查。"""
        if not self.api_key:
            return _UNAVAILABLE
        try:
            resp = self._get_client().chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ],
            )
            content = (resp.choices[0].message.content or "") if resp.choices else ""
            return parse_intent(content)
        except Exception as exc:                     # 网络/超时/接口异常都放行
            return IntentResult(Intent.ANSWERABLE, f"分类器异常，按可答放行：{exc}"[:200])
