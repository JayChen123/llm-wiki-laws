# 项目交接文档

> 本文档用于交接，供后续接入的大模型（或开发者）快速掌握本项目全貌，无需重新摸索即可继续开发。
> 最后更新：2026-09-13（v1.1：民法典 + Milvus 向量 + 意图识别 + 反思式多跳 + 双层记忆 + Web 多用户）

---

## 0. 一句话速览

基于 **llm-wiki** 理念构建的**法律知识问答系统**（**非 RAG**）：将《中华人民共和国劳动法》《中华人民共和国劳动合同法》《中华人民共和国民法典》提前"编译"为**结构化知识点 + 关联图谱**（1465 个知识点 / 21252 条边），查询时通过**关键词(BM25) + 向量(bge-m3，存于 Milvus) 混合检索**定位知识点，由大模型 **qwen-max** 综合生成回答，带**双层记忆**（短期 MySQL + 长期画像/历史 Milvus）支持多轮对话，并提供 **Ragas 评估**体系。

---

## 1. 核心设计理念（务必先理解）

本项目**不是 RAG 系统**，这是贯穿所有代码的约束：

| 维度 | RAG | 本项目（llm-wiki） |
|---|---|---|
| 知识构建时机 | 查询时 | **摄入时**（提前"编译"） |
| 知识库形态 | 向量切块 | **法条级知识点 + 关联图谱**（另有 Markdown 人读视图） |
| 存储载体 | 向量数据库 | JSON 图谱（权威源）+ Neo4j 副本；`wiki/` 仅人读视图 |
| 查询行为 | 检索→拼接→推理 | 定位知识点 → 综合生成 |

- **不切块**：文档按"编（第X编）→ 章（第X章）→ 节（第X节）→ 条（第X条）"切分为知识点（KnowledgePoint），**粒度：每一条一个知识点**，而非定长 chunk。
- **向量不承载知识库**：bge-m3 仅用于（a）语义关联边构建、（b）查询时向量召回融合；知识本体在 `knowledge_graph.json`（应用检索读 Neo4j 副本），`wiki/` 只是由同一份数据生成的**人读视图**（2026-09-13 起只落盘少量示例页，且不参与检索）。向量**存于 Milvus** 只是把它当"向量索引"用，不是知识载体（`.npy` 文件缓存已废弃）。
- **多跳支持**：检索后沿关联图 BFS 扩展（最多 3 跳）+ prompt 引导 LLM 跨知识点推理。

---

## 2. 环境与依赖

- Python **3.13+**，虚拟环境由 **uv** 管理（环境已就绪）。
- 依赖见 `pyproject.toml`：
  - **核心**：`jieba`、`markitdown[pdf]`、`openai`、`rank-bm25`、`sentence-transformers`、**`pymilvus>=2.5,<2.6`**
  - **dev**：`pytest`、`ragas==0.3.0`、`dashscope`、`langchain`(最新)、`langchain-community==0.4.0`、`langchain-core`、`langchain-openai`
- **关键版本约束**（踩坑后确定的兼容组合）：
  - `ragas==0.3.0` + `langchain-community==0.4.0` + langchain 最新（1.x）为**已验证兼容**组合。
  - ragas 0.4.x 与 langchain-community≥0.4.2 不兼容（`vertexai` 模块被移除）；langchain 0.3 栈与 ragas 0.2.x 的 `agenerate_prompt` API 不兼容。**不要随意改动这三个的版本组合**。
- 本地 bge-m3 模型路径见 `app/config.py` 的 `BGE_MODEL_PATH`（**根 `config.py` 已不存在**，历史文档里的"双份配置"说法已作废）。
- 向量库用 **Milvus**（`pymilvus>=2.5,<2.6`；2.4.x 在 Python 3.13 上无法导入），服务端见 `docker/docker-compose.yml`。

### 常用命令
```bash
uv run python -m app.ingest.pipeline     # 构建知识库（全量重建，幂等）
uv run python -m app.cli                 # 启动命令行问答
cd web && npm run build && cd ..         # 构建前端（产物 web/dist，由 FastAPI 托管）
uv run python -m app.api.main            # 启动 Web 服务（http://localhost:8080）
uv run python -m pytest tests/ -v        # 运行全部测试（当前 217 项）
uv run python -m eval.evaluate --limit 3 # 评估（试跑3条）
uv run python -m eval.evaluate --skip-ragas  # 仅检索质量指标（recall@1/3/5/8）
uv run python -m scripts.analyze_logs    # 汇总问答日志画像（意图/检索/反思/成本/耗时）
uv run python -m app.ingest.sync_graph   # knowledge_graph.json → Neo4j（--check-only 只比对）
```

---

## 3. 目录结构

```
llm_wiki_laws/
├── pyproject.toml          # 依赖（uv 管理）
├── uv.lock                 # 锁文件（提交）
├── CLAUDE.md               # 开发规范（先理解→方案→确认→编码；测试后验收）
├── HANDOVER.md             # 本文档
├── README.md               # 使用文档
├── .gitignore
├── data/
│   ├── src/                # 原始 PDF 源文件（新增放这里）
│   │   ├── 中华人民共和国劳动合同法.pdf
│   │   ├── 中华人民共和国民法典.pdf
│   │   └── 中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.pdf
│   └── raw/                # markdown 原始文件（PDF产物 + 用户直接放入）
│       ├── 中华人民共和国劳动合同法.md
│       ├── 中华人民共和国民法典.md          # 7 编 1260 条，含"编/分编"与千位条号
│       └── 中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md
├── docker/
│   └── docker-compose.yml  # Milvus + etcd/minio + redis + mysql + neo4j（端口见文件）
├── web/                    # 前端（React+Vite+Tailwind v4）：模块导航 / 问答 / 起诉状（骨架）
├── app/
│   ├── api/                # ⑤ Web API（FastAPI）：认证 + 会话 + SSE 问答 + 托管前端产物
│   ├── config.py           # 应用配置（**唯一配置源**：路径、bge-m3、LLM、Milvus、MySQL、JWT、检索参数）
│   ├── kb.py               # 知识库加载与统一检索入口
│   ├── cli.py              # 命令行交互式问答（含短期记忆 + 日志）
│   ├── logger.py           # 结构化问答日志
│   ├── ingest/             # ① 知识构建管线
│   │   ├── pipeline.py         # 管线入口（PDF→MD→知识点→图谱→Wiki）
│   │   ├── pdf_to_markdown.py  # markitdown 转换
│   │   ├── knowledge_extract.py# 清洗 + 编/章/节/条切分提取知识点
│   │   ├── concept_map.py      # 法律概念→条文映射表 + 关键词增强
│   │   ├── graph_build.py      # 关联图谱构建（4 类边）
│   │   ├── graph_store.py      # 图谱的 Neo4j 读写（四类关系拆分/全量对齐/批量 UNWIND/seq 排序）
│   │   ├── sync_graph.py       # knowledge_graph.json → Neo4j 同步入口
│   │   └── wiki_writer.py      # 人读视图落盘（index + log + 示例页）
│   ├── retrieval/          # ② 混合检索
│   │   ├── embedder.py         # bge-m3 向量编码（测试用假编码器在各测试文件内定义）
│   │   ├── vector_store.py     # Milvus 向量库：建集合/全量对齐写入/余弦检索
│   │   ├── keyword_retriever.py# BM25 关键词检索（索引含关键词字段）
│   │   ├── vector_retriever.py # bge-m3 编码 query → Milvus 检索 → 映射回图谱节点
│   │   ├── hybrid_retriever.py # RRF 融合 + 基础图谱多跳扩展（expand=False 只回种子）
│   │   ├── reflector.py        # 检索反思器：判"是否已充分 / 该补哪几条被引条文"
│   │   └── reflective_retriever.py # 反思式多跳：基础扩展 + 反思补取（默认链路）
│   ├── generation/         # ③ 回答生成
│   │   ├── intent.py           # 意图识别（可答/法律未覆盖/无关）+ 兜底话术
│   │   └── answer.py           # qwen-max 流式生成（法律领域 prompt）
│   └── memory/             # ④ 双层记忆
│       ├── mysql_store.py      # 短期记忆 + 用户画像（MySQL，唯一后端）
│       ├── long_term.py        # 长期记忆向量库（Milvus，强制 user_id 过滤）
│       ├── profile_extractor.py# 画像低频抽取（qwen-turbo）
│       └── __init__.py         # create_memory_store() + 检索词注入策略
├── wiki/                   # 人读视图（不参与检索；由 pipeline 重建）
│   ├── index.md            #   全量索引（核心概念导航 + 按源文档分组；有"编"的法律按 编→章 两级）
│   ├── log.md              #   构建日志
│   └── concepts/KP_*.md    #   少量示例页（WIKI_SAMPLE_REFS，讲课时演示用）
├── knowledge_graph.json    # 知识点关联图谱（可重新生成，被 gitignore）
├── logs/qa.log             # 问答日志（被 gitignore）
├── eval/                   # 评估（Ragas 0.3）
│   ├── qa_test_cases.jsonl #   55 条法律评估用例（劳动法/劳动合同法 35 + 民法典 20）
│   ├── metrics.py          #   recall@k 指标（自定义）
│   ├── evaluate.py         #   评估主程序
│   └── results/            #   结果输出（被 gitignore）
└── tests/                  # 单元测试（pytest，217 项；全部离线，不连 Milvus/大模型）
```

