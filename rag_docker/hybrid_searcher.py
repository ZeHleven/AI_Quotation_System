import json
import os
import re
import threading
from pathlib import Path

# ==========================================
# HuggingFace / Transformers 离线优先配置
# 必须在导入 sentence_transformers 前设置，避免请求时访问 huggingface.co。
# ==========================================
HF_HOME = os.environ.get("HF_HOME", "/model_cache")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HOME", HF_HOME)
os.environ.setdefault("HF_HUB_CACHE", os.path.join(HF_HOME, "hub"))
os.environ.setdefault("TRANSFORMERS_CACHE", os.path.join(HF_HOME, "transformers"))
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", HF_HOME)

RAG_MODEL_OFFLINE = os.environ.get("RAG_MODEL_OFFLINE", "true").lower() in {"1", "true", "yes", "on"}
if RAG_MODEL_OFFLINE:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import jieba
from pymilvus import connections, Collection
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi

# ==========================================
# 架构配置区
# ==========================================
MILVUS_HOST = os.environ.get("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")
COLLECTION_NAME = "enterprise_quotation_rag"
EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", "maidalun1020/bce-embedding-base_v1")
EMBEDDING_MODEL_PATH = os.environ.get("EMBEDDING_MODEL_PATH", "").strip()
RAG_DATA_PATH = "/app/rag_materials.json"
MILVUS_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("MILVUS_CONNECT_TIMEOUT_SECONDS", "5"))
MILVUS_LOAD_TIMEOUT_SECONDS = float(os.environ.get("MILVUS_LOAD_TIMEOUT_SECONDS", "90"))
MILVUS_SEARCH_TIMEOUT_SECONDS = float(os.environ.get("MILVUS_SEARCH_TIMEOUT_SECONDS", "20"))
RAG_MAX_SUB_QUERIES = int(os.environ.get("RAG_MAX_SUB_QUERIES", "6"))
RAG_MAX_QUERY_CHARS = int(os.environ.get("RAG_MAX_QUERY_CHARS", "80"))
RAG_BM25_FALLBACK_ON_MILVUS_ERROR = (
    os.environ.get("RAG_BM25_FALLBACK_ON_MILVUS_ERROR", "true").lower() in {"1", "true", "yes", "on"}
)
RAG_VECTOR_ENABLED = os.environ.get("RAG_VECTOR_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

# RRF 最低分数阈值——低于此值视为无关结果，直接丢弃
RRF_SCORE_THRESHOLD = 0.008

# ==========================================
# 读取物料库（jieba 词典注册依赖此数据）
# ==========================================
with open(RAG_DATA_PATH, "r", encoding="utf-8") as f:
    _RAG_MATERIALS = json.load(f)

# ==========================================
# jieba 初始化 + 建筑领域自定义词典
# 将 rag_materials.json 的 item_name 全部注册，
# 防止专业术语被误切（如"直线型吊顶"→["直线","型","吊顶"]）
# ==========================================
print(f"[System] 正在初始化 jieba 分词器...")
jieba.initialize()
for _item in _RAG_MATERIALS:
    _meta = _item.get("metadata", _item)
    _name = _meta.get("item_name", _item.get("item_name", ""))
    if _name:
        jieba.add_word(_name)
print(f"[System] ✅ jieba 初始化完毕，已注册 {len(_RAG_MATERIALS)} 个领域词条\n")


def _tokenize(text: str) -> list:
    """
    jieba 中文分词，过滤纯数字和单字，保留有效词条。
    替代原有字符级分词，准确识别建筑专业术语。
    """
    words = jieba.lcut(str(text))
    return [w for w in words if len(w) >= 2 and w.strip() and not re.match(r'^\d+\.?\d*$', w)]


def _normalize_sub_query(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"^\[从文件识别到的内容\]\s*[:：]?\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:RAG_MAX_QUERY_CHARS]


def _is_noise_sub_query(text: str) -> bool:
    if len(text) <= 2:
        return True
    if re.match(r"^\d+(\.\d+)?\s*(平米|平方米|㎡|m2|m²|米|个|项|套|遍)?$", text, re.IGNORECASE):
        return True
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", text):
        return True
    return False


def _build_sub_queries(query_text: str) -> list:
    raw_sub_queries = re.split(r'[；。;|\n、，,]', query_text)
    sub_queries = []
    seen = set()
    for raw in raw_sub_queries:
        sq = _normalize_sub_query(raw)
        if _is_noise_sub_query(sq):
            continue
        key = sq.lower()
        if key in seen:
            continue
        seen.add(key)
        sub_queries.append(sq)
        if len(sub_queries) >= RAG_MAX_SUB_QUERIES:
            break
    if not sub_queries:
        sub_queries = [_normalize_sub_query(query_text)]
    return sub_queries


# ==========================================
# BM25 关键词索引（建筑术语精确召回）
# 索引文本 = item_name + notes + page_content
# 相比原版多了 notes 字段，大幅扩充检索覆盖面
# ==========================================
print(f"[System] 正在构建 BM25 关键词索引...")

_BM25_DOCS = []
_BM25_RECORDS = []

for item in _RAG_MATERIALS:
    meta = item.get("metadata", item)
    page_content = item.get("page_content", "")
    item_name = meta.get("item_name", item.get("item_name", ""))
    notes = meta.get("notes", item.get("notes", ""))
    # notes 补充进索引，捕获材料品牌、工艺描述等关键词
    searchable = f"{item_name} {notes} {page_content}".strip()
    _BM25_DOCS.append(_tokenize(searchable))
    _BM25_RECORDS.append({
        "item_name": item_name,
        "unit_price": meta.get("price_total", item.get("price_total", item.get("unit_price"))),
        "unit": meta.get("unit", item.get("unit")),
        "content": page_content or notes
    })

_BM25_INDEX = BM25Okapi(_BM25_DOCS)
print(f"[System] ✅ BM25 索引构建完毕，共 {len(_BM25_DOCS)} 条记录！\n")


# ==========================================
# 向量模型全局单例（常驻内存）
# ==========================================
print(f"[System] 正在全局预热 BCEmbedding 模型...")


def _model_cache_dir_name(model_name: str) -> str:
    return "models--" + model_name.replace("/", "--")


def _snapshot_score(snapshot_dir: Path) -> int:
    score = 0
    required_markers = [
        "modules.json",
        "config.json",
        "sentence_bert_config.json",
        "1_Pooling",
    ]
    weight_files = [
        "model.safetensors",
        "pytorch_model.bin",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.txt",
    ]
    for name in required_markers:
        if (snapshot_dir / name).exists():
            score += 5
    for name in weight_files:
        if (snapshot_dir / name).exists():
            score += 3
    return score


def _discover_local_model_snapshot() -> str:
    if EMBEDDING_MODEL_PATH:
        path = Path(EMBEDDING_MODEL_PATH)
        if path.exists():
            return str(path)
        print(f"[System] ⚠️ EMBEDDING_MODEL_PATH 不存在，将自动扫描缓存: {EMBEDDING_MODEL_PATH}")

    cache_root = Path(os.environ.get("HF_HUB_CACHE", os.path.join(HF_HOME, "hub")))
    snapshots_root = cache_root / _model_cache_dir_name(EMBEDDING_MODEL_NAME) / "snapshots"
    if not snapshots_root.exists():
        return EMBEDDING_MODEL_NAME

    candidates = []
    for snapshot in snapshots_root.iterdir():
        if not snapshot.is_dir():
            continue
        score = _snapshot_score(snapshot)
        if score > 0:
            candidates.append((score, snapshot.stat().st_mtime, snapshot))

    if not candidates:
        return EMBEDDING_MODEL_NAME

    candidates.sort(reverse=True)
    best = candidates[0][2]
    print(f"[System] 自动发现本地模型 snapshot: {best} | score={candidates[0][0]}")
    return str(best)


_MODEL_SOURCE = _discover_local_model_snapshot()
print(f"[System] 模型来源: {_MODEL_SOURCE} | cache={HF_HOME} | offline={RAG_MODEL_OFFLINE}")
_GLOBAL_MODEL = SentenceTransformer(
    _MODEL_SOURCE,
    cache_folder=HF_HOME,
    local_files_only=RAG_MODEL_OFFLINE,
)
print(f"[System] ✅ BCEmbedding 模型加载完毕，已常驻内存！\n")

_MILVUS_LOCK = threading.Lock()
_MILVUS_COLLECTION = None
_MILVUS_COLLECTION_LOADED = False


# ==========================================
# RRF 融合算法（Reciprocal Rank Fusion）
# ==========================================
def _rrf_merge(vector_hits: dict, bm25_hits: dict, k: int = 60) -> list:
    """RRF 公式: score(d) = Σ 1/(k + rank(d))，两路结果按排名融合"""
    all_keys = set(vector_hits.keys()) | set(bm25_hits.keys())

    vector_ranked = sorted(vector_hits.keys(),
                           key=lambda x: vector_hits[x]["distance"], reverse=True)
    bm25_ranked = sorted(bm25_hits.keys(),
                         key=lambda x: bm25_hits[x]["bm25_score"], reverse=True)

    scores = {}
    for key in all_keys:
        score = 0.0
        if key in vector_ranked:
            score += 1.0 / (k + vector_ranked.index(key) + 1)
        if key in bm25_ranked:
            score += 1.0 / (k + bm25_ranked.index(key) + 1)
        scores[key] = score

    merged = []
    for key in sorted(scores.keys(), key=lambda x: scores[x], reverse=True):
        record = vector_hits.get(key) or bm25_hits.get(key)
        record["rrf_score"] = scores[key]
        merged.append(record)

    return merged


def init_milvus_connection():
    global _MILVUS_COLLECTION, _MILVUS_COLLECTION_LOADED
    if _MILVUS_COLLECTION is not None and _MILVUS_COLLECTION_LOADED:
        return _MILVUS_COLLECTION

    with _MILVUS_LOCK:
        if _MILVUS_COLLECTION is not None and _MILVUS_COLLECTION_LOADED:
            return _MILVUS_COLLECTION

        print(f"[Milvus] 正在连接 {MILVUS_HOST}:{MILVUS_PORT} | collection={COLLECTION_NAME}")
        connections.connect(
            "default",
            host=MILVUS_HOST,
            port=MILVUS_PORT,
            timeout=MILVUS_CONNECT_TIMEOUT_SECONDS,
        )
        print("[Milvus] 连接成功")

        collection = Collection(COLLECTION_NAME)
        print(f"[Milvus] 正在加载 collection，timeout={MILVUS_LOAD_TIMEOUT_SECONDS}s")
        collection.load(timeout=MILVUS_LOAD_TIMEOUT_SECONDS)
        print("[Milvus] collection 加载成功，后续请求将复用常驻 collection")

        _MILVUS_COLLECTION = collection
        _MILVUS_COLLECTION_LOADED = True
        return collection


def reset_milvus_connection():
    global _MILVUS_COLLECTION, _MILVUS_COLLECTION_LOADED
    with _MILVUS_LOCK:
        _MILVUS_COLLECTION = None
        _MILVUS_COLLECTION_LOADED = False
        try:
            connections.disconnect("default")
        except Exception:
            pass


def execute_strict_retrieval(query_text, top_k=5):
    """
    企业级 RAG 混合检索器：
    - 通道A：向量语义检索（BCEmbedding + Milvus HNSW，ef=128）
    - 通道B：BM25 关键词检索（jieba 分词 + item_name/notes 双字段索引）
    - 融合：RRF 倒数排名融合 + 低分阈值过滤
    """
    print(f"\n[System] 接收到业务端查询指令: '{query_text[:80]}'")

    # 多意图拆分：过滤数量行和重复片段，并限制子查询数量，避免 n8n 长时间等待。
    sub_queries = _build_sub_queries(query_text)
    print(f"[System] 多意图拆分为 {len(sub_queries)} 个子通道，最多 {RAG_MAX_SUB_QUERIES} 个")

    # ef=128，提升 HNSW 小数据集检索精度（原 ef=64）
    search_params = {"metric_type": "COSINE", "params": {"ef": 128}}

    # ---- 通道A：向量检索 ----
    vector_hits = {}
    if RAG_VECTOR_ENABLED:
        try:
            collection = init_milvus_connection()
            print(f"  [向量] 批量向量化 {len(sub_queries)} 个子查询")
            query_vectors = _GLOBAL_MODEL.encode(sub_queries, normalize_embeddings=True).tolist()
            results = collection.search(
                data=query_vectors,
                anns_field="vector",
                param=search_params,
                limit=top_k,
                expr=None,
                output_fields=["metadata", "page_content"],
                timeout=MILVUS_SEARCH_TIMEOUT_SECONDS,
            )
            print(f"  [向量] Milvus 批量返回 {sum(len(hits) for hits in results)} 条")
            for sq, hits in zip(sub_queries, results):
                print(f"  [向量] 召回: '{sq[:30]}' -> {len(hits)} 条")
                for hit in hits:
                    meta = hit.entity.get("metadata")
                    item_name = meta.get("item_name")
                    if item_name not in vector_hits or hit.distance > vector_hits[item_name]["distance"]:
                        vector_hits[item_name] = {
                            "distance": hit.distance,
                            "bm25_score": 0.0,
                            "meta": meta,
                            "content": hit.entity.get("page_content"),
                            "item_name": item_name,
                            "unit_price": meta.get("price_total"),
                            "unit": meta.get("unit")
                        }
        except Exception as exc:
            reset_milvus_connection()
            if not RAG_BM25_FALLBACK_ON_MILVUS_ERROR:
                raise
            print(f"  [向量] Milvus 检索不可用，自动降级 BM25-only: {exc}")
    else:
        print("  [向量] RAG_VECTOR_ENABLED=false，跳过 Milvus，使用 BM25-only")

    # ---- 通道B：BM25 检索（jieba 分词） ----
    bm25_hits = {}
    for sq in sub_queries:
        print(f"  [BM25] 召回: '{sq[:30]}'")
        tokens = _tokenize(sq)
        scores = _BM25_INDEX.get_scores(tokens)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            record = _BM25_RECORDS[idx]
            item_name = record["item_name"]
            if item_name not in bm25_hits or scores[idx] > bm25_hits[item_name]["bm25_score"]:
                bm25_hits[item_name] = {
                    "distance": 0.0,
                    "bm25_score": float(scores[idx]),
                    "meta": {"item_name": item_name,
                             "price_total": record["unit_price"],
                             "unit": record["unit"]},
                    "content": record["content"],
                    "item_name": item_name,
                    "unit_price": record["unit_price"],
                    "unit": record["unit"]
                }

    # ---- RRF 融合 + 低分过滤 ----
    print(f"\n[System] 向量召回 {len(vector_hits)} 条 | BM25 召回 {len(bm25_hits)} 条 → RRF 融合中...")
    merged = _rrf_merge(vector_hits, bm25_hits)

    # 过滤 RRF 分数低于阈值的无关结果
    filtered = [h for h in merged if h.get("rrf_score", 0) >= RRF_SCORE_THRESHOLD]
    final_hits = filtered[:15]

    print("\n" + "=" * 50)
    print("混合检索融合结果")
    print("=" * 50)

    extracted_payloads = []
    for hit in final_hits:
        print(f"\n[命中] RRF={hit.get('rrf_score', 0):.4f} | 向量={hit.get('distance', 0):.4f} | BM25={hit.get('bm25_score', 0):.4f}")
        print(f"  施工项目: {hit.get('item_name')} | 单价: {hit.get('unit_price')} 元/{hit.get('unit')}")
        extracted_payloads.append({
            "item_name": hit.get("item_name"),
            "unit_price": hit.get("unit_price"),
            "unit": hit.get("unit")
        })

    return json.dumps(extracted_payloads, ensure_ascii=False)


def rebuild_indexes(materials: list):
    """
    用新物料数据重建内存中的 BM25 索引和 jieba 自定义词典。
    由 /admin/reload 接口在 Milvus 写入完成后调用，保持内存与向量库一致。
    """
    global _BM25_DOCS, _BM25_RECORDS, _BM25_INDEX, _RAG_MATERIALS

    _RAG_MATERIALS = materials

    # 重新注册 jieba 词典
    for item in materials:
        meta = item.get("metadata", item)
        name = meta.get("item_name", item.get("item_name", ""))
        if name:
            jieba.add_word(name)

    # 重建 BM25 索引
    new_docs = []
    new_records = []
    for item in materials:
        meta = item.get("metadata", item)
        page_content = item.get("page_content", "")
        item_name = meta.get("item_name", item.get("item_name", ""))
        notes = meta.get("notes", item.get("notes", ""))
        searchable = f"{item_name} {notes} {page_content}".strip()
        new_docs.append(_tokenize(searchable))
        new_records.append({
            "item_name": item_name,
            "unit_price": meta.get("price_total", item.get("price_total", item.get("unit_price"))),
            "unit": meta.get("unit", item.get("unit")),
            "content": page_content or notes,
        })

    _BM25_DOCS = new_docs
    _BM25_RECORDS = new_records
    _BM25_INDEX = BM25Okapi(_BM25_DOCS)
    print(f"[System] BM25 索引热更新完毕，共 {len(_BM25_DOCS)} 条记录")
