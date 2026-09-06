# 《法律问答系统(llm-wiki)》课程讲义

## 主线

```
理念(RAG vs llm-wiki) → 系统总览 → 摄入(编译知识)
→ 查询(定位 + 综合) → 短期记忆/CLI/日志 → 评估与指标
```

核心主张贯穿全程：**本系统不是 RAG**——把结构规整的法律文档在摄入期"编译"为可读可审计的知识点 + 关联图谱（`wiki/ + knowledge_graph.json`），查询期只做"定位知识点 + 让大模型依据给定知识点综合生成"；向量仅参与召回打分，不做知识载体。

## 八讲导航

| # | 讲                                                                | 对应代码 | 一句话要点 |
|---|------------------------------------------------------------------|---|---|
| 1 | [RAG 的局限与 llm-wiki 理念](../第1章-项目概述/01-RAG的局限与llm-wiki理念.md)       | —（概念课） | 为什么不做 RAG：编译 vs 临时检索 |
| 2 | [系统总览与工程环境](../第1章-项目概述/02-系统总览与工程环境.md)                          | `app/config.py`、`kb.py`、目录树 | 两阶段架构、选型、双份配置 |
| 3 | [知识构建（一）：PDF → 205 个知识点](../第2章-知识库构建/03-知识构建一-PDF到205个知识点.md)    | `ingest/knowledge_extract.py`、`pipeline.py` | 清洗、康熙部首、章-节-条状态机 |
| 4 | [知识构建（二）：概念增强、图谱与 Wiki](../第2章-知识库构建/04-知识构建二-概念增强关联图谱与Wiki落盘.md) | `ingest/concept_map.py`、`graph_build.py`、`wiki_writer.py` | 口语→法条、四类边、可读落盘 |
| 5 | [查询（一）：双路召回与 RRF 融合](../第3章-检索问答流程/05-查询一-BM25与向量双路召回与RRF融合.md)   | `retrieval/keyword_retriever.py`、`vector_retriever.py`、`hybrid_retriever.py` | BM25 + bge-m3 → 5 种子 |
| 6 | [查询（二）：图谱多跳扩展与生成](../第3章-检索问答流程/06-查询二-图谱多跳扩展与qwen-max生成.md)                | `hybrid_retriever.py::_bfs_expand`、`generation/answer.py` | BFS ≤3 跳、crossref、法律名标题 |
| 7 | [短期记忆、CLI 与结构化日志](../第4章-工程化开发/07-短期记忆CLI与结构化日志.md)                 | `memory/memory_store.py`、`cli.py`、`logger.py` | 两个轻量注入点、闭环、42 项测试 |
| 8 | [评估与指标](../第5章-测试与评估/08-评估与指标.md)                                   | `eval/metrics.py`、`evaluate.py` | recall@k、ragas、基线解读与改进 |
