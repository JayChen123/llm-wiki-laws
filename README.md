# llm-wiki 劳动法问答系统

基于 **llm-wiki** 理念构建的**劳动法知识问答系统**，提供**命令行交互式问答**。系统将《中华人民共和国劳动法》《中华人民共和国劳动合同法》PDF
提前"编译"为结构化的**知识点图谱**，查询时通过 **关键词（BM25）+ 向量（bge-m3）混合检索**定位知识点，再由大模型 `qwen-max`
综合生成回答，并带**短期记忆**支持多轮对话。

> 本项目**不是 RAG 系统**：不对文档切块、不把向量作为知识库存储载体。知识库本体是结构化 Wiki 页面 + 关联图谱，向量检索仅作为混合召回手段之一。

---

## 一、核心特性

- **llm-wiki 知识编译**：摄入时把 PDF 编译为知识点 + 关联图谱，查询时只做"定位 + 综合"，知识与答案随时间复利积累。
- **非 RAG 知识库**：不切块、不向量化存储；知识库是 `wiki/` 下的 Markdown 页面集 + `knowledge_graph.json`
  关联图谱，可直接用上下文窗口承载、可读可审计。
- **法条级知识点**：按"章（第X章）→ 节（第X节）→ 条（第X条）"切分，**每一条一个知识点**（劳动合同法 98 条 + 劳动法 107 条 = 205
  个），可精确溯源到具体法条。
- **混合检索**：BM25 关键词检索（索引含**概念关键词增强**，弥合法律措辞与用户口语鸿沟）与 bge-m3 向量检索双路召回，*
  *RRF（Reciprocal Rank Fusion）** 融合排序，并沿关联边扩展相关知识点。
- **多跳推理**：图谱四类边（结构/关键词/语义/法条交叉引用）+ BFS 多跳扩展，prompt 引导 LLM 跨知识点组合推理，支持"
  试用期被解除有经济补偿吗"这类需综合多个法条的问题。
- **概念导航**：`wiki/index.md` 提供"核心概念导航"，把"试用期/竞业限制/加班费/劳务派遣…"等法律概念链接到具体法条页。
- **短期记忆（轻量）**：SQLite 单文件存储多轮会话；检索时注入最近用户问题（处理"那/具体/继续"等指代），生成时注入对话历史。
- **引用溯源**：回答末尾标注引用的知识点标题（**含法律名**，避免两部法律同条号混淆），支持回溯原文（来源行号）。
- **流式输出**：CLI 采用 SSE 流式逐字渲染回答（打字机效果）。
- **结构化日志**：每次问答记录一条 JSON 事件，滚动写入 `logs/qa.log`。

### 后续优化功能

- 前端页面系统，支持多用户注册登录
- 用户记忆模块优化，采用长期和短期记忆相结合的方式
- 记忆存储sqlite --> mysql或者milvus
- 多跳检索。需要智能体自主决定检索，尤其是检索到相互引用的情况，支持继续检索引用知识
- 问答系统增加意图识别，与法律不想关的生成兜底回答
- 系统改为异步过程，增加系统的并发能力
- 系统压力测试
- docker 部署
- wiki的知识图谱存储，考虑用图数据库存储和检索

---

## 二、技术架构

