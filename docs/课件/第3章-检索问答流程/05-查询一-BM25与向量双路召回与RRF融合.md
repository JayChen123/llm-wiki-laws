# 第 5 讲：查询（一）—— BM25 + bge-m3 双路召回与 RRF 融合

> 所属课程：《法律问答系统(llm-wiki)》八讲课件 · 第 5/8 讲
> 定位：查询期的第一步"定位"。本讲对应 `app/retrieval/` 下 keyword / vector / hybrid 三个文件。先把用户问题变成一份**排序好的种子知识点列表**。

本讲目标：
1. 看懂查询从 `kb.py` 到 `hybrid_retriever.search()` 的调用链；
2. 理解 BM25 为什么必须吃"含概念关键词的语料"；
3. 理解 bge-m3 向量召回怎么算、向量缓存从哪来、缺失时怎么退化；
4. 能手算 RRF 融合分数，并解释"为什么不用分数相加而用排名倒数"。

---

## 1. 查询调用链

```
cli.py: kb.search(search_query, top_k=5)
  → KnowledgeBase.search()        (kb.py)
      → HybridRetriever.search()  (hybrid_retriever.py)  ★今天的主战场
          ├─ KeywordRetriever.search()   BM25，各取 top_k*3
          ├─ VectorRetriever.search()    bge-m3 余弦，各取 top_k*3
          ├─ RRF 融合 → 前 top_k 个 = 种子
          └─ 图谱 BFS 扩展（第 6 讲）
```

> 注：`cli.py` 在调用前会先做**短期记忆注入**——把最近一条用户问题拼进检索词（如 `join(past + [q])`），解决"那/具体/继续"这类指代。细节在第 7 讲，这里只需知道 query 可能不是孤立的当前句。

`KnowledgeBase.load()` 把摄入期产物加载进来：读 `knowledge_graph.json` 拿节点，读 `wiki/knowledge_vectors.npy` 拿向量缓存，组装三个检索器。

```python
# kb.py（要点）
graph  = load_graph(GRAPH_PATH)
nodes  = graph["nodes"]
vectors = np.load(VECTOR_CACHE_PATH) if VECTOR_CACHE_PATH.exists() else None
kw  = KeywordRetriever(nodes)
vec = VectorRetriever(nodes, vectors, embedder) if vectors is not None else None
hybrid = HybridRetriever(nodes, graph, kw, vec)
```

**无向量缓存时 `vec = None`** → 混合器自动退化为纯关键词（`hybrid_retriever.py` 里对 `vector_retriever is None` 做了判空）。这是系统"没有向量也能跑"的兜底。

---

## 2. 关键词路：jieba + BM25Okapi

### 2.1 语料为什么是"标题 + 正文 + 关键词"

```python
# keyword_retriever.py
class KeywordRetriever:
    def __init__(self, nodes):
        self.nodes = nodes
        corpus = [
            jieba.lcut(f"{n['title']} {n['content']} {' '.join(n.get('keywords') or [])}")
            for n in nodes
        ]
        self.bm25 = BM25Okapi(corpus)      # 建一次索引
```

对照第 4 讲——`keywords` 里已被 concept_map **注入了用户口语词**（"加班费/欠薪/试用期"）。把它们并进 BM25 的索引语料后，**用户口语词可以直接命中法律条文**。这就是关键词路的关键 trick：**召回不吃原文的词，还吃注入进去的"用户会用的词"**。

### 2.2 BM25 打分直觉

BM25 对每个 query 词给一个"该词在这篇文档里多重要"的分数，累加得到文档分。直觉上它做了三件事：

- **逆文档频率（IDF）**：越稀有的词越有判别力——"拖欠工资"比"应当"值钱得多；
- **文档长度归一**：同样命中，短文相对更"满"，分更高。
- **词频饱和机制**：词出现越多越相关（但做饱和处理，不是线性）；

```python
def search(self, query, top_k=5):
    tokens = [t for t in jieba.lcut(query) if t.strip()]
    scores = self.bm25.get_scores(tokens)
    order = scores.argsort()[::-1]
    # 取分数>0 的前 top_k 个 → [(node, score)]
```

