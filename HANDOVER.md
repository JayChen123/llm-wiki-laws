# 项目交接文档

> 本文档用于交接，供后续接入的大模型（或开发者）快速掌握本项目全貌，无需重新摸索即可继续开发。
> 最后更新：2026-08-12

---

## 0. 一句话速览

基于 **llm-wiki** 理念构建的**劳动法知识问答系统**（**非 RAG**）：将《中华人民共和国劳动法》《中华人民共和国劳动合同法》PDF 提前"编译"为**结构化知识点 + 关联图谱**，查询时通过**关键词(BM25) + 向量(bge-m3) 混合检索**定位知识点，由大模型 **qwen-max** 综合生成回答，带**短期记忆**（SQLite）支持多轮对话，并提供 **Ragas 评估**体系。

---

## 1. 核心设计理念（务必先理解）

本项目**不是 RAG 系统**，这是贯穿所有代码的约束：

| 维度 | RAG | 本项目（llm-wiki） |
|---|---|---|
| 知识构建时机 | 查询时 | **摄入时**（提前"编译"） |
| 知识库形态 | 向量切块 | **结构化 Wiki 页面 + 关联图谱** |
| 存储载体 | 向量数据库 | Markdown 页面集 + JSON 图谱 |
| 查询行为 | 检索→拼接→推理 | 定位知识点 → 综合生成 |

- **不切块**：文档按"章（第X章）→ 节（第X节）→ 条（第X条）"切分为知识点（KnowledgePoint），**粒度：每一条一个知识点**，而非定长 chunk。
- **向量不承载知识库**：bge-m3 仅用于（a）语义关联边构建、（b）查询时向量召回融合；知识本体在 `wiki/` 与 `knowledge_graph.json`。
- **多跳支持**：检索后沿关联图 BFS 扩展（最多 3 跳）+ prompt 引导 LLM 跨知识点推理。

---

## 2. 环境与依赖

- Python **3.13+**，虚拟环境由 **uv** 管理（环境已就绪）。
- 依赖见 `pyproject.toml`：
  - **核心**：`jieba`、`markitdown[pdf]`、`openai`、`rank-bm25`、`sentence-transformers`
  - **dev**：`pytest`、`ragas==0.3.0`、`dashscope`、`langchain`(最新)、`langchain-community==0.4.0`、`langchain-core`、`langchain-openai`
- **关键版本约束**（踩坑后确定的兼容组合）：
  - `ragas==0.3.0` + `langchain-community==0.4.0` + langchain 最新（1.x）为**已验证兼容**组合。
  - ragas 0.4.x 与 langchain-community≥0.4.2 不兼容（`vertexai` 模块被移除）；langchain 0.3 栈与 ragas 0.2.x 的 `agenerate_prompt` API 不兼容。**不要随意改动这三个的版本组合**。
- 本地 bge-m3 模型路径见根 `config.py` 的 `bge_m3`。

### 常用命令
```bash
uv run python -m app.ingest.pipeline     # 构建知识库（全量重建，幂等）
uv run python -m app.cli                 # 启动命令行问答
uv run python -m pytest tests/ -v        # 运行全部测试（当前 42 项）
uv run python -m eval.evaluate --limit 3 # 评估（试跑3条）
uv run python -m eval.evaluate --skip-ragas  # 仅检索质量指标（recall@1/3/5/8）
```

---

## 3. 目录结构

