# llm-wiki 法律知识问答系统

基于 **llm-wiki** 理念构建的**法律知识问答系统**，提供**Web 界面**（多用户注册登录）与**命令行**两种入口。系统将《中华人民共和国劳动法》《中华人民共和国劳动合同法》《中华人民共和国民法典》PDF
提前"编译"为结构化的**知识点图谱**，查询时通过 **关键词（BM25）+ 向量（bge-m3）混合检索**定位知识点，再由大模型 `qwen-max`
综合生成回答，并带**短期记忆**支持多轮对话。

> 本项目**不是 RAG 系统**：不对文档切块、不把向量作为知识库存储载体。知识库本体是结构化 Wiki 页面 + 关联图谱
> （`knowledge_graph.json` 为权威源，另同步一份到 Neo4j 供图查询），向量检索仅作为混合召回手段之一。

---

## 一、核心特性

- **Web 界面（多用户）**：注册/登录（bcrypt + JWT HttpOnly Cookie）、多会话管理与历史切换、**流式打字机**回答、**引用溯源面板**（列出命中的知识点、法律名、分数与原文行号）；深色科技墨蓝 + 金色点缀、法条正文用衬线体。前端构建产物由 FastAPI **同源托管**（无 CORS/CSRF 配置负担）。
- **意图识别与兜底**：问答前先分类为「可答 / 法律相关但本库未覆盖 / 与法律无关」，后两类直接给兜底话术（说明本库覆盖范围 + 可问示例），**不检索也不生成**；分类器超时或异常时保守放行，不阻断正常问答。
- **llm-wiki 知识编译**：摄入时把 PDF 编译为知识点 + 关联图谱，查询时只做"定位 + 综合"，知识与答案随时间复利积累。
- **非 RAG 知识库**：不切块、不向量化存储；知识库是 `wiki/` 下的 Markdown 页面集 + `knowledge_graph.json`
  关联图谱，可直接用上下文窗口承载、可读可审计。
- **法条级知识点**：按"编（第X编）→ 章（第X章）→ 节（第X节）→ 条（第X条）"切分，**每一条一个知识点**（劳动合同法 98 条 +
  劳动法 107 条 + 民法典 1260 条 = **1465 个**），可精确溯源到具体法条。
- **混合检索**：BM25 关键词检索（索引含**概念关键词增强**，弥合法律措辞与用户口语鸿沟）与 bge-m3 向量检索（**存于 Milvus**）双路召回，*
  *RRF（Reciprocal Rank Fusion）** 融合排序，并沿关联边扩展相关知识点。
- **多跳推理（反思式）**：图谱四类边（结构/关键词/语义/法条交叉引用）扩展出基础上下文后，由 **LLM 反思**自主决定是否继续沿**引用关系**深挖——判"已充分"就停、判"需要补"则只从**候选白名单**里挑被引条文（防幻觉）；无引用关系时**不调用 LLM 直接停止**。轮数上限 3，失败即停（fail-closed）。
- **两条链路可切换**：`RETRIEVAL_MODE=reflect`（默认，= 图谱扩展 + 反思补取，上下文是 bfs 的超集）/ `bfs`（原固定深度扩展，保留用于 A/B 对比与回退）。
- **图数据库（Neo4j）**：图谱同步到 Neo4j，**四类边拆成四种关系类型**（`:SIBLING`/`:KEYWORD`/`:SEMANTIC`/`:CROSSREF`，共 30569 条关系），其中 `:CROSSREF` 的**引用方向在同步时从正文派生**，可直接做有向引用链遍历；应用检索时从图数据库读图；
  `knowledge_graph.json` 仍是**权威源**（可 git diff、可审计），改动后跑 `python -m app.ingest.sync_graph` 即让 Neo4j 与之一致。
- **概念导航**：`wiki/index.md` 提供"核心概念导航"，把"试用期/竞业限制/加班费/劳务派遣…"等法律概念链接到具体法条页。
- **双层记忆**：**短期**=会话消息（**MySQL** `messages`），按需注入检索词（只在"像追问"时才并入上一问，见下）与生成历史；**长期**=用户画像（MySQL `user_profile`）与历史问答语义索引（**Milvus** `llm_wiki_user_memory`），新问题先按 `user_id` 过滤召回该用户的相关记忆，作为"用户背景"注入生成（**不得作为法律依据**）。画像用 qwen-turbo **低频抽取**（默认每 5 轮），历史记忆用本地 bge-m3 编码（零 API 成本）。
- **记忆只有 MySQL/Milvus 一套后端**：不做 SQLite 降级、不做连接失败回退——连不上就直接报错，避免数据分散或"以为在写 MySQL 其实在写本地文件"。
- **引用溯源**：回答末尾标注引用的知识点标题（**含法律名**，避免多部法律同条号混淆），支持回溯原文（来源行号）。
- **向量库（Milvus）**：知识点向量存于 Milvus（HNSW + COSINE），每次构建**全量对齐并清理陈旧主键**；Milvus 不可用时自动退化为纯关键词检索。
- **流式输出**：CLI 采用 SSE 流式逐字渲染回答（打字机效果）。
- **结构化日志**：每次问答记录一条 JSON 事件，滚动写入 `logs/qa.log`。

### 后续优化功能

- 系统改为异步过程，增加系统的并发能力
- 系统压力测试
- 知识库更新（新增法律时重跑 pipeline；概念导航需在 `concept_map.py` 补映射）

