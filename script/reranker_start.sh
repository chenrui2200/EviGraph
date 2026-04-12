#!/bin/bash
CONTAINER="bge-reranker"
PORT=8000
LOCAL_DIR="./model_cache/bge-reranker-v2-m3"
CONTAINER_DIR="/model"

# 1. 校验本地模型
if [ ! -f "$LOCAL_DIR/config.json" ]; then
  echo "❌ 未检测到模型，请确保 $LOCAL_DIR 下已下载完整 HF 模型文件"
  exit 1
fi

# 2. 清理旧容器
docker rm -f "$CONTAINER" 2>/dev/null

# 3. 启动服务
docker run -d \
  --name "$CONTAINER" \
  -p "$PORT":9997 \
  -v "$LOCAL_DIR:$CONTAINER_DIR" \
  --gpus all \
  --ipc=host \
  xprobe/xinference:latest \
  xinference-local --host 0.0.0.0

echo "⏳ 等待服务初始化..."
sleep 6

# 4. 注册本地模型（直接指向挂载路径，不联网下载）
curl -s -X POST "http://localhost:9997/v1/models" \
  -H "Content-Type: application/json" \
  -d '{
    "model_uid": "bge-reranker-v2-m3",
    "model_name": "bge-reranker-v2-m3",
    "model_path": "'"$CONTAINER_DIR"'",
    "model_type": "rerank",
    "model_format": "pytorch",
    "n_gpu": -1
  }' > /dev/null

echo "✅ 启动完成"
echo "🌐 API: http://localhost:$PORT/v1/rerank"
echo "📝 调用示例:"
echo "curl -X POST http://localhost:$PORT/v1/rerank \\"
echo "  -H 'Content-Type: application/json' \\"
echo "  -d '{\"model\":\"bge-reranker-v2-m3\",\"query\":\"大模型优化\",\"documents\":[\"GPU显存调优\",\"传统数据库设计\"]}'"