```
llm_wiki_laws/
├── config.py               # 配置单一来源（用户维护）：模型路径、LLM、DB 等
├── pyproject.toml          # 依赖（uv 管理）
├── uv.lock                 # 锁文件（提交）
├── CLAUDE.md               # 开发规范（先理解→方案→确认→编码；测试后验收）
├── HANDOVER.md             # 本文档
├── README.md               # 使用文档
├── 任务描述.md              # 原始任务需求（llm-wiki 问答系统要求）
├── .gitignore
├── data/
│   ├── src/                # 原始 PDF 源文件（新增放这里）
│   │   ├── 中华人民共和国劳动合同法.pdf
│   │   └── 中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.pdf
│   └── raw/                # markdown 原始文件（PDF产物 + 用户直接放入）
│       ├── 中华人民共和国劳动合同法.md
│       └── 中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md
├── app/
│   ├── config.py           # 应用配置（复用根 config.py，含路径与检索参数）
│   ├── kb.py               # 知识库加载与统一检索入口
│   ├── cli.py              # 命令行交互式问答（含短期记忆 + 日志）
│   ├── logger.py           # 结构化问答日志
│   ├── ingest/             # ① 知识构建管线
│   │   ├── pipeline.py         # 管线入口（PDF→MD→知识点→图谱→Wiki）
│   │   ├── pdf_to_markdown.py  # markitdown 转换
│   │   ├── knowledge_extract.py# 清洗 + 章/节/条切分提取知识点
│   │   ├── concept_map.py      # 法律概念→条文映射表 + 关键词增强
│   │   ├── graph_build.py      # 关联图谱构建（4 类边）
│   │   └── wiki_writer.py      # Wiki 页面落盘（含核心概念导航）
│   ├── retrieval/          # ② 混合检索
│   │   ├── embedder.py         # bge-m3 向量编码（含 DummyEmbedder 测试用）
│   │   ├── keyword_retriever.py# BM25 关键词检索（索引含关键词字段）
│   │   ├── vector_retriever.py # bge-m3 向量检索
│   │   └── hybrid_retriever.py # RRF 融合 + 图谱多跳扩展
│   ├── generation/         # ③ 回答生成
│   │   └── answer.py           # qwen-max 流式生成（法律领域 prompt）
│   └── memory/             # ④ 短期记忆
│       └── memory_store.py     # SQLite 存储
├── wiki/                   # 编译出的知识库（llm-wiki 页面集，可重新生成，被 gitignore）
│   ├── index.md            #   知识索引（核心概念导航 + 按源文档分组）
│   ├── log.md              #   构建日志
│   ├── knowledge_vectors.npy  # 知识点向量缓存（bge-m3）
│   └── concepts/KP_*.md    #   每个知识点一页（含双向链接）
├── knowledge_graph.json    # 知识点关联图谱（可重新生成，被 gitignore）
├── logs/qa.log             # 问答日志（被 gitignore）
├── memory.db               # 短期记忆 SQLite（运行生成，被 gitignore）
├── eval/                   # 评估（Ragas 0.3）
│   ├── qa_test_cases.jsonl #   30 条法律评估用例
│   ├── metrics.py          #   recall@k 指标（自定义）
│   ├── evaluate.py         #   评估主程序
│   └── results/            #   结果输出（被 gitignore）
└── tests/                  # 单元测试（pytest，42 项）
```

---

## 4. 数据约定（重要）

**新增知识源 → 放入对应目录 → 重跑 pipeline 自动并入**：
- `data/src/*.pdf`：原始 PDF。流水线自动用 markitdown 转成 `data/raw/{同名}.md`（**增量**：已存在则跳过）。
- `data/raw/*.md`：markdown。可放转换产物，也**支持用户直接放入 .md** 构建知识库。

流水线每次运行：PDF→MD 为增量；MD→知识点→图谱→Wiki 为**全量幂等重建**（md 更新即生效）。

当前知识库：**205 个知识点**（劳动合同法 98 条 + 劳动法 107 条）、**2962 条关联边**。

---

## 5. 模块详解

### 5.1 配置层

**根 `config.py`**（用户维护，单一配置源，应用通过 `app/config.py` 复用）：
- `bge_m3`：bge-m3 模型路径（本地）
- `bge_reranker`：reranker 路径（当前未使用）
- `model`/`dashscope_api_key`/`dashscope_base_url`：大模型配置（qwen-max，DashScope OpenAI 兼容接口，key 读 `API_KEY` 环境变量）
- 另有 MySQL/Redis/Milvus 配置，**本项目未使用**，勿被误导。

**`app/config.py`**：
- `SRC_DIR` / `RAW_DIR`：`data/src` / `data/raw`
- `WIKI_DIR` / `CONCEPTS_DIR` / `GRAPH_PATH` / `VECTOR_CACHE_PATH` / `MEMORY_DB_PATH`
- `BGE_MODEL_PATH` / `LLM_MODEL` / `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL`（来自根 config）
- 检索参数：`TOP_K=5`、`EXPAND_K=3`、`EXPAND_HOPS=3`、`EXPAND_DECAY=0.5`

