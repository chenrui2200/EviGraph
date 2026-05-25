<template>
  <div class="kb-pipeline-track">
    <header class="kpt-header">
      <div class="header-left">
        <div class="brand" @click="router.push('/')">
          <span class="brand-icon">◈</span>
          <span>Knowledge EviGraph</span>
        </div>
      </div>
      <div class="header-right">
        <span class="page-title">KB Pipeline 追踪</span>
        <button class="btn-back" @click="router.push({ name: 'KbPipelineLaunch' })">
          <span class="back-arrow">←</span>
          <span>返回启动台</span>
        </button>
      </div>
    </header>

    <main class="kpt-main">
      <!-- Summary Card -->
      <div class="kpt-summary">
        <div class="summary-id">
          <span class="id-label">Pipeline</span>
          <span class="id-value">{{ pipelineId }}</span>
        </div>
        <div class="summary-status" :class="'status-' + getBaseStatus(pipelineStatus.value)">
          <span class="status-dot"></span>
          <span class="status-text">{{ statusText }}</span>
        </div>
      </div>

      <!-- Stages Track -->
      <div class="stages-track">
        <div
          v-for="(stage, idx) in stages"
          :key="stage.name"
          class="stage-wrapper"
        >
          <div class="stage-card" :class="'stage-' + getBaseStatus(stage.status)">
            <div class="stage-header">
              <div class="stage-num">{{ idx + 1 }}</div>
              <div class="stage-icon">{{ stageIcon(stage.status) }}</div>
            </div>
            <div class="stage-body">
              <div class="stage-label">{{ stage.label }}</div>
              <div class="stage-message">{{ getStageDisplayMessage(stage) }}</div>

              <a
                v-if="stage.link && (getBaseStatus(stage.status) === 'completed' || getBaseStatus(stage.status) === 'processing')"
                class="stage-link"
                @click.prevent="openLink(stage.link)"
                href="javascript:;"
              >
                查看详情 →
              </a>
              <div
                v-if="stage.name === 'project_creation' && getBaseStatus(stage.status) === 'completed' && stage.result"
                class="project-info"
              >
                <div class="info-row">
                  <span class="info-label">项目名称</span>
                  <span class="info-value">{{ stage.result.project_name || '-' }}</span>
                </div>
                <div class="info-row">
                  <span class="info-label">项目 ID</span>
                  <code class="info-code">{{ stage.result.project_id || '-' }}</code>
                </div>
              </div>
              <br/>
              <button
                v-if="stage.name === 'graph_building' && getBaseStatus(stage.status) !== 'processing'"
                class="stage-reset"
                @click="resetGraphBuilding(stage)"
              >
                重置构建
              </button>

              <!-- App Creation / Attach Actions -->
              <div v-if="stage.name === 'app_creation' && getBaseStatus(stage.status) === 'completed' && pipeline?.app_id" class="app-actions">
                <!-- attach 模式：只显示打开应用 -->
                <div v-if="stage.result?.mode === 'attach'" class="attach-info">
                  <div class="attach-badge">
                    <span class="attach-icon">🔗</span>
                    <span>已关联到现有应用</span>
                  </div>
                  <button class="btn-aiqa" @click="goApp">
                    打开应用 →
                  </button>
                </div>
                <!-- create 模式：显示发布按钮 -->
                <template v-else>
                  <button
                    v-if="!publishedMap[pipeline.app_id]"
                    class="btn-publish"
                    :disabled="publishLoading[pipeline.app_id]"
                    @click="handlePublish(pipeline.app_id)"
                  >
                    <span v-if="publishLoading[pipeline.app_id]">发布中...</span>
                    <span v-else>🚀 发布 Web 访问</span>
                  </button>
                  <div v-else class="published-card">
                    <div class="published-header">
                      <div class="published-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                          <polyline points="20 6 9 17 4 12"></polyline>
                        </svg>
                      </div>
                      <div class="published-info">
                        <div class="published-title">应用已发布</div>
                        <div class="published-desc">任何人都可以通过下方链接访问</div>
                      </div>
                    </div>
                    <div class="public-url-box">
                      <span class="url-text">{{ getPublicUrl(pipeline.app_id) }}</span>
                      <button class="url-btn" @click="copyUrl(pipeline.app_id)" title="复制链接">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                        </svg>
                      </button>
                      <a class="url-btn open" :href="getPublicUrl(pipeline.app_id)" target="_blank" title="在新窗口打开">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                          <polyline points="15 3 21 3 21 9"></polyline>
                          <line x1="10" y1="14" x2="21" y2="3"></line>
                        </svg>
                      </a>
                    </div>
                  </div>
                </template>
                <!-- 重新关联按钮（attach / create 模式均显示）-->
                <button
                  class="stage-reset"
                  style="margin-top: 8px;"
                  :disabled="reassociateLoading"
                  @click="handleReassociate"
                >
                  <span v-if="reassociateLoading">关联中...</span>
                  <span v-else>🔄 重新关联</span>
                </button>
              </div>
            </div>

            <!-- Stage Logs -->
            <div
              v-if="stage.result?.task_id"
              class="stage-logs"
              :class="{ expanded: stageLogMap[stage.name]?.expanded }"
            >
              <div class="logs-header" @click="toggleLogs(stage.name)">
                <span class="logs-title">
                  <span class="logs-icon">📋</span>
                  实时日志
                </span>
                <span class="logs-toggle">{{ stageLogMap[stage.name]?.expanded ? '收起' : '展开' }}</span>
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
            <!-- 执行耗时：卡片右下角 -->
            <div v-if="stage.duration_ms" class="stage-duration">{{ formatDuration(stage.duration_ms) }}</div>
          </div>

          <div v-if="idx < stages.length - 1" class="stage-connector">
            <div class="connector-line" :class="{ active: connectorActive(idx) }"></div>
            <div class="connector-arrow">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 18 15 12 9 6"></polyline>
              </svg>
            </div>
          </div>
        </div>
      </div>

      <!-- Footer Result -->
      <div class="kpt-footer">
        <div v-if="pipelineStatus === 'total_completed'" class="result-box result-success">
          <div class="result-icon">🎉</div>
          <div class="result-title">流水线执行完成</div>
          <div class="result-desc">您的知识库与图谱已成功构建</div>
          <div class="result-row">
            <div class="result-item">
              <span class="item-label">项目 ID</span>
              <code>{{ pipeline?.project_id }}</code>
            </div>
            <div class="result-item">
              <span class="item-label">应用 ID</span>
              <code>{{ pipeline?.app_id }}</code>
            </div>
          </div>
          <div class="result-actions">
            <button class="btn-primary" @click="goApp" v-if="pipeline?.app_id">
              <span>打开应用</span>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 18 15 12 9 6"></polyline>
              </svg>
            </button>
            <button class="btn-secondary" @click="goProject" v-if="pipeline?.project_id">
              查看项目
            </button>
          </div>
        </div>

        <div v-else-if="getBaseStatus(pipelineStatus.value) === 'failed'" class="result-box result-error">
          <div class="result-icon">❌</div>
          <div class="result-title">流水线执行失败</div>
          <div class="error-message">{{ pipeline?.error || '未知错误，请检查后端日志或重试' }}</div>
        </div>

        <div v-else class="result-box result-processing">
          <div class="processing-ring">
            <div class="processing-spinner"></div>
          </div>
          <div class="result-title">流水线执行中</div>
          <div class="result-desc">请稍候，各阶段任务正在自动处理...</div>
        </div>
      </div>
    </main>

    <div class="bg-blobs">
      <div class="blob blob-1"></div>
      <div class="blob blob-2"></div>
      <div class="blob blob-3"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getKbPipeline, getKbPipelineEventsURL, getTaskStatus, getTaskEventsURL, retryGraphBuilding } from '../api/graph'
