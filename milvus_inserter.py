import json
import os
from pymilvus import (
    connections,
    utility,
    FieldSchema,
    CollectionSchema,
    DataType,
    Collection,
)
from sentence_transformers import SentenceTransformer

# ==========================================
# 架构配置区 (Configuration)
# ==========================================
MILVUS_HOST = "127.0.0.1"
MILVUS_PORT = "19530"
COLLECTION_NAME = "enterprise_quotation_rag"
DIMENSION = 768  # BCEmbedding 默认维度

# 强烈建议：首次运行会自动从 HuggingFace/ModelScope 下载模型
# 如果服务器无法连外网，请提前下载到本地并替换为本地绝对路径
EMBEDDING_MODEL_NAME = "maidalun1020/bce-embedding-base_v1"


def create_milvus_collection():
    """
    建立高可用向量表结构：采用 JSON 动态字段解耦刚性元数据
    """
    print(f"[System] 正在连接 Milvus 数据库 {MILVUS_HOST}:{MILVUS_PORT}...")
    connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)

    if utility.has_collection(COLLECTION_NAME):
        print(f"[Warn] 发现同名 Collection: {COLLECTION_NAME}，执行物理清理...")
        utility.drop_collection(COLLECTION_NAME)

    # 1. 定义 Schema (表结构)
    # 核心设计：不再将每个元数据写死为列，而是使用 DataType.JSON 挂载动态元数据，极大增加系统扩展性
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True, description="主键自增ID"),
        FieldSchema(name="page_content", dtype=DataType.VARCHAR, max_length=65535, description="纯语义文本"),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=DIMENSION, description="文本语义特征向量"),
        FieldSchema(name="metadata", dtype=DataType.JSON, description="解耦的结构化价格与分类数据")
    ]

    schema = CollectionSchema(fields=fields, description="企业级报价库_混合检索架构")
    collection = Collection(name=COLLECTION_NAME, schema=schema)
    print(f"[System] Collection '{COLLECTION_NAME}' 创建成功，JSON 元数据隔离通道已就绪。")
    return collection


def build_index(collection):
    """构建高并发索引"""
    index_params = {
        "metric_type": "COSINE",  # 采用余弦相似度，对长文本特征抓取更好
        "index_type": "HNSW",  # 工业界标配高召回率索引算法
        "params": {"M": 8, "efConstruction": 64}
    }
    collection.create_index(field_name="vector", index_params=index_params)
    print("[System] HNSW 向量索引构建完成。")


def ingest_data_to_milvus(jsonl_path):
    """
    ETL 入库主干逻辑
    """
    if not os.path.exists(jsonl_path):
        raise FileNotFoundError(f"未找到 payload 文件: {jsonl_path}，请确认上一步清洗已成功执行。")

    # 1. 挂载 Collection
    collection = create_milvus_collection()

    # 2. 加载 Embedding 模型
    print(f"[System] 正在加载 Embedding 模型: {EMBEDDING_MODEL_NAME} (首次运行需下载权重)...")
    model = SentenceTransformer(EMBEDDING_MODEL_NAME)

    page_contents = []
    metadatas = []

    print("[System] 正在解析 JSONL Payload...")
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            item = json.loads(line)
            page_contents.append(item["page_content"])
            metadatas.append(item["metadata"])

    if not page_contents:
        print("[System] 数据为空，入库终止。")
        return

    # 3. 批量生成向量特征
    print(f"[System] 正在对 {len(page_contents)} 条原子语义执行 {DIMENSION} 维降维投射...")
    # 转换为 NumPy 数组格式，符合 Milvus 写入标准
    vectors = model.encode(page_contents, normalize_embeddings=True).tolist()

    # 4. 执行解耦写入
    print("[System] 正在向 Milvus 物理层写入数据...")
    insert_data = [
        page_contents,
        vectors,
        metadatas
    ]
    res = collection.insert(insert_data)

    # 5. 落盘并加载至内存（不可省略，否则无法查询）
    collection.flush()
    build_index(collection)
    collection.load()

    print("=" * 50)
    print(f"✅ RAG 深水区第一阶段【入库】大获成功！")
    print(f"入库总量: {collection.num_entities} 行独立语义切片")
    print("当前系统已具备了基于 HNSW 的模糊召回能力，且数值完全与语义隔离。")
    print("=" * 50)


if __name__ == "__main__":
    # 指向你刚才生成的 jsonl 产物
    payload_file = "rag_payload.jsonl"
    ingest_data_to_milvus(payload_file)