### 5.2 知识构建管线（`app/ingest/`）

**`pipeline.py`**（入口）：
- `discover_pdf_sources()`：扫描 `data/src/*.pdf`
- `convert_pdf_to_md()`：增量转换 PDF→MD（`raw` 中缺失才转）
- `collect_points()`：逐个 md 提取知识点，合并后**全局统一编号** `KP_001…`，随后调用 `concept_map.enrich_keywords()` 注入概念关键词
- `run_pipeline()`：转 PDF → 提知识点 → 概念关键词增强 → bge-m3 语义向量（缓存）→ 建图谱 → 落盘 Wiki

**`knowledge_extract.py`**（知识点提取核心，面向法律法规文档）：
- `KnowledgePoint` dataclass：`id/title/chapter/chapter_title/clause/content/keywords/source`，**粒度：每一条一个知识点**（节内为 `第X节 节名·第X条`）
- **章/节/条边界**：在**原始行**（保留空白）上检测，要求"第X章/节/条"后紧跟空白 → 排除正文折行处的交叉引用（"第三十九条和第四十条…"条后无空白）。数字含"零"（否则漏掉第一百零一条）。
- **康熙部首归一**：法律 PDF 文本用康熙部首变体（如"第⼀条"U+2F00）；`_KANGXI_TABLE` 用 NFKC 归一 + 手工映射（⺠→民、⻅→见、⻓→长、⻔→门），**在匹配正则前对行做 translate**。
- **两阶段状态机**：正文首个"第X条"出现前视为"目录/元数据"阶段，只更新章/节上下文、丢弃内容行。
- **表格回填**：markdown 表格块若在正文阶段，行文本并入当前条文。
- **噪音清洗**：页眉日期、页码、URL、网页脚注、目录点线统一过滤。
- `extract_keywords()`：jieba TF-IDF + 章节/条文主题词，过滤法律停用词（用人单位/劳动者/应当…）。

**`concept_map.py`**（法律概念映射表，内容交付物）：
- `CONCEPT_MAP`：法律概念 → (来源文档名关键字, 条文) 列表，如 `"试用期": [("劳动合同法","第十九条"), …]`。覆盖劳动合同订立/试用期/竞业限制/解除/经济补偿/集体合同/劳务派遣/非全日制/加班费/劳动监察/法律责任等 20+ 概念。
- `CONCEPT_KEYWORDS`：概念 → 用户常用查询词（避免法律停用词），弥合"法律措辞（延长工作时间/工资报酬）↔ 用户口语（加班费）"鸿沟。
- `concept_terms_for(point)`：按 `law in source` + `article in clause` **文档感知**匹配。
- `enrich_keywords(points)`：把概念查询词注入对应条文知识点关键词（幂等）。

**`graph_build.py`**（关联图谱）：
- **四类边**：
  - `sibling`：同章内条文（结构关联，权重 1.0）
  - `keyword`：关键词 Jaccard 重合 ≥0.3
  - `semantic`：bge-m3 余弦 ≥0.75（实测调参：0.65 会近全连接）
  - `crossref`：正文中"第X条"引用同法其他条文（权重 1.5，高于结构边，**跨法不加**）。这是多跳召回的关键——如 46 条引 38 条、65 条引 39 条、42 条引 45 条。
- 输出 `{"nodes":[{id,title,chapter,chapter_title,clause,keywords,content,source}], "edges":[{source,target,types,weight}]}`
- `save_graph` / `load_graph`

**`wiki_writer.py`**：
- `write_wiki()`：生成 `wiki/index.md`（**核心概念导航** + 按源文档分组）、`wiki/concepts/KP_*.md`（含双向链接 `[[KP_xxx]]`）、`wiki/log.md`
- `_resolve_concept(law, article, points)`：概念条文 → 知识点 id，未命中回退纯文本"《law》第X条"
- `_concept_index_section(points)`：渲染"## 核心概念导航"，概念项链接到 `[[KP_xxx|第X条]]`

### 5.3 混合检索（`app/retrieval/`）

