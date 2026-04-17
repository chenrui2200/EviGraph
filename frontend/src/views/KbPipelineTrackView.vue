<template>
  <div class="kb-pipeline-track">
    <header class="kpt-header">
      <div class="header-left">
        <div class="brand" @click="router.push('/')">Knowledge EviGraph</div>
      </div>
      <div class="header-right">
        <span class="page-title">KB Pipeline 追踪</span>
        <button class="btn-back" @click="router.push({ name: 'KbPipelineLaunch' })">返回启动台</button>
      </div>
    </header>

    <main class="kpt-main">
      <div class="kpt-summary">
        <div class="summary-id">Pipeline: {{ pipelineId }}</div>
        <div class="summary-status" :class="'status-' + pipelineStatus">
          <span class="dot"></span>
          {{ statusText }}
        </div>
      </div>

      <div class="stages-track">
        <div
          v-for="(stage, idx) in stages"
          :key="stage.name"
          class="stage-wrapper"
        >
          <div class="stage-card" :class="'stage-' + stage.status">
            <div class="stage-num">{{ idx + 1 }}</div>
            <div class="stage-body">
              <div class="stage-label">{{ stage.label }}</div>
              <div class="stage-message">{{ stage.message || statusMessage(stage.status) }}</div>
              <a
                v-if="stage.link && (stage.status === 'completed' || stage.status === 'processing')"
                class="stage-link"
                @click.prevent="openLink(stage.link)"
                href="javascript:;"
              >
                查看 →
              </a>
            </div>
            <div class="stage-icon">{{ stageIcon(stage.status) }}</div>

            <!-- Stage Logs -->
            <div
              v-if="stage.result?.task_id"
              class="stage-logs"
              :class="{ expanded: stageLogMap[stage.name]?.expanded }"
            >
              <div class="logs-header" @click="toggleLogs(stage.name)">
                <span>实时日志</span>
                <span class="logs-toggle">{{ stageLogMap[stage.name]?.expanded ? '▲' : '▼' }}</span>
              </div>
              <div v-show="stageLogMap[stage.name]?.expanded" class="logs-body">
                <div v-if="!stageLogMap[stage.name]?.logs?.length" class="logs-empty">暂无日志</div>
                <div v-else class="log-list">
                  <div
                    v-for="(log, i) in stageLogMap[stage.name].logs"
                    :key="i"
                    class="log-line"
                  >{{ log }}</div>
                </div>
              </div>
            </div>
          </div>
          <div v-if="idx < stages.length - 1" class="stage-connector">
            <div class="connector-line" :class="{ active: connectorActive(idx) }"></div>
            <div class="connector-arrow">▶</div>
          </div>
        </div>
      </div>

      <div class="kpt-footer">
        <div v-if="pipelineStatus === 'completed'" class="result-box">
          <div class="result-title">🎉 流水线执行完成</div>
          <div class="result-row">
            <span>项目 ID: <code>{{ pipeline?.project_id }}</code></span>
            <span>图谱 ID: <code>{{ pipeline?.graph_id }}</code></span>
            <span>应用 ID: <code>{{ pipeline?.app_id }}</code></span>
          </div>
          <div class="result-actions">
            <button class="btn-primary" @click="goApp" v-if="pipeline?.app_id">打开应用</button>
            <button class="btn-secondary" @click="goProject" v-if="pipeline?.project_id">查看项目</button>
          </div>
        </div>
        <div v-else-if="pipelineStatus === 'failed'" class="result-box error">
          <div class="result-title">❌ 流水线执行失败</div>
          <div class="error-message">{{ pipeline?.error || '未知错误' }}</div>
        </div>
        <div v-else class="result-box processing">
          <div class="result-title">⏳ 流水线执行中，请稍候...</div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getKbPipeline, getKbPipelineEventsURL, getTaskStatus, getTaskEventsURL, buildGraph } from '../api/graph'

const router = useRouter()
const route = useRoute()
const pipelineId = route.params.pipelineId
const pipeline = ref(null)
const stages = ref([])
let eventSource = null

// stageName -> { logs: string[], expanded: boolean, taskId: string|null, source: EventSource|null }
const stageLogMap = ref({})

const pipelineStatus = computed(() => pipeline.value?.status || 'pending')

const statusText = computed(() => {
  const map = {
    pending: '等待中',
    processing: '执行中',
    completed: '已完成',
    failed: '失败',
    skipped: '已跳过'
  }
  return map[pipelineStatus.value] || pipelineStatus.value
})

const statusMessage = (status) => {
  const map = {
    pending: '等待开始',
    processing: '正在处理',
    completed: '完成',
    failed: '失败',
    skipped: '已跳过'
  }
  return map[status] || status
}

const stageIcon = (status) => {
  const map = {
    pending: '◯',
    processing: '⏳',
    completed: '✓',
    failed: '✕',
    skipped: '−'
  }
  return map[status] || '◯'
}