---

## 4. 数据约定（重要）

**新增知识源 → 放入对应目录 → 重跑 pipeline 自动并入**：
- `data/src/*.pdf`：原始 PDF。流水线自动用 markitdown 转成 `data/raw/{同名}.md`（**增量**：已存在则跳过）。
- `data/raw/*.md`：markdown。可放转换产物，也**支持用户直接放入 .md** 构建知识库。

流水线每次运行：PDF→MD 为增量；MD→知识点→图谱→Wiki 为**全量幂等重建**（md 更新即生效）。

当前知识库：**1465 个知识点**（劳动合同法 98 条 + 劳动法 107 条 + **民法典 1260 条**）、**21252 条关联边**；向量存于 Milvus 集合 `llm_wiki_laws`（1465 条，dim 1024，HNSW/COSINE）。

---

## 5. 模块详解

### 5.1 配置层

⚠️ **根 `config.py` 不存在**（未纳入 git，工作区里也没有）。历史文档写的"根 config.py 用户维护 + app/config.py 复用"已经不成立，`app/config.py` 里那行 `# from config import Config` 是注释掉的。**配置只有 `app/config.py` 一处**。

**`app/config.py`**：
- `SRC_DIR` / `RAW_DIR`：`data/src` / `data/raw`
- `WIKI_DIR` / `CONCEPTS_DIR` / `GRAPH_PATH`
- **记忆（MySQL/Milvus）**：`MYSQL_HOST`/`MYSQL_PORT`（默认 **3308**）/`MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_DATABASE`、`MEMORY_COLLECTION`、`MEMORY_RECALL_K`、`MEMORY_SUMMARY_CHARS`、`PROFILE_EXTRACT_EVERY`、`PROFILE_MODEL`、`MEMORY_INJECT_MODE`
- `BGE_MODEL_PATH`（**本地模型路径，机器相关，换环境先改这里**；指向 Itcast 目录仅是用户提供的配置值，**不作为实现参考**）/ `LLM_MODEL` / `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL`
- **Milvus**：`MILVUS_HOST` / `MILVUS_PORT`（默认 **19532**，是宿主机映射端口，容器内是 19530）/ `MILVUS_COLLECTION` / `MILVUS_METRIC=COSINE` / `MILVUS_INDEX=HNSW` / `EMBED_DIM=1024`，均可用环境变量覆盖
- **意图识别**：`INTENT_ENABLED`（默认开）/ `INTENT_MODEL`（默认 `qwen-turbo`）/ `INTENT_TIMEOUT_S`（默认 3，超时保守放行）
- **服务凭证集中在 `docker/.env`**（`MYSQL_*`/`NEO4J_*`/`REDIS_PASSWORD`/`MINIO_*`/`JWT_SECRET`）：
  compose 自动读取它，应用侧用 `python-dotenv` 的 `load_dotenv(..., override=False, interpolate=False)` 读同一份
  —— **只填空缺、不覆盖已有环境变量**（真实环境变量优先，容器化/CI 可用导出变量覆盖），
  且不做 `$` 变量展开（避免密码被悄悄改写）。文件不入库，模板见 `docker/.env.example`。
- **图数据库**：`NEO4J_URI`（默认 `bolt://localhost:7687`）/`NEO4J_USER`/`NEO4J_PASSWORD`/`NEO4J_DATABASE`
- **多跳链路**：`RETRIEVAL_MODE`（`reflect` 默认 / `bfs` 对照）/ `REFLECT_MODEL`（默认 `qwen-turbo`）/ `REFLECT_MAX_ROUNDS`（默认 3，防失控）/ `REFLECT_TIMEOUT_S`（默认 15，超时即停）
- 检索参数：`TOP_K=10`、`EXPAND_K=5`、`EXPAND_HOPS=3`、`EXPAND_DECAY=0.5`（**均可环境变量覆盖**，便于 A/B）
  → 生成上下文 = 10 种子 + ≤5 扩展 + 反思补取，**15 起**（实测见过 17）

### 5.2 知识构建管线（`app/ingest/`）

**`pipeline.py`**（入口）：
- `discover_pdf_sources()`：扫描 `data/src/*.pdf`
- `convert_pdf_to_md()`：增量转换 PDF→MD（`raw` 中缺失才转）
- `collect_points()`：逐个 md 提取知识点，合并后**全局统一编号** `KP_001…`，随后调用 `concept_map.enrich_keywords()` 注入概念关键词
- `run_pipeline()`：转 PDF → 提知识点 → 概念关键词增强 → bge-m3 语义向量（缓存）→ 建图谱 → 落盘 Wiki

**`knowledge_extract.py`**（知识点提取核心，面向法律法规文档）：
- `KnowledgePoint` dataclass：`id/title/chapter/chapter_title/part/part_title/clause/content/keywords/source`，**粒度：每一条一个知识点**（节内为 `第X节 节名·第X条`）
- **编/章/节/条边界**：在**原始行**（保留空白）上检测，要求"第X编/章/节/条"后紧跟空白 → 排除正文折行处的交叉引用（"第三十九条和第四十条…"条后无空白）。数字字符集 `CN_NUM_CHARS` **必须含"千"**（民法典第一千二百六十条）**和"零"**（第一百零一条）——缺"千"会让民法典从第 1000 条起整段漏切（曾只抽到 999/1260 条）。
- **"编"是必需层级**：`part`/`part_title`；民法典 7 编各自从"第一章"重新起算，`title` 形如 `第二编 物权·第二章·第十三条`（编名参与 BM25 索引与语义编码）。`_PART_RE` 以 `^` 锚定行首，故正文"依照本法第一编第六章…"不会误判。
- **"分编"只作结构边界**（`_SUBPART_RE`）：民法典物权编/合同编内部的分组标题，既不产生知识点、也不并入上一条文正文（曾污染 6 条条文正文），仅重置章上下文。
- **章/节标题不产生知识点**：`flush()` 要求 `clause` 非空（否则页眉噪音会让章标题单独成点，曾产生 34 个垃圾知识点）。
- **康熙部首归一**：法律 PDF 文本用康熙部首变体（如"第⼀条"U+2F00）；`_KANGXI_TABLE` 用 NFKC 归一 + 手工映射（⺠→民、⻅→见、⻓→长、⻔→门），**在匹配正则前对行做 translate**。
- **行号按物理行**：源文件含换页符 `\f`（民法典 130 处），`splitlines()` 会把 `\f` 当换行、令 `source` 行号比编辑器实际行号大（民法典末尾偏移 129 行，溯源对不上）。现按 `\n` 拆物理行，行内再按 `\f` 分段并**共用同一行号**。
- **两阶段状态机**：正文首个"第X条"出现前视为"目录/元数据"阶段，只更新编/章/节上下文、丢弃内容行。
- **表格回填**：markdown 表格块若在正文阶段，行文本并入当前条文。
- **噪音清洗**：页眉日期（`2024/11/15` 与 `8/23/2020` 两种写法）、页码、URL（含**裸域名 `www.`**）、网页脚注、目录点线统一过滤。
- `extract_keywords()`：jieba TF-IDF + 编/章节/条文主题词，过滤法律停用词（用人单位/劳动者/应当…）。

