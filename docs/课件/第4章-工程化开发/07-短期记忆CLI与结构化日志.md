# 第 7 讲：短期记忆、CLI 与结构化日志 —— 工程闭环

> 所属课程：《法律问答系统(llm-wiki)》八讲课件 · 第 7/8 讲
> 定位：前面 6 讲是"单轮问答"的骨架；这一讲把记忆、交互、日志、测试拼成**能连续多轮对话、可观测、可回归**的完整工程。对应
`app/memory/memory_store.py`、`app/cli.py`、`app/logger.py`。

本讲目标：

1. 理解"短期记忆"两个极其轻量的注入点：检索侧解指代、生成侧保连贯；
2. 看懂 CLI 主循环的完整数据流与三条命令；
3. 知道 `logs/qa.log` 每条 JSON 事件里有什么、能拿来做哪些分析；
4. 了解 42 项全离线单元测试怎么搭起来的、测什么。

---

## 1. 为什么需要"短期记忆"？

对话是**多轮**的，用户不会每句都自报家门：

```
用户：试用期最长几个月？
AI：  （答…）
用户：那要是没签合同呢？     ← "那"指代上一轮，"没签合同"是话题延续
用户：具体点               ← 完全依赖前文，孤立看没有意义
```

两个工程问题因此产生：

- **检索侧**：第二句"那要是没签合同呢"如果单独拿去检索，"那""呢"全是噪音词，召回会偏。理想做法是把**最近的问题**
  （试用期）并进来做检索词。
- **生成侧**：模型需要**看到前面几轮**才能知道"那"指的是什么，上下文连贯。

本项目用一块 **SQLite 单文件**做轻量记忆，不引 Redis、不引向量记忆，够用且零运维。

---

## 2. `memory_store.py`：极简 SQLite 存储

```python
class MemoryStore:
    def __init__(self, db_path, history_rounds=5):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "session TEXT NOT NULL,"
            "role TEXT NOT NULL," - - 'user' / 'assistant'
                                               "content TEXT NOT NULL,"
                                               "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )

    def add(self, session, role, content): ...  # 追加一条消息

    def recent(self, session, limit=None): ...  # 最近 N 条（旧→新），供生成

    def recent_user_queries(self, session, n=2): ...  # 最近 N 条【用户问句】，供检索

    def clear(self, session): ...  # /clear 清空
```

- 一张 `messages` 表，字段极简（session/role/content/created_at），零外部依赖；
- **`session` 维度隔离**：不同会话互不串台，重开 CLI 输入同一 session 可续聊；
- 查询用 `ORDER BY id DESC LIMIT ?` 再 `reversed` 成"旧→新"，天然按写入顺序取最近轮次。

```python
def recent_user_queries(self, session, n=2):
    rows = self.conn.execute(
        "SELECT content FROM messages WHERE session=? AND role='user' "
        "ORDER BY id DESC LIMIT ?", (session, n)).fetchall()
    return [c for (c,) in reversed(rows)]
```

---

## 3. 两个"轻量注入点"（本讲重点）

### 3.1 检索侧：并入最近一问 → 解指代

`cli.py` 在真正检索前：

```python
past = store.recent_user_queries(session, n=1)  # 最近 1 条用户问题
search_query = " ".join(past + [q])  # ★并进当前问句
results = kb.search(search_query, top_k=TOP_K)
```

于是用户说"**那**要是没签合同呢"，真正去检索的是"试用期最长几个月 那要是没签合同呢"——词面里带上了上文的"试用期"，BM25
能命中试用期条款，语义路也能站在试用期语境上召回。**用"拼字符串"这种最朴素的方式解决指代延续，不引 NER、不调额外 LLM**
，是这个项目"轻量优先"的典型取舍。

### 3.2 生成侧：注入最近历史 → 保连贯

调用生成器时把历史塞进 messages（`SYSTEM_PROMPT` 之后的中间层）：

```python
history = store.recent(session, limit=10)  # 最近 10 条消息
for piece in gen.generate_stream(q, results, history):
    print(piece, end="", flush=True)
```

`AnswerGenerator.generate_stream` 里：

```python
messages = [{"role": "system", "content": SYSTEM_PROMPT}]
messages.extend(history or [])  # 历史作为上下文中间消息
messages.append({"role": "user", "content": user_prompt})
```

模型看到前面几轮（含自己的回答），才知道"那""具体点"指什么——**多轮连贯性来自把对话历史喂给模型**，而不是把历史塞进检索。

