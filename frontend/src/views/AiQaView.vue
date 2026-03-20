<template>
  <div class="ai-qa-view">
    <!-- Header/Toolbar -->
    <header class="qa-header">
      <div class="header-left">
        <button class="back-btn" @click="router.back()">←</button>
        <div class="app-name-editor">
          <input
            v-if="isEditingAppName"
            v-model="appName"
            ref="appNameInput"
            @blur="isEditingAppName = false"
            @keyup.enter="isEditingAppName = false"
            class="app-name-input"
          />
          <span v-else class="view-title" @click="toggleEditAppName">
            {{ appName }} <span class="edit-hint">✏️</span>
          </span>
        </div>
      </div>
      <div class="header-right">
        <button class="action-btn save-btn" :disabled="saving" @click="saveWorkflowApp">
          <span v-if="!saving">💾 保存应用</span>
          <span v-else class="spinner-sm"></span>
        </button>
        <button class="action-btn run-btn" :disabled="running" @click="runWorkflow">
          <span v-if="!running">运行流程</span>
          <span v-else class="spinner-sm"></span>
        </button>
        <button class="action-btn reset-btn" @click="resetWorkflow">重置</button>
      </div>
    </header>

    <!-- Canvas Area -->
    <div class="main-container">
      <div class="canvas-area" ref="canvas" @mousemove="handleDrag" @mouseup="stopDrag" @mouseleave="stopDrag">
        <svg class="connections-svg">
          <path v-for="(conn, idx) in connections" :key="idx" :d="getConnectionPath(conn)" class="conn-path" />
        </svg>

        <!-- Nodes -->
        <div
          v-for="node in nodes"
          :key="node.id"
          class="flow-node"
          :class="[node.type, { 'active': node.id === activeNodeId, 'running': node.status === 'running', 'completed': node.status === 'completed' }]"
          :style="{ left: node.x + 'px', top: node.y + 'px' }"
          @mousedown="startDrag(node, $event)"
        >
          <div class="node-header">
            <span class="node-icon">{{ node.icon }}</span>
            <span class="node-title">{{ node.title }}</span>
            <div v-if="node.status === 'running'" class="node-spinner"></div>
            <div v-if="node.status === 'completed'" class="node-check">✓</div>
          </div>

          <div class="node-content" @mousedown.stop>
            <!-- Input Node Content -->
            <div v-if="node.type === 'input'" class="input-content">
              <textarea v-model="workflowData.query" placeholder="在这里输入您的问题..."></textarea>
            </div>

            <!-- Retrieval Node Content -->
            <div v-if="node.type === 'retrieval'" class="retrieval-content">
              <div class="kb-tools-header">
                <button class="kb-tools-btn" @click="showKbTools = true">
                  ⚙️ 知识库配置
                </button>
                <span class="kb-count">已选 {{ workflowData.selectedGraphIds.length }} 个库</span>
              </div>

              <div class="selected-kbs">
                <span v-for="id in workflowData.selectedGraphIds" :key="id" class="kb-tag">
                  {{ getProjectName(id) }}
                </span>
              </div>

              <div v-if="results.facts.length > 0" class="facts-preview">
                <div class="facts-header">检索到 {{ results.facts.length }} 条事实</div>
                <div class="facts-scroll-area">
                  <div v-for="(fact, idx) in results.facts" :key="idx" class="fact-card">
                    <div class="fact-text">{{ fact.text }}</div>
                    <div class="fact-footer">
                      <span class="fact-source-tag" v-if="fact.source !== 'Unknown'">
                        📄 {{ fact.source }} <template v-if="fact.page">(P{{ fact.page }})</template>
                      </span>
                      <button
                        v-if="fact.source !== 'Unknown' && fact.source !== 'Graph Knowledge' && fact.source !== 'Local Search'"
                        class="view-doc-btn"
                        @click="viewDocument(fact)"
                      >
                        定位文档
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- LLM Node Content -->
            <div v-if="node.type === 'llm'" class="llm-content">
              <div class="form-group">
                <label>大语言模型 (LLM)</label>
                <div class="system-config-badge">System Configured</div>
                <div class="config-hint">使用 .env 配置文件中的模型</div>
              </div>
              <div class="form-group">
                <label>温度: {{ workflowData.temperature }}</label>
                <input type="range" v-model="workflowData.temperature" min="0" max="1" step="0.1" />
              </div>
            </div>

            <!-- Output Node Content -->
            <div v-if="node.type === 'output'" class="output-content">
              <div v-if="!results.answer" class="output-placeholder">等待运行结果...</div>
              <div v-else class="answer-text">{{ results.answer }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Right Panel: Document Viewer -->
      <div class="document-viewer" :class="{ 'open': showDocViewer }">
        <div class="viewer-header">
          <div class="viewer-title-group">
            <span class="viewer-icon">📄</span>
            <span class="viewer-filename">{{ currentDoc.filename }}</span>
          </div>
          <button class="viewer-close" @click="showDocViewer = false">✕</button>
        </div>
        <div class="viewer-content">
          <iframe
            v-if="currentDoc.url"
            :key="currentDoc.url"
            :src="currentDoc.url"
            class="pdf-iframe"
            frameborder="0"
          ></iframe>
          <div v-else class="viewer-empty">
            <span class="empty-icon">📂</span>
            <p>点击“定位文档”查看源文件</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Knowledge Base Tools Dialog -->
    <div v-if="showKbTools" class="modal-overlay" @click.self="showKbTools = false">
      <div class="kb-modal">
        <div class="modal-header">
          <h3>配置知识库</h3>
          <button class="close-btn" @click="showKbTools = false">×</button>
        </div>
        <div class="modal-body">
          <p class="modal-desc">选择要包含在检索范围内的项目图谱：</p>
          <div v-if="projectListLoading" class="modal-loading">加载中...</div>
          <div v-else class="project-grid">
            <div
              v-for="project in projects"
              :key="project.project_id"
              class="project-select-item"
              :class="{ 'selected': workflowData.selectedGraphIds.includes(project.graph_id), 'disabled': !project.graph_id && !isEditing(project.project_id) }"
              @click="!isEditing(project.project_id) && toggleProject(project)"
            >
              <div class="project-check">
                <span v-if="workflowData.selectedGraphIds.includes(project.graph_id)">✓</span>
              </div>
              <div class="project-info">
                <div v-if="isEditing(project.project_id)" class="edit-input-group" @click.stop>
                  <input
                    v-model="editProjectName"
                    class="edit-name-input"
                    @keyup.enter="saveProjectName(project.project_id)"
                    ref="editInput"
                    placeholder="请输入项目名称"
                  />
                  <button class="save-name-btn" @click="saveProjectName(project.project_id)">💾</button>
                  <button class="cancel-name-btn" @click="cancelEdit">✕</button>
                </div>
                <template v-else>
                  <div class="project-name">
                    {{ project.name }}
                    <span class="edit-icon" @click.stop="startEditProject(project)">✏️</span>
                  </div>
                  <div class="project-meta">ID: {{ project.project_id }} | Status: {{ project.status }}</div>
                </template>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="action-btn" @click="showKbTools = false">确定</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getProjectList, aiQa, updateProject } from '../api/graph'
