#!/bin/bash
docker stop vllm-Qwen3-30B-A3B-AWQ
docker container rm vllm-Qwen3-30B-A3B-AWQ
docker run \
-it \
-d \
-p 18000:8000 \
--shm-size=64g \
-v `pwd`:/home/vllm/model \
-e CUDA_VISIBLE_DEVICES=2,3 \
--name vllm-Qwen3-30B-A3B-AWQ \
--privileged \
--restart always \
docker.xuanyuan.me/vllm/vllm-openai:v0.9.1  \
--host 0.0.0.0 \
--port 8000 \
--served-model-name  Qwen/Qwen3-30B-A3B-Instruct-2507-AWQ \
--model /home/vllm/model/cpatonn-mirror/Qwen3-30B-A3B-Instruct-2507-AWQ/ \
--tokenizer /home/vllm/model/cpatonn-mirror/Qwen3-30B-A3B-Instruct-2507-AWQ/ \
--trust-remote-code \
--tokenizer-mode auto \
--max-model-len 40960 \
--tensor-parallel-size 2 \
--gpu-memory-utilization 0.85 \
--enable-auto-tool-choice \
--tool-call-parser hermes