**`concept_map.py`**（法律概念映射表，内容交付物）：
- `CONCEPT_MAP`：法律概念 → (来源文档名关键字, 条文) 列表，如 `"试用期": [("劳动合同法","第十九条"), …]`。**共 53 个概念**：劳动法侧覆盖劳动合同订立/试用期/竞业限制/解除/经济补偿/集体合同/劳务派遣/非全日制/加班费/劳动监察/法律责任等；民法典侧覆盖诉讼时效/表见代理/监护/善意取得/居住权/抵押担保/格式条款/情势变更/合同法定解除/违约责任/违约金/借款/房屋租赁/无因管理/不当得利/隐私权/个人信息保护/婚姻无效/夫妻共同财产与共同债务/离婚冷静期/法定继承顺序/遗嘱继承/产品责任/交通事故/医疗损害/高空抛物等 27 个。
- `CONCEPT_KEYWORDS`：概念 → 用户常用查询词（避免法律停用词），弥合"法律措辞（延长工作时间/工资报酬）↔ 用户口语（加班费）"鸿沟。
- `concept_terms_for(point)`：按 `law in source` + `article in clause` **文档感知**匹配。
- `enrich_keywords(points)`：把概念查询词注入对应条文知识点关键词（幂等）。

**`graph_build.py`**（关联图谱）：
- **四类边**：
  - `sibling`：同 **(编, 章)** 内条文（结构关联，权重 1.0）。**分组键必须是二元组**——民法典 7 编各自从"第一章"起算，只按章号分组会把不同编的同号章并成大团（曾产生约 1.97 万条假边）
  - `keyword`：关键词 Jaccard 重合 ≥0.3
  - `semantic`：bge-m3 余弦 ≥0.75（实测调参：0.65 会近全连接）
  - `crossref`：正文中"第X条"引用同法其他条文（权重 1.5，高于结构边，**跨法不加**）。这是多跳召回的关键——如 46 条引 38 条、65 条引 39 条、42 条引 45 条。正则同样含"千"（民法典跨引用大量指向第 1000 条以上条文）
- `semantic_text(point)`：语义编码文本模板（标题 + 正文前 200 字）。**语义边与向量库写入必须共用它**，否则图谱语义与检索语义不一致。
- `build_graph(points, embedder=None, vectors=None, …)`：可直接传入已算好的 `vectors` 复用（避免重复编码）
- 输出 `{"nodes":[{id,title,chapter,chapter_title,part,part_title,clause,keywords,content,source}], "edges":[{source,target,types,weight}]}`
- `save_graph` / `load_graph`

**`wiki_writer.py`**：
- `write_wiki(points, graph, wiki_dir=None, concepts_dir=None)`：生成 `wiki/index.md`（**核心概念导航** + 按源文档分组，目录仍是**全量**、仍用 `[[KP_xxx]]` 双链写法）、`wiki/log.md`，以及 `wiki/concepts/KP_*.md` —— **只有 `WIKI_SAMPLE_REFS` 列出的示例页**（知识点页不参与检索，故不再逐条落盘）。两个目录参数可注入：单测必须指向 `tmp_path`，否则会往真实 `wiki/` 写
- `WIKI_SAMPLE_REFS`：示例页白名单 `(法律关键字, 条文)`，复用 `_resolve_concept()` 在建库时解析成 KP id —— **不硬编码 KP 编号**（编号会随知识源增删重分配），未命中则跳过并打印提示
- `_index_md()`：**有"编"的法律按 编→章 两级分组**（`### 第二编 物权` → `#### 第二章`），无编的维持 doc→章 两级；排序用 `_order_no()`（复用 `cn_to_int`，支持"第二十九章"等）
- `_resolve_concept(law, article, points)`：概念条文 → 知识点 id，未命中回退纯文本"《law》第X条"
- `_concept_index_section(points)`：渲染"## 核心概念导航"，概念项链接到 `[[KP_xxx|第X条]]`

### 5.3 图谱存储（`app/ingest/graph_store.py` + `sync_graph.py`）

- **权威源是 `knowledge_graph.json`**（可 git diff、可审计；用户明确要求保留，不删）。pipeline 写完 json 后
  调 `sync_from_json()` 同步到 Neo4j；`kb.load()` 从 **Neo4j** 读图。
- **建模**：节点标签 `KnowledgePoint`（属性与 json 字段一一对应，**含正文 content** —— 反思候选
  `crossref_map()` 要解析正文里的引用）；边按四类拆成 `:SIBLING`/`:KEYWORD`/`:SEMANTIC`/`:CROSSREF`，
  一条多类型边拆成多条关系，故 **30569 条关系 > 21252 条边**；`load()` 按 (source,target) 聚合
  （types 取并集、weight 取 max）与 json 1:1 还原。
- **`:CROSSREF` 方向在同步时派生**：json 的 `source/target` 是 id 字典序而非引用方向（97 条 crossref 边
  全部 source<target），故同步时用 `crossref_map()`（与反思候选同一份实现）从正文解析方向，
  按 `引用方 -[:CROSSREF]-> 被引方` 建关系 → 可直接做有向引用链遍历（`-[:CROSSREF*1..3]->`），
  **无需改 json 格式**。只取 json 里存在的 crossref 节点对，保证仍是忠实副本。
- **节点对归一化**：`:CROSSREF` 有向、同一对节点可能双向各一条关系，而 json 一条边只对应 `(min,max)`；
  `load()` 与 `counts()` 都按 `(min,max)` 归一化后聚合，边数与 json 恒等（21252），recall 不变。
- **`seq` 属性**：id 形如 KP_1000 与 KP_999 的字典序 ≠ 数值序，用 seq 排序才能还原 json 顺序
  （顺序影响检索并列打破，是 recall 可比的前提）。
- **写入是全量对齐**：先清空 `:RELATED`、按新图重建、最后 `DETACH DELETE` 不在新图中的节点。
- **空列表不能作属性值**：关键词为空要存 None，读回再还原成 `[]`。
- **无降级**：Neo4j 不可用即报错（`kb.load()` 里若图为空会明确提示先跑同步），不退回读本地文件。
- 同步命令：`uv run python -m app.ingest.sync_graph [--check-only]`。

### 5.4 混合检索（`app/retrieval/`）

**`embedder.py`**：
- `Embedder`：基于 sentence-transformers 加载 bge-m3，`encode()` 输出 L2 归一化向量（dim 1024）
- **不再有 `.npy` 文件缓存**（原 `cache()` 已删除）：它只按"文件是否存在"判断命中，知识库重建后易残留与实际节点集不一致的旧向量。测试用假编码器由各测试文件自带（`StubEmbedder`/`FakeEmbedder`）

**`vector_store.py`**（Milvus 向量库）：
- `MilvusVectorStore(client=None, …)`：`client` 可注入假 client 供离线测试
- `ensure_collection()`：不存在则建（`kp_id` VARCHAR 主键 + `title`/`source` + `vector` FLOAT_VECTOR(1024)，HNSW(M=16, efConstruction=200) + COSINE）；已存在则校验维度，不一致就重建
- `upsert(nodes, vectors)`：**全量对齐 + 清理陈旧主键**（删除集合中已不在当前知识库的 id）；节点数与向量数不一致直接报错，避免 `zip` 静默截断后连带误删
- ⚠️ **Milvus 的写入与删除都要 `flush` 之后才能被 `query` 查到**。所以顺序必须是"先 flush 并取旧主键集合 → 再 upsert → 再 flush → 再 delete"，写反了陈旧主键清理会**静默失效**（已踩过，单测用带可见性延迟的假 client 卡住这个顺序）
- `search(qv, top_k)` 返回 `[(kp_id, score)]`；`count()` 用于校验集合规模