import { publishApp, getApp, addProjectToApp, getAppList } from '../api/ai_app'

const router = useRouter()
const route = useRoute()
const pipelineId = route.params.pipelineId
const pipeline = ref(null)
const stages = ref([])
let eventSource = null

// stageName -> { logs: string[], expanded: boolean, taskId: string|null, source: EventSource|null }
const stageLogMap = ref({})

const pipelineStatus = computed(() => pipeline.value?.status || 'pending')

// 从细分状态提取通用状态后缀
const getBaseStatus = (status) => {
  if (!status) return 'pending'
  if (status === 'pending' || status === 'total_completed') return status
  if (status.endsWith('_processing')) return 'processing'
  if (status.endsWith('_completed')) return 'completed'
  if (status.endsWith('_failed')) return 'failed'
  return 'pending'
}

const statusText = computed(() => {
  const base = getBaseStatus(pipelineStatus.value)
  const map = {
    pending: '等待中',
    processing: '执行中',
    completed: '已完成',
    failed: '失败',
    skipped: '已跳过',
    total_completed: '全部完成'
  }
  return map[base] || pipelineStatus.value
})

const statusMessage = (status) => {
  const base = getBaseStatus(status)
  const map = {
    pending: '等待开始',
    processing: '正在处理',
    completed: '完成',
    failed: '失败',
    skipped: '已跳过'
  }
  return map[base] || status
}

