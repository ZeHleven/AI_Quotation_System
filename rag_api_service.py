# 部署说明：在你的 Python 环境中运行此脚本
# 依赖安装：pip install fastapi uvicorn pydantic
# 核心目标：将 Milvus 检索器暴露为 RESTful API，供前端 n8n 工作流实时调用

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import json
# 导入我们上一轮写好的检索核心函数
from rag_docker.hybrid_searcher import execute_strict_retrieval

app = FastAPI(title="Enterprise RAG Core API", version="1.0.0")


class QueryRequest(BaseModel):
    query: str
    top_k: int = 1


@app.post("/api/v1/retrieve")
async def retrieve_exact_pricing(request: QueryRequest):
    """
    接收 n8n 传来的业务员大白话，返回绝对刚性的 JSON 价格数组
    """
    print(f"\n[API Gateway] 收到外部请求: {request.query}")
    try:
        # 执行严格检索并获取 JSON 字符串
        result_str = execute_strict_retrieval(request.query, request.top_k)
        # 将字符串转回 Python List 以便 FastAPI 序列化
        result_data = json.loads(result_str)

        return {
            "code": 200,
            "status": "success",
            "data": result_data
        }
    except Exception as e:
        print(f"[API Error] 检索崩溃: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    print("==================================================")
    print("🚀 核心 RAG 微服务已启动")
    print("📡 监听地址: http://0.0.0.0:8000/api/v1/retrieve")
    print("==================================================")
    # 启动高并发 ASGI 服务器
    uvicorn.run(app, host="0.0.0.0", port=8001)