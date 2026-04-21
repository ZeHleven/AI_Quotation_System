FROM python:3.10-slim

WORKDIR /app

# 使用阿里云镜像加速安装依赖
RUN pip install \
    fastapi==0.111.0 \
    "uvicorn[standard]==0.30.1" \
    pymilvus==2.3.6 \
    sentence-transformers==3.1.0 \
    jieba==0.42.1 \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --no-cache-dir

# 拷贝业务代码和初始数据
COPY hybrid_searcher.py .
COPY rag_api_service.py .
COPY rag_materials.json .
COPY seed_milvus.py .

EXPOSE 8001

# 环境变量：使用国内 HF 镜像 + 模型缓存目录 + Milvus 容器地址
ENV HF_ENDPOINT=https://hf-mirror.com
ENV HF_HOME=/model_cache
ENV MILVUS_HOST=milvus
ENV MILVUS_PORT=19530

CMD ["uvicorn", "rag_api_service:app", "--host", "0.0.0.0", "--port", "8001"]