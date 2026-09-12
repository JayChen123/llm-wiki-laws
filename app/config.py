"""项目应用配置：路径与检索参数。

LLM 与向量模型配置复用项目根目录 config.py（用户维护的单一配置源）。
"""
import sys
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# from config import Config as _UserConfig  # noqa: E402

# 数据与产物路径
DATA_DIR = BASE_DIR / "data"
SRC_DIR = DATA_DIR / "src"  # 原始 PDF 源文件（用户放入）
RAW_DIR = DATA_DIR / "raw"  # markdown 原始文件（PDF 转换产物 + 用户直接放入）
WIKI_DIR = BASE_DIR / "wiki"
CONCEPTS_DIR = WIKI_DIR / "concepts"
GRAPH_PATH = BASE_DIR / "knowledge_graph.json"
MEMORY_DB_PATH = BASE_DIR / "memory.db"

# Milvus 向量库：知识点向量的存储与检索（服务端见 docker/docker-compose.yml）
# 端口是宿主机映射端口（容器内为 19530），换环境改环境变量即可
MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19532")
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "llm_wiki_laws")
MILVUS_METRIC = "COSINE"  # bge-m3 向量已 L2 归一化，COSINE 与点积等价
MILVUS_INDEX = "HNSW"
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))  # bge-m3 输出维度

# 向量编码模型（bge-m3）与大模型（qwen-max）
BGE_MODEL_PATH = "/Users/chan/projects/Itcast_qa_system/rag_qa/models/bge-m3"
LLM_MODEL = "qwen-max"
DASHSCOPE_API_KEY = os.getenv("API_KEY") or "api_key"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 检索参数
TOP_K = 10  # 混合检索返回的知识点数量
EXPAND_K = 5  # 沿关联边扩展的知识点数量（最终补充条数）
EXPAND_HOPS = 3  # 图谱多跳扩展的最大跳数（BFS）
EXPAND_DECAY = 0.5  # 多跳扩展的分数逐跳衰减系数
