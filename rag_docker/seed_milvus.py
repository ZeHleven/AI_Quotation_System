"""
一次性数据初始化脚本
作用：将 rag_materials.json 中的物料单价向量化并写入 Milvus
运行方式：docker exec rag-api-service python seed_milvus.py
"""
import json, os, time
from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection
from sentence_transformers import SentenceTransformer

MILVUS_HOST = os.environ.get("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")
ALIAS      = "enterprise_quotation_rag"
BLUE       = "quotation_blue"
GREEN      = "quotation_green"
HF_HOME    = os.environ.get("HF_HOME", "/model_cache")

print(f"[Seed] 连接 Milvus: {MILVUS_HOST}:{MILVUS_PORT}")
for i in range(10):
    try:
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
        print("[Seed] Milvus 连接成功！")
        break
    except Exception as e:
        print(f"[Seed] 等待 Milvus 就绪... ({i+1}/10): {e}")
        time.sleep(6)

# 读取物料数据
with open("rag_materials.json", "r", encoding="utf-8") as f:
    materials = json.load(f)

if not materials:
    print("[Seed] rag_materials.json 为空，退出。")
    exit(0)

# ── 找出 alias 当前指向的物理集合，选另一个作为写入目标（蓝绿切换）──
def get_alias_target():
    for name in [BLUE, GREEN]:
        if utility.has_collection(name):
            try:
                if ALIAS in utility.list_aliases(name):
                    return name
            except Exception:
                pass
    return None

current_live = get_alias_target()
target = GREEN if current_live == BLUE else BLUE
print(f"[Seed] 当前在线集合: {current_live or '无'} → 写入目标: {target}")

# 清理目标集合（物理名，不走 alias）
if utility.has_collection(target):
    utility.drop_collection(target)
    print(f"[Seed] 已清理旧集合: {target}")

# 建新集合
fields = [
    FieldSchema(name="id",           dtype=DataType.INT64,         is_primary=True, auto_id=True),
    FieldSchema(name="page_content", dtype=DataType.VARCHAR,        max_length=65535),
    FieldSchema(name="vector",       dtype=DataType.FLOAT_VECTOR,   dim=768),
    FieldSchema(name="metadata",     dtype=DataType.JSON),
]
schema     = CollectionSchema(fields=fields, description="企业级报价库_混合检索架构")
collection = Collection(name=target, schema=schema)
print(f"[Seed] 集合 {target} 创建成功")

# 加载 Embedding 模型
print("[Seed] 加载 BCEmbedding 模型（首次需下载，请耐心等待）...")
model = SentenceTransformer("maidalun1020/bce-embedding-base_v1")
print("[Seed] 模型加载完成！")

# 构建向量数据
page_contents, metadatas = [], []
for item in materials:
    content = (f"施工作业项为：{item['item_name']}。"
               f"工艺及材料标准：{item['notes']}。"
               f"计价单位：{item['unit']}。")
    page_contents.append(content)
    metadatas.append({
        "item_name":   item["item_name"],
        "unit":        item["unit"],
        "price_total": float(item["unit_price"]),
        "notes":       item["notes"]
    })

vectors = model.encode(page_contents, normalize_embeddings=True).tolist()
collection.insert([page_contents, vectors, metadatas])
collection.flush()

# 建索引并加载
collection.create_index(
    field_name="vector",
    index_params={"metric_type": "COSINE", "index_type": "HNSW", "params": {"M": 8, "efConstruction": 64}}
)
collection.load()
print(f"[Seed] 成功写入 {collection.num_entities} 条数据")

# ── 原子切换 alias（零停机）──
try:
    # 新版 pymilvus：alter_alias 原子替换
    utility.alter_alias(target, ALIAS)
    print(f"[Seed] alias {ALIAS} 已切换 → {target}")
except Exception:
    # 旧版：先 drop 再 create
    try:
        utility.drop_alias(ALIAS)
    except Exception:
        pass
    utility.create_alias(target, ALIAS)
    print(f"[Seed] alias {ALIAS} 已重建 → {target}")

# 清理不再使用的旧集合
if current_live and utility.has_collection(current_live):
    utility.drop_collection(current_live)
    print(f"[Seed] 已清理下线集合: {current_live}")

print("[Seed] ✅ 知识库初始化完成，RAG 服务已就绪。")