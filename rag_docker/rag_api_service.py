from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
import json
import os

from hybrid_searcher import execute_strict_retrieval, rebuild_indexes, _GLOBAL_MODEL

app = FastAPI(title="Enterprise RAG Core API", version="2.0.0")

RELOAD_SECRET = os.environ.get("RELOAD_SECRET", "")

COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "enterprise_quotation_rag")
MILVUS_HOST = os.environ.get("MILVUS_HOST", "standalone")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")
BLUE  = "quotation_blue"
GREEN = "quotation_green"
ALIAS = COLLECTION_NAME


class QueryRequest(BaseModel):
    query: str
    top_k: int = 1


class MaterialItem(BaseModel):
    id: Optional[str] = None
    item_name: str
    unit_price: float
    unit: str
    notes: str = ""
    is_draft: Optional[bool] = False


class ReloadRequest(BaseModel):
    materials: List[MaterialItem]
    secret: str


@app.post("/api/v1/retrieve")
async def retrieve_exact_pricing(request: QueryRequest):
    print(f"\n[API Gateway] 收到外部请求: {request.query}")
    try:
        result_str = execute_strict_retrieval(request.query, request.top_k)
        result_data = json.loads(result_str)
        return {"code": 200, "status": "success", "data": result_data}
    except Exception as e:
        print(f"[API Error] 检索崩溃: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/reload")
async def reload_knowledge_base(request: ReloadRequest):
    """
    接收最新物料数据，执行蓝绿切换写入 Milvus，并热更新内存 BM25 索引。
    调用方：Windows FastAPI sync_milvus 接口。
    鉴权：request body 中的 secret 字段。
    """
    if RELOAD_SECRET and request.secret != RELOAD_SECRET:
        raise HTTPException(status_code=403, detail="secret 校验失败")

    materials = [m.dict() for m in request.materials]
    if not materials:
        raise HTTPException(status_code=400, detail="materials 不能为空")

    try:
        from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection

        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)

        # 蓝绿切换：确定写入目标
        all_physical = utility.list_collections()
        current = None
        for col_name in [BLUE, GREEN]:
            if col_name in all_physical:
                try:
                    if ALIAS in utility.list_aliases(col_name):
                        current = col_name
                        break
                except Exception:
                    pass

        next_col = GREEN if current == BLUE else BLUE

        if next_col in all_physical:
            utility.drop_collection(next_col)

        fields = [
            FieldSchema(name="id",           dtype=DataType.INT64,       is_primary=True, auto_id=True),
            FieldSchema(name="page_content", dtype=DataType.VARCHAR,      max_length=65535),
            FieldSchema(name="vector",       dtype=DataType.FLOAT_VECTOR, dim=768),
            FieldSchema(name="metadata",     dtype=DataType.JSON),
        ]
        new_col = Collection(name=next_col,
                             schema=CollectionSchema(fields=fields, description="企业级报价库"))

        page_contents, metadatas = [], []
        for item in materials:
            content = (f"施工作业项为：{item['item_name']}。"
                       f"工艺及材料标准：{item['notes']}。"
                       f"计价单位：{item['unit']}。")
            page_contents.append(content)
            metadatas.append({
                "item_name": item["item_name"],
                "unit": item["unit"],
                "price_total": float(item["unit_price"]),
                "notes": item["notes"],
            })

        # 复用已常驻内存的向量模型，无需重新加载
        vectors = _GLOBAL_MODEL.encode(page_contents, normalize_embeddings=True).tolist()
        new_col.insert([page_contents, vectors, metadatas])
        new_col.flush()
        new_col.create_index(
            field_name="vector",
            index_params={"metric_type": "COSINE", "index_type": "HNSW",
                          "params": {"M": 8, "efConstruction": 64}},
        )
        new_col.load()

        try:
            utility.alter_alias(collection_name=next_col, alias=ALIAS)
        except Exception:
            utility.create_alias(collection_name=next_col, alias=ALIAS)

        if current and current in utility.list_collections():
            utility.drop_collection(current)

        # 热更新内存 BM25 索引
        rebuild_indexes(materials)

        msg = f"零停机热更新完成，共同步 {len(materials)} 条（{next_col} -> {ALIAS}）"
        print(f"[Reload] {msg}")
        return {"code": 200, "message": msg}

    except Exception as e:
        print(f"[Reload Error] {str(e)}")
        raise HTTPException(status_code=500, detail=f"热更新失败: {str(e)}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