import { saveApp, getApp } from '../api/ai_app'

const router = useRouter()
const route = useRoute()

// Workflow State
const running = ref(false)
const saving = ref(false)
const showKbTools = ref(false)
const projectListLoading = ref(false)
const projects = ref([])
const activeNodeId = ref(null)

// App State
const appId = ref(route.query.appId || null)
const appName = ref('新 AI 知识库应用')
const isEditingAppName = ref(false)
const appNameInput = ref(null)

// Document Viewer State
const showDocViewer = ref(false)
const currentDoc = ref({
  filename: '',
  url: '',
  page: 1
})

const viewDocument = async (fact) => {
  if (!fact.source || fact.source === 'Unknown') return

  const identifier = fact.graph_id
  const filename = fact.source
  const page = fact.page || 1

  // Use Blob-based preview to shield from the download bug
  try {
    // 1. Clear previous Object URL to free memory
    if (currentDoc.value.url && currentDoc.value.url.startsWith('blob:')) {
      URL.revokeObjectURL(currentDoc.value.url)
    }

    showDocViewer.value = false
    const apiUrl = `${window.location.origin}/api/graph/project/${identifier}/document/${encodeURIComponent(filename)}`

    // 2. Fetch file as blob
    const response = await fetch(apiUrl)
    if (!response.ok) throw new Error('Failed to fetch document')
    const blob = await response.blob()

    // 3. Create Local Object URL and append page fragment
    const blobUrl = URL.createObjectURL(blob)
    const finalUrl = `${blobUrl}#page=${page}`

    nextTick(() => {
      currentDoc.value = { filename, url: finalUrl, page }
      showDocViewer.value = true
    })
  } catch (err) {
    console.error('Document preview error:', err)
    alert('无法加载文档，请重试')
  }
}

