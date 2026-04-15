<template>
  <div class="main-view">
    <!-- Header -->
    <header class="app-header">
      <div class="header-left">
        <div class="brand" @click="router.push('/')">Knowledge EviGraph</div>
      </div>
      
      <div class="header-center">
        <div class="view-switcher">
          <button 
            v-for="mode in ['graph', 'split', 'workbench']" 
            :key="mode"
            class="switch-btn"
            :class="{ active: viewMode === mode }"
            @click="viewMode = mode"
          >
            {{ { graph: 'Graph', split: 'Split', workbench: 'Workbench' }[mode] }}
          </button>
        </div>
      </div>

      <div class="header-right">
        <!-- Step Navigator -->
        <StepNavigator
          :projectId="currentProjectId"
          :projectStatus="statusForNavigator"
          :currentStep="2"
        />
        <div class="step-divider"></div>
        <span class="status-indicator" :class="statusClass">
          <span class="dot"></span>
          {{ statusText }}
        </span>
      </div>
    </header>

    <!-- Main Content Area -->
    <main class="content-area">
      <!-- Left Panel: Graph -->
      <div class="panel-wrapper left" :style="leftPanelStyle">
        <GraphPanel 
          :graphData="graphData"
          :loading="graphLoading"
          :currentPhase="currentPhase"
          @refresh="refreshGraph"
          @toggle-maximize="toggleMaximize('graph')"
        />
      </div>

      <!-- Right Panel: Step Components -->
      <div class="panel-wrapper right" :style="rightPanelStyle">
        <!-- Graph Build Panel -->
        <GraphBuild
          :currentPhase="currentPhase"
          :projectData="projectData"
          :buildProgress="buildProgress"
          :graphData="graphData"
          :systemLogs="systemLogs"
          :hasIntelligentChunks="hasIntelligentChunks"
          @reset-build="handleResetBuild"
          @start-build="startBuildGraph"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GraphPanel from '../components/GraphPanel.vue'
import GraphBuild from '../components/GraphBuild.vue'
import StepNavigator from '../components/StepNavigator.vue'
import { checkHasIntelligentChunks, getProject, buildGraph, getTaskStatus, getGraphData, getTaskEventsURL } from '../api/graph'
import { getPendingUpload, clearPendingUpload } from '../store/pendingUpload'

const route = useRoute()
const router = useRouter()

// Layout State
const viewMode = ref('split') // graph | split | workbench

// Data State
const currentProjectId = ref(route.params.projectId)
const hasIntelligentChunks = ref(false)
const loading = ref(false)
const graphLoading = ref(false)
const error = ref('')
const projectData = ref(null)
const graphData = ref(null)
const currentPhase = ref(-1) // -1: Upload, 0: Ready to build, 1: Build in progress, 2: Complete
const buildProgress = ref(null)
const systemLogs = ref([])

// Task connection source (SSE)
let taskSource = null
let graphPollTimer = null

// --- Computed Layout Styles ---
const leftPanelStyle = computed(() => {
  if (viewMode.value === 'graph') return { width: '100%', opacity: 1, transform: 'translateX(0)' }
  if (viewMode.value === 'workbench') return { width: '0%', opacity: 0, transform: 'translateX(-20px)' }
  return { width: '50%', opacity: 1, transform: 'translateX(0)' }
})

const rightPanelStyle = computed(() => {
  if (viewMode.value === 'workbench') return { width: '100%', opacity: 1, transform: 'translateX(0)' }
  if (viewMode.value === 'graph') return { width: '0%', opacity: 0, transform: 'translateX(20px)' }
  return { width: '50%', opacity: 1, transform: 'translateX(0)' }
})

// --- Status Computed ---
// Map currentPhase to status string for StepNavigator
const statusForNavigator = computed(() => {
  switch (currentPhase.value) {
    case -1: return 'created'
    case 0: return 'graph_chunking'
    case 1: return 'graph_building'
    case 2: return 'graph_completed'
    default: return 'created'
  }
})

const statusClass = computed(() => {
  if (error.value) return 'error'
  if (currentPhase.value >= 2) return 'completed'
  return 'processing'
})

const statusText = computed(() => {
  if (error.value) return 'Error'
  if (currentPhase.value >= 2) return 'Ready'
  if (currentPhase.value === 1) return 'Building Graph'
  if (currentPhase.value === 0) return 'Ready to Build'
  return 'Initializing'
})

// --- Helpers ---
const addLog = (msg) => {
  const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) + '.' + new Date().getMilliseconds().toString().padStart(3, '0')
  systemLogs.value.push({ time, msg })
  // Keep last 100 logs
  if (systemLogs.value.length > 100) {
    systemLogs.value.shift()
  }
}

// --- Layout Methods ---
const toggleMaximize = (target) => {
  if (viewMode.value === target) {
    viewMode.value = 'split'
  } else {
    viewMode.value = target
  }
}


// --- Data Logic ---