**`embedder.py`**：
- `Embedder`：基于 sentence-transformers 加载 bge-m3，`encode()` 输出 L2 归一化向量，`cache()` 缓存到 numpy 文件
- `DummyEmbedder`：测试用假编码器（哈希随机投影），避免加载大模型

**`keyword_retriever.py`**：jieba 分词 + `BM25Okapi`；**索引语料 = 标题 + 正文 + 关键词字段**（概念增强后的关键词参与 BM25，使"加班费"等口语词命中法律条文）。`search(query, top_k)` 返回 `[(node, score)]`

**`vector_retriever.py`**：bge-m3 编码 query，与预计算知识点向量点积（余弦），`search(query, top_k)`

**`hybrid_retriever.py`**（核心，多跳）：
- RRF 融合：`score = Σ 1/(60+rank)`，关键词与向量两路并集
- `_bfs_expand()`：图谱**多跳扩展**——从种子节点沿边 BFS 最多 `expand_hops` 跳，分数 = 种子分 × 边权重 × `decay^hop`，逐跳剪枝，最终取前 `expand_k` 个
- `search(query, top_k)` 返回 **top_k 种子 + 至多 expand_k 扩展 = 至多 8 块**（生成器全用）
- **已知局限**：宽泛"措施/责任"类问题（如同时涉及两法多条文）扩展易被稀释，部分期望条文会落入扩展区甚至丢失（详见 §6 评估结果）。

### 5.4 生成（`app/generation/answer.py`）

- `AnswerGenerator`：OpenAI 客户端指向 DashScope 兼容接口
- `generate_stream()`：**流式**生成（`stream=True`，逐块 yield），`generate()` 聚合完整结果
- **SYSTEM_PROMPT 为劳动法领域定制**："依据《中华人民共和国劳动法》《中华人民共和国劳动合同法》…涉及具体规定时点明法律依据（如"依据《劳动合同法》第五十八条"）…不要臆造法条…末尾列出全部引用"
- `_format_contexts()`：每条 context 截断 600 字；**标题带法律名**（从 `source` 提取，如 `中华人民共和国劳动合同法·第四章·第八十五条`）——两部法律同章同条号，不带法律名会导致 LLM 张冠李戴

### 5.5 短期记忆（`app/memory/memory_store.py`）

- SQLite 单文件 `memory.db`，表 `messages(session, role, content, created_at)`
- `add`/`recent`/`recent_user_queries`/`clear`
- 轻量注入：检索时把最近 1 条用户问题并入检索词（处理"那/继续"等指代）；生成时注入最近 10 条历史

### 5.6 知识库加载（`app/kb.py`）

- `KnowledgeBase.load()`：加载图谱 + 向量缓存 + 构建 HybridRetriever；无向量缓存时退化为仅关键词
- `search(query, top_k)` 统一检索入口

### 5.7 CLI（`app/cli.py`）

- 交互式问答：加载知识库 → 会话ID → 循环问答
- 短期记忆注入 → 混合检索 → 流式生成 → **记录日志** → 写记忆
- 命令：`/exit` `/clear` `/help`
- 未配置 `API_KEY` 时降级为仅展示检索结果

### 5.8 日志（`app/logger.py`）

- `RotatingFileHandler` 滚动写入 `logs/qa.log`（5MB×5）
- `log_qa_event()`：每次问答记录 JSON 事件 `{time,user_id,query,knowledge_blocks,answer,retrieval_ms,generation_ms}`
- `knowledge_blocks` 含 `id/title/score/source`（不含正文）

---

## 6. 评估体系（`eval/`）

**数据**：`eval/qa_test_cases.jsonl`，**30 条劳动法评估用例**：
- 字段：`id/type/question/answer/expected_knowledge/source_sections/sub_questions`
- `type`：`single_hop`(10) / `multi_hop`(10) / `complex`(10，含子问题拆分)
- 覆盖：试用期、竞业限制、经济补偿、加班、非全日制、劳务派遣、经济性裁员、孕期女职工保护、劳动争议处理、劳动监察等
- `answer` 作为 ragas `ground_truth`；`expected_knowledge`（知识点 id，与图谱 KP 编号对应）用于 recall@k 比对
- **30 条用例的 KP id 均已在知识图谱中存在**（校验脚本：逐条比对无缺失、无重复）

