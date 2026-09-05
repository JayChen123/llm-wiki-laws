# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 Python 3.13+ 的劳动法知识问答系统（llm-wiki 理念，**非 RAG**），虚拟环境由本地 **uv** 管理。逻辑分两阶段：

- **摄入（Ingest）**：把 PDF / `data/raw/*.md` 提前"编译"为结构化知识点（当前 205 个，**每一条法条一个知识点**）+ 关联图谱 `knowledge_graph.json`（2962 条边）+ Wiki 页面集 `wiki/`。
- **查询（Query）**：关键词(BM25) + 向量(bge-m3) 混合检索定位知识点，qwen-max 综合生成回答，带 SQLite 短期记忆（`memory.db`）。

**为什么不是 RAG**：知识本体是 Markdown 页面 + JSON 图谱（可读、可审计、可随问答复利积累），向量仅参与查询时的召回融合；文档不做切块、向量不作为知识库载体。这一约束贯穿所有代码，改动前先想清楚。

完整细节见 `README.md`（架构/评估）与 `HANDOVER.md`（交接/踩坑），本文件只摘录关键。

## 常用命令

统一用 `uv run python -m ...` 形式（`python -m` 把项目根加入 sys.path，裸 `pytest` 可能因 `app` 包不在路径而失败）：

```bash
uv sync                                             # 安装/还原依赖（含 dev: pytest）
uv run python -m app.ingest.pipeline                # 构建知识库：扫 data/src、data/raw，全量重建图谱+wiki（幂等）
uv run python -m app.cli                            # 命令行问答（需 export API_KEY 才有 LLM 生成，否则仅展示检索）
uv run python -m pytest tests/ -v                   # 全部单元测试（当前 42 项）
uv run python -m pytest tests/test_retrieval.py -v  # 跑单个测试文件
uv run python -m pytest tests/ -k "rrf or bfs" -v   # 按名称过滤
uv run python -m eval.evaluate --limit 3            # 评估试跑 3 条
uv run python -m eval.evaluate --skip-ragas         # 仅 recall@k（不调 LLM）
```

**测试全部离线**：用 `DummyEmbedder`（embedder.py:44）与 mock LLM，不加载真实 bge-m3、不调 DashScope。只有 eval 与 CLI 生成才需要真实模型和 `API_KEY`。

## 架构（需跨文件才能看清的部分）

代码按阶段分层在 `app/`：`ingest/`（构建）、`retrieval/`（混合检索）、`generation/`（生成）、`memory/`（SQLite）；`kb.py` 是查询统一入口。

