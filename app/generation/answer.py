"""回答生成：基于混合检索结果 + 短期记忆，由 qwen-max 综合生成。"""
from __future__ import annotations

from app.config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, LLM_MODEL

SYSTEM_PROMPT = (
    "你是中国劳动法领域问答助手，依据《中华人民共和国劳动法》《中华人民共和国劳动合同法》"
    "等法律法规解答劳动权益、劳动合同、工作时间、休息休假、工资报酬、社会保险、劳动争议等问题。"
    "请仅依据【参考资料】中给出的知识点回答，使用中文，条理清晰；涉及具体规定时点明法律依据"
    "（如\"依据《劳动合同法》第五十八条\"）。若问题需要综合多个知识点才能回答（多跳推理），"
    "请跨知识点组合、逻辑推理并说明依据，不要编造；单知识点可回答的按单点作答。"
    "若参考资料不足以回答，请如实说明不确定，不要臆造法条。"
    "回答末尾另起一行列出引用的全部知识点标题（格式：引用：【标题1】【标题2】）。"
)


class AnswerGenerator:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None):
        from openai import OpenAI

        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.model = model or LLM_MODEL
        if not self.api_key:
            raise RuntimeError("缺少 API_KEY 环境变量，无法调用大模型")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate_stream(
        self,
        query: str,
        contexts: list[dict],
        history: list[dict] | None = None,
    ):
        """流式生成回答，逐块 yield 文本片段（供 CLI 打字机式输出）。"""
        reference = self._format_contexts(contexts)
        user_prompt = (
            f"【参考资料】\n{reference}\n\n【问题】\n{query}\n\n请回答："
        )
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history or [])
        # 截断历史避免过长
        messages.append({"role": "user", "content": user_prompt})
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.3,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def generate(
        self,
        query: str,
        contexts: list[dict],
        history: list[dict] | None = None,
    ) -> str:
        """非流式生成回答（聚合 generate_stream 的完整结果）。"""
        return "".join(self.generate_stream(query, contexts, history)).strip()

    @staticmethod
    def _format_contexts(contexts: list[dict]) -> str:
        parts = []
        for i, ctx in enumerate(contexts, 1):
            law = ""
            src = ctx.get("source", "")
            if ".md" in src:                    # 同章同条可能跨两部法律，标题带法律名避免张冠李戴
                law = f"{src.split('.md')[0].strip()}·"
            title = f"{law}{ctx['title']}"
            body = ctx["content"][:600]
            parts.append(f"{i}. [{title}]\n{body}")
        return "\n\n".join(parts)