> 两者的分工可以一句话记：**记忆让"检索词更懂指代"，让"生成上下文更连贯"——一头一尾各司其职。**

---

## 4. CLI 主循环：整条链的胶水

`app/cli.py::main()`：

```python
kb = KnowledgeBase.load()
try:
    gen = AnswerGenerator()  # 无 API_KEY 会抛异常
except RuntimeError as e:
    gen = None  # ★降级：只展示检索，不生成

store = MemoryStore(MEMORY_DB_PATH)
logger = get_logger()
session = input("会话ID（回车默认 default…）") or "default"

while True:
    q = input("你: ")
    if q in ("/exit", "/clear", "/help"): …  # 命令处理
    past = store.recent_user_queries(session, n=1)
    results = kb.search(" ".join(past + [q]), top_k=TOP_K)  # ① 检索
    if gen:
        history = store.recent(session, limit=10)
        for piece in gen.generate_stream(q, results, history):  # ② 流式生成
            print(piece, end="", flush=True)
    else:
        _print_results(results)  # ③ 无 key：打印命中的知识点
    log_qa_event(..., logger=logger)  # ④ 写日志
    store.add(session, "user", q)  # ⑤ 写记忆（user + assistant）
    store.add(session, "assistant", answer)
```

**降级设计**：没配 `API_KEY` 时 `AnswerGenerator()` 抛 `RuntimeError`，捕获后 `gen=None`，系统仍然检索并**打印命中的知识点
**——让用户/老师在不烧 token 的情况下也能验证检索质量。这是这个项目一条很实用的设计：**生成可降级，检索永远在**。

`/clear` = `store.clear(session)`，一键清空当前会话记忆；`/exit` 退出并 `store.close()`。

---

## 5. 结构化日志：`logs/qa.log`

每次问答写一条 **JSON 事件**（`RotatingFileHandler`，单文件 5MB、滚动留 5 份），字段：

| 字段                               | 含义                                                  |
|----------------------------------|-----------------------------------------------------|
| `time`                           | 事件时间                                                |
| `user_id`                        | 会话 ID                                               |
| `query`                          | 用户问题                                                |
| `knowledge_blocks`               | 命中的知识块 `[{id,title,score,source}]`（**不含正文**，避免日志臃肿） |
| `answer`                         | 最终回答（含引用）                                           |
| `retrieval_ms` / `generation_ms` | 检索/生成耗时（毫秒；生成失败时为 null）                             |

```python
def log_qa_event(user_id, query, knowledge_blocks, answer,
                 retrieval_ms=None, generation_ms=None, logger=None):
    event = {"time": datetime.now().isoformat(timespec="seconds"),
             "user_id": user_id, "query": query,
             "knowledge_blocks": knowledge_blocks, "answer": answer,
             "retrieval_ms": round(retrieval_ms, 1) if retrieval_ms is not None else None,
             "generation_ms": round(generation_ms, 1) if generation_ms is not None else None}
    logger.info(json.dumps(event, ensure_ascii=False))  # 每行一条 JSON
```

**JSON 流可直接检索分析**：

```bash
grep -o '"query": "[^"]*"' logs/qa.log            # 所有用户问题
grep '"user_id": "user001"' logs/qa.log            # 某用户的问答
grep -c '"generation_ms": null' logs/qa.log        # 生成失败的次数
jq '.[].retrieval_ms' …                             # 耗时分布（配合 jq）
```

`knowledge_blocks` 让"每次回答到底引用了哪些知识点、分数多少、来自哪个文件"完全可回放——**回答可追溯在这里闭环**（结合第 3 讲
source 带行号）。

---

## 6. 测试策略：42 项全离线

工程能放心改的前提是测试。策略一句话：**绝不加载 2.2GB bge-m3、绝不调 DashScope**，用假编码器 + mock LLM。

- 向量编码用**测试专用假编码器**（按文本哈希到固定随机向量），保证语义边/向量检索逻辑可测且秒级；
- 生成测 `SYSTEM_PROMPT` 组装、多跳引导、**上下文标题带法律名**等，用 mock 的 LLM 客户端；
- 覆盖十个文件（大致分组）：