// Project Editing State
const editingProjectId = ref(null)
const editProjectName = ref('')
const editInput = ref(null)

const isEditing = (projectId) => editingProjectId.value === projectId

const startEditProject = (project) => {
  editingProjectId.value = project.project_id
  editProjectName.value = project.name
  nextTick(() => {
    if (editInput.value && editInput.value[0]) {
      editInput.value[0].focus()
    }
  })
}

const cancelEdit = () => {
  editingProjectId.value = null
  editProjectName.value = ''
}

const saveProjectName = async (projectId) => {
  if (!editProjectName.value.trim()) return

  try {
    const res = await updateProject(projectId, { name: editProjectName.value.trim() })
    if (res.success) {
      // Update local project list
      const project = projects.value.find(p => p.project_id === projectId)
      if (project) {
        project.name = res.data.name
      }
      editingProjectId.value = null
    } else {
      alert('更新失败: ' + res.error)
    }
  } catch (err) {
    console.error('Failed to update project name:', err)
    alert('保存出错')
  }
}

const workflowData = ref({
  query: '',
  selectedGraphIds: [],
  temperature: 0.7
})

const results = ref({
  facts: [],
  answer: ''
})

// Node Positions and Config
const nodes = ref([
  { id: 'n1', type: 'input', title: '用户输入 (Input)', icon: '📝', x: 50, y: 150, status: 'pending' },
  { id: 'n2', type: 'retrieval', title: '知识库检索 (Retrieval)', icon: '🔍', x: 350, y: 150, status: 'pending' },
  { id: 'n3', type: 'llm', title: '大模型推理 (LLM)', icon: '🧠', x: 650, y: 150, status: 'pending' },
  { id: 'n4', type: 'output', title: '结果输出 (Output)', icon: '✨', x: 950, y: 150, status: 'pending' }
])

const connections = [
  { from: 'n1', to: 'n2' },
  { from: 'n2', to: 'n3' },
  { from: 'n3', to: 'n4' }
]

// Drag and Drop Logic
const draggingNode = ref(null)
const dragOffset = ref({ x: 0, y: 0 })

const startDrag = (node, event) => {
  draggingNode.value = node
  activeNodeId.value = node.id
  dragOffset.value = {
    x: event.clientX - node.x,
    y: event.clientY - node.y
  }
}

const handleDrag = (event) => {
  if (!draggingNode.value) return
  draggingNode.value.x = event.clientX - dragOffset.value.x
  draggingNode.value.y = event.clientY - dragOffset.value.y
}

const stopDrag = () => {
  draggingNode.value = null
}

