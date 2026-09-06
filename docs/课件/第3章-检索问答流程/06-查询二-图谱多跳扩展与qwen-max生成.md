# 第 6 讲：查询（二）—— 图谱多跳扩展与 qwen-max 综合生成

> 所属课程：《法律问答系统(llm-wiki)》八讲课件 · 第 6/8 讲
> 定位：把第 5 讲的 5 个种子，扩展成最多 8 个知识块，再交给大模型**综合生成**。本讲对应 `app/retrieval/hybrid_retriever.py`
> 的 `_bfs_expand` 与 `app/generation/answer.py`。

本讲目标：

1. 说清"为什么只召回种子不够"，理解多跳推理的必要性；
2. 看懂 BFS 多跳扩展的分数衰减公式、剪枝与参数（hops/decay/expand_k）；
3. 走一遍真实多跳案例，理解 crossref 边如何把"被引用的条文"带进上下文；
4. 理解生成侧三段关键工程：领域 SYSTEM_PROMPT、上下文标题带法律名、流式输出。

---

## 1. 为什么只召回种子不够？

第 5 讲检索出 5 个种子。但很多问题**不是单条法条能答的**。例子（本项目评估用例的典型类型）：

> **"劳动者在试用期被解除劳动合同，能不能主张经济补偿？"**

它横跨多个知识点：

- 第十九条（试用期）→ 被引用，讲试用期不得约定情形；
- 第三十九条（用人单位单方解除，无补偿）→ **被第十九条、第二十一条引用**；
- 第四十六条（应当支付经济补偿的情形）→ 决定"有没有补偿"的**关键条文**。

如果检索只回 5 个与"试用期"字面相关的种子，**第四十六条很可能不在里面**——但答案恰恰取决于它。单跳召回 → 上下文缺关键条文 →
LLM 要么臆造、要么答不准。

解决之道就是第 1 讲埋的伏笔：**图谱 + 多跳扩展**。既然摄入期已经把"谁引用谁"建成 crossref 边，查询期就可以**从种子出发沿着边多走几步
**，把相关的（尤其被引用的）条文捞回来。

---

## 2. 图谱多跳扩展：`_bfs_expand`

### 2.1 算法骨架（BFS 分层 + 每跳剪枝）

```python
def _bfs_expand(self, seeds, seed_scores, hops, per_hop_k, decay):
    adj = self._adjacency_map()  # 邻接表：id -> [(邻居id, 权重)]，按权重降序
    visited = set(seeds)
    frontier = [(nid, seed_scores[nid]) for nid in seeds]
    expanded = {}
    for hop in range(1, hops + 1):  # 第 1~3 跳
        candidates = {}
        for nid, score in frontier:
            for nb, w in adj[nid]:
                if nb in visited:
                    continue
                s = score * w * (decay ** hop)  # ★分数衰减公式
                if nb not in candidates or s > candidates[nb]:
                    candidates[nb] = s
        top = sorted(candidates.items(), key=lambda kv: -kv[1])[:per_hop_k]  # ★每跳剪枝
        if not top:
            break
        frontier = []
        for nb, s in top:
            expanded[nb] = s
            visited.add(nb)
            frontier.append((nb, s))  # 下一跳从这批新节点出发
    return expanded
```

三个关键点：

**① 分数逐跳衰减**

```
扩展节点分 = 上层节点分 × 边权重 × decay^hop      （decay=0.5，hop=1/2/3）
```

每多走一跳分数砍一半——**越远越弱**，保证扩展"近处为主、远处为辅"。

**② 每跳剪枝** `[:per_hop_k]`

每跳只保留分数最高的前 `per_hop_k`（= EXPAND_K = 3）个作为下一跳前沿。若不剪枝，3 跳内节点数指数爆炸；剪枝让 BFS **收敛到局部高价值区域
**。

**③ 防环**：`visited` 集合保证不回访已到过的节点。

### 2.2 参数从哪来（app/config.py）

```python
EXPAND_K = 3  # 最终补充进结果的知识点数
EXPAND_HOPS = 3  # 最大跳数
EXPAND_DECAY = 0.5  # 衰减系数
```

### 2.3 并入最终结果

```python
# 第 5 讲得到 ranked_ids（5 个种子）+ base（各 RRF 分）
expanded_scores = self._bfs_expand(ranked_ids, base,
                                   self.expand_hops, self.expand_k, self.expand_decay)
extra_ids = sorted(expanded_scores, key=lambda k: -expanded_scores[k])[:self.expand_k]
ordered = ranked_ids + extra_ids  # ★ 5 种子 + 至多 3 扩展 = 至多 8 块
```

生成器拿到的**上下文 ≤ 8 块**（5 + 3），这也是第 8 讲 `recall@8` 为什么反映"生成器真实上下文"。