> 已完成并移入上方「核心特性」：前端页面与多用户注册登录、长期+短期双层记忆、SQLite → MySQL、
> 智能体自主多跳检索、意图识别与兜底、知识图谱存入 Neo4j（图数据库）。

---

## 二、技术架构

> 一句话：**摄入时把法律「编译」成知识点 + 关联图谱（知识本体是文件与图谱，不是向量）；
> 查询时先判意图，再混合检索定位知识点、沿引用关系反思式多跳，最后带着用户记忆生成。**

```
┌─ ① 入口层（两个入口共用同一条问答链路） ───────────────────────────────────────────┐
│浏览器（React 19 + Vite + Tailwind v4）：注册/登录 · 多会话 · 流式回答 · 引用溯源   │
│        │  HTTP + SSE（HttpOnly Cookie 认证；前端与 API 同源，免 CORS/CSRF）        │
│        ▼                                                                           │
│FastAPI（app/api/）：认证 · 会话 CRUD · SSE 问答 · 同源托管前端构建产物             │
│        │                                                                           │
│CLI（app/cli.py）────────────────┘  同一链路，区别只在「用户身份」来源              │
└────────────────────────────────────────────────────────────────────────────────────┘

┌─ ② 知识构建管线（Ingest · 离线，pipeline 幂等重建） ───────────────────────────────┐
│data/src/*.pdf ──markitdown（增量）──► data/raw/*.md                                │
│                                     │                                              │
│   清洗（页眉日期/页码/裸域名/康熙部首变体/换页符行号）                             │
│                                     ▼                                              │
│   按「编 → 章 → 节 → 条」切分 ──► KnowledgePoint × 1465（每条法条一个知识点）      │
│                                     │                                              │
│   ├─► 概念关键词增强（concept_map 53 个概念：加班费 → 劳动法第四十四条 …）         │
│   ├─► 图谱 knowledge_graph.json（权威源，21252 条边，可 git diff）                 │
│   │      sibling 同(编,章) │ keyword Jaccard≥0.3 │ semantic 余弦≥0.75              │
│   │      crossref 正文「第X条」引用（权重 1.5，仅同法内）                          │
│   │      └─► 同步 ──► Neo4j 图数据库（应用检索时从这里读图）                       │
│   ├─► 向量 → Milvus llm_wiki_laws（dim1024 · HNSW+COSINE · 全量对齐+清理陈旧）     │
│   └─► Wiki 页面集 wiki/（index.md 概念导航 + 1465 个知识点页 + log.md）            │
└────────────────────────────────────────────────────────────────────────────────────┘

┌─ ③ 问答链路（Query · 在线） ───────────────────────────────────────────────────────┐
│用户问题（已登录身份 user_id）                                                      │
│        │                                                                           │
│        ▼                                                                           │
│意图识别（qwen-turbo 三分类）                                                       │
│   ├─ 与法律无关 / 本库未覆盖 ──► 兜底话术（不检索不生成，仍写记忆与日志）          │
│   └─ 可答 ──► 短期记忆按需注入检索词（仅「像追问」时并入上一问，避免串题）         │
│        │                                                                           │
│        ▼                                                                           │
│双路召回 → RRF 融合（图谱节点与边均来自 Neo4j）                                     │
│   · 关键词：jieba + BM25（索引含概念关键词）                                       │
│   · 向量  ：bge-m3 → Milvus 余弦检索                                               │
│   → Σ 1/(60+rank) 融合排序 → Top-K = 10 个种子                                     │
│        │                                                                           │
│        ▼                                                                           │
│基础图谱扩展（≤3 跳，四类边，每跳预算 EXPAND_K=5）                                   │
│   └─► 反思循环（≤3 轮）：候选 = 被引条文白名单                                     │
│         无引用关系 → 不调 LLM 直接停；有则判「已充分 / 该补哪几条」；失败即停      │
│        │                                                                           │
│        ▼                                                                           │
│长期记忆召回（强制按 user_id 过滤）                                                 │
│   画像（MySQL user_profile）+ 相似历史（Milvus llm_wiki_user_memory）              │
│   └─► 作为「用户背景」注入生成（标注不得作为法律依据；绝不并入检索词）             │
│        │                                                                           │
│        ▼                                                                           │
│生成：qwen-max 流式（标题带法律名防跨法同条号混淆 + 多跳引导 + 列出引用）           │
│        │                                                                           │
│        ▼                                                                           │
│落库：MySQL messages（含 citations）+ Milvus 用户记忆 + logs/qa.log                 │
└────────────────────────────────────────────────────────────────────────────────────┘

┌─ ④ 存储分工 ───────────────────────────────────────────────────────────────────────┐
│Neo4j  图数据库（查询副本）：节点 + 四类关系（SIBLING/KEYWORD/SEMANTIC/CROSSREF）   │
│Milvus 向量（非知识载体）：llm_wiki_laws 法律向量 │ llm_wiki_user_memory 用户记忆   │
│MySQL  记忆唯一后端：users · sessions · messages(含 citations) · user_profile       │
│文件   权威源（人读/可审计/可 git diff）：knowledge_graph.json 图谱 │ wiki/ 页面集  │
└────────────────────────────────────────────────────────────────────────────────────┘
```

**两条链路的分工**

