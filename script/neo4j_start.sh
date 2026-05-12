#!/bin/bash
# neo4j-deploy.sh - Neo4j latest 部署脚本（去重修复版）
set -e

# ===== 配置区域 =====
NEO4J_VERSION="latest"
CONTAINER_NAME="neo4j-prod"
HOST_BASE="/opt/neo4j"
NEO4J_AUTH="neo4j/YourStrongPassword123!"
HEAP_MEM="2g"
PAGECACHE_MEM="4g"

# ===== 1. 清理旧容器 =====
echo "🛑 检查并清理旧容器..."
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  docker stop "${CONTAINER_NAME}" 2>/dev/null || true
  docker rm "${CONTAINER_NAME}" 2>/dev/null || true
  echo "✅ 旧容器已清理"
fi

# ===== 2. 创建目录 =====
echo "📁 创建数据目录..."
mkdir -p "${HOST_BASE}"/{data,logs,import,plugins,conf}
chown -R 7474:7474 "${HOST_BASE}" 2>/dev/null || true

# ===== 3. 安全处理配置文件（核心修复）=====
CONF_FILE="${HOST_BASE}/conf/neo4j.conf"
echo "⚙️  处理 neo4j.conf..."

if [ -f "${CONF_FILE}" ]; then
  # 备份旧配置
  cp "${CONF_FILE}" "${CONF_FILE}.bak.$(date +%s)"

  # 删除脚本托管的配置行（防重复追加）
  sed -i '/^dbms\.memory\.\(heap\.initial_size\|heap\.max_size\|pagecache\.size\)=/d' "${CONF_FILE}"
  sed -i '/^dbms\.default_listen_address=/d' "${CONF_FILE}"
  sed -i '/^dbms\.logs\.http\.enabled=/d' "${CONF_FILE}"
  sed -i '/^# ===== 自动托管配置 =====/d' "${CONF_FILE}"

  # 清理 Windows 换行符 & 空行
  sed -i 's/\r$//' "${CONF_FILE}"
  sed -i '/^[[:space:]]*$/d' "${CONF_FILE}"
fi

# 追加唯一且正确的配置
cat >> "${CONF_FILE}" << EOF
# ===== 自动托管配置 =====
dbms.default_listen_address=0.0.0.0
dbms.memory.heap.initial_size=${HEAP_MEM}
dbms.memory.heap.max_size=${HEAP_MEM}
dbms.memory.pagecache.size=${PAGECACHE_MEM}
dbms.logs.http.enabled=true
EOF

# ===== 4. 启动容器 =====
echo "🚀 启动 Neo4j ${NEO4J_VERSION}..."

docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart=always \
  --publish=7474:7474 \
  --publish=7687:7687 \
  --volume="${HOST_BASE}/data:/data" \
  --volume="${HOST_BASE}/logs:/logs" \
  --volume="${HOST_BASE}/import:/import" \
  --volume="${HOST_BASE}/plugins:/plugins" \
  --volume="${HOST_BASE}/conf:/var/lib/neo4j/conf" \
  --env="NEO4J_AUTH=${NEO4J_AUTH}" \
  --env="NEO4J_dbms_memory_heap_initial__size=${HEAP_MEM}" \
  --env="NEO4J_dbms_memory_heap_max__size=${HEAP_MEM}" \
  --env="NEO4J_dbms_memory_pagecache__size=${PAGECACHE_MEM}" \
  --memory="8g" \
  --cpus="2" \
  --health-cmd="curl -f http://localhost:7474 || exit 1" \
  --health-interval=30s \
  --health-retries=5 \
  --health-start-period=60s \
  "neo4j:${NEO4J_VERSION}"

# ===== 5. 验证 =====
echo "⏳ 等待 Neo4j 初始化..."
sleep 20

if docker ps --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
  echo ""
  echo "✅ Neo4j 启动成功！"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🌐 Browser: http://localhost:7474"
  echo "🔌 Bolt:    bolt://localhost:7687"
  echo "👤 账号:    neo4j"
  echo "🔑 密码:    ${NEO4J_AUTH#neo4j/}"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
  echo "❌ 启动失败，查看日志："
  docker logs "${CONTAINER_NAME}" --tail 50
  exit 1
fi