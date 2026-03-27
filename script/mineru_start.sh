#!/bin/bash
docker stop mineru_server
docker rm mineru_server


docker run -itd \
  --name=mineru_server \
  --gpus=all \
  -p 8188:8000 \
  -e OPENBLAS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  --cap-add=SYS_NICE \
  --cap-add=IPC_LOCK \
  --security-opt seccomp=unconfined \
  quincyqiang/mineru:0.1-models
