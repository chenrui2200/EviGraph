#!/usr/bin/env bash
# 如果用户误用 sh 执行，自动切换到 bash
if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi
set -euo pipefail

# =============================================
# Knowledge EviGraph 一键部署脚本
# 用法: 将项目放到 Linux 机器上，执行 bash deploy.sh
# =============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# 检查 Docker
if ! command -v docker &>/dev/null; then
    error "Docker 未安装，请先安装 Docker: https://docs.docker.com/engine/install/"
    exit 1
fi

# 检查 Docker Compose（兼容新旧版本）
if docker compose version &>/dev/null; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    error "Docker Compose 未安装，请先安装: https://docs.docker.com/compose/install/"
    exit 1
fi

info "使用 Compose 命令: ${COMPOSE_CMD}"

# 创建必要的本地持久化目录
mkdir -p backend/uploads
info "已确保目录存在: backend/uploads"

# 检查 .env 文件
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        warn "已根据 .env.example 创建 .env 文件，请根据实际情况修改配置后再部署！"
        warn "关键配置项: LLM_BASE_URL、NEO4J_URI、EMBEDDING_BASE_URL、MINERU_API_URL 等"
        read -rp "是否继续部署? [y/N]: " confirm
        case "$confirm" in
            [yY][eE][sS]|[yY]) ;;
            *) info "已取消部署，请修改 .env 后重新运行 bash deploy.sh"; exit 0 ;;
        esac
    else
        error "缺少 .env 文件且未找到 .env.example，无法继续部署"
        exit 1
    fi
fi

# 构建并启动服务
info "开始构建镜像 knowledge-evigraph:latest ..."
${COMPOSE_CMD} build --no-cache

info "启动服务..."
${COMPOSE_CMD} up -d

# 等待服务就绪
info "等待服务健康检查（约 10-30 秒）..."
for i in {1..30}; do
    if curl -sf http://localhost:5001/api/health &>/dev/null; then
        info "后端服务已就绪"
        break
    fi
    sleep 2
    if [ "$i" -eq 30 ]; then
        warn "后端服务健康检查超时，请手动查看日志: ${COMPOSE_CMD} logs -f knowledge-evigraph"
    fi
done

echo ""
echo "==============================================="
info "部署完成！"
echo "  - 前端访问: http://<服务器IP>"
echo "  - 后端 API: http://<服务器IP>:5001"
echo "  - Neo4j Browser: http://<服务器IP>:7474"
echo ""
echo "常用命令:"
echo "  查看日志: ${COMPOSE_CMD} logs -f knowledge-evigraph"
echo "  停止服务: ${COMPOSE_CMD} down"
echo "  重启服务: ${COMPOSE_CMD} restart"
echo "==============================================="