```
┌──────────────────────────────  知识构建管线（Ingest）  ──────────────────────────────┐
│                                                                                      │
│  data/src/中华人民共和国劳动合同法.pdf  +  中华人民共和国劳动法.pdf                      │
│        │  markitdown（微软）                                                          │
│        ▼                                                                             │
│  data/raw/*.md  ──► 清洗（页眉/页码/康熙部首变体） ──► 按"章→节→条"切分                 │
│                                                                                      │
│  知识点 KnowledgePoint（205 个）                                                       │
│     · id / 标题 / 章节 / 条文 / 正文 / 关键词 / 来源行号                                │
│        │                                                                             │
│        ├─► 概念关键词增强（concept_map：加班费→劳动法第四十四条 等）                     │
│        ├─► 关联图谱构建（四类边）                                                      │
│        │       · sibling   同章内条文的结构关系                                        │
│        │       · keyword   关键词重合（Jaccard 相似度）                                │
│        │       · semantic  bge-m3 语义相似度（余弦 ≥0.75）                             │
│        │       · crossref  正文"第X条"交叉引用（权重1.5，跨法不加）                     │
│        │       输出 knowledge_graph.json（2962 条边）                                  │
│        └─► Wiki 页面集落盘                                                            │
│              · wiki/index.md（核心概念导航 + 按源文档分组）                             │
│              · wiki/concepts/KP_*.md（每知识点一页，含双向链接 [[]]）                   │
│              · wiki/log.md（操作日志）                                                 │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                    │  query
                                    ▼
┌──────────────────────────────  检索问答（Query）  ─────────────────────────────────┐
│                                                                                      │
│  用户问题 + 短期记忆注入（最近一问并入检索词）                                          │
│        │                                                                             │
│        ├─► 关键词检索：jieba 分词 + BM25（含概念关键词）─┐                              │
│        └─► 向量检索：bge-m3 编码，余弦相似度 ─┼─► RRF 融合排序 ─► Top-K 知识点          │
│                                               │                                      │
│                                                └─► 沿关联图多跳扩展（≤3跳）           │
│        │                                                                             │
│        ▼                                                                             │
│  qwen-max（DashScope OpenAI 兼容接口）综合生成                                         │
│        │   + 短期记忆历史（最近 N 轮）+ 法律名上下文标题                                │
│        ▼                                                                             │
│  回答（带引用）→ 写入 SQLite 记忆（memory.db）                                         │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 三、项目结构

```
llm_wiki_user_qa/
├── config.py                     # 配置单一来源（模型路径、LLM、数据库等，用户维护）
├── pyproject.toml                # 依赖与元数据（uv 管理）
├── data/
│   ├── src/                        # 原始 PDF 源文件（只读，可新增）
│   │   ├── 中华人民共和国劳动合同法.pdf
│   │   └── 中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.pdf
│   └── raw/                        # markdown（PDF 转换产物 + 用户直接放入，可新增）
│       ├── 中华人民共和国劳动合同法.md
│       └── 中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md
├── app/
│   ├── config.py                 # 应用配置（复用根 config.py，含路径与检索参数）
│   ├── kb.py                     # 知识库加载与统一检索入口
│   ├── cli.py                    # 命令行交互式问答（含短期记忆 + 日志）
│   ├── logger.py                 # 结构化问答日志（滚动写入 logs/qa.log）
│   ├── ingest/                   # ① 知识构建管线
│   │   ├── pdf_to_markdown.py    #    markitdown PDF→Markdown
│   │   ├── knowledge_extract.py  #    清洗 + 章/节/条解析 + 知识点提取 + 关键词
│   │   ├── concept_map.py        #    法律概念→条文映射 + 关键词增强
│   │   ├── graph_build.py        #    关联图谱构建（sibling/keyword/semantic/crossref）
│   │   ├── wiki_writer.py        #    Wiki 页面集落盘（index 含核心概念导航）
│   │   └── pipeline.py           #    管线入口
│   ├── retrieval/                # ② 混合检索
│   │   ├── embedder.py           #    bge-m3 向量编码器（含缓存 + 测试用假编码器）
│   │   ├── keyword_retriever.py  #    BM25 关键词检索
│   │   ├── vector_retriever.py   #    bge-m3 向量检索
│   │   └── hybrid_retriever.py   #    RRF 融合 + 关联多跳扩展
│   ├── generation/
│   │   └── answer.py             # ③ qwen-max 综合生成（法律领域 prompt，带引用）
│   └── memory/
│       └── memory_store.py       # ④ SQLite 短期记忆
├── wiki/                         # 编译出的知识库（llm-wiki 页面集）
│   ├── index.md
│   ├── log.md
│   ├── knowledge_vectors.npy     # 知识点向量缓存（bge-m3）
│   └── concepts/KP_*.md          # 205 个知识点页面
├── knowledge_graph.json          # 知识点关联图谱（节点 + 带类型边）
├── logs/
│   └── qa.log                    # 问答日志（滚动，JSON 事件流）
├── eval/                         # 评估
│   ├── qa_test_cases.jsonl       #   30 条劳动法评估用例（单跳/多跳/复杂）
│   ├── metrics.py                #   recall@1/3/5/8 检索指标（自定义）
│   ├── evaluate.py               #   评估主程序（recall@k + ragas）
│   └── results/                  #   评估结果输出
├── tests/                        # 单元测试（pytest，42 项）
└── README.md
```

---

## 四、知识构建管线（Ingest）

数据源约定（新增源文件直接放入对应目录，重新运行流水线即可自动并入）：

- `data/src/*.pdf`：原始 PDF，自动转为 markdown（**增量**，已转换则跳过）
- `data/raw/*.md`：markdown 原始文件（PDF 转换产物 + 用户直接放入的 md），逐个解析提取知识点

1. **PDF → Markdown**：使用微软 [markitdown](https://github.com/microsoft/markitdown) 扫描 `data/src/*.pdf`，仅对
   `data/raw/` 中缺失对应 md 的 PDF 转换。
2. **噪音清洗**：过滤页眉日期、孤立页码、URL、网页脚注、目录点线；对**康熙部首变体**（法律 PDF 的"第⼀条"等）做字符归一。
3. **知识点提取**：按"章（第X章）→ 节（第X节）→ 条（第X条）"切分，**每一条一个知识点**，产出 `KnowledgePoint`：
   | 字段 | 含义 |
   |---|---|
   | `id` | 唯一标识（KP_001…） |
   | `title` | 如"第四章·第四十七条" |
   | `chapter` / `chapter_title` | 章节号 / 章节标题 |
   | `clause` | 条文（节内为"第X节 节名·第X条"） |
   | `content` | 清洗后的正文 |
   | `keywords` | jieba TF-IDF + 概念增强后的关键词 |
   | `source` | 来源文件与行号 |
4. **概念关键词增强**：`concept_map.py` 把用户常用查询词（加班费、拖欠工资、试用期…）注入对应条文知识点关键词，弥补法律措辞与口语鸿沟。
5. **关联图谱构建**：四类带类型边——`sibling`（同章条文）、`keyword`（关键词 Jaccard）、`semantic`（bge-m3 余弦 ≥0.75）、`crossref`
   （正文"第X条"交叉引用，权重 1.5），输出 `knowledge_graph.json`。
6. **Wiki 页面落盘**：生成 `wiki/index.md`（**核心概念导航** + 按源文档分组）、`wiki/concepts/*.md`（每页含双向链接
   `[[KP_xxx]]`）、`wiki/log.md` 日志。

---

## 五、混合检索（Query）

1. **记忆注入**：取出最近一条用户问题并入当前检索词，使"那/具体/继续"等指代延续有效。
2. **双路召回**：
    - 关键词：jieba 分词 + `BM25Okapi`，索引含标题、正文与**概念增强后的关键词**（用户口语词可命中法律条文）；
    - 向量：bge-m3 编码 query，与知识点向量做余弦相似度。
3. **RRF 融合**：`score = Σ 1/(60 + rank)`，两路结果合并去重排序，取 Top-K。
4. **图谱多跳扩展**：从命中种子沿关联边 **BFS 最多 3 跳**遍历，节点分数按 `seed × 边权重 × 0.5^跳数` 逐跳衰减，每跳剪枝、最终只取前
   `EXPAND_K` 个补充进结果——**crossref 边**（法条互相引用）权重 1.5，使被引用条文（如第六十五条引的第三十九条）稳定进入上下文。
5. **多跳推理生成**：将检索块（5 种子 + 至多 3 扩展）正文注入 prompt，`qwen-max` 综合生成；上下文标题**带法律名**，prompt
   显式引导跨知识点组合推理并列出全部引用。

---

## 六、短期记忆（轻量）

- **存储**：SQLite 单文件 `memory.db`，表结构极简（session/role/content/created_at），无外部依赖。
- **注入检索**：`recent_user_queries()` 取最近 1 问并入检索词，解决指代与话题延续。
- **注入生成**：`recent()` 取最近 10 条消息作为 LLM 对话历史，保证多轮连贯。
- **会话管理**：CLI 可指定会话 ID，重开后可续聊上次对话；`/clear` 一键清空。

---

## 七、问答日志

每次问答会写入一条 JSON 事件到 `logs/qa.log`（`RotatingFileHandler`，单文件 5MB、滚动保留 5 份），字段：

| 字段                               | 含义                                               |
|----------------------------------|--------------------------------------------------|
| `time`                           | 事件时间                                             |
| `user_id`                        | 会话 ID（CLI 输入的会话标识）                               |
| `query`                          | 用户问题                                             |
| `knowledge_blocks`               | 本次检索命中的知识块列表（`id`/`title`/`score`/`source`，不含正文） |
| `answer`                         | 最终回答（含引用）                                        |
| `retrieval_ms` / `generation_ms` | 检索 / 生成耗时（毫秒）                                    |

日志每行为一条 JSON，可用 `grep` / `jq` 直接检索分析：

```bash
grep -o '"query": "[^"]*"' logs/qa.log        # 所有用户问题
grep '"user_id": "user001"' logs/qa.log        # 某用户的问答记录
```

---

## 八、快速开始

### 环境要求

- Python 3.13+，[uv](https://docs.astral.sh/uv/) 管理虚拟环境
- 本地 bge-m3 模型（路径配置于 `config.py` 的 `bge_m3`）
- DashScope API Key（`qwen-max`）

### 安装

```bash
uv sync                     # 安装依赖（含 dev: pytest）
```

### 构建知识库

```bash
uv run python -m app.ingest.pipeline
```

首次构建会自动完成 PDF→MD→知识点→图谱→Wiki 落盘，并加载 bge-m3 编码语义向量（缓存于 `wiki/knowledge_vectors.npy`）。

### 启动问答

```bash
export API_KEY="your-dashscope-key"
uv run python -m app.cli
```

- 输入问题回车即答；重开程序输入同一会话 ID 可续聊。
- 命令：`/exit` 退出、`/clear` 清空记忆、`/help` 帮助。
- 未设置 `API_KEY` 时，系统仍会执行检索并展示命中的知识点，但不生成自然语言回答。

### 运行测试

```bash
uv run python -m pytest tests/ -v
```

---

## 九、配置说明

| 配置项                            | 位置                          | 说明                                                                  |
|--------------------------------|-----------------------------|---------------------------------------------------------------------|
| `bge_m3`                       | `config.py`                 | bge-m3 模型本地路径                                                       |
| `model`                        | `config.py`                 | 大模型名，默认 `qwen-max`                                                  |
| `dashscope_api_key`            | `config.py`                 | 从环境变量 `API_KEY` 读取                                                  |
| `dashscope_base_url`           | `config.py`                 | DashScope OpenAI 兼容接口地址                                             |
| `TOP_K` / `EXPAND_K`           | `app/config.py`             | 检索返回知识点数 / 关联扩展数                                                    |
| `EXPAND_HOPS` / `EXPAND_DECAY` | `app/config.py`             | 图谱多跳扩展的最大跳数（默认 3）/ 分数逐跳衰减系数（默认 0.5）                                 |
| `MEMORY_DB_PATH`               | `app/config.py`             | SQLite 记忆文件路径                                                       |
| 关联阈值                           | `app/ingest/graph_build.py` | `keyword_threshold=0.3`、`semantic_threshold=0.75`、`crossref` 权重 1.5 |
| 概念映射                           | `app/ingest/concept_map.py` | 法律概念→条文映射与用户查询词（可编辑扩充）                                              |

> 根目录 `config.py` 同时含数据库/Redis 等配置，本问答系统仅使用其中 `bge_m3` 与大模型三项，其余供其他模块扩展使用。

---

## 十、测试

`tests/` 覆盖四部分，全部基于轻量假数据（`DummyEmbedder`/`FakeEmbedder`），不依赖大模型与真实 PDF：

| 测试文件                        | 覆盖范围                                    |
|-----------------------------|-----------------------------------------|
| `test_knowledge_extract.py` | 清洗、章/节/条解析、康熙部首变体、交叉引用排除、表格回填、关键词       |
| `test_concept_map.py`       | 概念映射文档感知匹配、关键词注入去重                      |
| `test_graph_build.py`       | sibling/keyword/semantic/crossref 四类关联边 |
| `test_retrieval.py`         | BM25（含关键词字段）、向量检索、RRF 融合与图谱多跳扩展         |
| `test_memory.py`            | SQLite 记忆读写、会话隔离、历史轮数限制                 |
| `test_generation.py`        | 流式逐块输出、聚合、prompt 组装、上下文标题带法律名（mock LLM） |
| `test_logger.py`            | 日志 JSON 事件字段完整性、缺失耗时兜底                  |
| `test_eval.py`              | recall@k 指标、评估数据行组装（mock 检索/生成）         |
| `test_wiki_writer.py`       | 概念导航链接渲染、跨文档条文消歧                        |

---

## 十一、评估（Ragas）

基于 **Ragas 0.3.0** 评估框架（配合 langchain 最新版、`langchain-community==0.4.0`、DashScope）评估问答质量。

### 评估数据

`eval/qa_test_cases.jsonl`，30 条**劳动法**测试用例（试用期、竞业限制、经济补偿、加班、劳务派遣、裁员、孕期保护、劳动争议等），JSONL
每行一个对象：

| 字段                   | 含义                                                   |
|----------------------|------------------------------------------------------|
| `id` / `type`        | 用例编号 / 类型（`single_hop`·`multi_hop`·`complex`，各 10 条） |
| `question`           | 用户问题                                                 |
| `answer`             | 标准答案（作为 ragas 的 `ground_truth`）                      |
| `expected_knowledge` | 期望命中的知识点 id（用于 recall@k 比对）                          |
| `source_sections`    | 答案来源章节                                               |
| `sub_questions`      | 复杂问题的子问题拆分（complex 类型）                               |

### 指标映射

| 指标               | 来源                                                                        |
|------------------|---------------------------------------------------------------------------|
| `recall@1/3/5/8` | 自定义（`eval/metrics.py`），比对 `expected_knowledge` 与检索 Top-k 命中；@8 反映生成器真实上下文 |
| 上下文相关性           | ragas `ContextRelevance`（`nv_context_relevance`）                          |
| 上下文精确率           | ragas `context_precision`                                                 |
| 上下文召回率           | ragas `context_recall`                                                    |
| 答案相关性            | ragas `answer_relevancy`                                                  |
| 忠实度              | ragas `faithfulness`                                                      |

### 运行评估

```bash
uv run python -m eval.evaluate                 # 全量 30 条（生成+ragas，较慢）
uv run python -m eval.evaluate --limit 3       # 快速试跑前 3 条
uv run python -m eval.evaluate --skip-ragas    # 仅计算 recall@k（不调 LLM）
```

- 依赖 DashScope：LLM 用 `qwen-max`（`API_KEY` 环境变量），embedding 用 `text-embedding-v3`（
  `langchain_community.embeddings.DashScopeEmbeddings`）。
- 结果写入 `eval/results/evaluation_<时间戳>.json`，含 recall@k 汇总、ragas 各指标均值/最值/按类型分组、以及每用例明细。

**当前基线**（2026-08-12）：recall@1=0.40、recall@3=0.69、recall@5=0.84、recall@8=0.92；单跳用例 recall@3 全部=1.0。

---

## 十二、FAQ

- **为什么不叫 RAG？** 知识库以结构化 Wiki 页面 + 关联图谱为本体，摄入时一次编译；向量仅参与查询时的召回融合，不是存储与检索的载体。
- **更换/新增法律文档怎么办？** 更新 `data/` 下的 PDF 或 Markdown，重新运行构建管线即可，图谱与 Wiki 会全量重建；如需在"
  核心概念导航"中出现，在 `concept_map.py` 补映射。
- **知识点粒度如何调整？** 当前按"每一条（第X条）"切分；修改 `knowledge_extract.py` 的章/节/条切分正则可改为按"（一）"项或整章切分。
- **为什么正文里的"第三十九条和第四十条"不会被误当成新条文？** 条文头要求"第X条"后紧跟空白，折行交叉引用条后无空白，据此区分。