const formatDuration = (ms) => {
  if (!ms || ms < 0) return ''
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes > 0) {
    return `${minutes}分${seconds}秒`
  }
  return `${seconds}秒`
}

const getStageDisplayMessage = (stage) => {
  if (stage.name === 'chapter_analysis' && getBaseStatus(stage.status) === 'completed' && stage.result?.reason) {
    return stage.result.reason
  }
  return stage.message || statusMessage(stage.status)
}

const stageIcon = (status) => {
  const base = getBaseStatus(status)
  const map = {
    pending: '◯',
    processing: '⏳',
    completed: '✓',
    failed: '✕',
    skipped: '−'
  }
  return map[base] || '◯'
}

const connectorActive = (idx) => {
  const base = getBaseStatus(stages.value[idx]?.status)
  return base === 'completed' || base === 'processing'
}

const openLink = (link) => {
  if (link) window.open(link, '_blank')
}

const resetGraphBuilding = async (stage) => {
  try {
    const res = await retryGraphBuilding(pipelineId)
    if (res.success) {
      updateFromData(res.data)
      const newTaskId = stage.result?.task_id
      if (newTaskId) {
        closeTaskSSE(stage.name)
        ensureStageLog(stage.name)
        stageLogMap.value[stage.name].logs = []
        stageLogMap.value[stage.name].expanded = true
        openTaskSSE(stage.name, newTaskId)
      }
    }
  } catch (e) {
    console.error('重置图谱构建失败:', e)
    alert('重置失败: ' + (e.message || '未知错误'))
  }
}

const publishedMap = ref({})
const publishLoading = ref({})

const loadAppPublishStatus = async (appId) => {
  if (!appId || publishedMap.value[appId] !== undefined) return
  try {
    const res = await getApp(appId)
    if (res.success) {
      publishedMap.value[appId] = !!res.data?.is_published
    }
  } catch (e) {
    console.error('获取应用发布状态失败:', e)
  }
}

const handlePublish = async (appId) => {
  if (!appId) return
  publishLoading.value[appId] = true
  try {
    const res = await publishApp(appId, true)
    if (res.success) {
      publishedMap.value[appId] = true
    } else {
      alert('发布失败: ' + (res.error || '未知错误'))
    }
  } catch (e) {
    console.error('发布应用失败:', e)
    alert('发布失败: ' + (e.message || '未知错误'))
  } finally {
    publishLoading.value[appId] = false
  }
}

const reassociateLoading = ref(false)