| 测试文件                        | 覆盖                                          |
|-----------------------------|---------------------------------------------|
| `test_knowledge_extract.py` | 清洗、章/节/条解析、康熙部首、交叉引用排除、表格回填、真实 md 提取 98/107 |
| `test_concept_map.py`       | 文档感知匹配、关键词注入去重                              |
| `test_graph_build.py`       | sibling/keyword/semantic/**crossref** 四类边   |
| `test_retrieval.py`         | BM25、向量、RRF 融合、图谱多跳扩展                       |
| `test_memory.py`            | SQLite 读写、会话隔离、轮数限制                         |
| `test_generation.py`        | 流式、聚合、prompt 组装、标题带法律名                      |
| `test_logger.py`            | JSON 事件字段、耗时缺失兜底                            |
| `test_eval.py`              | recall@k 指标、评估数据行组装                         |
| `test_pipeline.py`          | PDF 发现、增量转换、多文档全局编号                         |
| `test_wiki_writer.py`       | 概念导航链接、跨文档条文消歧                              |

跑法：

```bash
uv run python -m pytest tests/ -v
```

> 为什么用 `uv run python -m` 而不是裸 `pytest`？因为 `python -m` 把项目根加进 sys.path，`app` 包才能被 import；裸 `pytest`
> 有时因路径问题找不到 `app` 而失败（详见 CLAUDE.md）。

---

## 7. 一次完整问答的"时间线"（第 5~7 讲合体）

```
你: 那要是没签合同呢？
  ↓ cli 取最近一问并词 → search_query = "试用期最长几个月 那要是没签合同呢"
  ↓ 混合检索（5 种子 + 图谱扩展 ≤3）
  ↓ qwen-max 用最近 10 条历史生成  → 流式打印
  ↓ log_qa_event()  → logs/qa.log 一条 JSON
  ↓ store.add(user) / store.add(assistant) → memory.db
你: /clear          → store.clear(session)
```

**观察一个工程取舍**：本项目一切"记忆/日志/配置"都走**极简基础设施**（SQLite、轮转文件、环境变量），把复杂度留在 ingest 的知识编译与
retrieval 的混合检索里——符合"该重的地方重、该轻的地方轻"。

---

## 8. 本讲小结

1. 短期记忆 = SQLite 一张表 + **两个注入点**：检索并最近一问解指代、生成注历史保连贯。
2. CLI 是胶水：记忆→检索→生成→日志→写回，且**无 API_KEY 自动降级为只展示检索**。
3. 日志是 JSON 事件流：一次问答一条，含命中的知识块与耗时，可 grep/jq 复盘。
4. 42 项测试全离线（假编码器 + mock），`uv run python -m pytest tests/ -v` 秒级回归。

---

## 企业常见面试问答

**Q1：你这个"短期记忆"是怎么实现的？为什么不用 Redis？**

> 实现极简：SQLite 单文件一张 `messages(session, role, content, created_at)` 表，按会话隔离。短期记忆要的就是"最近 N 轮"
> ，SQLite 读最近若干条完全够用，零部署零运维，正好契合项目"该轻则轻"的取向。Redis 适合高频并发、需要 TTL 的在线服务，本项目是单机
> CLI，SQLite 更合适——选型取决于量级与部署形态，不是越"重"越好。

**Q2：用户说"那没签合同呢"，你靠什么知道"那"指试用期？**

> 靠"检索词拼接"这个非常轻的手段，而不是 NER/大模型消解。CLI 在检索前取最近一条用户问题拼进当前问题（`"试用期最长几个月 那要是没签合同呢"`
> ），让"试用期"这个词面进入检索词，BM25 与向量就能在试用期语境上召回；生成侧再注入最近 10
> 条历史，让模型看到前文自行理解指代。两头一夹，"那/具体/继续"这类指代在多轮里基本能兜住，而成本几乎为零。这是对"轻量解决"
> 的一次刻意示范。

**Q3：一次问答前后端各记录了哪些东西？怎么复盘一个回答得对不对？**

> 每次问答写一条 JSON 到 `logs/qa.log`，含时间、会话 ID、query、命中知识块（id/title/score/source，不含正文避免日志过大）、最终回答、检索与生成耗时。复盘时用
`grep '"query": "…"'` 找到那次会话，就能回放"当时召回了哪几个知识点、各多少分、来自哪个文件哪一行、模型怎么答的、耗时多少"
> 。若怀疑答错，先看 knowledge_blocks 里有没有该有的法条——**八成问题出在召回而非生成**。

---

下一讲：[第 8 讲 评估与指标](../第5章-测试与评估/08-评估与指标.md)
上一讲：[第 6 讲 查询（二）：图谱多跳扩展与 qwen-max 生成](../第3章-检索问答流程/06-查询二-图谱多跳扩展与qwen-max生成.md)