const initProject = async () => {
  addLog('GraphBuild view initialized.')
  if (currentProjectId.value === 'new') {
    router.replace({ name: 'ChunkAnalysis', params: { projectId: currentProjectId.value } })
    return
  }
  try {
    const res = await checkHasIntelligentChunks(currentProjectId.value)
    if (res.success) {
      hasIntelligentChunks.value = res.data.has_intelligent_chunks
      if (!res.data.has_intelligent_chunks) {
        addLog('No intelligent_chunks.json found, redirecting to chunk_analysis...')
        router.replace({ name: 'ChunkAnalysis', params: { projectId: currentProjectId.value } })
        return
      }
    }
  } catch (err) {
    console.warn('Failed to check intelligent_chunks:', err)
  }
  await loadProject()
}


const loadProject = async () => {
  try {
    loading.value = true
    addLog(`Loading project ${currentProjectId.value}...`)
    const res = await getProject(currentProjectId.value)
    if (res.success) {
      projectData.value = res.data

      updatePhaseByStatus(res.data.status)
      addLog(`Project loaded. Status: ${res.data.status}`)

      // 已在 graph_build 状态，继续轮询
      const buildStatuses = ['graph_building', 'graph_chunking', 'graph_embedding', 'graph_indexing']
      if (buildStatuses.includes(res.data.status) && res.data.graph_build_task_id) {
        currentPhase.value = 1
        startPollingTask(res.data.graph_build_task_id)
        startGraphPolling()
      } else if (res.data.status === 'graph_completed' && res.data.graph_id) {
        // 图谱已完成
        currentPhase.value = 2
        await loadGraph(res.data.graph_id)
      }
    } else {
      error.value = res.error
      addLog(`Error loading project: ${res.error}`)
    }
  } catch (err) {
    error.value = err.message
    addLog(`Exception in loadProject: ${err.message}`)
  } finally {
    loading.value = false
  }
}

const updatePhaseByStatus = (status) => {
  switch (status) {
    case 'graph_building':
    case 'graph_chunking':
    case 'graph_embedding':
    case 'graph_indexing':
      currentPhase.value = 1; break; // Graph build in progress
    case 'graph_chunked':
      currentPhase.value = 0; break; // Chunks ready, Step 01 is active (show start-build button)
    case 'graph_completed':
      currentPhase.value = 2; break;
    case 'failed':
      error.value = projectData.value?.error || 'Project failed'; break;
    default:
      // 如果没有 graph_id，说明还没有开始构建，停留在 phase 0
      if (!projectData.value?.graph_id) {
        currentPhase.value = 0
      }
      break;
  }
}

const handleResetBuild = async () => {
  stopPolling()
  stopGraphPolling()
  graphData.value = null
  systemLogs.value = []
  await startBuildGraph(true)
}

const startBuildGraph = async (force = false) => {
  if (!hasIntelligentChunks.value && !force) {
    addLog('Cannot build: no intelligent_chunks.json found')
    return
  }
  try {
    currentPhase.value = 1
    buildProgress.value = { progress: 0, message: force ? 'Resetting and starting build...' : 'Starting build...' }
    addLog('Initiating graph build...')

    const res = await buildGraph({
      project_id: currentProjectId.value,
      force: force
    })
    if (res.success) {
      addLog(`Graph build task started. Task ID: ${res.data.task_id}`)
      startGraphPolling()
      startPollingTask(res.data.task_id)
    } else {
      error.value = res.error
      addLog(`Error starting build: ${res.error}`)
    }
  } catch (err) {
    // 从 AxiosError 中提取后端返回的 error 信息
    const msg = err.response?.data?.error || err.response?.data?.message || err.message
    error.value = msg
    addLog(`Build request failed: ${msg}`)
  }
}

const startGraphPolling = () => {
  addLog('Started polling for graph data...')
  fetchGraphData()
  graphPollTimer = setInterval(fetchGraphData, 10000)
}

const fetchGraphData = async () => {
  try {
    // Refresh project info to check for graph_id and status
    const projRes = await getProject(currentProjectId.value)
    if (projRes.success) {
      // 当后端状态已是 graph_completed 但前端 phase 尚未升级时，同步状态
      if (projRes.data.status === 'graph_completed' && currentPhase.value < 2) {
        currentPhase.value = 2
        buildProgress.value = null
        addLog('Project status synced: graph_completed')
      }
      if (projRes.data.graph_id) {
        const gRes = await getGraphData(projRes.data.graph_id)
        if (gRes.success) {
          const newNodeCount = gRes.data.node_count || gRes.data.nodes?.length || 0
          const newEdgeCount = gRes.data.edge_count || gRes.data.edges?.length || 0
          const oldNodeCount = graphData.value?.node_count || graphData.value?.nodes?.length || 0
          const oldEdgeCount = graphData.value?.edge_count || graphData.value?.edges?.length || 0
          // Only log when node/edge count actually changes
          if (newNodeCount !== oldNodeCount || newEdgeCount !== oldEdgeCount) {
            graphData.value = gRes.data
            addLog(`Graph data refreshed. Nodes: ${newNodeCount}, Edges: ${newEdgeCount}`)
          } else {
            graphData.value = gRes.data
          }
        }
      }
    }
  } catch (err) {
    console.warn('Graph fetch error:', err)
  }
}