中文必须**先分词**（`jieba.lcut`）才能交给 BM25——因为 BM25 按词为单位，中文没有天然空格，不分词"加班费怎么算"会被当成一整块。

---

## 3. 向量路：bge-m3 余弦召回

### 3.1 为什么有"预计算向量"

摄入期（第 3 讲）`Embedder().cache()` 已把 205 个知识点的 `标题\n正文前200字` 编码成向量，存进 `wiki/knowledge_vectors.npy`。查询期**只对 query 现编码一次**，然后和预计算的 205 个向量点积即可。

```python
# vector_retriever.py
class VectorRetriever:
    def search(self, query, top_k=5):
        qv = self.embedder.encode([query])[0]   # 只编码 query
        cos = self.vectors @ qv                  # (205,dim)·(dim,) = 205 个余弦
        order = cos.argsort()[::-1][:top_k]
        return [(self.nodes[int(i)], float(cos[int(i)])) for i in order]
```

`encode()` 输出 **L2 归一化**向量（`normalize_embeddings=True`），归一化后点积 == 余弦相似度，直接 `@` 矩阵乘一次算完，非常快。

```python
# embedder.py（要点）
vecs = self.model.encode(texts, normalize_embeddings=True,
                         convert_to_numpy=True, batch_size=16)
```

### 3.2 向量路的价值

关键词要求**字面命中**；向量路抓**语义近似**——用户说"公司不让我走还扣工资"，BM25 可能凑不齐好词，但向量能召回"未足额支付劳动报酬/违法解除"这类语义相近的条文。**两条路一互补，一宽一严。**

### 3.3 缓存与退化

`Embedder.cache()`：缓存文件存在就直接 `np.load`，否则编码后 `np.save`。2.2GB 的 bge-m3 只加载一次、只批量编码一次。若 `knowledge_vectors.npy` 不存在（未建语义或文件丢失），`kb.load()` 让 `vec=None`，系统退化为纯关键词混合器——**功能不中断，只是少了语义一路**。

---

## 4. 融合：RRF（Reciprocal Rank Fusion）

两路各自给出一份**排名**（分数尺度完全不同：BM25 是几千的量级，余弦是 0~1），不能直接相加。业界常用 **RRF：按排名算分，而不是按原始分数**。

### 4.1 公式与代码

```python
# hybrid_retriever.py
RRF_K = 60

def search(self, query, top_k=5):
    kw = self.keyword_retriever.search(query, top_k=top_k * 3)   # 每路多取点，15
    vec = self.vector_retriever.search(query, top_k=top_k * 3) if self.vector_retriever else []

    rrf = {}
    for rank, (node, _) in enumerate(kw):      # rank 从 0 开始
        rrf[node["id"]] = rrf.get(node["id"], 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, (node, _) in enumerate(vec):
        rrf[node["id"]] = rrf.get(node["id"], 0.0) + 1.0 / (RRF_K + rank + 1)

    ranked_ids = sorted(rrf, key=lambda k: -rrf[k])[:top_k]   # 前 5 个 = 种子
```

- 每个节点在**每一路出现的名次**都折算成一个倒数分 `1/(60+rank+1)`；
- 两路分**累加**；只在一路靠前、或两路都中游，都能拿到不错的融合分；
- `K=60` 是个平滑常数：压住第 1 名与第 20 名的差距，避免排名差异被过度放大。

### 4.2 手算一个例子（加深直觉）

假设两路都召回节点 A、B，名次如下：

| 节点 | 关键词路名次 | 向量路名次 |
|---|---|---|
| A | 1 | 3 |
| B | 2 | —（没召回） |
| C | — | 1 |

- A：`1/(60+0+1) + 1/(60+2+1) = 1/61 + 1/63 ≈ 0.0323`
- B：`1/(60+1+1) = 1/62 ≈ 0.0161`
- C：`1/(60+0+1) = 1/61 ≈ 0.0164`

