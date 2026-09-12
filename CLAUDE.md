# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 Python 3.13+ 的劳动法知识问答系统（llm-wiki 理念，**非 RAG**），虚拟环境由本地 **uv** 管理。逻辑分两阶段：

- **摄入（Ingest）**：把 PDF / `data/raw/*.md` 提前"编译"为结构化知识点（当前 **1465** 个，**每一条法条一个知识点**：劳动合同法 98 + 劳动法 107 + 民法典 1260）+ 关联图谱 `knowledge_graph.json`（**21252** 条边）+ Wiki 页面集 `wiki/`。
- **查询（Query）**：先做**意图识别**（可答 / 法律未覆盖 / 无关，非"可答"直接兜底）→ 关键词(BM25) + 向量(bge-m3，存于 **Milvus**) 混合检索定位知识点 → 召回长期记忆（画像+相似历史，按 user_id 过滤）→ qwen-max 综合生成回答，记忆存 MySQL + Milvus。

**为什么不是 RAG**：知识本体是 Markdown 页面 + JSON 图谱（可读、可审计、可随问答复利积累），向量仅参与查询时的召回融合；文档不做切块、向量不作为知识库载体。这一约束贯穿所有代码，改动前先想清楚。

完整细节见 `README.md`（架构/评估）与 `HANDOVER.md`（交接/踩坑），本文件只摘录关键。

## 常用命令

统一用 `uv run python -m ...` 形式（`python -m` 把项目根加入 sys.path，裸 `pytest` 可能因 `app` 包不在路径而失败）：

```bash
uv sync                                             # 安装/还原依赖（含 dev: pytest）
uv run python -m app.ingest.pipeline                # 构建知识库：扫 data/src、data/raw，全量重建图谱+wiki（幂等）
uv run python -m app.cli                            # 命令行问答（需 export API_KEY 才有 LLM 生成，否则仅展示检索）
uv run python -m pytest tests/ -v                   # 全部单元测试（当前 140 项）
uv run python -m pytest tests/test_retrieval.py -v  # 跑单个测试文件
uv run python -m pytest tests/ -k "rrf or bfs" -v   # 按名称过滤
uv run python -m eval.evaluate --limit 3            # 评估试跑 3 条
uv run python -m eval.evaluate --skip-ragas         # 仅 recall@k（不调 LLM）
```

**测试全部离线**：各测试文件自带假编码器（`tests/test_retrieval.py` 的 `StubEmbedder`、`tests/test_graph_build.py` 的 `FakeEmbedder`）与假向量库（`StubVectorStore`、`tests/test_milvus_store.py` 的 `FakeMilvusClient`）、mock LLM，不加载真实 bge-m3、不连 Milvus、不调 DashScope。只有 eval 与 CLI 生成才需要真实模型和 `API_KEY`。

## 架构（需跨文件才能看清的部分）

代码按阶段分层在 `app/`：`ingest/`（构建）、`retrieval/`（混合检索）、`generation/`（生成）、`memory/`（MySQL 短期+画像 / Milvus 长期向量）；`kb.py` 是查询统一入口。

**查询数据流**：`cli.py` 先做意图识别（`generation/intent.py`：可答 / 法律未覆盖 / 无关；**非"可答"直接兜底，不检索不生成**，但仍写记忆与日志）→ 把最近一问并入检索词（解"那/具体/继续"指代）→ `KnowledgeBase.search` → `HybridRetriever.search`：
1. `keyword_retriever`：jieba 分词 + BM25Okapi，索引含标题/正文/**概念增强后关键词**（口语词可命中法条）；
2. `vector_retriever`：bge-m3 编码 query → **Milvus**（`app/retrieval/vector_store.py`）余弦检索 → `kp_id` 映射回图谱节点；Milvus 不可用时混合器退化为纯关键词（**不再有 `.npy` 文件缓存**）；
3. RRF 融合 `Σ 1/(60+rank)` 取 Top-K（默认 10）；
4. 沿关联图 BFS ≤3 跳扩展（分数 = 种子 × 边权重 × 0.5^hop，每跳剪枝）补 EXPAND_K=5；
   `RETRIEVAL_MODE=reflect`（默认）时，在此基础上再由 **LLM 反思**决定是否沿**引用关系**继续补取
   （`retrieval/reflector.py` + `reflective_retriever.py`）：无引用关系则**不调 LLM 直接停**；
   候选是白名单编号（防幻觉）；`sufficient`/无候选/达 `REFLECT_MAX_ROUNDS`/要取的已在上下文 → 停；
5. `generation/answer.py` 拼上下文（参考资料 + 用户背景）给 qwen-max 流式生成（带引用），结果写 MySQL（短期）+ Milvus（长期历史）与 `logs/qa.log`。

**KnowledgePoint**（ingest/knowledge_extract.py）：按"**编（第X编）→** 章（第X章）→ 节（第X节）→ 条（第X条）"切分，字段 id/title/chapter/chapter_title/**part/part_title**/clause/content/keywords/source。多文档合并后**全局统一编号 KP_001…**。清洗要点：页眉日期（含 `2024/11/15` 与 `8/23/2020` 两种）/孤立页码/网址（含**裸域名 `www.`**）/网页脚注噪音、康熙部首变体字符归一（U+2F00 区间 + U+2E80 的 ⺠⻅⻓⻔ 手工映射）、目录阶段丢弃、条文头要求"第X条"后跟空白（否则正文交叉引用会被误当新条）。

