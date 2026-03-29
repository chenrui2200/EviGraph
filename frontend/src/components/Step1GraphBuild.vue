<template>
  <div class="workbench-panel">
    <div class="scroll-container">
      <!-- Step 01: Graph Build -->
      <div class="step-card" :class="{ 'active': currentPhase === 0, 'completed': currentPhase > 0 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">01</span>
            <span class="step-title">知识图谱构建 (GraphRAG)</span>
          </div>
          <div class="step-status">
            <span v-if="currentPhase > 0" class="badge success">已完成</span>
            <div v-else-if="currentPhase === 0 && buildProgress" class="status-with-action">
              <span class="badge processing">{{ buildProgress?.progress || 0 }}%</span>
            </div>
            <div v-else class="badge pending">等待中</div>
          </div>
        </div>

        <!-- 重置按钮 -->
        <div v-if="currentPhase > 0 || buildProgress" class="reset-action-bar">
          <button class="reset-btn" @click="handleReset" title="重置并重新构建">
            ↻ 重置
          </button>
        </div>

        <div class="card-content">
          <p class="description">
            基于多层级分块结果，系统会自动对文档进行切片，并调用 Neo4j 构建知识图谱，提取实体和关系，形成记忆摘要。
          </p>

          <!-- 启动构建按钮 -->
          <button
            v-if="currentPhase === 0 && !buildProgress"
            class="action-btn start-build-btn"
            @click="emit('start-build')"
          >
            ▶ 开始构建图谱
          </button>

          <!-- Stats Cards -->
          <div class="stats-grid">
            <div class="stat-card">
              <span class="stat-value">{{ graphStats.nodes }}</span>
              <span class="stat-label">实体节点</span>
            </div>
            <div class="stat-card">
              <span class="stat-value">{{ graphStats.edges }}</span>
              <span class="stat-label">关系事实</span>
            </div>
            <div class="stat-card">
              <span class="stat-value">{{ graphStats.types }}</span>
              <span class="stat-label">图谱 Schema 类型</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 02: Knowledge Recall Hit Test -->
      <div class="step-card" :class="{ 'active': currentPhase === 1, 'completed': currentPhase >= 1 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">02</span>
            <span class="step-title">知识召回命中测试 (Hit Test)</span>
          </div>
          <div class="step-status">
            <span v-if="currentPhase >= 1" class="badge accent">就绪</span>
            <span v-else class="badge pending">等待中</span>
          </div>
        </div>

        <div class="card-content">
          <p class="api-note">测试 GraphRAG 检索准确性</p>
          <p class="description">图谱构建已完成。建议通过视觉化的命中测试验证知识库的召回能力和关联结构。</p>

          <button
            v-if="currentPhase >= 1"
            class="action-btn hit-test-btn"
            @click="router.push({ name: 'HitTest', params: { projectId: projectData.project_id } })"
          >
            进入命中测试面板 🔍
          </button>

          <button
            v-if="currentPhase >= 1"
            class="action-btn"
            style="background: #fff; border: 1px solid #000; color: #000;"
            @click="router.push({ name: 'AiQa', params: { id: projectData.project_id } })"
          >
            直接创建 AI 应用 ➝
          </button>
        </div>
      </div>
    </div>

    <!-- Bottom Info / Logs -->
    <div class="system-logs">
      <div class="log-header">
        <span class="log-title">系统看板</span>
        <span class="log-id">{{ projectData?.project_id || '未关联项目' }}</span>
      </div>
      <div class="log-content" ref="logContent">
        <div class="log-line" v-for="(log, idx) in systemLogs" :key="idx">
          <span class="log-time">{{ log.time }}</span>
          <span class="log-msg">{{ log.msg }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const props = defineProps({
  currentPhase: { type: Number, default: 0 },
  projectData: Object,
  buildProgress: Object,
  graphData: Object,
  systemLogs: { type: Array, default: () => [] }
})

const emit = defineEmits(['next-step', 'reset-build', 'start-build'])

const handleReset = () => {
  if (confirm('确定要重置"知识图谱构建"并重新开始吗？')) {
    emit('reset-build')
  }
}

const logContent = ref(null)
const creatingSimulation = ref(false)

const graphStats = computed(() => {
  const nodes = props.graphData?.node_count || props.graphData?.nodes?.length || 0
  const edges = props.graphData?.edge_count || props.graphData?.edges?.length || 0
  const types = props.projectData?.ontology?.entity_types?.length || 0
  return { nodes, edges, types }
})

const formatDate = (dateStr) => {
  if (!dateStr) return '--:--:--'
  const d = new Date(dateStr)
  return d.toLocaleTimeString('en-US', { hour12: false }) + '.' + d.getMilliseconds()
}

// Auto-scroll logs section
watch(() => props.systemLogs.length, () => {
  nextTick(() => {
    if (logContent.value) {
      logContent.value.scrollTop = logContent.value.scrollHeight
    }
  })
})
</script>

<style scoped>
.workbench-panel {
  height: 100%;
  background-color: #FAFAFA;
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}

.scroll-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.step-card {
  background: #FFF;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  border: 1px solid #EAEAEA;
  transition: all 0.3s ease;
  position: relative; /* For absolute overlay */
}

.step-card.active {
  border-color: #FF5722;
  box-shadow: 0 4px 12px rgba(255, 87, 34, 0.08);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.step-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-num {
  font-family: 'JetBrains Mono', monospace;
  font-size: 20px;
  font-weight: 700;
  color: #E0E0E0;
}

.step-card.active .step-num,
.step-card.completed .step-num {
  color: #000;
}

.step-title {
  font-weight: 600;
  font-size: 14px;
  letter-spacing: 0.5px;
}

.badge {
  font-size: 10px;
  padding: 4px 8px;
  border-radius: 4px;
  font-weight: 600;
  text-transform: uppercase;
}

.badge.success { background: #E8F5E9; color: #2E7D32; }
.badge.processing { background: #FF5722; color: #FFF; }
.badge.accent { background: #FF5722; color: #FFF; }
.badge.pending { background: #F5F5F5; color: #999; }

.status-with-action {
  display: flex;
  align-items: center;
  gap: 8px;
}

.reset-action-bar {
  padding: 8px 0;
  border-top: 1px dashed #EEE;
  margin-top: 12px;
}

.reset-btn {
  background: #FFF;
  border: 1px solid #FF5722;
  color: #FF5722;
  font-size: 12px;
  padding: 6px 16px;
  border-radius: 4px;
  cursor: pointer;
  font-weight: 600;
  transition: all 0.2s;
}

.reset-btn:hover {
  background: #FF5722;
  color: #FFF;
}

.api-note {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #999;
  margin-bottom: 8px;
}

.description {
  font-size: 12px;
  color: #666;
  line-height: 1.5;
  margin-bottom: 16px;
}

/* Step 01 Stats */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 12px;
  background: #F9F9F9;
  padding: 16px;
  border-radius: 6px;
}

.stat-card {
  text-align: center;
}

.stat-value {
  display: block;
  font-size: 20px;
  font-weight: 700;
  color: #000;
  font-family: 'JetBrains Mono', monospace;
}

.stat-label {
  font-size: 9px;
  color: #999;
  text-transform: uppercase;
  margin-top: 4px;
  display: block;
}

/* Step 02 Button */
.action-btn {
  width: 100%;
  background: #000;
  color: #FFF;
  border: none;
  padding: 14px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s;
  margin-bottom: 12px;
}

.action-btn:hover:not(:disabled) {
  opacity: 0.8;
}

.action-btn:disabled {
  background: #CCC;
  cursor: not-allowed;
}

.start-build-btn {
  background: #FF5722;
}

.progress-section {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #FF5722;
  margin-bottom: 12px;
}

.spinner-sm {
  width: 14px;
  height: 14px;
  border: 2px solid #FFCCBC;
  border-top-color: #FF5722;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

/* System Logs */
.system-logs {
  background: #000;
  color: #DDD;
  padding: 16px;
  font-family: 'JetBrains Mono', monospace;
  border-top: 1px solid #222;
  flex-shrink: 0;
}

.log-header {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid #333;
  padding-bottom: 8px;
  margin-bottom: 8px;
  font-size: 10px;
  color: #888;
}

.log-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  height: 80px; /* Approx 4 lines visible */
  overflow-y: auto;
  padding-right: 4px;
}

.log-content::-webkit-scrollbar {
  width: 4px;
}

.log-content::-webkit-scrollbar-thumb {
  background: #333;
  border-radius: 2px;
}

.log-line {
  font-size: 11px;
  display: flex;
  gap: 12px;
  line-height: 1.5;
}

.log-time {
  color: #666;
  min-width: 75px;
}

.log-msg {
  color: #CCC;
  word-break: break-all;
}
</style>
