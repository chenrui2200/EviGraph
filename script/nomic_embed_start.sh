#!/bin/bash

CONTAINER_NAME="nomic_embed_server"
IMAGE_NAME="ghcr.io/huggingface/text-embeddings-inference:1.5"
MODEL_ID="/data/nomic-embed-text-v1.5"

# 创建一个本地目录用于存储模型缓存
mkdir -p ./model_cache

echo "Stopping and removing old container..."
docker stop $CONTAINER_NAME 2>/dev/null
docker rm $CONTAINER_NAME 2>/dev/null

echo "Starting new container..."
echo "Image: $IMAGE_NAME"
echo "Model: $MODEL_ID"
echo "Port: 3020 -> 80"

# 注意：docker run 命令中间不能有空行或注释，必须是一整行或通过 \ 连续连接
docker run -itd \
  --name=$CONTAINER_NAME \
  --gpus=all \
  -p 3020:80 \
  -v $(pwd)/model_cache:/data \
  -e HF_ENDPOINT=https://hf-mirror.com \
  -e OPENBLAS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  --cap-add=SYS_NICE \
  --cap-add=IPC_LOCK \
  --security-opt seccomp=unconfined \
  $IMAGE_NAME \
  --model-id $MODEL_ID \
  --port 80

if [ $? -eq 0 ]; then
  echo "Container started successfully."
  echo "Waiting for model to load (check logs)..."
  sleep 3
  docker logs --tail 20 $CONTAINER_NAME
else
  echo "Failed to start container."
fi