几处**易错且已踩过坑**的点，改这块务必先看：
- **中文数字字符集 `CN_NUM_CHARS` 必须含"千"**（民法典"第一千二百六十条"）。缺了会从第 1000 条起整段漏切；该常量同时被 `cn_to_int`、`graph_build` 的交叉引用正则、关键词清洗正则使用，改一处要一起改。
- **"编"是必需层级**：民法典 7 编各自从"第一章"重新起算，`part` 不参与分组就会把不同编的同号章并成一个大团（曾产生约 1.97 万条假 sibling 边）。
- **"分编"标题只作结构边界**（`_SUBPART_RE`）：既不产生知识点，也不能并入上一条文正文。
- **行号按物理行算**：源文件含换页符 `\f`，用 `splitlines()` 会把 `\f` 当换行、使 `source` 行号比编辑器实际行号大；现按 `\n` 拆行、行内再按 `\f` 分段（共用同一行号）。
- 章/节标题**不产生知识点**：`flush()` 要求 `clause` 非空。

**关联图四类带类型边**（ingest/graph_build.py）：

| 边 | 来源 | 权重/阈值 |
|---|---|---|
| sibling | 同 **(编, 章)** 内条文（分组键必须是二元组，见上） | 1.0 |
| keyword | 关键词 Jaccard | ≥0.3 |
| semantic | bge-m3 余弦 | ≥0.75 |
| crossref | 正文"第X条"交叉引用 | 1.5（**仅同法内**，跨法不建边）|

crossref 是让"46条引38条/65条引39条"等被引用条文稳定进入生成上下文的关键手段（权重大于 sibling）。

**上下文标题带法律名**：`answer.py` 的 `_format_contexts` 从 `source` 的文件名前缀拼入标题（如 `中华人民共和国劳动合同法·第四章·第三十九条`），防两部法律同条号张冠李戴。做任何跨法/多文档相关改动都要保持这一点。

## 配置（单一来源）

- **`app/config.py` 是唯一配置源**（历史上曾分工为"根 `config.py` 用户维护 + app 复用"，但根 `config.py` 已不存在、`app/config.py` 里那行复用是注释掉的，勿再按旧说法找它）。
- 路径常量：`SRC_DIR`/`RAW_DIR`/`WIKI_DIR`/`GRAPH_PATH` 等（`MEMORY_DB_PATH` 仅作迁移脚本源路径，运行时不用）。
- 记忆：`MYSQL_*`（**唯一后端，不做 SQLite 降级/失败回退**）、`MEMORY_COLLECTION`、`MEMORY_RECALL_K`、`MEMORY_INJECT_MODE`（`auto` 默认，仅追问时并入上一问）、`PROFILE_EXTRACT_EVERY`、`PROFILE_MODEL`。
- 模型与大模型：`BGE_MODEL_PATH`（**机器相关的本地模型路径，换环境先改这里**；指向 Itcast 目录仅是用户提供的配置值，**不作为实现参考**）、`LLM_MODEL`、`DASHSCOPE_API_KEY`（读 `API_KEY` 环境变量）、`DASHSCOPE_BASE_URL`。
- Milvus 向量库：`MILVUS_HOST`/`MILVUS_PORT`（默认 **19532**，是宿主机映射端口，容器内为 19530）/`MILVUS_COLLECTION`/`MILVUS_METRIC=COSINE`/`MILVUS_INDEX=HNSW`/`EMBED_DIM=1024`，均可用环境变量覆盖。
- 意图识别：`INTENT_ENABLED`（默认开）、`INTENT_MODEL`（默认 `qwen-turbo`，分类要快）、`INTENT_TIMEOUT_S`（默认 3，**超时即保守放行**）。
- 多跳链路：`RETRIEVAL_MODE`（`reflect` 默认 / `bfs` 对照）、`REFLECT_MODEL`（`qwen-turbo`）、`REFLECT_MAX_ROUNDS`（3）、`REFLECT_TIMEOUT_S`（15）。
- 检索参数：`TOP_K=10`、`EXPAND_K=5`、`EXPAND_HOPS=3`、`EXPAND_DECAY=0.5`（生成上下文最多 15 块）。

数据新增/更换：把 PDF 放 `data/src/` 或 md 直接放 `data/raw/` 后重跑 pipeline 即自动并入（PDF→MD 增量、知识点合并重编号、图谱与 Wiki 全量重建、**向量全量重写 Milvus 并清理陈旧主键**）。想让概念出现在"核心概念导航"（`wiki/index.md`），需在 `ingest/concept_map.py` 补映射；Wiki 页面落盘在 `ingest/wiki_writer.py`。**改知识源后必须重跑 pipeline**——向量库不会自己发现知识库变了（旧 `.npy` 缓存曾因此静默残留 205 行对着 1238 节点）。

## 关键版本约束（勿随意改）

`ragas==0.3.0` + `langchain-community==0.4.0` + langchain 1.x（`langchain-core`/`langchain-openai`）是**唯一已验证兼容**组合；ragas 0.4.x 与 langchain-community≥0.4.2 不兼容。改动这三个版本前先确认。

Milvus 客户端必须用 `pymilvus>=2.5,<2.6`：**2.4.x 在 Python 3.13 上无法导入**（`pymilvus/client/__init__.py` 里 `from pkg_resources import ...`，而 setuptools 已不随 venv 提供）。2.5.x 客户端可连 Milvus **v2.4.4** 服务端（已实测）。装完务必跑一次 `pytest` + ragas/langchain 导入检查——它们与 pymilvus 在 protobuf 上有潜在冲突。

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