排序后 A 第一（两路都中游靠前反而赢），C 第二，B 第三。

> 关键 insight：**"两路都排 3~5 名"往往比"单路第 1 名"更可信**——两路独立证据互相印证；RRF 恰恰奖励这种"共识"，抑制单路噪声。

---

## 5. 这一步的产出

融合排序后取前 `TOP_K=5` 个作为**种子知识点**，供第 6 讲的图谱扩展继续用。种子带各自 RRF 分（`base = {nid: rrf[nid]}`），这个分将作为第 6 讲 BFS 扩展的"种子分数"——**本讲的输出直接喂给下讲的输入**。

---

## 6. 本讲小结

1. 查询 = 加载摄入产物 → 关键词路（BM25，语料含注入口语词）+ 向量路（bge-m3，query 现编码）→ RRF 融合 → 前 5 种子。
2. BM25 能命中口语词，靠的是摄入期把词**写进了 keywords 并进了索引语料**，不是查询期魔法。
3. 向量路补"语义近似"，且**缓存缺失时系统退化为纯关键词**，健壮。
4. RRF 按排名折算分、`K=60` 平滑，奖励"两路共识"，抑制单路噪声。

---

## 企业常见面试问答

**Q1：为什么检索要"双路"？关键词和向量各解决什么问题？**

> 单路都有盲区。关键词路（BM25）优点是精确、可解释、命中即稳定，但它要求**字面词命中**——用户说法一变（"加班费" vs 原文"延长工作时间的工资报酬"）就脱靶；系统通过在摄入期把口语词注入知识点 keywords 并纳入 BM25 语料来缓解字面鸿沟。向量路（bge-m3）优点是不看字面看**语义**，能召回"换种说法意思相近"的条文；缺点是不可解释、对精确数字条款（"第几条第几款"）不敏感。两路互补后经 RRF 融合，召回率和稳健性都好于任一单路。这就是"混合检索"的意义。

**Q2：为什么用 RRF 融合而不是把两路的分数直接相加或加权？**

> 因为两路分数的**量纲和分布完全不同**——BM25 可以上千，余弦只在 0~1，直接相加等于让关键词路支配结果；加权又引入一组难调的超参。RRF 干脆不看原始分数、只看**排名**：每路给 `1/(60+rank)` 再累加，K=60 平滑名次差异。这样既消除量纲问题，又奖励"在两路都排名不错"的节点——两路独立证据互相印证，比单路第 1 名更可信。实践中这是融合异质检索器最省心、最稳的方案。

**Q3：向量缓存在哪、丢了会怎样？bge-m3 每次都要重新编码吗？**

> 向量在摄入期批量编码好，存 `wiki/knowledge_vectors.npy`（205 个知识点的向量矩阵）。查询期只对 **query 现编码一次**，与缓存矩阵做一次矩阵乘就得到全部余弦，所以运行时开销很小。如果缓存文件不存在，`KnowledgeBase.load()` 把向量检索器置空，混合检索**退化为纯关键词**——功能不降级到不能用，只是少一路语义召回。首次摄入要加载 2.2GB 的 bge-m3，慢但一次性。

**Q4：中文检索为什么一定要先分词？你们用的是什么分词方案？**

> 英文天然以空格分 token；中文没有词边界，"加班费怎么算"若不分词在 BM25 眼里是一个无法匹配的整串。本项目用 jieba 对 query 和文档语料都做 `jieba.lcut` 切词，再喂 BM25Okapi。同一套 jieba 还在摄入期用 `jieba.analyse.extract_tags` 做 TF-IDF 抽知识点关键词（第 3 讲），一套分词器管两头，也保证"关键词路用的词"和"关键词抽取用的词"出自同一词表口径。

---

下一讲：[第 6 讲 查询（二）：图谱多跳扩展与 qwen-max 生成](06-查询二-图谱多跳扩展与qwen-max生成.md)
上一讲：[第 4 讲 知识构建（二）：概念增强、关联图谱与 Wiki 落盘](./04-知识构建二-概念增强关联图谱与Wiki落盘.md)
