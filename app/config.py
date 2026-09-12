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
MEMORY_DB_PATH = BASE_DIR / "memory.db"  # 仅作一次性迁移脚本的源路径（运行时不再使用）

# 记忆后端：MySQL（唯一后端，无降级）；下述连接参数可用环境变量覆盖
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3308"))       # 宿主机映射端口（容器内 3306）
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "12345678")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "subjects_kg")

# 长期记忆（用户画像 + 历史问答语义索引）
MEMORY_COLLECTION = os.getenv("MEMORY_COLLECTION", "llm_wiki_user_memory")
MEMORY_RECALL_K = int(os.getenv("MEMORY_RECALL_K", "3"))        # 注入生成的记忆条数
MEMORY_SUMMARY_CHARS = int(os.getenv("MEMORY_SUMMARY_CHARS", "400"))  # 单条记忆保留字数
PROFILE_EXTRACT_EVERY = int(os.getenv("PROFILE_EXTRACT_EVERY", "5"))  # 每 N 轮抽一次画像
PROFILE_MODEL = os.getenv("PROFILE_MODEL", "qwen-turbo")

# 短期记忆注入检索词的策略：auto=仅像追问时才并入上一问（默认）；always=总是并入；off=从不
MEMORY_INJECT_MODE = os.getenv("MEMORY_INJECT_MODE", "auto")

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

# 意图识别（问答前的路由：可答 / 法律未覆盖 / 无关）
INTENT_ENABLED = os.getenv("INTENT_ENABLED", "1") not in ("0", "false", "False")
INTENT_MODEL = os.getenv("INTENT_MODEL", "qwen-turbo")  # 分类用轻量模型，快且便宜
INTENT_TIMEOUT_S = float(os.getenv("INTENT_TIMEOUT_S", "3"))  # 超时即保守放行

# 多跳检索链路：reflect=反思式自主多跳（默认，LLM 决定是否继续沿引用检索）
#               bfs=原固定深度图谱扩展（保留用于 A/B 对比与回退）
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "reflect")
REFLECT_MODEL = os.getenv("REFLECT_MODEL", "qwen-turbo")  # 反思用轻量模型
REFLECT_MAX_ROUNDS = int(os.getenv("REFLECT_MAX_ROUNDS", "3"))  # 反思轮数上限，防失控
REFLECT_TIMEOUT_S = float(os.getenv("REFLECT_TIMEOUT_S", "15"))  # 超时即停止继续检索

# 检索参数
TOP_K = 10  # 混合检索返回的知识点数量
EXPAND_K = 5  # 沿关联边扩展的知识点数量（最终补充条数）
EXPAND_HOPS = 3  # 图谱多跳扩展的最大跳数（BFS）
EXPAND_DECAY = 0.5  # 多跳扩展的分数逐跳衰减系数
