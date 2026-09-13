# 2.4 属性图与 Cypher —— 从本体到工程实现

> 定位：本体（2.2/2.3）是"应该怎么建模"，图数据库是"怎么存、怎么查"。这一节用项目里的**真实 Cypher 语句**把两者接起来。

本节目标：

1. 掌握属性图的数据模型（节点 / 关系 / 属性）与它和本体的对应关系；
2. 学会 Cypher 的六个基本动作（建约束、批写、建关系、清关系、删陈旧、读回）；
3. 理解三个**工程细节**：唯一约束为什么必须有、关系类型为什么不能参数化、批写为什么要分批；
4. 看懂有向引用链遍历——**这是选图库的核心理由**。

---

## 1. 属性图的数据模型

```
节点               (n:KnowledgePoint {id: "KP_046", title: "...", seq: 46})
                    │        │                        │
                    │        │                        └── 属性（键值对，随便挂）
                    │        └── 标签（属于哪个类，对应本体里的"类"）
                    └── 变量名（查询里引用它用）

关系               (a)-[:CROSSREF {weight: 1.5}]->(b)
                         │        │              │
                         │        │              └── 方向（有向/无向）
                         │        └── 关系类型（对应本体里的"关系"）
                         └── 关系上也能挂属性
```

与本体（2.3）的对应关系：

| 本体 | 属性图 |
|---|---|
| 类 `KnowledgePoint` | 节点**标签** `:KnowledgePoint` |
| 个体（某一条法条） | 一个**节点** |
| 属性（title/content/…） | 节点上的**键值对** |
| 关系类型（四类） | 关系**类型** `:SIBLING` / `:KEYWORD` / `:SEMANTIC` / `:CROSSREF` |
| 关系的方向与权重 | 关系的**方向** + 关系上的 `weight` 属性 |
| 公理/约束（"id 唯一"） | **唯一约束** `CONSTRAINT` |

> 一句话：**本体告诉你"该建什么"，属性图给出"怎么落"**。本体里的"类/关系/方向/权重/约束"在属性图里全都有直接对应物——这也是属性图工程上好用的原因。

---

## 2. Cypher 基础速览

Cypher 的设计目标是"**查询写起来像在画图**"：节点用圆括号 `()`，关系用方括号加箭头 `-[:TYPE]->`。

| 动作 | 语法 | 读法 |
|---|---|---|
| 匹配节点 | `MATCH (n:Label {k: v})` | 找到标签为 Label、k=v 的节点 |
| 匹配关系 | `MATCH (a)-[:TYPE]->(b)` | a 经 TYPE 指向 b |
| **变长路径** | `MATCH (a)-[:TYPE*1..3]->(b)` | **沿 TYPE 走 1~3 跳**（图库的核心能力） |
| 过滤 | `WHERE n.seq > 100` | 条件 |
| 返回 | `RETURN n.id AS id` | 投影（可带别名） |
| 创建 | `CREATE (a)-[:T {w: 1}]->(b)` | 建节点/关系 |
| 合并 | `MERGE (n:L {id: $id})` | 有则匹配、无则创建（**幂等的关键**） |
| 更新 | `SET n.title = $t` | 改属性 |
| 删除关系 | `MATCH (:L)-[r]->(:L) DELETE r` | 删关系 |
| **删除节点** | `DETACH DELETE n` | 删节点**及其所有关系** |
| **批量** | `UNWIND $rows AS row …` | 把参数数组"展开"成多行，配一次提交写一批 |

**`UNWIND` 是批量写入的关键**：没有它，1465 个节点就要发 1465 次请求。

---

## 3. 项目里的真实 Cypher

下面全部摘自 `app/ingest/graph_store.py`。

### 3.1 建唯一约束

```python
_CONSTRAINT = (
    "CREATE CONSTRAINT kp_id_unique IF NOT EXISTS "
    "FOR (n:KnowledgePoint) REQUIRE n.id IS UNIQUE"
)
```

代码注释里写明了理由（值得照抄）：

> 建唯一约束：既保证 id 唯一，也让 MERGE 走索引（**否则 1465 次 MERGE 会退化成全表扫**）。

这是图库里最常见的一个性能陷阱：`MERGE` 要先"找有没有"，**没有索引时每次都是全表扫描**。规模小时看不出来，上万节点就会明显变慢——而且**不报错、只是慢**。

### 3.2 批量写节点

```python
for batch in _chunks(node_rows, _BATCH):          # _BATCH = 2000
    self._run(
        "UNWIND $rows AS row "
        "MERGE (n:KnowledgePoint {id: row.id}) "
        "SET n.seq = row.seq, n.title = row.title, n.part = row.part, "
        "    n.part_title = row.part_title, n.chapter = row.chapter, "
        "    n.chapter_title = row.chapter_title, n.clause = row.clause, "
        "    n.content = row.content, n.source = row.source, n.keywords = row.keywords",
        rows=batch,
    )
```

三个点：