**查询数据流**：`cli.py` 把最近一问并入检索词（解"那/具体/继续"指代）→ `KnowledgeBase.search`（kb.py:40）→ `HybridRetriever.search`（hybrid_retriever.py:80）：
1. `keyword_retriever`：jieba 分词 + BM25Okapi，索引含标题/正文/**概念增强后关键词**（口语词可命中法条）；
2. `vector_retriever`：bge-m3 余弦（向量缓存 `wiki/knowledge_vectors.npy`；无缓存时混合器退化为纯关键词）；
3. RRF 融合 `Σ 1/(60+rank)` 取 Top-K（默认 5）；
4. 沿关联图 BFS ≤3 跳扩展（分数 = 种子 × 边权重 × 0.5^hop，每跳剪枝）补 EXPAND_K=3；
5. `generation/answer.py` 拼上下文给 qwen-max 流式生成（带引用），结果写 `memory.db` 与 `logs/qa.log`。

**KnowledgePoint**（ingest/knowledge_extract.py:73）：按"章（第X章）→ 节（第X节）→ 条（第X条）"切分，字段 id/title/chapter/chapter_title/clause/content/keywords/source。多文档合并后**全局统一编号 KP_001…**。清洗要点：页眉日期/孤立页码/网址/网页脚注噪音、康熙部首变体字符归一（U+2F00 区间 + U+2E80 的 ⺠⻅⻓⻔ 手工映射）、目录阶段丢弃、条文头要求"第X条"后跟空白（否则正文交叉引用会被误当新条）。

**关联图四类带类型边**（ingest/graph_build.py）：

| 边 | 来源 | 权重/阈值 |
|---|---|---|
| sibling | 同章节条文 | 1.0 |
| keyword | 关键词 Jaccard | ≥0.3 |
| semantic | bge-m3 余弦 | ≥0.75 |
| crossref | 正文"第X条"交叉引用 | 1.5（**仅同法内**，跨法不建边）|

crossref 是让"46条引38条/65条引39条"等被引用条文稳定进入生成上下文的关键手段（权重大于 sibling）。

**上下文标题带法律名**：`answer.py` 的 `_format_contexts` 从 `source` 的文件名前缀拼入标题（如 `中华人民共和国劳动合同法·第四章·第三十九条`），防两部法律同条号张冠李戴。做任何跨法/多文档相关改动都要保持这一点。

## 配置（双份）

- 根 `config.py`：**用户维护的单一配置源**，含 MySQL/Redis/Milvus 等无关旧配置，本系统只用 `bge_m3`、`model`、`dashscope_api_key`、`dashscope_base_url`。其中 `bge_m3` 是**机器相关的本地模型路径**，换环境先改这里；该路径指向 Itcast 目录仅是用户提供的模型配置值，**不作为实现参考**。
- `app/config.py`：路径常量与检索参数（TOP_K=5 / EXPAND_K=3 / EXPAND_HOPS=3 / EXPAND_DECAY=0.5），启动时 `sys.path` 注入项目根，并复用根 `config.py`。

数据新增/更换：把 PDF 放 `data/src/` 或 md 直接放 `data/raw/` 后重跑 pipeline 即自动并入（PDF→MD 增量、知识点合并重编号、图谱与 Wiki 全量重建）。想让概念出现在"核心概念导航"（`wiki/index.md`），需在 `ingest/concept_map.py` 补映射；Wiki 页面落盘在 `ingest/wiki_writer.py`。

## 关键版本约束（勿随意改）

`ragas==0.3.0` + `langchain-community==0.4.0` + langchain 1.x（`langchain-core`/`langchain-openai`）是**唯一已验证兼容**组合；ragas 0.4.x 与 langchain-community≥0.4.2 不兼容。改动这三个版本前先确认。

## 开发规范

### 0. 测试框架

本项目使用 **pytest** 作为测试框架。测试文件放在 `tests/` 目录下，文件命名遵循 `test_*.py` 规范。

### 1. 先理解，再方案，后确认

收到任何开发需求后，必须遵循以下流程：

1. **理解需求**：仔细分析需求，确保完全理解意图。
2. **整理方案**：梳理实现思路，包括涉及的文件、改动范围、技术方案。
3. **输出方案**：用简洁清晰的语言向用户说明大致的处理思路。
4. **等待确认**：**必须在用户明确同意方案后**，才能开始编写代码。

**禁止**在未获得用户确认的情况下直接开始编码。

### 2. 需求不明必须及时确认

如果需求存在以下情况，**必须先向用户提问澄清**：

- 描述模糊或有歧义
- 信息不完整，缺少关键细节
- 存在多种可能的理解方式
- 边界情况未明确

**禁止**自行假设或猜测用户意图后直接执行。

### 3. 新功能必须创建单元测试

每次开发新功能，必须同步创建对应的单元测试：

- 测试文件放在 `tests/` 目录下
- 测试函数命名清晰，反映测试场景
- 覆盖正常路径和关键边界情况

### 4. 修改后必须运行单元测试

每次功能修改完成后，必须运行 pytest 验证：

```bash
uv run python -m pytest tests/ -v
```

- 确保全部测试通过
- 如有测试失败，必须先修复再通知用户

### 5. 测试通过后再通知验收

只有所有单元测试通过后，才能通知用户进行检查验收。通知时应简要说明：

- 修改了哪些内容
- 测试结果（全部通过）

如果测试未通过，不要通知用户验收，先定位并修复问题。
如果项目包含了前后端系统的，尤其是有前端页面的需要调用"控制浏览器的MCP工具"，测试输入输出正常，再给我使用。