**`keyword_retriever.py`**：jieba 分词 + `BM25Okapi`；**索引语料 = 标题 + 正文 + 关键词字段**（概念增强后的关键词参与 BM25，使"加班费"等口语词命中法律条文）。`search(query, top_k)` 返回 `[(node, score)]`

**`vector_retriever.py`**：bge-m3 编码 query → `store.search()` → 用 `kp_id → node` 映射回图谱节点；集合中残留当前图谱已不存在的 id 时跳过

**`hybrid_retriever.py`**（核心，多跳）：
- RRF 融合：`score = Σ 1/(60+rank)`，关键词与向量两路并集
- `_bfs_expand()`：图谱**多跳扩展**——从种子节点沿边 BFS 最多 `expand_hops` 跳，分数 = 种子分 × 边权重 × `decay^hop`，逐跳剪枝，最终取前 `expand_k` 个
- `search(query, top_k)` 返回 **top_k 种子 + 至多 expand_k 扩展**（默认 10 + 5 = 15 起；反思链路的 `ReflectiveRetriever` 还会在其上补取，实测最多见过 17 块，生成器全用）
- **已知局限**：宽泛"措施/责任"类问题（如同时涉及多法多条文）扩展易被稀释，部分期望条文会落入扩展区甚至丢失（详见 §6 评估结果）。

### 5.5 生成（`app/generation/`）

**`intent.py`**（问答前路由）：
- 三分类 `Intent`：`ANSWERABLE`（可答）/ `LEGAL_UNCOVERED`（法律相关但本库未覆盖，如刑法/行政法/公司法）/ `OFF_TOPIC`（与法律无关）
- `IntentClassifier.classify(query)`：调 `INTENT_MODEL`（默认 `qwen-turbo`，`temperature=0`），要求只输出 JSON；`parse_intent()` 容错解析（容忍 markdown 围栏与前后噪音）
- **保守放行**：超时/异常/无 key/解析不出 → 一律 `ANSWERABLE`，reason 里带上原因便于排查。宁可多答一次，也不要把正常法律问题挡掉
- `fallback_answer(intent)`：两套兜底话术（未覆盖 → 说明覆盖范围 + 建议改写；无关 → 礼貌拒绝 + 可问示例）
- `COVERED_LAWS`：本库覆盖的法律（提示词与话术共用，**新增知识源时同步这里**）
- 接入点：`cli.py` 在检索前调用，非 `ANSWERABLE` 直接兜底并 `continue`（跳过检索与生成，但**照常写记忆与日志**，否则多轮上下文会断）；`logger` 增加可选 `intent` 字段；`eval --intent` 只统计误判率、不改变 recall 口径

**`answer.py`**：

- `AnswerGenerator`：OpenAI 客户端指向 DashScope 兼容接口
- `generate_stream()`：**流式**生成（`stream=True`，逐块 yield），`generate()` 聚合完整结果
- **SYSTEM_PROMPT 为民事法律领域定制**："依据《中华人民共和国民法典》《中华人民共和国劳动法》《中华人民共和国劳动合同法》…解答劳动权益、劳动合同…以及合同、物权、侵权责任、婚姻家庭、继承等民事法律问题…涉及具体规定时点明法律依据…不要臆造法条…末尾列出全部引用"
- `_format_contexts()`：每条 context 截断 600 字；**标题带法律名**（从 `source` 提取，如 `中华人民共和国劳动合同法·第四章·第八十五条`）——多部法律同章同条号，不带法律名会导致 LLM 张冠李戴。民法典的标题还会自带编名（`中华人民共和国民法典·第七编 侵权责任·第十章·第一千二百五十四条`）

### 5.6 双层记忆（`app/memory/`）

**短期（`mysql_store.MySQLMemoryStore`，MySQL 唯一后端）**
- 表：`users` / `sessions` / `messages(session_id, user_id, role, content)` / `user_profile`（utf8mb4）
- 接口：`add`/`recent`/`recent_user_queries`/`turn_count`/`clear`/`clear_user`/`add_fact`/`facts`/`ensure_user`/`ensure_session`
- **不做 SQLite 降级、不做连接失败回退**：连不上直接报错（避免数据分散、"以为在写 MySQL 实际写本地文件"）

**长期（`long_term.LongTermMemory`）**
- 画像（结构化事实）→ MySQL `user_profile`，`UNIQUE(user_id, fact)` 去重
- 历史问答（语义索引）→ Milvus 集合 `llm_wiki_user_memory`（与法律知识 `llm_wiki_laws` 分离）
- **红线一：检索必须带 `filter=user_id == "..."`**，否则跨用户泄漏
- **红线二：记忆只注入生成的"用户背景"段落**并标注"不得作为法律依据"，**绝不并入检索词**
  （否则重演"上一问污染法条检索"）
- 向量用本地 bge-m3 编码（零 API 成本）；历史摘要直接取"问题+答案"，不额外调 LLM

**画像抽取（`profile_extractor.ProfileExtractor`）**：`PROFILE_EXTRACT_EVERY`（默认每 5 轮）用
qwen-turbo 抽一次，只抽用户自身相关且跨会话有用的事实，**排除敏感信息**；失败返回空列表。

**检索词注入（`memory.build_search_query`）**：`MEMORY_INJECT_MODE=auto`（默认）只在问题"像追问"
（含指代词，或很短且无法律实体词）时才并入上一问，修掉换话题时的串题污染。

### 5.7 Web API（`app/api/`，P4）

- **认证**：`auth.py` —— bcrypt 存密码（显式截断 72 字节）、JWT 放 **HttpOnly + SameSite=Lax Cookie**（JS 读不到，防 XSS 窃取；同源托管免 CSRF/CORS 配置）；注册即登录；登录失败**不区分**账号是否存在（防枚举）。
- **依赖注入**：`deps.py` —— 当前用户 / KB / 记忆 / 生成器 / 分类器全部走 `Depends`，测试用 `app.dependency_overrides` 换成假实现，**接口测试完全离线**（不连 MySQL/Milvus/LLM）。
  ⚠️ 依赖内部不要直接调用别的依赖函数（如 `current_user` 里直接 `get_memory_store()`），否则替换失效——必须写成 `Depends(...)` 参数。
- **接口**：`POST /api/auth/{register,login,logout}`、`GET /api/auth/me`、`GET|POST /api/sessions`、`GET /api/sessions/{id}/messages`、`DELETE /api/sessions/{id}`、`POST /api/chat`（SSE）。
- **SSE 事件**：`meta`（引用+耗时）→ `delta`*（正文分片）→ `citations` → `done`；兜底路径为 `fallback`（意图非可答）；生成失败发 `error` 但**仍返回 citations 并写记忆**。
- **越权防护**：所有会话接口先 `session_belongs_to(session, user_id)`，不属于当前用户一律 **404**（不暴露存在性），且不触发检索。
- **前端**（`web/`）：React 19 + Vite 8 + Tailwind v4（`@tailwindcss/vite`，无 config 文件）。`web/src/api.js` 用 **fetch + ReadableStream 手动解析 SSE**（`EventSource` 只支持 GET，而问答是 POST）。引用随 assistant 消息存 `messages.citations`（JSON），刷新/切会话后仍可溯源。
- **模块化外壳与导航**（2026-09-13）：`src/modules.js` 是**功能模块注册表**（`MODULES` 数组，字段
  `key/glyph/label/hint/component/enabled`），`components/AppShell.jsx` 依它渲染顶栏 + 横向 tab 导航 + 当前模块。
  **加模块只改注册表**，不动 App/ChatPage；`enabled:false` 的模块照常出现在导航里并带「开发中」标记
  （`ComplaintPage` 即此状态）。原先写在 ChatPage 里的品牌栏/登出按钮已**上移到 AppShell**，好让导航对所有模块生效；
  模块组件统一收 `{ user, onLogout }`。