| 点 | 说明 |
|---|---|
| `MERGE` + `SET` 而不是 `CREATE` | **幂等**：重跑同步不会产生重复节点 |
| `UNWIND $rows` + 参数化 | 一次提交写一批（2000 条），走参数绑定（不是字符串拼 SQL） |
| 一次性 `SET` 全部字段 | 与 JSON 字段一一对应，读回才能 1:1 还原（3.5） |

### 3.3 按类型建关系

```python
for json_type, rel_type in _JSON_TYPE_TO_REL.items():
    rows = [... for e in edges if json_type in (e.get("types") or [])]
    for batch in _chunks(rows, _BATCH):
        self._run(
            "UNWIND $rows AS row "
            "MATCH (a:KnowledgePoint {id: row.source}) "
            "MATCH (b:KnowledgePoint {id: row.target}) "
            f"CREATE (a)-[:{rel_type} {{weight: row.weight}}]->(b)",
            rows=batch,
        )
```

**注意这里用的是 `CREATE` 而不是 `MERGE`**——因为跑之前会先把所有旧关系删掉（3.4），所以不存在重复。这是"全量对齐"的实现方式：**先清空、再重建**。

⚠️ 同时注意 `f"[:{rel_type}]"` 是**字符串插值**，不是参数绑定。原因是 **Cypher 不允许把关系类型当参数传**（类型是语法的一部分）。所以关系类型必须来自可信来源——这里来自内部常量映射 `_JSON_TYPE_TO_REL`，不接受外部输入。**这是一个值得记住的语言限制**：如果哪天要让用户指定关系类型，必须做白名单校验，否则就是注入风险。

### 3.4 清空关系与删除陈旧节点

```python
# 先删掉所有旧关系
self._run("MATCH (:KnowledgePoint)-[r]->(:KnowledgePoint) DELETE r")

# ……重建节点与关系……

# 最后清理陈旧节点（DETACH 会一并删掉它的关系）
self._run("MATCH (n:KnowledgePoint) WHERE NOT n.id IN $ids DETACH DELETE n", ids=ids)
```

顺序不能反：**先清关系 → 重建 → 最后删陈旧节点**。如果先删节点，就会连带删掉刚建好的关系。

`DETACH DELETE` 的 `DETACH` 是必须的：Neo4j 不允许删除"还有关系连着的节点"（防止留下悬空边），`DETACH` 表示"连同它的关系一起删"。

### 3.5 读回与计数

```python
# 读回节点（供 kb.load() 还原图谱）
"MATCH (n:KnowledgePoint) RETURN n.id AS id, n.seq AS seq, n.title AS title, ..."

# 读回关系（按类型返回，供归一化聚合）
"MATCH (a:KnowledgePoint)-[r]->(b:KnowledgePoint) "
"RETURN a.id AS source, b.id AS target, type(r) AS rel_type, r.weight AS weight"

# 计数（供 sync_graph --check-only）
"MATCH (:KnowledgePoint)-[r]->(:KnowledgePoint) RETURN type(r) AS rel_type, count(r) AS n"
```

`type(r)` 是**取关系类型**的函数——因为四类关系拆开后，载入时要按类型聚合回 JSON 的 `types` 数组（3.5 讲的"节点对归一化"）。

---

## 4. 有向引用链遍历：选图库的核心理由

回到最初的问题——为什么值得为它多运维一个服务？因为下面这类查询，用 JSON 或 SQL 都很别扭：

```cypher
// 从某条法条出发，沿引用关系走最多 3 跳，返回所有可达条文
MATCH (a:KnowledgePoint {id: $id})-[:CROSSREF*1..3]->(b)
RETURN b.id, b.title
```

- **变长路径 `*1..3`** 是语法原生支持的，不需要写递归；
- 因为 `:CROSSREF` 在同步时**从正文派生了方向**（3.5），"往下走"才有意义；
- 如果哪天要做"反向查：谁引用了我"，把箭头反过来即可（`<-[:CROSSREF*1..3]-`）。

对照一下其他方案：

| 方案 | 写这个查询要做什么 |
|---|---|
| **Neo4j / Cypher** | 一行 `*1..3` |
| JSON + Python | 把全图读进内存、自建邻接表、手写 BFS、自己去重与剪枝 |
| 关系库 | 三张自连接或递归 CTE + 每一跳手动去重（还不含路径权重） |

> **注意**：本项目当前的多跳扩展（4.3）实际是用 **Python 侧的 BFS**（在 `kb.load()` 读回的邻接表上跑），而不是在 Cypher 里跑 `*1..N`。原因是要**自定义打分**（分数 = 种子分 × 边权 × decay^hop）与**每跳剪枝**，这些用 Cypher 表达反而更绕。
>
> 那图库的价值在哪？在于 **`kb.load()` 之外的能力**：按类型遍历、有向引用链、以及直接在图库上做交互式排查（Neo4j Browser 里点着看）。**这是一个诚实的说明——图库给的是"查询与排查能力"，不是"替我们算分"。**

---

## 5. 三个工程细节小结

