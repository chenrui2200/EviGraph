#!/usr/bin/env bash
git pull
# 如果用户误用 sh 执行，自动切换到 bash
if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi
set -euo pipefail

# =============================================
# Knowledge EviGraph 一键部署脚本
# 用法: 将项目放到 Linux 机器上，执行 bash deploy.sh
#
# 首次部署: 构建完整镜像并打 tag 为 base
# 后续部署: 仅同步代码到运行中的容器，重启服务（跳过依赖层重建）
# 强制重建: bash deploy.sh --force-rebuild
# =============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()  { echo -e "${CYAN}[STEP]${NC}  $*"; }

# =============================================
# 参数解析
# =============================================
FORCE_REBUILD=false
if [[ "${1:-}" == "--force-rebuild" ]]; then
    FORCE_REBUILD=true
fi

# =============================================
# Docker 检查
# =============================================
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

# =============================================
# 系统依赖检查（仅 Linux）
# =============================================
if [[ "$(uname -s)" == "Linux" ]]; then
    info "检查系统依赖..."
    if ! command -v soffice &>/dev/null; then
        if command -v apt-get &>/dev/null; then
            # 检查是否有 root/sudo 权限
            if [ "$(id -u)" -eq 0 ] || sudo -n true 2>/dev/null; then
                warn "LibreOffice (soffice) 未安装，正在安装..."
                apt-get update -qq && apt-get install -y --no-install-recommends libreoffice-writer
                info "LibreOffice 安装完成"
            else
                warn "LibreOffice (soffice) 未安装（缺少 root 权限，跳过自动安装）"
                info "Docker 容器内已包含 LibreOffice，勿需担心。也可手动安装: sudo apt-get install libreoffice-writer"
            fi
        else
            warn "LibreOffice (soffice) 未安装，apt-get 不可用"
            info "如需安装: https://www.libreoffice.org/download/download/"
        fi
    else
        info "LibreOffice 已安装: $(soffice --version 2>/dev/null || echo 'soffice found')"
    fi
fi

mkdir -p backend/uploads
info "已确保目录存在: backend/uploads"

# =============================================
# .env 检查
# =============================================
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

# =============================================
# 镜像是否存在（决定走首次构建还是代码同步）
# =============================================
BASE_IMAGE="knowledge-evigraph:base"
LATEST_IMAGE="knowledge-evigraph:latest"
CONTAINER_NAME="knowledge-evigraph"

check_base_exists() {
    docker image inspect "${BASE_IMAGE}" &>/dev/null
}

# =============================================
# 构建策略
# =============================================
if $FORCE_REBUILD; then
    # --force-rebuild: 删除旧镜像，重新完整构建
    step "强制重建模式，删除旧镜像..."
    docker rmi "${BASE_IMAGE}" "${LATEST_IMAGE}" 2>/dev/null || true
    step "完整构建镜像 (知识图谱 base 层 + 代码层，--no-cache 确保最新)..."
    ${COMPOSE_CMD} build --no-cache
    docker tag "${LATEST_IMAGE}" "${BASE_IMAGE}"
    info "Base 镜像已更新: ${BASE_IMAGE}"
    RESTART_MODE="recreate"