---

## 3. 多跳案例推演（真实图谱数据）

以"试用期被解除能否主张经济补偿"为例，设种子命中了试用期相关的 **第二十一条** 与 **第三十九条**：

```
种子：KP_021（第二十一条·试用期内解除须说明理由）
      KP_039（第三十九条·劳动者过失，单位可单方解除——无需补偿）

第 1 跳（decay^1=0.5，边权重取 crossref=1.5 / sibling=1.0）：
   从 KP_039 出发，图谱里 第三十九条 有 crossref 边连到 第四十六条……
   候选分 ≈ seed分 × 1.5 × 0.5        → 第四十六条进入前 3
   同时 sibling 连到 同章 第四十条、第四十一条 …

第 2 跳：从第四十六条继续，可能再带到 第四十七条（经济补偿计算标准）…
```

于是最终上下文 = 5 种子 + 扩展出的**第四十六条（该不该补偿）+ 第四十七条（补偿怎么算）**。qwen-max 拿到后能组合出："
试用期被解除，要看属不属于第三十九条过失解除——若属过失则无补偿；否则按第四十六条/第四十七条支付经济补偿"——**这才是"
综合生成 + 多跳推理"的完成形态**。

> 图谱实测的 crossref 链（第 4
> 讲）：第十四条→第三十九/四十条、第二十一条→第三十九/四十条、第二十三条→第二十五条、第二十六条→第三十八/三十九条……扩展时它们稳定地把"
> 被引条文"带进来。这也是为什么 recall@8 能到 0.92（第 8 讲）。

---

## 4. 生成：把知识块变成"带引用的人话"

`app/generation/answer.py::AnswerGenerator` 用 OpenAI 客户端指向 DashScope 兼容接口，模型 **qwen-max**。

### 4.1 领域 SYSTEM_PROMPT（控制生成纪律）

```python
SYSTEM_PROMPT = (
    "你是中国劳动法领域问答助手，依据《中华人民共和国劳动法》《中华人民共和国劳动合同法》…"
    "请仅依据【参考资料】中给出的知识点回答，使用中文，条理清晰；涉及具体规定时点明法律依据"
    "（如\"依据《劳动合同法》第五十八条\"）。若问题需要综合多个知识点才能回答（多跳推理），"
    "请跨知识点组合、逻辑推理并说明依据，不要编造；单知识点可回答的按单点作答。"
    "若参考资料不足以回答，请如实说明不确定，不要臆造法条。"
    "回答末尾另起一行列出引用的全部知识点标题（格式：引用：【标题1】【标题2】）。"
)
```

拆解它约束了四件事：**① 只依据参考资料**（防模型用自己记忆里的旧法条）；**② 涉及具体规定要点明法律依据**（"
依据《劳动合同法》第四十六条"）；**③ 多跳就组合推理**（引导 LLM 用好扩展来的多个知识点）；**④ 不够就承认不确定 + 末尾列引用**
（防臆造、给溯源）。

### 4.2 上下文标题带法律名（防张冠李戴）

两部法律**同条号极多**（都有 98 条、85 条）。若上下文只写"第四章·第八十五条"，LLM 无法分辨是劳动法还是劳动合同法那一条——**会答串
**。所以格式化上下文时，从 `source` 的文件名前缀把法律名拼进标题：

```python
@staticmethod
def _format_contexts(contexts):
    parts = []
    for i, ctx in enumerate(contexts, 1):
        law = ""
        src = ctx.get("source", "")
        if ".md" in src:
            law = f"{src.split('.md')[0].strip()}·"  # "中华人民共和国劳动合同法·"
        title = f"{law}{ctx['title']}"  # ★带法律名的标题
        body = ctx["content"][:600]  # 每条正文截断 600 字
        parts.append(f"{i}. [{title}]\n{body}")
    return "\n\n".join(parts)
```

真实效果：

```text
1. [中华人民共和国劳动合同法·第四章·第八十五条]
   （正文…）
2. [中华人民共和国劳动法·第十二章·第九十八条]
   （正文…）
```

### 4.3 拼 user_prompt 与流式生成

```python
def generate_stream(self, query, contexts, history=None):
    reference = self._format_contexts(contexts)
    user_prompt = f"【参考资料】\n{reference}\n\n【问题】\n{query}\n\n请回答："
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])  # 短期记忆历史（第 7 讲）
    messages.append({"role": "user", "content": user_prompt})
    stream = self.client.chat.completions.create(
        model=self.model, messages=messages, temperature=0.3, stream=True)
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content  # 逐块吐字（打字机效果）
```

