#!/bin/bash

# 定义容器名称和镜像
CONTAINER_NAME="nomic_embed_server"
IMAGE_NAME="ai/nomic-embed-text-v1.5"

echo "Stopping existing container: $CONTAINER_NAME..."
docker stop $CONTAINER_NAME 2>/dev/null

echo "Removing existing container: $CONTAINER_NAME..."
docker rm $CONTAINER_NAME 2>/dev/null

echo "Starting new container: $CONTAINER_NAME..."
echo "Mapping host port 3020 to container port 80 (assuming standard TEI/HTTP port)..."

docker run -itd \
  --name=$CONTAINER_NAME \
  --gpus=all \
  -p 3020:80 \
  -e OPENBLAS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  --cap-add=SYS_NICE \
  --cap-add=IPC_LOCK \
  --security-opt seccomp=unconfined \
  $IMAGE_NAME

echo "Container started. Checking logs..."
sleep 2
docker logs --tail 20 $CONTAINER_NAME

echo "Done. Service should be available at http://localhost:3020"