const handleReassociate = async () => {
  if (!pipeline.value?.project_id) {
    alert('当前 Pipeline 无项目信息')
    return
  }
  // 获取可用 App 列表
  let appList = []
  try {
    const res = await getAppList(100)
    if (res.success && res.data) {
      appList = res.data
    }
  } catch (e) {
    console.error('获取 App 列表失败:', e)
  }

  let targetAppId = ''
  if (appList.length > 0) {
    const currentId = pipeline.value.app_id
    const options = appList.map(a => {
      const mark = a.app_id === currentId ? ' [当前]' : ''
      return `${a.app_id}: ${a.name}${mark}`
    }).join('\n')
    targetAppId = prompt(
      `选择要关联的目标 App（输入 app_id）：\n\n可用应用：\n${options}\n\n或直接输入 app_id：`,
      currentId || ''
    )
  } else {
    targetAppId = prompt('输入要关联的目标 App ID：')
  }

  if (!targetAppId) return
  // 提取冒号前的 app_id
  targetAppId = targetAppId.split(':')[0].trim()

  reassociateLoading.value = true
  try {
    const res = await addProjectToApp(targetAppId, pipeline.value.project_id)
    if (res.success) {
      alert('重新关联成功')
      // 更新 pipeline 显示
      pipeline.value.app_id = targetAppId
      const appStage = stages.value.find(s => s.name === 'app_creation')
      if (appStage) {
        appStage.result = { app_id: targetAppId, app_name: res.data?.name, mode: 'attach' }
        appStage.message = `已关联到应用 ${res.data?.name || targetAppId}`
      }
      loadAppPublishStatus(targetAppId)
    } else {
      alert('重新关联失败: ' + (res.error || '未知错误'))
    }
  } catch (e) {
    console.error('重新关联失败:', e)
    alert('重新关联失败: ' + (e.message || '未知错误'))
  } finally {
    reassociateLoading.value = false
  }
}

const openAiQa = (appId) => {
  if (appId) {
    router.push({ name: 'AiQa', params: { id: appId } })
  }
}

const getPublicUrl = (appId) => {
  return `${window.location.origin}/chat/${appId}`
}