- `temperature=0.3`：法律问答要**稳定可复现**，温度调低，少些天马行空；
- `generate_stream` **逐块 yield** → CLI 逐字渲染；`generate()` 用 `"".join(...)` 聚合成完整字符串（评估与日志需要整段文本）。

---

## 5. 一个"端到端"的查询数据流（把 5、6 讲串起来）

```
用户：试用期被解除能不能要经济补偿？
  ↓ cli 并入最近一问（短期记忆）
  ↓ HybridRetriever.search()
  ↓  BM25（含口语词语料）→ 召回 试用期/解除相关 条文
  ↓  bge-m3          → 语义召回 经济补偿/解除 条文
  ↓  RRF 融合 → 5 个种子
  ↓  图谱 BFS ≤3 跳 → 带出 被引用的 46/47 条（crossref 权重 1.5）
  ↓  ≤8 个知识块，标题带法律名
qwen-max 依据这些条文 组合推理 生成：
  "…依据《劳动合同法》第三十九条（过失解除无补偿）…若不符合则依第四十六条
   应支付经济补偿，按第四十七条标准计算。引用：【…第四十六条】【…第四十七条】"
  ↓ CLI 流式打印 → 写 memory.db + logs/qa.log
```

---

## 6. 本讲小结

1. 单跳召回不够答跨条问题 → 用**图谱多跳扩展**补知识，BFS ≤3 跳 + 分数衰减（×边权×0.5^hop）+ 每跳剪枝。
2. **crossref 权重 1.5** 保证扩展优先带"被引用的关键条文"（46/38/39 等）。
3. 生成侧三板斧：只依据参考资料的领域 prompt、**标题带法律名**防跨法同条号张冠李戴、流式输出。
4. 5 种子 + 3 扩展 = ≤8 块上下文 → 直接决定了第 8 讲的 recall@8。

---

## 企业常见面试问答

**Q1：你们怎么做"多跳推理"？检索只回了 5 个知识点够吗？**

> 不够，所以做了两步。第一步检索侧：拿到 5 个种子后，从种子出发沿关联图谱做 BFS 多跳扩展，最多 3 跳，每跳分数按
`上层分×边权重×0.5^hop` 衰减、只保留前 3 个候选防爆炸，最终把至多 3 个扩展知识点补进上下文（共 ≤8 块）。尤其靠 **crossref
边（权重 1.5）** 把正文里被引用的关键条文（如 39 条引出的 46/47 条）稳定带进来。第二步生成侧：SYSTEM_PROMPT 里显式要求"
> 跨知识点组合、逻辑推理并说明依据"。检索补料 + prompt 引导，两者合起来才算完整的多跳。

**Q2：BFS 扩展会不会把一堆无关条文带进来、污染上下文？怎么控制？**

> 会，所以有三道闸：① **分数逐跳衰减**（×0.5^hop），离种子越远越弱，扩展以近处为主；② **每跳剪枝**（只保留 top3
> 继续往深走），避免指数级遍历也避免无关长链；③ **边类型质量**——crossref 是高价值显式引用、semantic 阈值调到 0.75
> 防止近全连接，摄入期边本身够"干净"扩展才可信。最后生成时 prompt 限定"仅依据给定知识点作答"，即便个别扩展块不相关，模型也不会拿它瞎编。

**Q3：两部法律都有"第九十八条"，怎么保证不答串？**

> 三个层面防。最底层在摄入：知识点用 `KP_xxx` 全局唯一编号，条号不做主键；crossref 建边按来源文件分组，**只在同法内连**
> ，跨法不连。最关键在生成：`_format_contexts` 从知识点的 `source`（文件名）把法律名拼进上下文标题，例如
`[中华人民共和国劳动合同法·第四章·第八十五条]`，LLM 一眼能区分。不做这个处理，模型很可能把劳动法 98 条的内容安到劳动合同法上——这是实测踩过的坑。

**Q4：生成时怎么防止模型"编法条"？如果检索到的资料确实不够呢？**

> 系统提示词立规矩：只依据【参考资料】作答、涉及具体规定必须点明法律依据、给不出就如实说不确定、末尾列出全部引用；温度也压到
> 0.3 减少发散。检索侧尽力把料备足（多跳扩展把被引条文带进来）。若参考资料确实不足，按 prompt 要求模型应"如实说明不确定"
> ，而不是编造——这是 prompt 设计的兜底契约。真正评估"够不够"是第 8 讲 recall@8 + ragas faithfulness 的活，用指标反过来暴露哪些类问题料备不齐。

---

下一讲：[第 7 讲 短期记忆、CLI 与结构化日志](../第4章-工程化开发/07-短期记忆CLI与结构化日志.md)
上一讲：[第 5 讲 查询（一）：BM25 + bge-m3 双路召回与 RRF 融合](05-查询一-BM25与向量双路召回与RRF融合.md)
