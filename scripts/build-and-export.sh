#!/usr/bin/env bash
set -euo pipefail

# =============================================
# 离线镜像构建与导出脚本
# 用法：在【能联网】的机器上执行，生成离线部署包
# =============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

IMAGE_NAME="knowledge-evigraph:latest"
EXPORT_DIR="${PROJECT_DIR}/offline-dist"
TAR_FILE="${EXPORT_DIR}/knowledge-evigraph-image.tar"

echo "=========================================="
echo "  离线镜像构建与导出"
echo "=========================================="

# 检查 Docker
if ! command -v docker &>/dev/null; then
    echo "[ERROR] Docker 未安装"
    exit 1
fi

if ! docker compose version &>/dev/null && ! command -v docker-compose &>/dev/null; then
    echo "[ERROR] Docker Compose 未安装"
    exit 1
fi

# 构建镜像
echo "[INFO] 开始构建镜像 ${IMAGE_NAME} ..."
docker build -t "${IMAGE_NAME}" .

# 创建导出目录
mkdir -p "${EXPORT_DIR}"

# 导出镜像
echo "[INFO] 导出镜像到 ${TAR_FILE} ..."
docker save "${IMAGE_NAME}" -o "${TAR_FILE}"

# 复制部署所需文件
echo "[INFO] 复制部署文件到 ${EXPORT_DIR} ..."
cp docker-compose.yml "${EXPORT_DIR}/"
cp deploy.sh "${EXPORT_DIR}/"
cp nginx.conf "${EXPORT_DIR}/" 2>/dev/null || true
cp supervisord.conf "${EXPORT_DIR}/" 2>/dev/null || true

# 生成离线部署说明
cat > "${EXPORT_DIR}/README.txt" <<'EOF'
Knowledge EviGraph 离线部署包
==============================

1. 将此目录全部文件复制到目标 Linux 服务器
2. 在服务器上执行：

     bash deploy-offline.sh

3. 如果服务器上没有 .env 文件，请先从 .env.example 创建并修改后重试

文件说明：
- knowledge-evigraph-image.tar : Docker 镜像
- docker-compose.yml           : 服务编排配置
- deploy-offline.sh            : 离线部署启动脚本
EOF

echo ""
echo "=========================================="
echo "  离线包已生成: ${EXPORT_DIR}"
echo "  镜像大小: $(du -h "${TAR_FILE}" | cut -f1)"
echo "=========================================="
echo ""
echo "下一步：将整个 offline-dist 目录复制到目标服务器，"
echo "        然后执行 bash deploy-offline.sh"