const startPollingTask = (taskId, type = 'build') => {
  if (taskSource) {
    taskSource.close()
  }

  console.log(`📡 Starting SSE listener (GraphBuild) for task: ${taskId}`)
  const url = getTaskEventsURL(taskId)
  taskSource = new EventSource(url)

  taskSource.onmessage = (event) => {
    try {
      const { type: msgType, data } = JSON.parse(event.data)

      if (msgType === 'init') {
        const task = data
        console.log(`✅ SSE initialized (GraphBuild)`)

        if (task.message) {
          addLog(task.message)
        }

        updateTaskUI(task)
        return
      }

      if (msgType === 'update') {
        const payload = data

        if (payload.message && payload.message !== buildProgress.value?.message) {
          addLog(payload.message)
        }

        updateTaskUI(payload)

        if (payload.status === 'completed' || payload.status === 'failed') {
          handleTaskFinished(payload)
          stopPolling()
        }
      }
    } catch (err) {
      console.error('SSE parsing error (GraphBuild):', err)
    }
  }

  taskSource.onerror = (err) => {
    console.error('SSE connection error (GraphBuild):', err)
    getTaskStatus(taskId).then(res => {
      if (res.success) {
        updateTaskUI(res.data)
        if (res.data.status === 'completed' || res.data.status === 'failed') {
          handleTaskFinished(res.data)
          stopPolling()
        }
      }
    })
  }
}

const updateTaskUI = (taskData) => {
  buildProgress.value = {
    progress: taskData.progress ?? (buildProgress.value?.progress || 0),
    message: taskData.message ?? (buildProgress.value?.message || 'Processing...')
  }
}

const handleTaskFinished = async (taskData) => {
  if (taskData.status === 'completed') {
    addLog('Graph build task completed.')
    stopGraphPolling()
    currentPhase.value = 2
    buildProgress.value = null
    const projRes = await getProject(currentProjectId.value)
    if (projRes.success && projRes.data.graph_id) {
      projectData.value = projRes.data
      await loadGraph(projRes.data.graph_id)
    }
  } else if (taskData.status === 'failed') {
    error.value = taskData.error || 'Task failed'
    addLog(`Graph build task failed: ${taskData.error}`)
  }
}

const loadGraph = async (graphId) => {
  graphLoading.value = true
  addLog(`Loading full graph data: ${graphId}`)
  try {
    const res = await getGraphData(graphId)
    if (res.success) {
      graphData.value = res.data
      addLog('Graph data loaded successfully.')
    } else {
      addLog(`Failed to load graph data: ${res.error}`)
    }
  } catch (e) {
    addLog(`Exception loading graph: ${e.message}`)
  } finally {
    graphLoading.value = false
  }
}

const refreshGraph = () => {
  if (projectData.value?.graph_id) {
    addLog('Manual graph refresh triggered.')
    loadGraph(projectData.value.graph_id)
  }
}

const stopPolling = () => {
  if (taskSource) {
    taskSource.close()
    taskSource = null
  }
}

const stopGraphPolling = () => {
  if (graphPollTimer) {
    clearInterval(graphPollTimer)
    graphPollTimer = null
    addLog('Graph polling stopped.')
  }
}

onMounted(() => {
  initProject()
})

onBeforeUnmount(() => {
  stopPolling()
  stopGraphPolling()
})
</script>

<style scoped>
.main-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #FFF;
  overflow: hidden;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

/* Header */
.app-header {
  height: 60px;
  border-bottom: 1px solid #EAEAEA;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: #FFF;
  z-index: 100;
  position: relative;
}

.header-center {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
}

.brand {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 18px;
  letter-spacing: 1px;
  cursor: pointer;
}

.view-switcher {
  display: flex;
  background: #F5F5F5;
  padding: 4px;
  border-radius: 6px;
  gap: 4px;
}

.switch-btn {
  border: none;
  background: transparent;
  padding: 6px 16px;
  font-size: 12px;
  font-weight: 600;
  color: #666;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.switch-btn.active {
  background: #FFF;
  color: #000;
  box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #666;
  font-weight: 500;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  justify-content: flex-end;
}

.workflow-step {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
}

.step-num {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  color: #999;
}

.step-name {
  font-weight: 700;
  color: #000;
}

.step-divider {
  width: 1px;
  height: 14px;
  background-color: #E0E0E0;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #CCC;
}

.status-indicator.processing .dot { background: #FF5722; animation: pulse 1s infinite; }
.status-indicator.completed .dot { background: #4CAF50; }
.status-indicator.error .dot { background: #F44336; }

@keyframes pulse { 50% { opacity: 0.5; } }

/* Content */
.content-area {
  flex: 1;
  display: flex;
  position: relative;
  overflow: hidden;
}

.panel-wrapper {
  height: 100%;
  overflow: hidden;
  transition: width 0.4s cubic-bezier(0.25, 0.8, 0.25, 1), opacity 0.3s ease, transform 0.3s ease;
  will-change: width, opacity, transform;
}

.panel-wrapper.left {
  border-right: 1px solid #EAEAEA;
}
</style>