| | 摄入（Ingest） | 查询（Query） |
|---|---|---|
| 何时跑 | 离线；数据变更后重跑 `app.ingest.pipeline`（幂等全量重建） | 每次提问；Web 与 CLI 共用 |
| 输入 | `data/src/*.pdf`、`data/raw/*.md` | 用户问题（+ 会话历史 + 用户记忆） |
| 产出 | `knowledge_graph.json`（权威源）、**同步到 Neo4j**（四类关系）、`wiki/` 页面集、Milvus 法律向量 | 带引用的回答；写回短期与长期记忆 |

**为什么不是 RAG**：文档不在查询时切块、向量不作为知识载体。知识本体是 `wiki/concepts/*.md`
（1465 页，每页一条法条）与 `knowledge_graph.json`（21252 条关联边），可直接用上下文窗口承载、
可读可审计；Neo4j 与 Milvus 都是**派生副本**（前者存图、后者存向量），不是知识本体。

---

## 三、项目结构

```
llm_wiki_laws/
├── pyproject.toml                # 依赖与元数据（uv 管理）
├── docker/
│   └── docker-compose.yml        # Milvus v2.4.4 + etcd/minio + redis/mysql + neo4j（端口见文件）
├── data/
│   ├── src/                        # 原始 PDF 源文件（只读，可新增）
│   │   ├── 中华人民共和国劳动合同法.pdf
│   │   ├── 中华人民共和国民法典.pdf
│   │   └── 中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.pdf
│   └── raw/                        # markdown（PDF 转换产物 + 用户直接放入，可新增）
│       ├── 中华人民共和国劳动合同法.md
│       ├── 中华人民共和国民法典.md
│       └── 中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md
├── web/                          # 前端（React + Vite + Tailwind v4）
│   ├── src/api.js                #   API 层：fetch + 手动解析 POST 的 SSE
│   ├── src/pages/                #   AuthPage（登录注册）/ ChatPage（问答主界面）
│   ├── src/components/           #   SessionList / MessageList / CitationPanel / Composer
│   └── dist/                     #   构建产物（由 FastAPI 托管）
├── app/
│   ├── api/                      # ⑤ Web API（FastAPI）
│   │   ├── main.py               #   应用入口 + 托管前端产物（SPA 回退）
│   │   ├── auth.py               #   bcrypt 密码 + JWT(HttpOnly Cookie)
│   │   ├── deps.py               #   依赖注入（当前用户/KB/记忆/生成器，测试可替换）
│   │   ├── routes_auth.py        #   注册/登录/登出/当前用户
│   │   └── routes_chat.py        #   会话 CRUD + SSE 流式问答
│   ├── config.py                 # 应用配置（**唯一配置源**：路径、bge-m3、LLM、Milvus、MySQL、检索参数）
│   ├── kb.py                     # 知识库加载与统一检索入口
│   ├── cli.py                    # 命令行交互式问答（含短期记忆 + 日志）
│   ├── logger.py                 # 结构化问答日志（滚动写入 logs/qa.log）
│   ├── ingest/                   # ① 知识构建管线
│   │   ├── pdf_to_markdown.py    #    markitdown PDF→Markdown
│   │   ├── knowledge_extract.py  #    清洗 + 编/章/节/条解析 + 知识点提取 + 关键词
│   │   ├── concept_map.py        #    法律概念→条文映射 + 关键词增强
│   │   ├── graph_build.py        #    关联图谱构建（sibling/keyword/semantic/crossref）
│   │   ├── graph_store.py        #    图谱的 Neo4j 读写（四类关系拆分、全量对齐、批量 UNWIND、seq 排序）
│   │   ├── sync_graph.py         #    knowledge_graph.json → Neo4j 同步入口（含 --check-only）
│   │   ├── wiki_writer.py        #    Wiki 页面集落盘（index 含核心概念导航）
│   │   └── pipeline.py           #    管线入口
│   ├── retrieval/                # ② 混合检索
│   │   ├── embedder.py           #    bge-m3 向量编码器
│   │   ├── vector_store.py       #    Milvus 向量库（建集合/全量对齐写入/余弦检索）
│   │   ├── keyword_retriever.py  #    BM25 关键词检索
│   │   ├── vector_retriever.py   #    bge-m3 编码 query → Milvus 检索 → 映射回知识点
│   │   ├── hybrid_retriever.py   #    RRF 融合 + 基础图谱多跳扩展（expand=False 时只要种子）
│   │   ├── reflector.py          #    检索反思器：判"是否已充分/该补哪几条引用条文"
│   │   └── reflective_retriever.py #  反思式多跳：基础扩展 + 反思补取（默认链路）
│   ├── generation/
│   │   └── answer.py             # ③ qwen-max 综合生成（法律领域 prompt，带引用）
│   └── memory/                   # ④ 双层记忆
│       ├── mysql_store.py        #    短期记忆 + 画像（MySQL，唯一后端）
│       ├── long_term.py          #    长期记忆向量库（Milvus，强制 user_id 过滤）
│       ├── profile_extractor.py  #    画像低频抽取（qwen-turbo）
│       └── __init__.py           #    create_memory_store() + 检索词注入策略
├── wiki/                         # 编译出的知识库（llm-wiki 页面集）
│   ├── index.md
│   ├── log.md
│   └── concepts/KP_*.md          # 1465 个知识点页面
├── knowledge_graph.json          # 知识点关联图谱（**权威源**，节点 + 带类型边；Neo4j 由它同步）
├── logs/
│   └── qa.log                    # 问答日志（滚动，JSON 事件流）
├── eval/                         # 评估
│   ├── qa_test_cases.jsonl       #   35 条劳动法评估用例（单跳/多跳/复杂）
│   ├── metrics.py                #   recall@1/3/5/8 检索指标（自定义）
│   ├── evaluate.py               #   评估主程序（recall@k + ragas）
│   └── results/                  #   评估结果输出
├── tests/                        # 单元测试（pytest，201 项）
└── README.md
```