const copyUrl = async (appId) => {
  const url = getPublicUrl(appId)
  try {
    await navigator.clipboard.writeText(url)
    alert('链接已复制到剪贴板')
  } catch (e) {
    // fallback
    const input = document.createElement('input')
    input.value = url
    document.body.appendChild(input)
    input.select()
    document.execCommand('copy')
    document.body.removeChild(input)
    alert('链接已复制到剪贴板')
  }
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
        if (stage && getBaseStatus(stage.status) === 'processing' && entry.taskId) {
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

    const baseStatus = getBaseStatus(stage.status)
    if (baseStatus === 'processing') {
      entry.expanded = true
      if (!entry.source) {
        openTaskSSE(stage.name, taskId)
      }
    } else if (baseStatus === 'completed' || baseStatus === 'failed') {
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
  if (data.app_id) {
    loadAppPublishStatus(data.app_id)
  }
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
      if (pipelineStatus.value === 'total_completed' || getBaseStatus(pipelineStatus.value) === 'failed') {
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
          if (getBaseStatus(stage.status) === 'processing') {
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
  background: linear-gradient(135deg, #f5f7fa 0%, #e4e8f0 50%, #f0f4f8 100%);
  position: relative;
  overflow-x: hidden;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.bg-blobs {
  position: fixed;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
  z-index: 0;
}
.blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.45;
}
.blob-1 {
  width: 500px;
  height: 500px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  top: -150px;
  right: -150px;
  animation: float 8s ease-in-out infinite;
}
.blob-2 {
  width: 400px;
  height: 400px;
  background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
  bottom: -100px;
  left: -100px;
  animation: float 10s ease-in-out infinite reverse;
}
.blob-3 {
  width: 300px;
  height: 300px;
  background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  animation: float 12s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33% { transform: translate(30px, -30px) scale(1.05); }
  66% { transform: translate(-20px, 20px) scale(0.95); }
}

.kpt-header {
  position: relative;
  z-index: 10;
  height: 72px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.6);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 48px;
}
.header-left .brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 1.15rem;
  color: #1a1a2e;
  cursor: pointer;
  transition: opacity 0.2s;
}
.header-left .brand:hover { opacity: 0.8; }
.brand-icon {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  font-size: 1.3rem;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 24px;
}
.page-title {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.9rem;
  color: #4a5568;
  font-weight: 500;
  letter-spacing: 0.5px;
}
.btn-back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  color: #4a5568;
  border: 1px solid #cbd5e0;
  padding: 8px 16px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  border-radius: 10px;
  transition: all 0.2s;
}
.btn-back:hover {
  border-color: #667eea;
  color: #667eea;
  background: rgba(102, 126, 234, 0.06);
}
.back-arrow {
  font-size: 0.9rem;
}

.kpt-main {
  position: relative;
  z-index: 5;
  max-width: 1200px;
  margin: 0 auto;
  padding: 40px 24px 80px;
}

/* Summary Card */
.kpt-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow:
    0 4px 6px -1px rgba(0, 0, 0, 0.05),
    0 20px 40px -10px rgba(0, 0, 0, 0.08),
    inset 0 1px 0 rgba(255, 255, 255, 0.6);
  padding: 24px 32px;
  margin-bottom: 32px;
}
.summary-id {
  display: flex;
  align-items: center;
  gap: 12px;
}
.id-label {
  font-size: 0.8rem;
  color: #a0aec0;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.id-value {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1rem;
  color: #2d3748;
  font-weight: 600;
  background: #f7fafc;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid #edf2f7;
}
.summary-status {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 10px 18px;
  border-radius: 50px;
  font-weight: 600;
  font-size: 0.9rem;
  background: #f7fafc;
  border: 1px solid #edf2f7;
}
.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #999;
}
.status-pending { color: #718096; }
.status-pending .status-dot { background: #a0aec0; }
.status-processing { color: #dd6b20; }
.status-processing .status-dot { background: #dd6b20; animation: pulse 1.5s infinite; }
.status-completed { color: #38a169; }
.status-completed .status-dot { background: #38a169; }
.status-failed { color: #e53e3e; }
.status-failed .status-dot { background: #e53e3e; }
.status-skipped { color: #718096; }
.status-skipped .status-dot { background: #a0aec0; }

@keyframes pulse {
  0% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.85); }
  100% { opacity: 1; transform: scale(1); }
}

/* Stages Track */
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
  margin-bottom: 24px;
  max-width: 33.33%;
}
.stage-card {
  flex: 1;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow:
    0 4px 6px -1px rgba(0, 0, 0, 0.05),
    0 16px 32px -8px rgba(0, 0, 0, 0.08),
    inset 0 1px 0 rgba(255, 255, 255, 0.6);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: relative;
  transition: transform 0.2s, box-shadow 0.2s;
}
.stage-card:hover {
  transform: translateY(-2px);
  box-shadow:
    0 4px 6px -1px rgba(0, 0, 0, 0.05),
    0 24px 48px -12px rgba(0, 0, 0, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.6);
}
.stage-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}
.stage-num {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  font-weight: 700;
  color: #a0aec0;
  background: #f7fafc;
  padding: 4px 10px;
  border-radius: 20px;
}
.stage-icon {
  font-size: 1.3rem;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: #f7fafc;
}
.stage-label {
  font-weight: 700;
  font-size: 1.05rem;
  color: #1a202c;
}
.stage-duration {
  position: absolute;
  bottom: 16px;
  right: 20px;
  font-size: 0.75rem;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
  color: #667eea;
  background: rgba(102, 126, 234, 0.08);
  padding: 2px 8px;
  border-radius: 4px;
  z-index: 2;
}
.stage-message {
  font-size: 0.85rem;
  color: #718096;
  min-height: 1.2em;
  line-height: 1.5;
  word-break: break-word;
  white-space: pre-wrap;
}
.stage-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.8rem;
  color: #667eea;
  text-decoration: none;
  margin-top: 6px;
  font-weight: 500;
  transition: color 0.2s;
}
.stage-link:hover {
  color: #764ba2;
  text-decoration: underline;
}
.project-info {
  margin-top: 10px;
  padding: 12px 14px;
  background: #f7fafc;
  border: 1px solid #edf2f7;
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 0.8rem;
}
.info-label {
  color: #a0aec0;
  font-weight: 500;
  flex-shrink: 0;
}
.info-value {
  color: #2d3748;
  font-weight: 600;
  text-align: right;
  word-break: break-word;
}
.info-code {
  background: #fff;
  padding: 3px 8px;
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: #2d3748;
  border: 1px solid #e2e8f0;
}
.stage-reset {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.75rem;
  color: #fff;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  padding: 6px 12px;
  margin-top: 10px;
  cursor: pointer;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
  border-radius: 8px;
  transition: opacity 0.2s;
}
.stage-reset:hover {
  opacity: 0.9;
}

/* App Creation Actions */
.app-actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 10px;
}
.attach-info {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.attach-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  background: linear-gradient(135deg, rgba(102, 126, 234, 0.08) 0%, rgba(118, 75, 162, 0.04) 100%);
  border: 1px solid rgba(102, 126, 234, 0.2);
  border-radius: 10px;
  font-size: 0.85rem;
  color: #667eea;
  font-weight: 500;
  width: fit-content;
}
.attach-icon {
  font-size: 1rem;
}

.btn-aiqa {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: #000;
  color: #fff;
  border: none;
  padding: 8px 14px;
  font-size: 0.8rem;
  font-weight: 600;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-aiqa:hover {
  background: #333;
}

.btn-publish {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  border: none;
  padding: 8px 14px;
  font-size: 0.8rem;
  font-weight: 600;
  border-radius: 8px;
  cursor: pointer;
  transition: opacity 0.2s;
}

.btn-publish:hover:not(:disabled) {
  opacity: 0.9;
}

.btn-publish:disabled {
  background: #a0aec0;
  cursor: not-allowed;
}

.published-card {
  margin-top: 10px;
  background: linear-gradient(135deg, rgba(56, 161, 105, 0.08) 0%, rgba(72, 187, 120, 0.04) 100%);
  border: 1px solid rgba(56, 161, 105, 0.2);
  border-radius: 12px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.published-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.published-icon {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: #38a169;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.published-icon svg {
  width: 14px;
  height: 14px;
}

.published-info {
  display: flex;
  flex-direction: column;
}

.published-title {
  font-size: 0.85rem;
  font-weight: 700;
  color: #276749;
}

.published-desc {
  font-size: 0.7rem;
  color: #48bb78;
}

.public-url-box {
  display: flex;
  align-items: center;
  gap: 6px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 6px 8px 6px 10px;
}

.url-text {
  flex: 1;
  font-size: 0.75rem;
  color: #2d3748;
  font-family: 'JetBrains Mono', monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.url-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f7fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  color: #4a5568;
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
}

.url-btn:hover {
  background: #edf2f7;
  color: #2d3748;
  border-color: #cbd5e0;
}

.url-btn.open:hover {
  background: rgba(102, 126, 234, 0.1);
  color: #667eea;
  border-color: rgba(102, 126, 234, 0.3);
}

.url-btn svg {
  width: 13px;
  height: 13px;
}

/* Stage status colors */
.stage-card.stage-processing {
  border-color: rgba(221, 107, 32, 0.4);
  box-shadow:
    0 0 0 3px rgba(221, 107, 32, 0.08),
    0 16px 32px -8px rgba(221, 107, 32, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.6);
}
.stage-card.stage-processing .stage-icon {
  background: rgba(221, 107, 32, 0.1);
  color: #dd6b20;
}
.stage-card.stage-completed {
  border-color: rgba(56, 161, 105, 0.4);
}
.stage-card.stage-completed .stage-icon {
  background: rgba(56, 161, 105, 0.1);
  color: #38a169;
}
.stage-card.stage-failed {
  border-color: rgba(229, 62, 62, 0.4);
}
.stage-card.stage-failed .stage-icon {
  background: rgba(229, 62, 62, 0.1);
  color: #e53e3e;
}
.stage-card.stage-pending .stage-icon {
  color: #a0aec0;
}
.stage-card.stage-skipped .stage-icon {
  color: #a0aec0;
}

/* Connector */
.stage-connector {
  display: flex;
  align-items: center;
  color: #e2e8f0;
  padding: 0 12px;
}
.connector-line {
  width: 24px;
  height: 3px;
  background: #e2e8f0;
  border-radius: 2px;
  transition: background 0.3s;
}
.connector-line.active {
  background: linear-gradient(90deg, #38a169 0%, #48bb78 100%);
}
.connector-arrow {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-left: 2px;
}
.connector-arrow svg {
  width: 14px;
  height: 14px;
}

/* Stage Logs */
.stage-logs {
  margin-top: 8px;
  background: rgba(247, 250, 252, 0.8);
  border: 1px solid #edf2f7;
  border-radius: 12px;
  overflow: hidden;
  transition: box-shadow 0.2s;
}
.stage-logs.expanded {
  box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.02);
}
.logs-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  font-size: 0.8rem;
  color: #4a5568;
  cursor: pointer;
  background: #edf2f7;
  user-select: none;
  transition: background 0.2s;
}
.logs-header:hover {
  background: #e2e8f0;
}
.logs-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
}
.logs-icon {
  font-size: 0.9rem;
}
.logs-toggle {
  font-size: 0.75rem;
  color: #667eea;
  font-weight: 600;
}
.logs-body {
  max-height: 220px;
  overflow-y: auto;
  padding: 12px 14px;
}
.log-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.log-line {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: #4a5568;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  padding: 6px 10px;
  background: #fff;
  border-radius: 6px;
  border: 1px solid #edf2f7;
}
.logs-empty {
  font-size: 0.8rem;
  color: #a0aec0;
  padding: 16px 0;
  text-align: center;
}

/* Footer */
.kpt-footer {
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow:
    0 4px 6px -1px rgba(0, 0, 0, 0.05),
    0 20px 40px -10px rgba(0, 0, 0, 0.08),
    inset 0 1px 0 rgba(255, 255, 255, 0.6);
  padding: 40px;
  text-align: center;
}
.result-box {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.result-icon {
  font-size: 3rem;
  margin-bottom: 12px;
}
.result-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: #1a202c;
  margin-bottom: 8px;
}
.result-desc {
  font-size: 1rem;
  color: #718096;
  margin-bottom: 24px;
}
.result-row {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 16px;
  margin-bottom: 28px;
}
.result-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  background: #f7fafc;
  padding: 14px 20px;
  border-radius: 12px;
  border: 1px solid #edf2f7;
  min-width: 120px;
}
.item-label {
  font-size: 0.7rem;
  color: #a0aec0;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.result-item code {
  background: #fff;
  padding: 4px 10px;
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  color: #2d3748;
  border: 1px solid #e2e8f0;
}
.result-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
}
.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  border: none;
  padding: 12px 24px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
  font-size: 0.9rem;
  cursor: pointer;
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.35);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.btn-primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(102, 126, 234, 0.45);
}
.btn-primary svg {
  width: 16px;
  height: 16px;
}
.btn-secondary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: #fff;
  color: #2d3748;
  border: 1px solid #e2e8f0;
  padding: 12px 24px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
  font-size: 0.9rem;
  cursor: pointer;
  border-radius: 12px;
  transition: all 0.2s;
}
.btn-secondary:hover {
  border-color: #cbd5e0;
  background: #f7fafc;
}

.error-message {
  color: #e53e3e;
  font-size: 0.95rem;
  background: #fff5f5;
  padding: 16px 20px;
  border-radius: 12px;
  border: 1px solid #fed7d7;
  max-width: 600px;
}

.result-processing .result-title {
  color: #dd6b20;
}
.processing-ring {
  width: 64px;
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 16px;
  border-radius: 50%;
  background: rgba(221, 107, 32, 0.08);
}
.processing-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid rgba(221, 107, 32, 0.2);
  border-top-color: #dd6b20;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 900px) {
  .stages-track {
    flex-direction: column;
  }
  .stage-wrapper {
    width: 100%;
    max-width: 100%;
    margin-bottom: 0;
  }
  .stage-connector {
    display: none;
  }
  .kpt-header {
    padding: 0 24px;
    height: 64px;
  }
  .kpt-main {
    padding: 28px 16px 48px;
  }
  .kpt-summary {
    flex-direction: column;
    gap: 16px;
    align-items: flex-start;
    padding: 20px 24px;
  }
  .kpt-footer {
    padding: 28px 24px;
  }
  .result-title {
    font-size: 1.25rem;
  }
}
</style>