| 细节 | 不这么做会怎样 |
|---|---|
| **唯一约束** | `MERGE` 退化成全表扫——**不报错，只是慢**，规模大了才暴露 |
| **关系类型用插值 + 内部白名单** | Cypher 不支持参数化类型；若接外部输入而不校验 → 注入风险 |
| **批量 `UNWIND` + 分批（2000）** | 一次一条发请求（1465 次往返）；或一次全塞（单事务过大） |

---

## 6. 本节小结

- 属性图三要素：**节点**（标签 + 属性）、**关系**（类型 + 方向 + 属性）、**约束**；它们与本体里的类/关系/公理**一一对应**。
- Cypher 六个动作：建约束、`UNWIND` 批写节点（`MERGE`+`SET`）、按类型建关系（`CREATE`）、清关系、`DETACH DELETE` 陈旧节点、`MATCH … RETURN` 读回。
- 三个工程细节：**唯一约束必须有**（否则 `MERGE` 全表扫，静默变慢）；**关系类型不能参数化**（必须插值 + 白名单）；**批量要 UNWIND + 分批**。
- 同步顺序不可反：**先清关系 → 重建节点与关系 → 最后删除陈旧节点**（`DETACH` 会连带删关系）。
- **变长路径 `*1..N`** 是图库的核心价值；但本项目实际打分用的是 Python 侧 BFS（要自定义衰减与剪枝），图库的收益在**按类型遍历与交互式排查**——这一点要诚实说明。

## 企业常见面试问答

**Q1：为什么要在图库里建唯一约束？**

> 两个作用，第二个更关键：① 保证 `id` 唯一（数据正确性）；② **让 `MERGE` 走索引**——`MERGE` 的语义是"有则匹配、无则创建"，它必须先查找；**没有索引时每次查找都是全表扫描**。1465 个节点看起来无所谓，但这个坑的性质是"**不报错、只是慢**"，等到几万节点才发现，就要回头补约束并重新评估性能。所以约束应该在**建库时就建**，而不是等出问题。这也是一类值得警惕的模式的代表：**性能问题不像 bug 那样给你报错，它只是安静地变慢**，所以必须靠约定（建库即建约束、批量要分批）而不是靠"发现问题再修"。

**Q2：Cypher 里为什么不能把关系类型当参数？**

> 因为**关系类型是语法的一部分，不是值**。Cypher 的解析器需要知道 `:CROSSREF` 是类型才能构建查询计划，它不像 `WHERE n.title = $title` 那样可以在运行时替换。所以项目里用了字符串插值 `f"CREATE (a)-[:{rel_type} {{weight: row.weight}}]->(b)"`。这带来一个**安全约束**：插值的内容必须来自可信来源。这里 `rel_type` 来自内部常量映射 `_JSON_TYPE_TO_REL`（四个固定值），不接受外部输入，所以是安全的。**如果将来要支持"用户自定义关系类型"，必须先做白名单校验**，否则就是 Cypher 注入。这类"语言限制导致必须拼字符串"的情况，在 SQL 的标识符（表名/列名）上也存在——处理原则是一样的：**只能拼白名单里的值。**

**Q3：为什么用 `MERGE` 建节点，却用 `CREATE` 建关系？**

> 因为两者的幂等需求不同。**节点用 `MERGE`**（有则匹配、无则创建）是为了让同步可以重复执行而不产生重复节点。**关系用 `CREATE`** 是因为在它之前已经执行了一句 `MATCH (:KnowledgePoint)-[r]->(:KnowledgePoint) DELETE r` ——**所有旧关系都被清空了**，所以此刻 `CREATE` 不会撞上已存在的关系。这背后是"全量对齐"的完整顺序：**清关系 → `MERGE` 节点（幂等）→ `CREATE` 关系 → `DETACH DELETE` 陈旧节点**。如果顺序写反（比如先删陈旧节点再建关系），或者漏掉清关系那一步，就会出现重复关系或悬空边。**这类"顺序正确性"问题不报错，只会让图慢慢变脏**——所以我们在测试里也用假 driver 把顺序卡住了（5.1）。

**Q4：既然多跳是 Python 里算的，那图库到底帮了什么？**

> 这是个好问题，需要诚实回答：**当前的打分与剪枝确实是 Python 侧跑的**，因为我们要"分数 = 种子分 × 边权 × decay^hop"加每跳剪枝，这些用 Cypher 表达反而更绕。图库带给我们的价值有三块：① **按类型遍历**——四类关系拆开存储后，可以直接问"只走引用链""只看结构边"，这在 JSON 里要靠自己筛；② **有向引用链**——同步时从正文派生了 `:CROSSREF` 的方向，所以 `-[:CROSSREF*1..3]->` 这种查询是现成的；③ **交互式排查**——Neo4j Browser 里能点着看某个节点连了谁、权重多少，这在排查"某条法条为什么没进上下文"时比翻 JSON 直观得多（和 `wiki/` 人读视图是同一个用途）。所以准确的说法是：**图库提供的是"查询与排查能力"，不是"替我们算分"。** 如果只说"用图库做多跳检索"，那是把实现说错了。