const connectorActive = (idx) => {
  const current = stages.value[idx]?.status
  return current === 'completed' || current === 'processing'
}

const openLink = (link) => {
  if (link) window.open(link, '_blank')
}

const goApp = () => {
  if (pipeline.value?.app_id) {
    router.push({ name: 'AiQa', params: { id: pipeline.value.app_id } })
  }
}

const goProject = () => {
  if (pipeline.value?.project_id) {
    router.push({ name: 'ChunkAnalysis', params: { projectId: pipeline.value.project_id } })
  }
}

const toggleLogs = (stageName) => {
  ensureStageLog(stageName)
  stageLogMap.value[stageName].expanded = !stageLogMap.value[stageName].expanded
}

function ensureStageLog(stageName) {
  if (!stageLogMap.value[stageName]) {
    stageLogMap.value[stageName] = {
      logs: [],
      expanded: false,
      taskId: null,
      source: null
    }
  }
}

function closeTaskSSE(stageName) {
  const entry = stageLogMap.value[stageName]
  if (entry && entry.source) {
    entry.source.close()
    entry.source = null
  }
}

async function fetchTaskLogs(stageName, taskId) {
  ensureStageLog(stageName)
  try {
    const res = await getTaskStatus(taskId)
    if (res.success && res.data?.logs?.length) {
      stageLogMap.value[stageName].logs = res.data.logs.map(l => l.message || l)
    }
  } catch (e) {
    console.error('fetch task logs failed:', e)
  }
}

function openTaskSSE(stageName, taskId) {
  ensureStageLog(stageName)
  const entry = stageLogMap.value[stageName]
  if (entry.source) return
  entry.taskId = taskId

  fetchTaskLogs(stageName, taskId).then(() => {
    const url = getTaskEventsURL(taskId)
    const source = new EventSource(url)
    entry.source = source

    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data)
        if (payload.type === 'init') {
          const logs = payload.data?.logs || []
          entry.logs = logs.map(l => l.message || l)
        } else if (payload.type === 'update') {
          const data = payload.data
          if (data.logs?.length) {
            entry.logs = data.logs.map(l => l.message || l)
          }
          if (data.log) {
            entry.logs.push(data.log)
          }
        }
      } catch (e) {
        // ignore
      }
    }

    source.onerror = () => {
      source.close()
      entry.source = null
      setTimeout(() => {
        const stage = stages.value.find(s => s.name === stageName)
        if (stage && stage.status === 'processing' && entry.taskId) {
          openTaskSSE(stageName, entry.taskId)
        }
      }, 5000)
    }
  })
}

function syncTaskSSEs() {
  stages.value.forEach(stage => {
    const taskId = stage.result?.task_id
    if (!taskId) return
    ensureStageLog(stage.name)
    const entry = stageLogMap.value[stage.name]
    entry.taskId = taskId

    if (stage.status === 'processing') {
      entry.expanded = true
      if (!entry.source) {
        openTaskSSE(stage.name, taskId)
      }
    } else if (stage.status === 'completed' || stage.status === 'failed') {
      closeTaskSSE(stage.name)
      entry.expanded = true
      if (entry.logs.length === 0) {
        fetchTaskLogs(stage.name, taskId)
      }
    }
  })
}

const updateFromData = (data) => {
  pipeline.value = data
  stages.value = data.stages || []
  syncTaskSSEs()
}

const fetchPipeline = async () => {
  try {
    const res = await getKbPipeline(pipelineId)
    if (res.success) {
      updateFromData(res.data)
    }
  } catch (e) {
    console.error('获取 Pipeline 失败:', e)
  }
}

const startSSE = () => {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  const url = getKbPipelineEventsURL(pipelineId)
  eventSource = new EventSource(url)
  eventSource.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data)
      if (payload.type === 'init' || payload.type === 'update') {
        updateFromData(payload.data)
      }
      if (payload.type === 'error') {
        console.error('Pipeline SSE error:', payload.message)
      }
    } catch (e) {
      // ignore parse error
    }
  }
  eventSource.onerror = () => {
    eventSource.close()
    eventSource = null
    const interval = setInterval(() => {
      if (pipelineStatus.value === 'completed' || pipelineStatus.value === 'failed') {
        clearInterval(interval)
        return
      }
      fetchPipeline()
    }, 5000)
  }
}

onMounted(() => {
  fetchPipeline().then(() => {
    startSSE()
    stages.value.forEach(stage => {
      const taskId = stage.result?.task_id
      if (taskId) {
        ensureStageLog(stage.name)
        fetchTaskLogs(stage.name, taskId).then(() => {
          if (stage.status === 'processing') {
            stageLogMap.value[stage.name].expanded = true
            openTaskSSE(stage.name, taskId)
          }
        })
      }
    })
  })
})