const getConnectionPath = (conn) => {
  const fromNode = nodes.value.find(n => n.id === conn.from)
  const toNode = nodes.value.find(n => n.id === conn.to)

  if (!fromNode || !toNode) return ''

  const x1 = fromNode.x + 280 // width of node roughly
  const y1 = fromNode.y + 40 // header center
  const x2 = toNode.x
  const y2 = toNode.y + 40

  const dx = (x2 - x1) / 2
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`
}

// Logic Methods
const loadProjects = async () => {
  projectListLoading.value = true
  try {
    const res = await getProjectList()
    if (res.success) {
      projects.value = res.data

      // Auto-select current project if we came from one
      if (route.params.projectId) {
        const currentProj = projects.value.find(p => p.project_id === route.params.projectId)
        if (currentProj && currentProj.graph_id && !workflowData.value.selectedGraphIds.includes(currentProj.graph_id)) {
          workflowData.value.selectedGraphIds.push(currentProj.graph_id)
        }
      }
    }
  } catch (err) {
    console.error('Failed to load projects:', err)
  } finally {
    projectListLoading.value = false
  }
}

const toggleProject = (project) => {
  if (!project.graph_id) return
  const idx = workflowData.value.selectedGraphIds.indexOf(project.graph_id)
  if (idx > -1) {
    workflowData.value.selectedGraphIds.splice(idx, 1)
  } else {
    workflowData.value.selectedGraphIds.push(project.graph_id)
  }
}

const getProjectName = (graphId) => {
  const p = projects.value.find(p => p.graph_id === graphId)
  return p ? p.name : graphId
}

const resetWorkflow = () => {
  results.value = { facts: [], answer: '' }
  nodes.value.forEach(n => n.status = 'pending')
}

const runWorkflow = async () => {
  if (!workflowData.value.query.trim()) {
    alert('请输入问题')
    return
  }
  if (workflowData.value.selectedGraphIds.length === 0) {
    alert('请选择至少一个知识库')
    return
  }

  running.value = true
  resetWorkflow()

  try {
    // Step 1: Input
    nodes.value[0].status = 'completed'

    // Step 2: Retrieval
    nodes.value[1].status = 'running'

    const res = await aiQa({
      query: workflowData.value.query,
      graph_ids: workflowData.value.selectedGraphIds,
      temperature: workflowData.value.temperature
    })

    if (res.success) {
      nodes.value[1].status = 'completed'
      results.value.facts = res.data.retrieved_facts || []

      // Step 3: LLM
      nodes.value[2].status = 'running'
      // Simulating some thinking time for UX
      await new Promise(r => setTimeout(r, 800))

      nodes.value[2].status = 'completed'
      results.value.answer = res.data.answer

      // Step 4: Output
      nodes.value[3].status = 'completed'
    } else {
      throw new Error(res.error || '运行失败')
    }
  } catch (err) {
    console.error('Workflow error:', err)
    alert('运行出错: ' + err.message)
    nodes.value.forEach(n => { if (n.status === 'running') n.status = 'failed' })
  } finally {
    running.value = false
  }
}

const saveWorkflowApp = async () => {
  saving.value = true
  try {
    const payload = {
      app_id: appId.value,
      name: appName.value,
      nodes: nodes.value,
      workflow_data: workflowData.value
    }
    const res = await saveApp(payload)
    if (res.success) {
      appId.value = res.data.app_id
      // Update URL with appId if it's new, without reload
      if (!route.query.appId) {
        router.replace({ query: { ...route.query, appId: res.data.app_id } })
      }
      alert('应用保存成功')
    } else {
      alert('保存失败: ' + res.error)
    }
  } catch (err) {
    console.error('Save app error:', err)
    alert('保存出错')
  } finally {
    saving.value = false
  }
}

const loadAppConfig = async (id) => {
  try {
    const res = await getApp(id)
    if (res.success) {
      const app = res.data
      appName.value = app.name
      if (app.nodes && app.nodes.length > 0) {
        nodes.value = app.nodes
      }
      if (app.workflow_data) {
        workflowData.value = { ...workflowData.value, ...app.workflow_data }
      }
    }
  } catch (err) {
    console.error('Load app error:', err)
  }
}

const toggleEditAppName = () => {
  isEditingAppName.value = true
  nextTick(() => {
    if (appNameInput.value) appNameInput.value.focus()
  })
}

onMounted(async () => {
  await loadProjects()
  if (appId.value) {
    await loadAppConfig(appId.value)
  }
})

onUnmounted(() => {
  if (currentDoc.value.url && currentDoc.value.url.startsWith('blob:')) {
    URL.revokeObjectURL(currentDoc.value.url)
  }
})
</script>

<style scoped>
.ai-qa-view {
  width: 100%;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background-color: #f0f2f5;
  color: #333;
  font-family: 'Inter', -apple-system, sans-serif;
  overflow: hidden;
}

.qa-header {
  height: 60px;
  background: #fff;
  border-bottom: 1px solid #e0e0e0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  z-index: 100;
  box-shadow: 0 2px 4px rgba(0,0,0,0.02);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 15px;
}

.back-btn {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  color: #666;
}

.view-title {
  font-weight: 700;
  font-size: 18px;
  cursor: pointer;
}

.edit-hint {
  font-size: 14px;
  opacity: 0.3;
}

.view-title:hover .edit-hint {
  opacity: 1;
}

.app-name-input {
  font-size: 18px;
  font-weight: 700;
  border: 1px solid #000;
  padding: 2px 8px;
  border-radius: 4px;
  outline: none;
  font-family: inherit;
}

.save-btn {
  background: #fff;
  border: 1px solid #000;
  color: #000;
}

.save-btn:hover {
  background: #f0f0f0;
}

.header-right {
  display: flex;
  gap: 10px;
}

.action-btn {
  padding: 8px 16px;
  border-radius: 6px;
  font-weight: 600;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
  border: 1px solid transparent;
}

.run-btn {
  background: #000;
  color: #fff;
}

.run-btn:hover:not(:disabled) {
  background: #333;
}

.run-btn:disabled {
  background: #ccc;
  cursor: not-allowed;
}

.reset-btn {
  background: #fff;
  border-color: #dcdfe6;
  color: #606266;
}

.reset-btn:hover {
  border-color: #000;
  color: #000;
}

/* Canvas Area */
.main-container {
  flex: 1;
  display: flex;
  position: relative;
  overflow: hidden;
}

.canvas-area {
  flex: 1;
  position: relative;
  overflow: hidden;
  background-image: radial-gradient(#d1d1d1 1px, transparent 1px);
  background-size: 30px 30px;
  transition: all 0.3s ease;
}

/* Document Viewer Panel */
.document-viewer {
  width: 0;
  background: #fff;
  border-left: 1px solid #e0e0e0;
  display: flex;
  flex-direction: column;
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
}

.document-viewer.open {
  width: 40%;
}

.viewer-header {
  height: 50px;
  padding: 0 15px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #f8f9fa;
}

.viewer-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
}

.viewer-filename {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

.viewer-close {
  background: none;
  border: none;
  font-size: 18px;
  cursor: pointer;
  color: #999;
}

.viewer-content {
  flex: 1;
  position: relative;
  background: #fff;
}

.pdf-iframe {
  width: 100%;
  height: 100%;
  border: none;
  display: block;
}

.viewer-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #999;
}

.viewer-empty .empty-icon {
  font-size: 40px;
  margin-bottom: 10px;
  opacity: 0.3;
}

/* Facts List Refinement */
.facts-scroll-area {
  max-height: 350px;
  overflow-y: auto;
  padding-right: 5px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.facts-scroll-area::-webkit-scrollbar {
  width: 4px;
}

.facts-scroll-area::-webkit-scrollbar-thumb {
  background: #dcdfe6;
  border-radius: 4px;
}

.fact-card {
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 10px;
  font-size: 12px;
  transition: border-color 0.2s;
}

.fact-card:hover {
  border-color: #409eff;
}

.fact-text {
  color: #333;
  line-height: 1.5;
  margin-bottom: 8px;
}

.fact-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px dashed #f0f0f0;
  padding-top: 8px;
}

.fact-source-tag {
  color: #909399;
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 150px;
}

.view-doc-btn {
  background: #f0f7ff;
  color: #409eff;
  border: 1px solid #c6e2ff;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10px;
  cursor: pointer;
  transition: all 0.2s;
}

.view-doc-btn:hover {
  background: #409eff;
  color: #fff;
}

.connections-svg {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1;
}

.conn-path {
  fill: none;
  stroke: #999;
  stroke-width: 2px;
  stroke-dasharray: 4;
}

.flow-node {
  position: absolute;
  width: 280px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
  border: 2px solid #fff;
  z-index: 10;
  user-select: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.flow-node.active {
  border-color: #000;
  box-shadow: 0 6px 16px rgba(0,0,0,0.15);
}

.flow-node.running {
  border-color: #409eff;
}

.flow-node.completed {
  border-color: #67c23a;
}

.node-header {
  padding: 10px 15px;
  background: #f8f9fa;
  border-bottom: 1px solid #f0f0f0;
  border-radius: 8px 8px 0 0;
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: move;
}

.node-icon {
  font-size: 16px;
}

.node-title {
  font-weight: 700;
  font-size: 13px;
  flex: 1;
}

.node-content {
  padding: 15px;
  min-height: 100px;
}

/* Specific Node Content Styles */
.input-content textarea {
  width: 100%;
  height: 100px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  padding: 8px;
  font-size: 13px;
  resize: none;
  outline: none;
}

.kb-tools-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.kb-tools-btn {
  background: #f0f2f5;
  border: 1px solid #dcdfe6;
  padding: 5px 10px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
}

.kb-count {
  font-size: 11px;
  color: #999;
}

.selected-kbs {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-bottom: 10px;
}

.kb-tag {
  background: #e1f3ff;
  color: #409eff;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 10px;
}

.facts-preview {
  background: #f8f9fa;
  padding: 8px;
  border-radius: 4px;
  font-size: 11px;
}

.facts-header {
  font-weight: 700;
  color: #666;
  margin-bottom: 5px;
}

.fact-mini-item {
  color: #444;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 2px;
}

.more-facts {
  color: #999;
}

.llm-content .form-group {
  margin-bottom: 12px;
}

.llm-content label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  color: #666;
  margin-bottom: 5px;
}

.system-config-badge {
  background: #f0f2f5;
  color: #666;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
  border: 1px dashed #dcdfe6;
  display: inline-block;
  margin-bottom: 4px;
}

.config-hint {
  font-size: 10px;
  color: #999;
}

.llm-content input[type="range"] {
  width: 100%;
}

.output-content {
  font-size: 13px;
  line-height: 1.5;
  color: #333;
}

.output-placeholder {
  color: #999;
  text-align: center;
  margin-top: 20px;
}

.answer-text {
  max-height: 300px;
  overflow-y: auto;
}

/* Modal Styles */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.kb-modal {
  background: #fff;
  width: 500px;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(0,0,0,0.2);
}

.modal-header {
  padding: 15px 20px;
  border-bottom: 1px solid #eee;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.close-btn {
  background: none;
  border: none;
  font-size: 24px;
  cursor: pointer;
  color: #999;
}

.modal-body {
  padding: 20px;
  max-height: 400px;
  overflow-y: auto;
}

.modal-desc {
  font-size: 13px;
  color: #666;
  margin-bottom: 15px;
}

.project-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.project-select-item {
  display: flex;
  align-items: center;
  gap: 15px;
  padding: 12px;
  border: 1px solid #eee;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.project-select-item:hover {
  background: #f9f9f9;
}

.project-select-item.selected {
  border-color: #000;
  background: #f0f0f0;
}

.project-select-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.project-check {
  width: 20px;
  height: 20px;
  border: 1px solid #ccc;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}

.project-select-item.selected .project-check {
  background: #000;
  color: #fff;
  border-color: #000;
}

.project-name {
  font-weight: 700;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.edit-icon {
  font-size: 12px;
  margin-left: 8px;
  opacity: 0.3;
  transition: opacity 0.2s;
  cursor: pointer;
}

.project-select-item:hover .edit-icon {
  opacity: 1;
}

.edit-input-group {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.edit-name-input {
  flex: 1;
  padding: 4px 8px;
  border: 1px solid #000;
  border-radius: 4px;
  font-size: 13px;
  outline: none;
}

.save-name-btn, .cancel-name-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  font-size: 14px;
}

.save-name-btn:hover { background: #f0f0f0; }
.cancel-name-btn:hover { background: #f0f0f0; }

.project-meta {
  font-size: 11px;
  color: #999;
}

.modal-footer {
  padding: 15px 20px;
  border-top: 1px solid #eee;
  display: flex;
  justify-content: flex-end;
}

.node-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #d1d1d1;
  border-top-color: #409eff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.node-check {
  color: #67c23a;
  font-weight: 700;
}

@keyframes spin { to { transform: rotate(360deg); } }

.spinner-sm {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  display: inline-block;
}
</style>
