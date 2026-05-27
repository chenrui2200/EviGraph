#!/bin/bash
docker stop mineru_server
docker rm mineru_server


docker run -itd --restart=always \
 --name=mineru_server \
 --gpus device=0,1 \
 -e NVIDIA_VISIBLE_DEVICES=0,1 \
 -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
 -p 8888:8888 \
 -p 30000:30000 \
 -e HF_ENDPOINT=https://hf-mirror.com \
 -e PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
 -e PIP_TRUSTED_HOST=mirrors.aliyun.com \
 -e NPM_CONFIG_REGISTRY=https://registry.npmmirror.com \
 -e YARN_NPM_REGISTRY_SERVER=https://registry.npmmirror.com \
 -v /etc/localtime:/etc/localtime \
 --add-host=host.docker.internal:host-gateway \
 chenrui/mineru-api-full:v2.1.11