- **正文 Markdown 统一入口**：`components/MarkdownBody.jsx`（内置 `remark-gfm` + 表格横向滚动容器 + `.answer-body`
  排版类）。问答回答与起诉状预览都用它——**新增模块请复用，别各自引入 `react-markdown`**（见 §9 第 38 条）。
- **起诉状生成模块**（`pages/ComplaintPage.jsx`）：只做前端骨架——表单（原告/被告/诉讼请求/事实与理由/证据清单/
  受诉法院）+ 本地拼接的实时预览，提交与导出按钮置灰。**不调后端**，后端能力（要素抽取、法条引用、导出）待迭代；
  接入时把 `buildComplaint()` 换成后端返回的文书即可，表单与预览不用动。

### 5.8 知识库加载（`app/kb.py`）

- `KnowledgeBase.load(embedder=None, store=None)`：加载图谱 + 连 Milvus + 构建 HybridRetriever；**Milvus 不可用**（服务未启动/集合不存在）时打印告警并退化为纯关键词检索，不抛异常。`store` 可注入供离线测试。
- `search(query, top_k)` 统一检索入口

### 5.9 CLI（`app/cli.py`）

- 交互式问答：加载知识库 → 会话ID → 循环问答
- 短期记忆注入 → 混合检索 → 流式生成 → **记录日志** → 写记忆
- 命令：`/exit` `/clear` `/help`
- 未配置 `API_KEY` 时降级为仅展示检索结果

### 5.10 日志（`app/logger.py`）

