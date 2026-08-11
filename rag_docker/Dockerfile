FROM python:3.10-slim

WORKDIR /app

# 使用阿里云镜像加速安装依赖
RUN pip install \
    fastapi==0.111.0 \
    "uvicorn[standard]==0.30.1" \
    pymilvus==2.3.6 \
    "marshmallow>=3.13.0,<4.0.0" \
    sentence-transformers==3.1.0 \
    jieba==0.42.1 \
    rank-bm25 \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --no-cache-dir

# 拷贝业务代码和初始数据
COPY hybrid_searcher.py .
COPY rag_api_service.py .
COPY reload_auth.py .
COPY tender_evidence_search.py .
COPY rag_materials.json .
COPY seed_milvus.py .

EXPOSE 8001

# 环境变量：使用国内 HF 镜像 + 模型缓存目录 + 离线优先 + Milvus 容器地址
ENV HF_ENDPOINT=https://hf-mirror.com
ENV HF_HOME=/model_cache
ENV HF_HUB_CACHE=/model_cache/hub
ENV TRANSFORMERS_CACHE=/model_cache/transformers
ENV SENTENCE_TRANSFORMERS_HOME=/model_cache
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
ENV RAG_MODEL_OFFLINE=true
ENV EMBEDDING_MODEL_NAME=maidalun1020/bce-embedding-base_v1
ENV EMBEDDING_MODEL_PATH=/model_cache/hub/models--maidalun1020--bce-embedding-base_v1/snapshots/9c0d82af44af61abe171ffae23fde5740c0ec1a8
ENV MILVUS_HOST=milvus
ENV MILVUS_PORT=19530

CMD ["uvicorn", "rag_api_service:app", "--host", "0.0.0.0", "--port", "8001"]
