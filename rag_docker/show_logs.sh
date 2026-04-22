#!/bin/bash
# 用法：
#   ./show_logs.sh          # 实时追踪所有 rag_service 容器日志
#   ./show_logs.sh rag      # 只看 rag-api-service
#   ./show_logs.sh -n 100   # 显示最近 100 行后实时追踪

SERVICES="milvus-etcd milvus-minio milvus-standalone rag-api-service"
LINES=${2:-50}

case "$1" in
  rag)   docker logs -f --tail=$LINES rag-api-service ;;
  milvus) docker logs -f --tail=$LINES milvus-standalone ;;
  etcd)  docker logs -f --tail=$LINES milvus-etcd ;;
  minio) docker logs -f --tail=$LINES milvus-minio ;;
  -n)    docker compose -f /opt/rag_service/docker-compose.yml logs -f --tail=$2 ;;
  *)
    echo "=== RAG 服务日志聚合 (Ctrl+C 退出) ==="
    docker compose -f /opt/rag_service/docker-compose.yml logs -f --tail=50
    ;;
esac