- `RotatingFileHandler` 滚动写入 `logs/qa.log`（5MB×5），每次问答一条 JSON，**字段按需出现**。
- `log_qa_event()` 的字段分工（**三类来源分开记**，用于排查"某个法条为什么没进上下文"）：
  | 字段 | 含义 |
  |---|---|
  | `query` / `answer` | 用户问题 / 最终回答 |
  | `mode` | 检索链路 `reflect` / `bfs` |
  | `seeds` | **检索命中**（RRF 种子，id/标题/分数） |
  | `graph_expanded` | **图谱扩展**带进来的（id/标题/分数/**hop**） |
  | `reflect_fetched` | **反思沿引用补取**的（id/标题/**第几轮**） |
  | `reflect` | `{rounds, reasons}`，审计"为什么停在第几轮" |
  | `intent` / `intent_reason` | 意图分类结果与理由（兜底路径也能看出为何不检索） |
  | `memory_recalled` | 长期记忆召回 `{count, kinds}`（**不含正文**） |
  | `knowledge_blocks` | 送进生成的**全部**知识块（id/title/score/source，不含正文） |
- 轨迹由检索器提供：`HybridRetriever.last_trace`（seeds/expanded，含跳数）、
  `ReflectiveRetriever.last_trace`（在其上补 fetched/rounds/reasons），`kb.last_trace` 透出；
  `log_qa_event(trace=...)` 负责拆成上面各字段。CLI 与 Web 两条入口记录字段一致。

---

## 6. 评估体系（`eval/`）

**数据**：`eval/qa_test_cases.jsonl`，**55 条**（劳动法/劳动合同法 35 条 + **民法典 20 条**）：
- 字段：`id/type/source/question/answer/expected_knowledge/source_sections/sub_questions`
- **`source`：来源法律**（`民法典`/`劳动法`/`劳动合同法`，跨法用例为多项）——评估结果可按法律分组；`--source 民法典` 可只跑某部法律的用例。由脚本按 `expected_knowledge` 反推并校验，非手填。
- **引用关系型用例**（民法典 10 条多跳）：`expected_knowledge` **同时列出"引用方"与"被引方"**，如「买卖合同价款约定不明」期望 第六百二十六条 + 第五百一十条 + 第五百一十一条 —— 这样 recall@k 直接量化"引用链有没有被带进上下文"。全部引用关系已用 `crossref_map()` 校验（被引条号确实出现在引用方正文里，共 17 处）。
- `type`：`single_hop` / `multi_hop` / `complex`（含子问题拆分）
- 覆盖：试用期、竞业限制、经济补偿、加班、非全日制、劳务派遣、经济性裁员、孕期女职工保护、劳动争议处理、劳动监察等
- `answer` 作为 ragas `ground_truth`；`expected_knowledge`（知识点 id，与图谱 KP 编号对应）用于 recall@k 比对
- **用例的 KP id 全部指向两部劳动法（最大 KP_196）**，而 `KP_001`–`KP_205` 的编号映射在新增民法典后**保持不变**（民法典排在 KP_206 之后），故历史 recall 基线仍可比

**指标**：
- 自定义 `eval/metrics.py::recall_at_k`：**recall@1/3/5/8**（@8 是沿用历史基线的汇报口径，**不是生成器上下文上限**——生成器拿的是检索返回的全部块，默认 top_k=5 种子 + 至多 5 个扩展）
- ragas 0.3.0：`context_precision`、`context_recall`、`ContextRelevance`、`answer_relevancy`、`faithfulness`

**`eval/evaluate.py`** 流程：加载用例 → 检索+生成（tqdm 进度条）→ recall@k（1/3/5/8）→ ragas 指标 → 结果落盘 `eval/results/evaluation_<时间戳>.json`
- 检索走 `KnowledgeBase.load()`，**即 Milvus**（Milvus 挂了会退化成纯关键词，recall 会明显下降——评估前先确认向量库在跑）
- LLM：`LangchainLLMWrapper(ChatOpenAI(base_url=DashScope, model=qwen-max))`
- Embeddings：**必须用 `langchain_community.embeddings.DashScopeEmbeddings`**（`text-embedding-v3`）经 `LangchainEmbeddingsWrapper` 包装。⚠️ 曾踩坑：`langchain_openai.OpenAIEmbeddings` 配 DashScope 会报 `input.contents is neither str nor list of str`。
- 参数：`--limit N` 试跑、`--skip-ragas` 仅检索质量、`--top-k`（默认 5）、`--case-file`、`--source <法律>`

**当前实测结果**（2026-09-13，55 条，`--skip-ragas`）：
- 总体：recall@1 **0.4894**、recall@3 **0.7288**、recall@5 **0.8591**、recall@8 **0.947**
- **按来源法律**：民法典（20 条）**0.5917 / 0.7583 / 0.8917 / 0.9833**；劳动合同法（26 条）0.4135 / 0.6923 / 0.8462 / 0.9487；劳动法（10 条）0.4333 / 0.6917 / 0.7417 / 0.775
- 两部劳动法 35 条的基线（0.431 / 0.7119 / 0.8405 / 0.9262）未变，可与历史结果直接对比
- 民法典 @8=0.983：多跳用例里**被引条文基本都进了上下文**（用例期望同时含引用方与被引方）
- 与民法典入库前基线（0.40 / 0.69 / 0.84 / 0.92）持平或略升——两部劳动法的知识点编号未变，向量检索也从"只在前 205 节点里排序"恢复为全库 1465 节点
- 多跳/复杂用例的依赖条文主要靠 **crossref 图谱扩展**带进上下文（46→38、65→39、42→45、41→46 等）
- **已知薄弱**：宽泛的"措施/责任"类问题（eval_026 拖欠工资维权、eval_030 维权途径、eval_025 孕期违法解除）期望条文未必全部落入 top-8；生成器在缺失部分条文时仍能基于已召回的相关条文给出**合法合理**的答案（冒烟测试验证）

---

## 7. 测试（`tests/`，217 项全过）

| 文件 | 覆盖 |
|---|---|
| `test_knowledge_extract.py` | 清洗、编/章/节/条解析、康熙部首变体、交叉引用排除、表格回填、关键词、**千位条号**、**编层级与同号章区分**、**分编边界**、**裸域名/斜杠日期噪音**、**`\f` 行号**、真实 md 提取（98/107/**1260** 条） |
| `test_concept_map.py` | 概念映射的文档感知匹配、关键词注入去重、未映射跳过、**民法典概念（含千位条号）** |
| `test_graph_build.py` | sibling/keyword/semantic 三类边 + **crossref 交叉引用边**（FakeEmbedder）+ **跨编同号章不建 sibling 边** + **千位条号 crossref** |
| `test_milvus_store.py` | 集合 schema/索引、维度不符重建、全量对齐写入、**陈旧主键清理（含可见性延迟下的顺序回归）**、`(kp_id, score)` 返回、条数统计（假 client，离线） |
| `test_kb.py` | Milvus 可用时启用向量检索、**不可用时退化为纯关键词且不抛异常** |
| `test_retrieval.py` | BM25（含关键词字段）、向量（假 store）、RRF 融合、图谱多跳扩展 |
| `test_reflector.py` | 候选编号白名单（防幻觉）、容错解析、无候选不调 LLM、失败即停 |
| `test_reflective_retriever.py` | 四个停止条件、迭代取回、轮数上限、不污染图谱节点 |
| `test_mysql_store.py` | MySQL 记忆后端：生成的 SQL 与绑定参数（user_id 隔离、排序 LIMIT、画像去重）、串题注入策略 |
| `test_long_term.py` | 长期记忆：用户隔离、画像+历史召回、注入段落标注、后端故障直接暴露 |
| `test_api_auth.py` | 注册/登录/登出、Cookie 安全属性、账号枚举防护、bcrypt 往返与超长密码 |
| `test_api_sessions.py` | 会话 CRUD、**越权一律 404**、删除会话连带清长期记忆 |
| `test_api_chat.py` | SSE 事件流、意图兜底跳过检索、生成失败仍返回引用、引用随历史持久化 |
| `test_intent.py` | 意图三分类、容错解析、故障保守放行、兜底话术（mock LLM） |
| `test_cli_intent.py` | CLI 意图路由：非可答跳过检索与生成、兜底仍写记忆与日志、混合序列只让可答题进检索 |
| `test_generation.py` | 流式逐块、聚合、prompt 组装与多跳引导、**上下文标题带法律名**（mock LLM） |
| `test_logger.py` | 日志 JSON 事件、缺失耗时兜底、**三类来源（seeds/graph_expanded/reflect_fetched）分开记录**、字段按需出现 |
| `test_analyze_logs.py` | 日志画像：解析容错（坏行/缺字段）、停止原因归类（未知归 other）、成本估算、用户过滤 |
| `test_graph_store.py` | Neo4j 图存储：Cypher 与参数、全量对齐、四类关系拆分、`:CROSSREF` 方向派生、节点对归一化、seq 排序（假 driver，离线） |
| `test_eval.py` | recall@k 指标、评估数据行组装（mock 检索/生成） |
| `test_pipeline.py` | PDF 发现、增量转换、md 发现、多文档全局编号 |
| `test_wiki_writer.py` | 概念导航链接渲染、跨文档条文消歧、**编→章 两级目录分组**、**章号数值排序**、**只落盘示例页而索引仍全量**、示例清单未命中不报错、**不写真实 `wiki/`**（目录可注入）、示例清单对真实图谱全部命中 |

测试策略：**不加载真实大模型/PDF、不连 Milvus、不调 LLM**，用各测试文件自带的假编码器（`StubEmbedder`/`FakeEmbedder`）、假向量库（`StubVectorStore`/`FakeMilvusClient`）与 mock 客户端。`test_knowledge_extract` 的 `test_real_markdown_extracts` 仅在 `data/raw` 有 md 时运行。

---

## 8. 运行流程与数据流

**构建**（`uv run python -m app.ingest.pipeline`）：
```
data/src/*.pdf ──markitdown(增量)──► data/raw/*.md
data/raw/*.md ──编/章/节/条切分──► KnowledgePoint[]（1465，全局编号）
  └─► concept_map 概念关键词增强（53 个概念）
  └─► bge-m3 语义向量（只编码一次：既供语义边、也写 Milvus）
  └─► 关联图谱 knowledge_graph.json（sibling/keyword/semantic/crossref，21252 边）
  └─► Milvus 集合 llm_wiki_laws（全量对齐写入 + 清理陈旧主键）
  └─► wiki/ 人读视图（index 全量索引 + log + 10 个示例页；不参与检索）
```

**问答**（`uv run python -m app.cli`）：
```
用户query → 意图识别（可答 / 法律未覆盖 / 无关）
  └─► 非"可答"：兜底话术（不检索不生成）→ 写记忆 + 写日志（带 intent）→ 继续
  └─► "可答"：短期记忆注入
  └─► BM25 关键词（含概念关键词）+ bge-m3 向量（Milvus）双路召回
  └─► RRF 融合 → Top-K 种子（默认 10）
  └─► 图谱多跳扩展（≤3跳，衰减0.5，crossref 权重1.5，至多 5 个）
  └─► 反思循环（reflect 模式）：候选=已收集条文的被引条文；无候选不调 LLM 直接停；
      否则 LLM 判 sufficient/fetch（白名单编号），至多 REFLECT_MAX_ROUNDS 轮
  └─► qwen-max 流式生成（民事法律 prompt + 法律名标题 + 引用）
  └─► 写日志 logs/qa.log + 短期记忆（MySQL messages）+ 长期记忆（Milvus 历史语义索引）
```

---

## 9. 关键注意事项 / 踩坑记录

1. **ragas/langchain 版本**：`ragas==0.3.0` + `langchain-community==0.4.0` + langchain 1.x 是唯一验证可行的组合。改版本必踩 `vertexai` 缺失或 `agenerate_prompt` 报错。
2. **DashScope embedding**：评估用 `DashScopeEmbeddings`，不要用 langchain 的 `OpenAIEmbeddings`（格式不兼容）。
4. **交叉引用误切**：正文折行的"第三十九条和第四十条…"条后无空白，与真条文头"第X条　"区分；若交叉引用被误判为条文会重复编号。
5. **中文数字必须含"零"和"千"**：`第一百零一条` 需含"零"，`第一千二百六十条` 需含"千"。**缺"千"让民法典只抽到 999/1260 条**（整整丢掉婚姻家庭编、继承编、侵权责任编等 261 条）。字符集集中在 `knowledge_extract.CN_NUM_CHARS`，`cn_to_int` / 交叉引用正则 / 关键词清洗正则都从它派生。
6. **"编"必须参与分组**：民法典 7 编章号重新起算，`graph_build` 的 sibling 分组键与 `wiki_writer` 的目录分组都必须带 `part`，否则不同编的同号章会合并（曾产生 3.7 万对假边、全库边数虚高到 40992）。
7. **章标题会变成垃圾知识点**：页眉噪音（如 `www.npc.gov.cn/...`、`8/23/2020`）若未被过滤，会被挂到"章"下，`flush()` 时生成 clause 为空的假知识点（曾 34 个）。`flush()` 要求 `clause` 非空可兜住这类问题。
8. **`\f` 会让行号漂移**：源文件含换页符（民法典 130 处），`splitlines()` 把 `\f` 当换行，`source` 行号比编辑器实际大（末尾偏移 129 行，溯源失效）。必须按 `\n` 拆行、行内按 `\f` 分段并共用行号。
9. **Milvus 写入/删除要 flush 才可见**：`upsert()`/`delete()` 后必须 `flush()`，`query()` 才能查到。所以"清理陈旧主键"必须**先取旧主键集合再写入**——写反了会读成空集、清理静默失效（已踩过，`test_milvus_store.py` 用带可见性延迟的假 client 卡住这个顺序）。
10. **`.npy` 向量缓存是历史包袱**：它只按"文件是否存在"判断命中，民法典入库后图谱 1238 节点、缓存却还是 205 行，`VectorRetriever` 不报错、只是永远只在前 205 个节点里排序（**民法典的语义召回长期静默失效**）。现已改为 Milvus + 全量对齐，`Embedder.cache()` 删除。
11. **`pymilvus` 版本**：必须 `>=2.5,<2.6`。2.4.x 在 Python 3.13 上 `import` 即失败（依赖已被移除的 `pkg_resources`）。2.5.x 客户端连 Milvus v2.4.4 服务端可用（已实测：写入 1465 条 + 建索引 + 检索正常）。
12. **多跳依赖条文召回**：靠 crossref 边（正文"第X条"引用）在图谱扩展阶段带出；概念映射关键词补"用户口语→法条"鸿沟。两者结合才使多跳用例 recall@8 达 0.926。
13. **多法同条号混淆**：多部法律同有 85/98 等条文号，生成上下文标题必须带法律名，否则 LLM 会张冠李戴；民法典标题还带编名。
14. **bge-m3 模型较大**（2.2GB），首次加载慢；`graph_build` 编码一次后向量复用（语义边 + Milvus 写入库），不要再重复编码。
15. **知识库规模**：1465 知识点 / 21252 边（sibling 15306 / semantic 11720 / keyword 3446 / crossref 97）；语义阈值 0.75（0.65 会近全连接），keyword 0.3，crossref 权重 1.5。
16. `.gitignore` 忽略：`logs/`、`eval/results/`、`.claude/`、`.venv/`、`__pycache__/`、`wiki/concepts/.obsidian` 等。
    ⚠️ 三处历史记载与现状不符（2026-09-13 核对）：① 文件里那行 `CLAUDE.md` 是**注释掉的**，所以 CLAUDE.md **已被 git 跟踪**（不是"被忽略"）；② `任务描述.md` **已不在工作区**（结构树里那条已删）；③ ` data/` 一行带**前导空格**，git 不当作忽略规则，`data/` 实际**未被忽略**——这与本意"数据源默认提交以重建知识库"一致，但规则写法是坏的。
    **知识入库情况**：`knowledge_graph.json`（权威源）与 `wiki/` 都被 git 跟踪，便于看差异与审计。
    ⚠️ 2026-09-13 起知识点页**只在磁盘保留 10 个示例页**（`WIKI_SAMPLE_REFS`），但 git 里**仍跟踪原有 1465 页**（当时按"仅删磁盘、不动 git"处理）——于是 `git status` 会长期显示约 1455 条 `D`，且 **`git checkout .` 会把它们全部恢复回磁盘**（恢复后就与"只留示例页"的预期不符，需手工清理）。`wiki/concepts/.obsidian/`（Obsidian 本地 UI 状态）已忽略且已移出跟踪。
17. **意图识别别"两头堵"**：分类器**只做路由**，不参与知识定位；且任何故障都要**保守放行**（归 `ANSWERABLE`）。反过来若把分类做得过于激进，正常法律问题会被误判成"未覆盖"而白白拒答——改动分类提示词或换模型后，跑 `uv run python -m eval.evaluate --skip-ragas --intent` 看误判率（用例全是法律题，非 `answerable` 即为误判）。
18. **反思式检索别做成"纯反思"**：曾实现过一版"反思完全替代图谱扩展、候选只有引用关系"，recall@8 从 **0.9262 掉到 0.8548** —— 掉的全是 sibling/semantic 邻居带来的补充（它们不属于"引用关系"）。现方案是 **reflect = 基础扩展 + 反思补取**（超集），recall 与 bfs 完全一致（0.431/0.7119/0.8405/0.9262）。改这块务必先跑 `RETRIEVAL_MODE=reflect` 与 `bfs` 两遍 `eval --skip-ragas` 对比。
19. **反思的增量天然有限**：全库只有 **68/1465** 个知识点正文里有可跟进的同法引用（97 处），且这些被引条文大多已被基础扩展（crossref 权重 1.5）带进上下文。实测 35 条用例中：19 条因"无引用关系"零额外调用、约 20 条各多 1 次 qwen-turbo、**只有 2 条真正补到条文（+4 个知识点）**。若想让反思承担更多，可考虑调低 BFS 的 crossref 权重把引用链让给反思（未验证）。
20. **跨话题串题（已在 P3 修复）**：`cli.py` 曾无条件把上一问并入检索词（`" ".join(past + [q])`）以处理"那/具体"指代，用户**换话题**时会污染检索词、并让反思顺着错话题补取条文（实测 P2：会话里先问"劳务派遣…"再问"试用期最长多久"，命中混入 KP_065/058/059）。现由 `memory.build_search_query(mode="auto")` 只在"像追问"（含指代词，或很短且无法律实体词）时才并入；修复后同一场景 Q2 的 13 块全是试用期条文，无劳务派遣残留。
21. **记忆不做降级是有意为之**：曾实现过 SQLite 降级后端，但它在 `facts()` 里漏了 `user_id` 过滤，导致**画像跨用户泄漏**（单测 `test_alice/bob` 抓到）——降级路径同样要守隔离红线，且多用户场景（P4）下 MySQL 一挂就会踩到。现已**彻底删除 SQLite 记忆实现**：MySQL 是唯一后端，连不上直接报错，避免数据分散、也避免"以为在写 MySQL 实际写本地文件"。（旧 `memory.db` 的 42 条已于 2026-09-13 迁入 MySQL 并删除文件与迁移脚本。）
22. **Milvus 的 flush 坑在记忆库上又踩了一次**：`UserMemoryStore.clear()` 一开始漏了 `flush()`，真实 Milvus 上"清除我的数据"看似成功、其实延迟生效（当时 `count()` 仍读到旧值）。**写入与删除都要 flush**（见第 9 条）；假 client 必须忠实模拟"未 flush 查不到"，否则这类顺序问题在单测里蒙混过关。
23. **长期记忆只注入生成、绝不并入检索词**：记忆是"用户背景"，不是法律知识。并入检索会重演串题污染，且可能让 LLM 把用户历史当成法条依据。注入段落显式标注"不得作为法律依据、不得列入引用"，`test_long_term.py` 有专测卡住这一点。
24. **容器化已放弃（决策，2026-09-13）**：曾写出多阶段 Dockerfile 与 compose `web` 服务并排障，最终按用户决定**移除容器化配置**，改为本地 `uv run python -m app.api.main` + `npm run build`。留档三条结论，日后若重做可省时间：① ghcr.io 与 `deb.debian.org` 在部分网络下不可达（后者解析到代理 fake-IP 段返回 503）——镜像不要依赖 ghcr，也尽量不用 apt；② uv 装 torch 时 `UV_TORCH_BACKEND` 与 `--torch-backend` 都**不可用**（本机 uv 0.8.5 无此特性、容器内 0.12.13 亦无），无法让 uv 自动切 CPU 源；③ Linux 的 PyPI torch 轮子是 **CUDA 链接版**（`import` 硬要 `libcudart.so.13`），只排除 `nvidia-*`/`triton`/`cuda-*` 会直接 `import torch` 失败；要纯 CPU 必须从 `https://download.pytorch.org/whl/cpu` 换成 `+cpu` 轮子（该源确有大 arm64 轮子）。
25. **引用溯源要跟着消息一起存**：一开始引用只留在前端内存里，`onDone` 把流式文本转成历史消息时丢掉 citations，**回答一完成、最该被核对的溯源信息就消失了**；且刷新/切会话后彻底没有。现把 citations 以 JSON 存进 `messages.citations`，历史回看仍可溯源。改这块时注意：`add()` 与 `session_messages()` 都要带上该字段。
26. **FastAPI 的 included router 不在 `app.routes` 顶层**：新版 FastAPI 用 `_IncludedRouter` 包装子路由，用 `[r.path for r in app.routes]` 检查会以为"路由没注册"（我据此误判过一次）。要确认路由请直接发请求或用 `/openapi.json`。
27. **前端选择器别用 `main button` 这类模糊匹配**：页面上出现引用面板后，`main button` 命中的是面板折叠按钮而非"提问"按钮（浏览器实测时踩到）。用 `div.border-t button` 这类带结构特征的写法。
28. **图谱存储的分工别搞反**：`knowledge_graph.json` 是**权威源**（用户要求保留以便 git diff），Neo4j 是**派生副本**。改了 json 或知识源后必须重跑同步（`python -m app.ingest.sync_graph`），否则出现"json 已更新、Neo4j 还是旧图"的静默不一致——kb 只读 Neo4j，不会发现 json 变了。
29. **Neo4j 的两个坑**：① 不允许**空列表**作为属性值（关键词为空要存 None，读回再还原 `[]`，否则写入直接失败）；② id 形如 `KP_1000` 与 `KP_999` 的**字典序不等于数值序**，必须用 `seq` 属性排序才能还原 json 的节点顺序（顺序影响检索并列打破）。
30. **同一函数里派生副本的失败策略要一致**：pipeline 里 Milvus 写入原本 try/except 警告后继续，而 Neo4j 同步直接失败——两者都是派生副本，已统一为**都显式失败**（json 已落盘，构建结果不会丢）。
31. **改完 `kb.load()` 后留意变量名**：我曾用 `store = Neo4jGraphStore()` 覆盖了 `load(store=...)` 这个**注入 Milvus 向量库的参数**，导致测试注入失效（`test_kb` 抓到）。图存储请用 `graph_store` 之类的名字。
32. **凭证只放一处**：服务账号密码集中在 `docker/.env`（不入库），compose 与应用共用；应用侧用 `python-dotenv`（`override=False, interpolate=False`）只填空缺、**不覆盖已有环境变量**、不展开 `$`。密码里应避免 `$`（compose 需 `$$` 转义，而 dotenv 按字面读取不还原）。注意由此带来的变化：应用的 MySQL 连接账号从 `root` 变成了 `.env` 里的 `vincent`（最小权限，已验证建表/读写/画像都够用）；想继续用 root 就把 `MYSQL_USER` 改回 root。
33. **图谱已纳入 git**：`knowledge_graph.json` 现被跟踪（提交在「数据库迁移neo4j」），`.gitignore` 里那条旧忽略规则已删——留着虽对已跟踪文件无效，却会让 `git check-ignore` 误导性地报"已忽略"。同时把 `wiki/` 从忽略里移除、1260 个新知识点页纳入版本库（git 里现为 1465 页 + index + log），并把 5 个 `.obsidian/` UI 状态文件移出跟踪（磁盘保留）——此前它们是"已跟踪但被忽略"，与刚修的那个矛盾同源。（2026-09-13 起磁盘上只保留 10 个示例页，git 索引未动，见第 16 条。）
34. **测试绝不能写生产日志**：API 层用的是模块级真实 logger，`tests/conftest.py` 若不替换它，**跑一次 pytest 就往 `logs/qa.log` 写几十条假问答事件**（曾污染 297 条、占日志 69%，既毁审计也毁日志分析）。夹具已注入 `NullLogger`，并有回归测试卡住（`test_api_tests_do_not_write_production_log`）。
35. **config 里的参数一定确认真的传下去了**：`kb.py` 构造 `HybridRetriever` 时曾**没传** `EXPAND_K/EXPAND_HOPS/EXPAND_DECAY`，于是长期沿用构造函数默认 `expand_k=3`，而 config 与文档都写着 5 —— 是日志里 `graph_expanded` 恒为 3 才暴露的。`TOP_K` 之所以正常，是因为它由调用方（CLI/Web/eval）显式传给 `search(top_k=...)`。现已显式传入并有回归测试（`test_expand_params_flow_from_config`）。
36. **`EXPAND_K` 只影响第 6 位之后的补充条数**：eval 用 `--top-k 5`，`recall@8` 只看前 8 位（= 5 种子 + 前 3 扩展），而 hop=1 的扩展分数最高，所以 `EXPAND_K` 从 3 改到 5 **recall@1/3/5/8 逐位不变**（实测两组数字完全相同）；它的真实影响在**生成上下文规模**：生产路径 13 → 15 块。评估想反映扩展规模，得调 `--top-k` 而不是只看 recall。
37. 项目已 `git init`（当前分支 **`v2.0`**，主分支 main；另有 `v1.0`/`v1.1` 历史分支）。
38. **`react-markdown` 默认不含 GFM，表格会原样吐出来**：本项目一直只装了 `react-markdown`（它的依赖里只有
    remark-parse/remark-rehype），所以回答里的 markdown 表格**不渲染**，`| a | b |` 与 `| --- | --- |` 被当成普通
    段落文字显示。修复：装 `remark-gfm` 并给 `<Markdown>` 挂 `remarkPlugins={[remarkGfm]}`（顺带支持删除线、
    任务列表、自动链接）。表格还要**外包一层 `overflow-x:auto`**——气泡不定宽，宽表会撑破布局。
    为避免再踩：渲染 markdown 一律走 `components/MarkdownBody.jsx`，不要在模块里各自写 `<Markdown>`。
39. **前端骨架模块别急着接后端**：起诉状模块先只做"表单 + 本地拼接预览"，是为了把**字段与文书格式**定稿，
    再接生成能力；按钮置灰 + 「开发中」标记（`MODULES[].enabled=false`）能诚实地告诉使用者当前边界。

---

## 10. 后续开发建议（供参考）

- **检索增强**：可选实现"查询分解"（LLM 拆子问题分别检索再合并）——已讨论，当前未实现（已实现 A：图谱多跳 + crossref + 概念关键词增强 + E：prompt 多跳引导）。
- **起诉状生成的后端接入**（前端骨架已就位，见 §5.7 与 §9 第 39 条）：确定接口形态（是否复用检索链路取"事实与理由"
  的法条依据）→ 把 `ComplaintPage.buildComplaint()` 换成调后端 → `MODULES` 里该项 `enabled` 改 true、去掉按钮 `disabled`。
  注意：起诉状属于**文书生成**而非问答，若要引用法条，应复用 `KnowledgeBase.search` 而不是重造检索。
- **宽泛措施题**：eval_026/030 类"维权途径/责任"题，可考虑按预期条文补 crossref 或概念映射，或放宽生成上下文。
- **实体链接 + 图传播**：从 query 抽实体 → 图谱节点 → 影响力传播召回。
- **评估完善**：可增加"测试集抽样"、`--limit` 随机采样、结果对比历史版本。
- **新增法律**：把新法律 PDF 放入 `data/src/` → 重跑 pipeline；如需其在概念导航出现，需在 `concept_map.py` 补映射。若新法律也有"编/分编"层级或千位条号，先确认 `CN_NUM_CHARS`/`_PART_RE`/`_SUBPART_RE` 覆盖得住。
- **图数据库**：Neo4j（`neo4j:5.26.26`，7474 Browser / 7687 Bolt）已接入并成为检索读图来源；可选增强：把 `:RELATED` 拆成四种关系类型以支持更图原生的查询（会改变关系计数）。
- **多用户/并发/异步**、**意图识别与兜底回答**、**图数据库存图谱**：见 README「后续优化功能」。

---

## 11. 验收/继续开发检查清单

- [ ] Milvus 在跑：`docker ps` 见 `milvus-standalone`（宿主机端口 **19532**）
- [ ] `uv run python -m pytest tests/ -v` → **217 passed**
- [ ] `uv run python -m app.ingest.pipeline` → **1465 知识点 / 21252 关联边** + `向量已写入 Milvus 集合 llm_wiki_laws：1465 条`
- [ ] Milvus 条数核对：`MilvusVectorStore().count()` → 1465
- [ ] `export API_KEY=... && uv run python -m app.cli` → 多轮问答正常（含民法典问题，如"高空抛物致人损害谁担责"应命中第一千二百五十四条）
- [ ] `uv run python -m eval.evaluate --skip-ragas` → recall@1≈0.43 / @3≈0.71 / @5≈0.84 / @8≈0.93
- [ ] 新增知识源：放 `data/src/*.pdf` 或 `data/raw/*.md` → 重跑 pipeline（**必须重跑，向量库不会自己发现知识库变了**）
- [ ] 新增法律概念导航：编辑 `app/ingest/concept_map.py` 补映射 → 重跑 pipeline