onUnmounted(() => {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  Object.keys(stageLogMap.value).forEach(name => closeTaskSSE(name))
})
</script>

<style scoped>
.kb-pipeline-track {
  min-height: 100vh;
  background: #fafafa;
}
.kpt-header {
  height: 60px;
  background: #000;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 40px;
}
.header-left .brand {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 1.1rem;
  cursor: pointer;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 20px;
}
.page-title {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.9rem;
  opacity: 0.9;
}
.btn-back {
  background: transparent;
  color: #fff;
  border: 1px solid #666;
  padding: 6px 14px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  cursor: pointer;
}
.kpt-main {
  max-width: 1200px;
  margin: 0 auto;
  padding: 40px 20px;
}
.kpt-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border: 1px solid #e5e5e5;
  padding: 20px 30px;
  margin-bottom: 30px;
}
.summary-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.9rem;
  color: #666;
}
.summary-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}
.summary-status .dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #999;
}
.status-pending .dot { background: #999; }
.status-processing .dot { background: #ff4500; animation: pulse 1.5s infinite; }
.status-completed .dot { background: #00c853; }
.status-failed .dot { background: #ff1744; }

@keyframes pulse {
  0% { opacity: 1; }
  50% { opacity: 0.4; }
  100% { opacity: 1; }
}

.stages-track {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  margin-bottom: 40px;
}
.stage-wrapper {
  display: flex;
  align-items: stretch;
  flex: 1 1 auto;
  min-width: 280px;
  margin-bottom: 30px;
  max-width: 33.33%;
}
.stage-card {
  flex: 1;
  background: #fff;
  border: 1px solid #e5e5e5;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: relative;
}
.stage-card.stage-processing {
  border-color: #ff4500;
  box-shadow: 0 0 0 2px rgba(255, 69, 0, 0.1);
}
.stage-card.stage-completed {
  border-color: #00c853;
}
.stage-card.stage-failed {
  border-color: #ff1744;
}
.stage-num {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: #999;
}
.stage-label {
  font-weight: 600;
  font-size: 1rem;
}
.stage-message {
  font-size: 0.8rem;
  color: #666;
  min-height: 1.2em;
}
.stage-link {
  font-size: 0.8rem;
  color: #ff4500;
  text-decoration: none;
  margin-top: 4px;
}
.stage-link:hover {
  text-decoration: underline;
}
.stage-icon {
  position: absolute;
  top: 16px;
  right: 16px;
  font-size: 1.2rem;
}
.stage-connector {
  display: flex;
  align-items: center;
  color: #ccc;
  padding: 0 8px;
}
.connector-line {
  width: 20px;
  height: 2px;
  background: #e5e5e5;
}
.connector-line.active {
  background: #00c853;
}
.connector-arrow {
  font-size: 0.6rem;
  margin-left: 2px;
}

/* Stage Logs */
.stage-logs {
  margin-top: 8px;
  background: #f8f9fa;
  border: 1px solid #e5e5e5;
  border-radius: 4px;
  overflow: hidden;
}
.logs-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  font-size: 0.8rem;
  color: #666;
  cursor: pointer;
  background: #f0f0f0;
  user-select: none;
}
.logs-toggle {
  font-size: 0.7rem;
  color: #999;
}
.logs-body {
  max-height: 200px;
  overflow-y: auto;
  padding: 10px 12px;
}
.log-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.log-line {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: #444;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  padding: 3px 0;
  border-bottom: 1px solid #f0f0f0;
}
.log-line:last-child {
  border-bottom: none;
}
.logs-empty {
  font-size: 0.8rem;
  color: #999;
  padding: 10px 0;
}

.kpt-footer {
  background: #fff;
  border: 1px solid #e5e5e5;
  padding: 30px;
}
.result-title {
  font-size: 1.2rem;
  font-weight: 600;
  margin-bottom: 15px;
}
.result-row {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  margin-bottom: 20px;
  font-size: 0.9rem;
  color: #444;
}
.result-row code {
  background: #f4f4f4;
  padding: 2px 6px;
  font-family: 'JetBrains Mono', monospace;
}
.result-actions {
  display: flex;
  gap: 12px;
}
.btn-primary {
  background: #ff4500;
  color: #fff;
  border: none;
  padding: 10px 20px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  cursor: pointer;
}
.btn-secondary {
  background: #fff;
  color: #000;
  border: 1px solid #000;
  padding: 10px 20px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  cursor: pointer;
}
.error-message {
  color: #ff1744;
  font-size: 0.9rem;
  background: #fff0f2;
  padding: 12px;
}
.processing .result-title {
  color: #ff4500;
}

@media (max-width: 900px) {
  .stages-track {
    flex-direction: column;
  }
  .stage-wrapper {
    width: 100%;
    max-width: 100%;
  }
  .stage-connector {
    display: none;
  }
}
</style>