**指标**：
- 自定义 `eval/metrics.py::recall_at_k`：**recall@1/3/5/8**（@8 反映生成器真实上下文——检索返回 5 种子 + 3 扩展）
- ragas 0.3.0：`context_precision`、`context_recall`、`ContextRelevance`、`answer_relevancy`、`faithfulness`

**`eval/evaluate.py`** 流程：加载用例 → 检索+生成（tqdm 进度条）→ recall@k（1/3/5/8）→ ragas 指标 → 结果落盘 `eval/results/evaluation_<时间戳>.json`
- LLM：`LangchainLLMWrapper(ChatOpenAI(base_url=DashScope, model=qwen-max))`
- Embeddings：**必须用 `langchain_community.embeddings.DashScopeEmbeddings`**（`text-embedding-v3`）经 `LangchainEmbeddingsWrapper` 包装。⚠️ 曾踩坑：`langchain_openai.OpenAIEmbeddings` 配 DashScope 会报 `input.contents is neither str nor list of str`。
- 参数：`--limit N` 试跑、`--skip-ragas` 仅检索质量、`--top-k`、`--case-file`

**当前实测结果**（2026-08-12，30 条，`--skip-ragas`）：
- recall@1：**0.40**、recall@3：**0.69**、recall@5：**0.84**、recall@8：**0.92**
- 单跳用例：10 条全部 recall@3=1.0（其中 7 条 recall@1=1.0）
- 多跳/复杂用例的依赖条文主要靠 **crossref 图谱扩展**带进上下文（46→38、65→39、42→45、41→46 等）
- **已知薄弱**：宽泛的"措施/责任"类问题（eval_026 拖欠工资维权、eval_030 维权途径、eval_025 孕期违法解除）期望条文未必全部落入 top-8；生成器在缺失部分条文时仍能基于已召回的相关条文给出**合法合理**的答案（冒烟测试验证）

---

## 7. 测试（`tests/`，42 项全过）

| 文件 | 覆盖 |
|---|---|
| `test_knowledge_extract.py` | 清洗、章/节/条解析、康熙部首变体、交叉引用排除、表格回填、关键词、真实 md 提取（98/107 条） |
| `test_concept_map.py` | 概念映射的文档感知匹配、关键词注入去重、未映射跳过 |
| `test_graph_build.py` | sibling/keyword/semantic 三类边 + **crossref 交叉引用边**（FakeEmbedder） |
| `test_retrieval.py` | BM25（含关键词字段）、向量、RRF 融合、图谱多跳扩展 |
| `test_memory.py` | SQLite 记忆读写、会话隔离、轮数限制 |
| `test_generation.py` | 流式逐块、聚合、prompt 组装与多跳引导、**上下文标题带法律名**（mock LLM） |
| `test_logger.py` | 日志 JSON 事件、缺失耗时兜底 |
| `test_eval.py` | recall@k 指标、评估数据行组装（mock 检索/生成） |
| `test_pipeline.py` | PDF 发现、增量转换、md 发现、多文档全局编号 |
| `test_wiki_writer.py` | 概念导航链接渲染、跨文档条文消歧 |

测试策略：**不加载真实大模型/PDF**，用 `DummyEmbedder`/`FakeEmbedder`/mock 客户端。`test_knowledge_extract` 的 `test_real_markdown_extracts` 仅在 `data/raw` 有 md 时运行。

---

## 8. 运行流程与数据流

**构建**（`uv run python -m app.ingest.pipeline`）：
```
data/src/*.pdf ──markitdown(增量)──► data/raw/*.md
data/raw/*.md ──章/节/条切分──► KnowledgePoint[]（205，全局编号）
  └─► concept_map 概念关键词增强
  └─► bge-m3 语义向量（缓存 wiki/knowledge_vectors.npy）
  └─► 关联图谱 knowledge_graph.json（sibling/keyword/semantic/crossref，2962 边）
  └─► wiki/ 页面集（index 含核心概念导航）
```