elif check_base_exists; then
    # 有 base 镜像：仅同步代码到运行中容器
    step "检测到 base 镜像 '${BASE_IMAGE}'，进入增量部署模式"

    # 容器是否在运行
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        # 检查 requirements.txt 是否有新增依赖（通过 hash 判断）
        REQ_CUR_SHA="$(sha256sum backend/requirements.txt 2>/dev/null | cut -d' ' -f1)"
        REQ_CONTAINER_SHA="$(docker exec "${CONTAINER_NAME}" sha256sum /usr/local/lib/python3.12/site-packages/requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "")"

        REQS_CHANGED=false
        if [ -n "$REQ_CUR_SHA" ] && [ "$REQ_CUR_SHA" != "$REQ_CONTAINER_SHA" ]; then
            REQS_CHANGED=true
        fi

        if $REQS_CHANGED; then
            step "检测到 requirements.txt 有变更，执行依赖更新..."
            docker cp backend/requirements.txt "${CONTAINER_NAME}:/usr/local/lib/python3.12/site-packages/requirements.txt"
            docker exec "${CONTAINER_NAME}" pip install --no-cache-dir -r /usr/local/lib/python3.12/site-packages/requirements.txt
            info "依赖安装完成"
        fi

        step "同步代码到容器..."
        # 前端 rebuild（利用 Stage 1 Node.js，不走 --no-cache，靠 cache 自动判断变更层）
        step "前端 rebuild（利用 Docker 缓存）..."
        ${COMPOSE_CMD} build
        docker tag "${LATEST_IMAGE}" "${BASE_IMAGE}"

        # 从刚构建的镜像里提取新 dist，复制到运行中容器（不用本地旧 filesystem）
        step "从镜像提取新 dist 到运行中容器..."
        docker run --rm \
            -v "$(pwd)/frontend/dist:/dist_out" \
            --entrypoint /bin/sh \
            "${LATEST_IMAGE}" \
            -c "cp -r /app/frontend/dist/. /dist_out/ 2>/dev/null || true"
        docker cp frontend/dist/. "${CONTAINER_NAME}:/app/frontend/dist/"

        # 同步后端代码 + 配置文件
        docker cp backend/. "${CONTAINER_NAME}:/app/backend/"
        [ -f nginx.conf ]       && docker cp nginx.conf "${CONTAINER_NAME}:/etc/nginx/nginx.conf"
        [ -f supervisord.conf ] && docker cp supervisord.conf "${CONTAINER_NAME}:/etc/supervisor/conf.d/supervisord.conf"
        [ -f .env ]             && docker cp .env "${CONTAINER_NAME}:/app/.env"

        step "代码已同步，重启服务..."
        docker restart "${CONTAINER_NAME}"
    else
        warn "容器未运行，以增量模式启动新容器..."
        ${COMPOSE_CMD} up -d --no-build
    fi
    RESTART_MODE="restart"

else
    # 无 base 镜像：首次完整构建
    step "首次部署，完整构建镜像并打 tag 为 base..."
    ${COMPOSE_CMD} build --no-cache
    docker tag "${LATEST_IMAGE}" "${BASE_IMAGE}"
    info "Base 镜像已生成: ${BASE_IMAGE}"
    step "启动服务..."
    ${COMPOSE_CMD} up -d
    RESTART_MODE="recreate"
fi

# =============================================
# 健康检查
# =============================================
info "等待服务就绪（约 10-30 秒）..."
for i in {1..30}; do
    if curl -sf http://localhost:5001/api/health &>/dev/null; then
        info "后端服务已就绪"
        break
    fi
    sleep 2
    if [ "$i" -eq 30 ]; then
        warn "健康检查超时，请手动查看: ${COMPOSE_CMD} logs -f ${CONTAINER_NAME}"
    fi
done

echo ""
echo "==============================================="
info "部署完成！"
[ "$RESTART_MODE" == "restart" ] && info "(增量模式: 仅同步代码 + 重启)"
[ "$RESTART_MODE" == "recreate" ] && info "(全新模式: 完整构建 + 启动)"
echo "  - 前端访问:  http://<服务器IP>"
echo "  - 后端 API:  http://<服务器IP>:5001"
echo "  - Neo4j:    http://<服务器IP>:7474"
echo "  - Supervisor: http://<服务器IP>:9001 (admin/admin)"
echo ""
echo "常用命令:"
echo "  查看日志:   ${COMPOSE_CMD} logs -f ${CONTAINER_NAME}"
echo "  停止服务:   ${COMPOSE_CMD} down"
echo "  重启服务:   docker restart ${CONTAINER_NAME}"
echo "  强制重建:   bash deploy.sh --force-rebuild"
echo "==============================================="