---

## 四、知识构建管线（Ingest）

数据源约定（新增源文件直接放入对应目录，重新运行流水线即可自动并入）：

- `data/src/*.pdf`：原始 PDF，自动转为 markdown（**增量**，已转换则跳过）
- `data/raw/*.md`：markdown 原始文件（PDF 转换产物 + 用户直接放入的 md），逐个解析提取知识点

1. **PDF → Markdown**：使用微软 [markitdown](https://github.com/microsoft/markitdown) 扫描 `data/src/*.pdf`，仅对
   `data/raw/` 中缺失对应 md 的 PDF 转换。
2. **噪音清洗**：过滤页眉日期（`2024/11/15`、`8/23/2020` 两种写法）、孤立页码、URL（含**裸域名 `www.`**）、网页脚注、目录点线；对**康熙部首变体**（法律 PDF 的"第⼀条"等）做字符归一；`source` 行号按物理行计算（换页符 `\f` 不再让行号漂移）。
3. **知识点提取**：按"编（第X编）→ 章（第X章）→ 节（第X节）→ 条（第X条）"切分，**每一条一个知识点**，产出 `KnowledgePoint`：
   | 字段 | 含义 |
   |---|---|
   | `id` | 唯一标识（KP_001…） |
   | `title` | 如"第四章·第四十七条"；有编的法律为"第二编 物权·第二章·第十三条" |
   | `part` / `part_title` | 编号 / 编标题（如"第二编" / "物权"），无编的法律为空 |
   | `chapter` / `chapter_title` | 章节号 / 章节标题 |
   | `clause` | 条文（节内为"第X节 节名·第X条"） |
   | `content` | 清洗后的正文 |
   | `keywords` | jieba TF-IDF + 概念增强后的关键词 |
   | `source` | 来源文件与行号 |

   > 民法典各编章号重新起算（7 编里都有"第一章"），因此 **`part` 必须参与分组**：图谱的 sibling 边按 (编, 章) 划分，Wiki 目录按 编→章 两级组织。
4. **概念关键词增强**：`concept_map.py` 把用户常用查询词（加班费、拖欠工资、试用期、离婚冷静期、高空抛物…，共 53 个概念）注入对应条文知识点关键词，弥补法律措辞与口语鸿沟。
5. **关联图谱构建**：四类带类型边——`sibling`（同 **(编, 章)** 条文）、`keyword`（关键词 Jaccard ≥0.3）、`semantic`（bge-m3 余弦 ≥0.75）、`crossref`
   （正文"第X条"交叉引用，权重 1.5，仅同法内），输出 `knowledge_graph.json`（1465 节点 / 21252 边）。
6. **向量写入 Milvus**：知识点向量（dim 1024）写入集合 `llm_wiki_laws`，索引 HNSW + COSINE；写入采用**全量对齐**并删除已不在知识库中的陈旧主键，保证集合内容与当前知识库一致。
7. **Wiki 页面落盘**：生成 `wiki/index.md`（**核心概念导航** + 按源文档分组）、`wiki/concepts/*.md`（每页含双向链接
   `[[KP_xxx]]`）、`wiki/log.md` 日志。

---

## 五、混合检索（Query）

0. **意图识别**（`app/generation/intent.py`，可通过 `INTENT_ENABLED` 关闭）：判定为`legal_uncovered`（刑法/行政法/公司法等其他部门法）或`off_topic`（与法律无关）时，直接返回兜底话术，**不进检索与生成**，但照常写记忆与日志（日志带 `intent` 字段）；分类器超时（默认 3s）、异常或无 `API_KEY` 时保守放行。
1. **记忆注入**：取出最近一条用户问题并入当前检索词，使"那/具体/继续"等指代延续有效。
2. **双路召回**：
    - 关键词：jieba 分词 + `BM25Okapi`，索引含标题、正文与**概念增强后的关键词**（用户口语词可命中法律条文）；
    - 向量：bge-m3 编码 query，到 **Milvus** 做余弦检索，命中的 `kp_id` 映射回知识点。
3. **RRF 融合**：`score = Σ 1/(60 + rank)`，两路结果合并去重排序，取 Top-K（默认 **10**）。
4. **图谱多跳扩展**：从命中种子沿关联边 **BFS 最多 3 跳**遍历，节点分数按 `seed × 边权重 × 0.5^跳数` 逐跳衰减，每跳剪枝、最终只取前
   `EXPAND_K` 个（默认 **5**）补充进结果——**crossref 边**（法条互相引用）权重 1.5，使被引用条文（如第六十五条引的第三十九条）稳定进入上下文。
5. **多跳推理生成**：将检索块（10 种子 + 至多 5 扩展）正文注入 prompt，`qwen-max` 综合生成；上下文标题**带法律名**，prompt
   显式引导跨知识点组合推理并列出全部引用。
6. **向量库不可用时**：`KnowledgeBase.load()` 打印告警并退化为纯关键词检索（不再有 `.npy` 缓存回退路径）。

---

## 六、双层记忆

**短期（会话内）** —— MySQL 表 `users` / `sessions` / `messages`（utf8mb4），无本地文件依赖：

- **按需注入检索**：只有当前问题"像追问"（含指代词，或很短且不含法律实体词）时，才把上一问并入检索词
  （`MEMORY_INJECT_MODE=auto`，可设 `always` / `off`）。**这修掉了换话题时的串题污染**：
  此前无条件并入，导致"先问劳务派遣、再问试用期"会混入劳务派遣条文。
- **注入生成**：取最近 10 条消息作为 LLM 对话历史，保证多轮连贯。
- **会话管理**：Web 端多会话切换 / 新建 / 删除；CLI 可指定会话 ID 重开续聊；`/clear` 清空当前会话。

**长期（跨会话）** —— 画像存 MySQL `user_profile`，历史问答语义索引存 Milvus `llm_wiki_user_memory`：

- **写入**：每个轮次把"问题 + 回答要点"写入 Milvus —— 用**本地 bge-m3 编码，零 API 成本**，
  且**不额外调 LLM**；画像由 `qwen-turbo` **每 5 轮抽一次**（只抽跨会话仍有用的用户事实，排除敏感信息）。
- **召回**：提问时**强制按 `user_id` 过滤**召回（画像 + 相似历史），作为「用户背景」注入生成，
  并显式标注**不得作为法律依据**；**绝不并入检索词**——记忆不是法律知识，并入会重演串题污染。
- **清除**：删除会话时连带清空该用户的长期记忆（避免已删内容仍被召回）。
- **不做降级**：记忆只有 MySQL/Milvus 一套后端，连不上直接报错，不静默退回本地文件。

---

## 七、问答日志

每次问答写入一条 JSON 事件到 `logs/qa.log`（`RotatingFileHandler`，单文件 5MB、滚动保留 5 份）。
**字段按需出现**（未检索/未启用记忆时不写），便于 grep 与统计：

| 字段 | 含义 |
|---|---|
| `time` / `user_id` / `query` | 时间 / 会话（用户）标识 / **用户问题** |
| `answer` | **最终回答**（含引用） |
| `mode` | 检索链路：`reflect`（默认）/ `bfs` |
| `seeds` | **检索命中**的知识点（RRF 融合后的种子，含 id/标题/分数） |
| `graph_expanded` | **图谱扩展**带进来的知识点（含 id/标题/分数/**hop 跳数**） |
| `reflect_fetched` | **反思循环沿引用关系补取**的条文（含 id/标题/**第几轮**） |
| `reflect` | 反思轨迹 `{rounds, reasons}`，用于审计"为什么停在第几轮" |
| `intent` / `intent_reason` | 意图分类结果与理由（兜底路径也能看出为什么没检索） |
| `memory_recalled` | 长期记忆召回情况（`{count, kinds}`，**不含正文**） |
| `knowledge_blocks` | 本次送进生成的**全部**知识块（`id`/`title`/`score`/`source`，不含正文） |
| `retrieval_ms` / `generation_ms` | 检索 / 生成耗时（毫秒） |

> 三类来源分开记，是为了排查"**某个法条为什么没进上下文**"：
> `seeds` 没命中 → 检索问题；`graph_expanded` 没带出 → 边/权重问题；
> `reflect_fetched` 为空且理由是"无可跟进的引用关系" → 正常的零成本停止。

日志每行为一条 JSON，可直接用 `grep` / `jq` 分析：

### 日志画像分析

```bash
uv run python -m scripts.analyze_logs                    # 汇总：意图分流/检索链路/上下文构成/反思/成本/耗时
uv run python -m scripts.analyze_logs --tail 200         # 只看最近 200 条
uv run python -m scripts.analyze_logs --since 2026-09-13 # 只看某时间之后
uv run python -m scripts.analyze_logs --exclude-user alice  # 排除某账号（如测试账号）
uv run python -m scripts.analyze_logs --json             # 机器可读
```

它回答的是"**反思值不值那次 LLM 调用**""意图分流了多少""检索与扩展各带进来多少"这类问题——
靠单条日志看不出结论。输出示例（节选）：

```
【上下文构成】   命中 seeds   平均 10.0    图谱扩展   平均 3.0    最终上下文块  平均 10.67
【反思】  停止原因 no_candidates 10.9% / llm_stop 30.4% / no_trace 47.8%
          实际补取到条文的事件：0/2（0.0%）
【LLM 调用成本（估算）】  反思调用/次 平均 0.33   总调用/次 平均 1.76
```

### 直接 grep 日志

```bash
grep -o '"query": "[^"]*"' logs/qa.log                 # 所有用户问题
grep '"user_id": "user001"' logs/qa.log                # 某会话的问答记录
jq -r 'select(.mode=="reflect") | .reflect_fetched[].id' logs/qa.log   # 反思实际补取了哪些条文
jq -r 'select(.graph_expanded) | .query, (.graph_expanded[] | .id + " hop=" + (.hop|tostring))' logs/qa.log
```

---

## 八、快速开始

### 环境要求

- Python 3.13+，[uv](https://docs.astral.sh/uv/) 管理虚拟环境
- 本地 bge-m3 模型（路径配置于 `app/config.py` 的 `BGE_MODEL_PATH`）
- DashScope API Key（`qwen-max`）
- **Milvus** 向量库（`docker/docker-compose.yml` 已含 Milvus v2.4.4 + etcd/minio）

### 安装

```bash
uv sync                     # 安装依赖（含 dev: pytest）
```

### 配置服务凭证（一次性）

```bash
cd docker && cp .env.example .env      # 填 MySQL / Neo4j / Redis / MinIO 的账号密码 + JWT_SECRET
```

**账号密码集中在 `docker/.env`**：docker compose 自动读取它，应用（`uv run ...`）也读同一份，
所以改一处即可让容器与应用一致。该文件不入库（`.gitignore` 已忽略），模板是 `docker/.env.example`。
若密码里含 `$`，compose 需写成 `$$`。环境变量优先于 `.env`（容器化/CI 里可继续用导出变量覆盖）。

### 启动依赖服务

```bash
cd docker && docker compose up -d      # Milvus（19532）+ MySQL（3308）+ Neo4j（7474/7687）+ Redis
docker ps                              # 确认都在跑
```

### 启动 Web 服务（多用户注册登录）

```bash
cd web && npm run build && cd ..      # 构建前端（产物 web/dist 由 FastAPI 同源托管）
uv run python -m app.api.main         # 打开 http://localhost:8080
```

- 前端开发模式：`cd web && npm run dev`（已配 `/api` 代理到 8080）
- 冒烟自检：服务起来后跑 `scripts/smoke_api.sh`（注册→建会话→流式问答→引用持久化→登出）
- **JWT_SECRET**：生产环境务必用环境变量覆盖（默认值仅供本地开发）
- 记忆与向量依赖 MySQL（3308）与 Milvus（19532），需先 `cd docker && docker compose up -d` 启动这两个服务

### 构建知识库

```bash
uv run python -m app.ingest.pipeline
```

首次构建会自动完成 PDF→MD→知识点→图谱→Wiki 落盘，加载 bge-m3 编码语义向量并**写入 Milvus**（集合 `llm_wiki_laws`），
最后把图谱**同步到 Neo4j**。**改了知识源（PDF/md）后必须重跑 pipeline** —— 派生副本不会自己发现知识库变了。

若只改了 `knowledge_graph.json`（或想重新对齐副本），单独跑同步即可：

```bash
uv run python -m app.ingest.sync_graph              # json → Neo4j
uv run python -m app.ingest.sync_graph --check-only # 只比对两侧计数是否一致
```

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
| `BGE_MODEL_PATH`               | `app/config.py`             | bge-m3 模型本地路径（**机器相关，换环境先改这里**）                                     |
| `LLM_MODEL`                    | `app/config.py`             | 大模型名，默认 `qwen-max`                                                   |
| `DASHSCOPE_API_KEY`            | `app/config.py`             | 从环境变量 `API_KEY` 读取                                                  |
| `DASHSCOPE_BASE_URL`           | `app/config.py`             | DashScope OpenAI 兼容接口地址                                            |
| `MILVUS_HOST` / `MILVUS_PORT`  | `app/config.py`             | Milvus 地址（默认 `localhost:19532`，宿主机映射端口，容器内为 19530）                  |
| `MILVUS_COLLECTION`            | `app/config.py`             | 向量集合名（默认 `llm_wiki_laws`）                                           |
| `MILVUS_METRIC` / `MILVUS_INDEX` | `app/config.py`           | 度量（COSINE）/ 索引（HNSW）                                                 |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | `docker/.env` → `app/config.py` | 图数据库连接（默认 `bolt://localhost:7687`） |
| `JWT_SECRET`                   | `docker/.env` → `app/config.py` | 令牌密钥（**生产必须改**） |
| **服务凭证**（MySQL/Neo4j/Redis/MinIO） | `docker/.env`        | **账号密码集中在这里**：compose 与应用读同一份，改一处即可；文件不入库，模板见 `docker/.env.example` |
| `NEO4J_DATABASE`               | `app/config.py`             | Neo4j 数据库名（默认 `neo4j`）                                              |
| `INTENT_ENABLED`               | `app/config.py`             | 是否启用意图识别（默认开）                                                       |
| `INTENT_MODEL`                 | `app/config.py`             | 意图分类用的轻量模型（默认 `qwen-turbo`）                                          |
| `INTENT_TIMEOUT_S`             | `app/config.py`             | 分类超时秒数（默认 3），超时即保守放行                                                |
| `RETRIEVAL_MODE`               | `app/config.py`             | 多跳链路：`reflect`（默认，反思式）/ `bfs`（原固定扩展，用于对比回退）                        |
| `REFLECT_MODEL`                | `app/config.py`             | 反思用轻量模型（默认 `qwen-turbo`）                                             |
| `REFLECT_MAX_ROUNDS`           | `app/config.py`             | 反思轮数上限（默认 3），防失控                                                   |
| `REFLECT_TIMEOUT_S`            | `app/config.py`             | 反思超时秒数（默认 15），超时即停止继续检索                                             |
| `EMBED_DIM`                    | `app/config.py`             | 向量维度（默认 1024，对应 bge-m3）                                             |
| `TOP_K` / `EXPAND_K`           | `app/config.py`             | 检索返回知识点数（默认 10）/ 关联扩展数（默认 5）                                        |
| `EXPAND_HOPS` / `EXPAND_DECAY` | `app/config.py`             | 图谱多跳扩展的最大跳数（默认 3）/ 分数逐跳衰减系数（默认 0.5）                                 |
| `MYSQL_HOST` / `MYSQL_PORT`    | `app/config.py`             | MySQL 地址（默认 `localhost:3308`，宿主机映射端口）                              |
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | `app/config.py` | MySQL 账号与库（默认 root / 12345678 / `subjects_kg`）                    |
| `MEMORY_COLLECTION`            | `app/config.py`             | 长期记忆向量集合（默认 `llm_wiki_user_memory`）                                   |
| `MEMORY_RECALL_K`              | `app/config.py`             | 每次注入生成的记忆条数（默认 3）                                                   |
| `MEMORY_INJECT_MODE`           | `app/config.py`             | 检索词注入策略：`auto`（默认，仅追问时并入上一问）/ `always` / `off`                        |
| `PROFILE_EXTRACT_EVERY`        | `app/config.py`             | 每 N 轮抽一次画像（默认 5；0=关闭）                                              |
| `JWT_SECRET` / `JWT_EXPIRE_DAYS` | `app/config.py`           | 令牌密钥（**生产必须用环境变量覆盖**）/ 有效期天数（默认 7）                                 |
| `API_HOST` / `API_PORT`        | `app/config.py`             | Web 服务监听地址（默认 `0.0.0.0:8080`）                                        |
| 关联阈值                           | `app/ingest/graph_build.py` | `keyword_threshold=0.3`、`semantic_threshold=0.75`、`crossref` 权重 1.5 |
| 概念映射                           | `app/ingest/concept_map.py` | 法律概念→条文映射与用户查询词（可编辑扩充）                                              |

> ⚠️ 历史文档提到的**根目录 `config.py` 已不存在**（未纳入 git）。配置只有 `app/config.py` 一处，`BGE_MODEL_PATH` 等直接在此维护。

---

## 十、测试

`tests/`（**201 项**）全部使用轻量假数据，**不加载大模型、不连 Milvus、不调 LLM**：

| 测试文件                        | 覆盖范围                                                              |
|-----------------------------|-------------------------------------------------------------------|
| `test_intent.py`            | 意图三分类、容错解析（围栏/噪音）、故障保守放行、兜底话术含覆盖范围与示例                              |
| `test_cli_intent.py`        | CLI 意图路由：非可答路径跳过检索与生成、兜底仍写记忆与日志、混合序列只让可答题进检索                        |
| `test_knowledge_extract.py` | 清洗、编/章/节/条解析、千位条号、编层级与同号章区分、分编边界、噪音过滤、`\f` 行号、康熙部首变体、表格回填、关键词 |
| `test_concept_map.py`       | 概念映射文档感知匹配、关键词注入去重、民法典概念（含千位条号）                                   |
| `test_graph_build.py`       | sibling/keyword/semantic/crossref 四类边、跨编同号章不建边、千位条号 crossref   |
| `test_graph_store.py`       | Neo4j 图存储：Cypher 与参数、全量对齐、四类关系拆分、引用方向派生、节点对归一化（假 driver，离线） |
| `test_milvus_store.py`      | 集合 schema/索引、维度不符重建、全量对齐写入、陈旧主键清理、检索返回（假 client，离线）               |
| `test_kb.py`                | Milvus 可用时启用向量检索、不可用时退化为纯关键词                                     |
| `test_retrieval.py`         | BM25（含关键词字段）、向量检索、RRF 融合与图谱多跳扩展                                   |
| `test_reflector.py`         | 候选编号白名单（防幻觉）、容错解析、无候选不调 LLM、失败即停（mock LLM）                        |
| `test_reflective_retriever.py` | 停止条件（无引用/已充分/要取的已收集）、迭代取回、轮数上限、不污染图谱节点                       |
| `test_mysql_store.py`       | MySQL 记忆后端：**生成的 SQL 与绑定参数**（user_id 隔离、排序 LIMIT、画像去重、只吞唯一冲突）、串题注入策略 |
| `test_long_term.py`         | 长期记忆：**用户隔离（安全关键）**、画像+历史召回、注入段落标注"不得作为法律依据"、后端故障直接暴露 |
| `test_api_auth.py`          | 注册/登录/登出、Cookie HttpOnly+SameSite、**账号枚举防护**、bcrypt 往返与超长密码 |
| `test_api_sessions.py`      | 会话 CRUD、**越权访问一律 404**（读/删/提问都不放行）、删除会话连带清长期记忆 |
| `test_api_chat.py`          | SSE 事件流、意图兜底跳过检索、生成失败仍返回引用、**引用随历史持久化**、串题注入策略 |
| `test_generation.py`        | 流式逐块输出、聚合、prompt 组装、上下文标题带法律名（mock LLM）                           |
| `test_analyze_logs.py`       | 日志画像：解析容错（坏行/缺字段）、停止原因归类（含"未知归 other"）、成本估算、用户过滤 |
| `test_logger.py`            | 日志 JSON 事件字段完整性、缺失耗时兜底                                            |
| `test_eval.py`              | recall@k 指标、评估数据行组装（mock 检索/生成）                                   |
| `test_wiki_writer.py`       | 概念导航链接渲染、跨文档条文消歧、编→章 两级目录分组、章号数值排序                                |

---

## 十一、评估（Ragas）

基于 **Ragas 0.3.0** 评估框架（配合 langchain 最新版、`langchain-community==0.4.0`、DashScope）评估问答质量。

### 评估数据

`eval/qa_test_cases.jsonl`，**55 条**测试用例：劳动法与劳动合同法 35 条（试用期、竞业限制、经济补偿、加班、劳务派遣、裁员、孕期保护、劳动争议等）+ **民法典 20 条**（诉讼时效、离婚冷静期、高空抛物、继承顺序、夫妻财产与债务、自甘风险、收养等）。JSONL
每行一个对象：

| 字段                   | 含义                                                   |
|----------------------|------------------------------------------------------|
| `id` / `type`        | 用例编号 / 类型（`single_hop`·`multi_hop`·`complex`）        |
| `source`             | **来源法律**（`民法典` / `劳动法` / `劳动合同法`；跨法用例为多项），用于按法律分组看检索质量 |
| `question`           | 用户问题                                                 |
| `answer`             | 标准答案（作为 ragas 的 `ground_truth`）                      |
| `expected_knowledge` | 期望命中的知识点 id（用于 recall@k 比对）。**引用关系型用例会同时列出"引用方"与"被引方"**，这样 recall@k 直接量化"引用链有没有被带进上下文" |
| `source_sections`    | 答案来源章节                                               |
| `sub_questions`      | 复杂问题的子问题拆分（complex 类型）                               |

### 指标映射

| 指标               | 来源                                                                        |
|------------------|---------------------------------------------------------------------------|
| `recall@1/3/5/8` | 自定义（`eval/metrics.py`），比对 `expected_knowledge` 与检索 Top-k 命中；@8 是沿用历史基线的汇报口径，非生成器上下文上限 |
| 上下文相关性           | ragas `ContextRelevance`（`nv_context_relevance`）                          |
| 上下文精确率           | ragas `context_precision`                                                 |
| 上下文召回率           | ragas `context_recall`                                                    |
| 答案相关性            | ragas `answer_relevancy`                                                  |
| 忠实度              | ragas `faithfulness`                                                      |

### 运行评估

```bash
uv run python -m eval.evaluate                 # 全量 55 条（生成+ragas，较慢）
uv run python -m eval.evaluate --limit 3       # 快速试跑前 3 条
uv run python -m eval.evaluate --skip-ragas    # 仅计算 recall@k（不调 LLM）
uv run python -m eval.evaluate --skip-ragas --source 民法典   # 只看某部法律的用例
```

- **评估前先确认 Milvus 在跑**：检索走 `KnowledgeBase.load()`，Milvus 挂了会退化为纯关键词，recall 会明显下降。
- 依赖 DashScope：LLM 用 `qwen-max`（`API_KEY` 环境变量），embedding 用 `text-embedding-v3`（
  `langchain_community.embeddings.DashScopeEmbeddings`）。
- 结果写入 `eval/results/evaluation_<时间戳>.json`，含 recall@k 汇总、ragas 各指标均值/最值/按类型分组、以及每用例明细。

**当前基线**（2026-09-13，55 条，`--skip-ragas`）：recall@1=**0.489**、recall@3=**0.729**、recall@5=**0.859**、recall@8=**0.947**。

按来源法律分组（评估会一并打印）：

| 来源 | 条数 | recall@1 | recall@3 | recall@5 | recall@8 |
|---|---|---|---|---|---|
| 民法典 | 20 | **0.592** | 0.758 | 0.892 | **0.983** |
| 劳动合同法 | 26 | 0.414 | 0.692 | 0.846 | 0.949 |
| 劳动法 | 10 | 0.433 | 0.692 | 0.742 | 0.775 |

> 两部劳动法 35 条的基线（0.431 / 0.712 / 0.841 / 0.926）未变，可与历史结果直接对比；
> 民法典 20 条 @8=0.983 说明**多跳用例里的被引条文基本都进了上下文**（用例期望同时含引用方与被引方）。

---

## 十二、FAQ

- **为什么不叫 RAG？** 知识库以结构化 Wiki 页面 + 关联图谱为本体，摄入时一次编译；向量仅参与查询时的召回融合，不是存储与检索的载体。向量放进 Milvus 也只是把它当"向量索引"，不是知识载体。
- **更换/新增法律文档怎么办？** 更新 `data/` 下的 PDF 或 Markdown，**重新运行构建管线**（图谱、Wiki、Milvus 向量都会重建/重写）；如需在"核心概念导航"中出现，在 `concept_map.py` 补映射。
- **知识点粒度如何调整？** 当前按"每一条（第X条）"切分；修改 `knowledge_extract.py` 的编/章/节/条切分正则可改为按"（一）"项或整章切分。
- **为什么正文里的"第三十九条和第四十条"不会被误当成新条文？** 条文头要求"第X条"后紧跟空白，折行交叉引用条后无空白，据此区分。
- **民法典 7 编都有"第一章"，会混淆吗？** 不会：`part`（编）参与图谱分组与 Wiki 目录组织，知识点标题也带编名（如"第二编 物权·第二章·第十三条"）。
- **Milvus 没启动会怎样？** 检索自动退化为纯关键词并打印告警，CLI 仍可用（但语义召回失效，recall 会下降）；重新 `docker compose up -d` 并重跑 pipeline 即可恢复。