**问答**（`uv run python -m app.cli`）：
```
用户query + 短期记忆注入
  └─► BM25 关键词（含概念关键词）+ bge-m3 向量 双路召回
  └─► RRF 融合 → Top-K 种子
  └─► 图谱多跳扩展（≤3跳，衰减0.5，crossref 权重1.5）
  └─► qwen-max 流式生成（法律领域 prompt + 法律名标题 + 引用）
  └─► 写日志 logs/qa.log + 写记忆 memory.db
```

---

## 9. 关键注意事项 / 踩坑记录

1. **ragas/langchain 版本**：`ragas==0.3.0` + `langchain-community==0.4.0` + langchain 1.x 是唯一验证可行的组合。改版本必踩 `vertexai` 缺失或 `agenerate_prompt` 报错。
2. **DashScope embedding**：评估用 `DashScopeEmbeddings`，不要用 langchain 的 `OpenAIEmbeddings`（格式不兼容）。
3. **康熙部首变体**：法律 PDF 的"第⼀条"用 U+2F00 康熙部首，NFKC 归一前不匹配 `[一二三四五六七八九十百零]`；`_KANGXI_TABLE` 需在**匹配正则前**对原始行 translate，否则劳动法（107 条）会漏切。
4. **交叉引用误切**：正文折行的"第三十九条和第四十条…"条后无空白，与真条文头"第X条　"区分；若交叉引用被误判为条文会重复编号。
5. **中文数字含"零"**：`第一百零一条` 需要数字正则含"零"，否则漏掉 101-107 条。
6. **多跳依赖条文召回**：靠 crossref 边（正文"第X条"引用）在图谱扩展阶段带出；概念映射关键词补"用户口语→法条"鸿沟。两者结合才使多跳用例 recall@8 达 0.92。
7. **跨法同条号混淆**：两部法律同有 85/98 等条文号，生成上下文标题必须带法律名，否则 LLM 会张冠李戴（如把劳动法 98 引成劳动合同法 98）。
8. **bge-m3 模型较大**（2.2GB），首次加载慢；向量已缓存，语义边用缓存加载。
9. **知识库规模**：205 知识点 / 2962 边；语义阈值 0.75（0.65 会近全连接），keyword 0.3，crossref 权重 1.5。
10. `.gitignore` 忽略：`data/`、`wiki/`、`knowledge_graph.json`、`logs/`、`memory.db`、`eval/results/`、`.claude/`、`CLAUDE.md`、`任务描述.md`、`.venv/`、`__pycache__/` 等。
11. 项目已 `git init`（分支 dev_law，主分支 main）。CLAUDE.md 与 任务描述.md 虽被 gitignore 但仍在工作区。

---

## 10. 后续开发建议（供参考）

- **检索增强**：可选实现"查询分解"（LLM 拆子问题分别检索再合并）——已讨论，当前未实现（已实现 A：图谱多跳 + crossref + 概念关键词增强 + E：prompt 多跳引导）。
- **宽泛措施题**：eval_026/030 类"维权途径/责任"题，可考虑按预期条文补 crossref 或概念映射，或放宽生成上下文。
- **实体链接 + 图传播**：从 query 抽实体 → 图谱节点 → 影响力传播召回。
- **评估完善**：可增加"测试集抽样"、`--limit` 随机采样、结果对比历史版本。
- **新增法律**：把新法律 PDF 放入 `data/src/` → 重跑 pipeline；如需其在概念导航出现，需在 `concept_map.py` 补映射。
- **Web API**：用户偏好 CLI，如需前端需另行确认。

---

## 11. 验收/继续开发检查清单

- [ ] `uv run python -m pytest tests/ -v` → 42 passed
- [ ] `uv run python -m app.ingest.pipeline` → 205 知识点 / 2962 关联边
- [ ] `export API_KEY=... && uv run python -m app.cli` → 多轮问答正常
- [ ] `uv run python -m eval.evaluate --skip-ragas` → recall@1/3/5/8 输出
- [ ] 新增知识源：放 `data/src/*.pdf` 或 `data/raw/*.md` → 重跑 pipeline
- [ ] 新增法律概念导航：编辑 `app/ingest/concept_map.py` 补映射 → 重跑 pipeline
