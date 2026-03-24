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
            @blur="handleAppNameBlur"
            @keyup.enter="handleAppNameBlur"
            class="app-name-input"
          />
          <span v-else class="view-title" @click="toggleEditAppName">
            {{ appName }} <span class="edit-hint">✏️</span>
          </span>
        </div>
      </div>
      <div class="header-right">
        <button class="action-btn publish-btn" :class="{ 'published': isPublished }" :disabled="publishing || !appId" @click="handlePublish">
          <span v-if="!publishing">{{ isPublished ? '🌐 已发布' : '🚀 发布 API' }}</span>
          <span v-else class="spinner-sm"></span>
        </button>
        <button v-if="isPublished" class="icon-btn info-btn" @click="showApiModal = true" title="查看 API 文档">
          ℹ️
        </button>
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
            <div v-if="node.duration" class="node-duration">{{ node.duration }}s</div>
            <button v-if="node.type === 'output' && results.answer" class="expand-btn" title="全屏查看" @click.stop="toggleFullResult">
              ⛶
            </button>
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
                <div class="facts-header">
                  召回阶段: 发现 {{ results.facts.length }} 条相关事实
                </div>
                <div v-if="results.rerank_results.length > 0" class="retrieval-done-hint-mini">
                  ✅ 初步检索完成
                </div>
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

            <!-- Rerank Node Content -->
            <div v-if="node.type === 'rerank'" class="rerank-content">
              <div class="rerank-config">
                <div class="threshold-label">
                  <span>🎯 知识过滤阈值:</span>
                  <span class="threshold-value" :class="getThresholdClass(workflowData.rerankThreshold)">{{ workflowData.rerankThreshold }}分</span>
                </div>
                <div class="thermometer-container">
                  <input type="range" v-model="workflowData.rerankThreshold" min="0" max="100" step="5" class="thermometer-input" />
                  <div class="thermometer-track">
                    <div class="thermometer-fill" :style="{ width: workflowData.rerankThreshold + '%', background: getThresholdColor(workflowData.rerankThreshold) }"></div>
                  </div>
                </div>
                <p class="config-hint">仅将高于此分数的检索事实发送给大模型推理</p>
              </div>

              <div v-if="results.rerank_results.length === 0" class="rerank-placeholder">
                等待运行...
              </div>
              <div v-else class="rerank-results-list">
                <div class="rerank-summary">LLM 已完成精排 (Top {{ results.facts.length }})</div>
                <div class="rerank-scroll-area">
                  <div v-for="(fact, idx) in results.facts" :key="idx" class="rerank-item-card" :class="{ 'high-score': fact.relevance_score > 80 }">
                    <div class="rerank-item-header">
                      <span class="rerank-score">{{ fact.relevance_score }}分</span>
                      <span class="rerank-index">Rank #{{ idx + 1 }}</span>
                    </div>
                    <div class="rerank-reason">理由: {{ fact.relevance_reasoning }}</div>
                    <div class="rerank-text-snippet">{{ fact.text }}</div>
                    <div class="fact-footer" style="margin-top: 8px;">
                      <span class="fact-source-tag">
                        📄 {{ fact.source }} <template v-if="fact.page">(P{{ fact.page }})</template>
                      </span>
                      <button
                        v-if="fact.source !== 'Unknown' && fact.source !== 'Graph Knowledge' && fact.source !== 'Local Search'"
                        class="view-doc-btn"
                        @click="viewDocument(fact)"
                      >
                        定位
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
              <div v-if="results.prompts.user" class="prompt-debug-entry">
                <button class="debug-btn" @click="showPromptModal = true">
                  🔍 查看输入信息 (Prompts)
                </button>
              </div>
            </div>

            <!-- Output Node Content -->
            <div v-if="node.type === 'output'" class="output-content">
              <div v-if="!results.answer" class="output-placeholder">等待运行结果...</div>
              <div v-else class="qa-result-container">
                <!-- 1. Knowledge Sources with "Screenshots" -->
                <div class="result-section">
                  <div class="section-header">📚 检索依据原文</div>
                  <div class="source-evidence-list">
                    <div v-for="(fact, idx) in results.facts" :key="idx" class="evidence-item">
                      <div class="evidence-meta">
                        <span class="source-tag">来源 {{ idx + 1 }}: {{ fact.source }} <template v-if="fact.page">(P{{ fact.page }})</template></span>
                      </div>
                      <!-- The "Screenshot" Canvas -->
                      <div class="evidence-screenshot-box">
                        <canvas :ref="el => setEvidenceRef(el, idx, 'node')" class="evidence-canvas"></canvas>
                        <div v-if="!fact.bbox" class="no-bbox-hint">（无位置信息，展示文本）: {{ fact.text }}</div>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- 2. Reasoning (Thought) -->
                <div v-if="parsedResult.thought" class="result-section">
                  <div class="section-header" @click="showThought = !showThought">
                    🧠 推理过程 (Thinking Process)
                    <span class="toggle-icon">{{ showThought ? '▼' : '▶' }}</span>
                  </div>
                  <div v-if="showThought" class="thought-content">
                    {{ parsedResult.thought }}
                  </div>
                </div>

                <!-- 3. Final Conclusion -->
                <div class="result-section">
                  <div class="section-header">✨ 最终结论</div>
                  <div class="conclusion-text">
                    {{ parsedResult.conclusion }}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div> <!-- End Canvas Area -->

      <!-- Right Panel: Document Viewer -->
      <div class="document-viewer" :class="{ 'open': showDocViewer }">
        <div class="viewer-header">
          <div class="viewer-title-group">
            <span class="viewer-icon">📄</span>
            <span class="viewer-filename">{{ currentDoc.filename }}</span>
          </div>
          <button class="viewer-close" @click="showDocViewer = false">✕</button>
        </div>
        <div class="viewer-content" ref="viewerContainer">
          <!-- Replacement: Use Canvas + SVG Overlay instead of iframe for highlighting support -->
          <div v-if="currentDoc.url" class="pdf-render-container">
            <div class="pdf-scroll-wrapper">
              <div class="pdf-page-wrapper">
                <canvas ref="pdfCanvas" class="pdf-canvas"></canvas>
                <svg v-if="currentDoc.bbox && currentDoc.bbox.length === 4" class="pdf-highlight-overlay" :viewBox="`0 0 ${currentDoc.pageWidth || 600} ${currentDoc.pageHeight || 800}`">
                  <rect
                    :x="currentDoc.bbox[0]"
                    :y="currentDoc.bbox[1]"
                    :width="currentDoc.bbox[2] - currentDoc.bbox[0]"
                    :height="currentDoc.bbox[3] - currentDoc.bbox[1]"
                    class="highlight-rect"
                  />
                </svg>
              </div>
            </div>
            <div v-if="pdfLoading" class="pdf-loading-overlay">
              <div class="spinner-sm"></div>
              <span>渲染中...</span>
            </div>
          </div>
          <div v-else class="viewer-empty">
            <span class="empty-icon">📂</span>
            <p>点击“定位文档”查看源文件</p>
          </div>
        </div>
      </div>
    </div> <!-- End Main Container -->

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

    <!-- Prompt Debug Modal -->
    <div v-if="showPromptModal" class="modal-overlay" @click.self="showPromptModal = false">
      <div class="prompt-modal">
        <div class="modal-header">
          <h3>大模型输入详情 (Prompts)</h3>
          <button class="close-btn" @click="showPromptModal = false">×</button>
        </div>
        <div class="modal-body prompt-debug-body">
          <div class="prompt-section">
            <div class="section-title">System Prompt</div>
            <pre class="prompt-pre">{{ results.prompts.system }}</pre>
          </div>
          <div class="prompt-section">
            <div class="section-title">User Prompt (Including Context)</div>
            <pre class="prompt-pre">{{ results.prompts.user }}</pre>
          </div>
        </div>
        <div class="modal-footer">
          <button class="action-btn" @click="showPromptModal = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- Full Result Detailed View -->
    <div v-if="showFullResult" class="modal-overlay" @click.self="showFullResult = false">
      <div class="full-result-modal">
        <div class="modal-header">
          <h3>
            <span class="header-icon">✨</span> 问答结果详情报告
          </h3>
          <div class="header-actions">
            <button class="close-btn" @click="showFullResult = false">×</button>
          </div>
        </div>
        <div class="modal-body full-result-body">
          <!-- 1. Source Evidence Section -->
          <div class="full-section">
            <div class="full-section-title">📚 检索知识出处 (Knowledge Evidence)</div>
            <div class="full-evidence-grid">
              <div v-for="(fact, idx) in results.facts" :key="idx" class="full-evidence-card">
                <div class="evidence-header">
                  <span class="evidence-idx">#{{ idx + 1 }}</span>
                  <span class="evidence-source">{{ fact.source }} <template v-if="fact.page">(第 {{ fact.page }} 页)</template></span>
                </div>
                <div class="full-evidence-screenshot">
                  <canvas :ref="el => setEvidenceRef(el, idx, 'modal')" class="full-evidence-canvas"></canvas>
                  <div v-if="!fact.bbox" class="full-no-bbox">
                    <p class="fact-text-fallback">{{ fact.text }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 2. Thinking Process Section -->
          <div v-if="parsedResult.thought" class="full-section thought-section">
            <div class="full-section-title">🧠 深度推理过程 (Thinking Process)</div>
            <div class="full-thought-box">
              {{ parsedResult.thought }}
            </div>
          </div>

          <!-- 3. Final Conclusion Section -->
          <div v-if="parsedResult.conclusion" class="full-section conclusion-section">
            <div class="full-section-title">✨ 最终结论 (Final Conclusion)</div>
            <div class="full-conclusion-box">
              {{ parsedResult.conclusion }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- API Publish Details & Mock Test Modal -->
    <div v-if="showApiModal" class="modal-overlay" @click.self="showApiModal = false">
      <div class="api-modal">
        <div class="modal-header">
          <h3><span class="header-icon">🌐</span> API 服务发布详情</h3>
          <button class="close-btn" @click="showApiModal = false">×</button>
        </div>
        <div class="modal-body api-modal-body">
          <div class="api-tabs">
            <div class="api-section">
              <div class="section-title">接口调用说明</div>
              <div class="api-info-card">
                <div class="info-row">
                  <span class="info-label">接口地址:</span>
                  <code class="info-value">{{ apiBaseUrl }}</code>
                </div>
                <div class="info-row">
                  <span class="info-label">请求方法:</span>
                  <span class="info-value method-tag">POST</span>
                </div>
                <div class="info-row">
                  <span class="info-label">Content-Type:</span>
                  <code class="info-value">application/json</code>
                </div>
              </div>

              <div class="code-block-wrapper">
                <div class="code-header">请求参数示例 (JSON)</div>
                <pre class="code-content">{
  "query": "这里输入您的查询问题..."
}</pre>
              </div>

              <div class="code-block-wrapper">
                <div class="code-header">Curl 调用示例</div>
                <pre class="code-content">curl -X POST {{ apiBaseUrl }} \
     -H "Content-Type: application/json" \
     -d '{"query": "您的问题"}'</pre>
              </div>
            </div>

            <div class="api-section mock-test-section">
              <div class="section-title">Mock 接口测试</div>
              <div class="mock-input-group">
                <textarea v-model="mockQuery" placeholder="输入测试问题..." class="mock-textarea"></textarea>
                <button class="action-btn run-btn" :disabled="mockLoading || !mockQuery" @click="runMockTest">
                  <span v-if="!mockLoading">发送请求</span>
                  <span v-else class="spinner-sm"></span>
                </button>
              </div>

              <div v-if="mockResult" class="mock-result-area">
                <div class="code-header">响应结果 (Response)</div>
                <pre class="code-content result-pre" :class="{ 'error': mockResult.error }">{{ JSON.stringify(mockResult, null, 2) }}</pre>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="action-btn" @click="showApiModal = false">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getProjectList, aiQa, updateProject } from '../api/graph'
import { saveApp, getApp, publishApp, executeAppApi } from '../api/ai_app'

const props = defineProps({
  id: String
})

const router = useRouter()
const route = useRoute()

// Workflow State
const running = ref(false)
const saving = ref(false)
const publishing = ref(false)
const showKbTools = ref(false)
const showApiModal = ref(false)
const projectListLoading = ref(false)
const projects = ref([])
const activeNodeId = ref(null)

// App State
const appId = ref(props.id?.startsWith('app_') ? props.id : null)
const appName = ref('新 AI 知识库应用')
const isEditingAppName = ref(false)
const appNameInput = ref(null)
const showThought = ref(true)
const showFullResult = ref(false)
const isPublished = ref(false)

const toggleEditAppName = () => {
  isEditingAppName.value = true
  nextTick(() => {
    if (appNameInput.value) appNameInput.value.focus()
  })
}

const handleAppNameBlur = () => {
  isEditingAppName.value = false
  if (appId.value) {
    saveWorkflowApp() // Auto-save name change if appId exists
  }
}

// API Mock Test State
const mockQuery = ref('')
const mockResult = ref(null)
const mockLoading = ref(false)

const apiBaseUrl = computed(() => `${window.location.origin}/api/ai-app/execute/${appId.value}`)

// Results parsing logic
const parsedResult = computed(() => {
  const text = results.value.answer || ''
  let thought = ''
  let conclusion = text

  // Match <thought> or <think> tags
  const thoughtMatch = text.match(/<(thought|think)>([\s\S]*?)<\/\1>/i)
  if (thoughtMatch) {
    thought = thoughtMatch[2].trim()
    conclusion = text.replace(thoughtMatch[0], '').trim()
  } else if (text.includes('思考过程：') || text.includes('Thinking Process:')) {
    // Fallback for custom markers
    const parts = text.split(/结论：|Conclusion:/i)
    if (parts.length > 1) {
      thought = parts[0].replace(/思考过程：|Thinking Process:/i, '').trim()
      conclusion = parts[1].trim()
    }
  }

  return { thought, conclusion }
})

// Evidence Canvas management
const evidenceCanvasRefs = ref({ node: {}, modal: {} })
const setEvidenceRef = (el, idx, type = 'node') => {
  if (el) evidenceCanvasRefs.value[type][idx] = el
}

const renderEvidenceScreenshots = async (type = 'node') => {
  const factsWithBbox = results.value.facts.filter(f => f.bbox && f.bbox.length === 4 && f.graph_id && f.source)

  if (factsWithBbox.length === 0) return

  // Ensure PDF.js is ready
  if (!pdfjsLib.value) await initPdfJs()

  // Cache for PDF documents
  const pdfDocCache = {}

  for (const fact of results.value.facts) {
    const idx = results.value.facts.indexOf(fact)
    const canvas = evidenceCanvasRefs.value[type][idx]
    if (!canvas || !fact.bbox || !fact.graph_id) continue

    try {
      const cacheKey = `${fact.graph_id}:${fact.source}`
      let pdfDoc = pdfDocCache[cacheKey]

      if (!pdfDoc) {
        const apiUrl = `${window.location.origin}/api/graph/project/${fact.graph_id}/document/${encodeURIComponent(fact.source)}`
        const response = await fetch(apiUrl)
        if (!response.ok) continue
        const blob = await response.blob()
        const arrayBuffer = await blob.arrayBuffer()
        pdfDoc = await pdfjsLib.value.getDocument({ data: arrayBuffer }).promise
        pdfDocCache[cacheKey] = pdfDoc
      }

      const page = await pdfDoc.getPage(fact.page || 1)
      const context = canvas.getContext('2d')

      // Logic: Extract the bbox area + some padding
      const bbox = fact.bbox
      const padding = type === 'modal' ? 40 : 20
      const cropX = Math.max(0, bbox[0] - padding)
      const cropY = Math.max(0, bbox[1] - padding)
      const cropW = (bbox[2] - bbox[0]) + padding * 2
      const cropH = (bbox[3] - bbox[1]) + padding * 2

      const scale = type === 'modal' ? 3.0 : 2.0
      const viewport = page.getViewport({ scale })

      const tempCanvas = document.createElement('canvas')
      tempCanvas.width = viewport.width
      tempCanvas.height = viewport.height
      const tempCtx = tempCanvas.getContext('2d')

      await page.render({ canvasContext: tempCtx, viewport }).promise

      const sX = cropX * scale
      const sY = cropY * scale
      const sW = cropW * scale
      const sH = cropH * scale

      const targetWidth = type === 'modal' ? 700 : 240
      canvas.width = targetWidth
      canvas.height = (sH / sW) * targetWidth

      context.drawImage(
        tempCanvas,
        sX, sY, sW, sH,
        0, 0, canvas.width, canvas.height
      )

      context.strokeStyle = 'rgba(255, 69, 0, 0.6)'
      context.lineWidth = type === 'modal' ? 3 : 2
      context.setLineDash([5, 3])
      const hX = (bbox[0] - cropX) * (canvas.width / cropW)
      const hY = (bbox[1] - cropY) * (canvas.height / cropH)
      const hW = (bbox[2] - bbox[0]) * (canvas.width / cropW)
      const hH = (bbox[3] - bbox[1]) * (canvas.height / cropH)
      context.strokeRect(hX, hY, hW, hH)

    } catch (err) {
      console.error(`Failed to render screenshot for fact ${idx}:`, err)
    }
  }
}

const toggleFullResult = () => {
  showFullResult.value = !showFullResult.value
  if (showFullResult.value) {
    nextTick(() => {
      renderEvidenceScreenshots('modal')
    })
  }
}

// Document Viewer State
const showDocViewer = ref(false)
const pdfLoading = ref(false)
const pdfCanvas = ref(null)
const viewerContainer = ref(null)
const pdfjsLib = ref(null)

const currentDoc = ref({
  filename: '',
  url: '',
  page: 1,
  bbox: null,
  pageWidth: 0,
  pageHeight: 0
})

// Load PDF.js from CDN
const initPdfJs = async () => {
  if (window.pdfjsLib) {
    pdfjsLib.value = window.pdfjsLib
    return
  }

  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js'
    script.onload = () => {
      pdfjsLib.value = window['pdfjs-dist/build/pdf']
      pdfjsLib.value.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js'
      resolve()
    }
    script.onerror = reject
    document.head.appendChild(script)
  })
}

const renderPdfPage = async (blob, pageNum) => {
  if (!pdfjsLib.value || !pdfCanvas.value) return

  pdfLoading.value = true
  try {
    const arrayBuffer = await blob.arrayBuffer()
    const loadingTask = pdfjsLib.value.getDocument({ data: arrayBuffer })
    const pdf = await loadingTask.promise
    const page = await pdf.getPage(pageNum)

    const canvas = pdfCanvas.value
    const context = canvas.getContext('2d')

    const containerWidth = viewerContainer.value?.clientWidth || 600
    const unscaledViewport = page.getViewport({ scale: 1 })
    const scale = (containerWidth - 40) / unscaledViewport.width
    const viewport = page.getViewport({ scale })

    canvas.height = viewport.height
    canvas.width = viewport.width

    const renderContext = {
      canvasContext: context,
      viewport: viewport
    }

    await page.render(renderContext).promise
  } catch (err) {
    console.error('PDF render error:', err)
  } finally {
    pdfLoading.value = false
  }
}

const viewDocument = async (fact) => {
  if (!fact.source || fact.source === 'Unknown') return

  const identifier = fact.graph_id
  const filename = fact.source
  const page = fact.page || 1
  const bbox = fact.bbox || null
  const pageWidth = fact.page_width || 0
  const pageHeight = fact.page_height || 0

  try {
    if (currentDoc.value.url && currentDoc.value.url.startsWith('blob:')) {
      URL.revokeObjectURL(currentDoc.value.url)
    }

    showDocViewer.value = false
    const apiUrl = `${window.location.origin}/api/graph/project/${identifier}/document/${encodeURIComponent(filename)}`

    const response = await fetch(apiUrl)
    if (!response.ok) throw new Error('Failed to fetch document')
    const blob = await response.blob()

    const blobUrl = URL.createObjectURL(blob)

    if (!pdfjsLib.value) await initPdfJs()

    nextTick(async () => {
      currentDoc.value = {
        filename,
        url: blobUrl,
        page,
        bbox,
        pageWidth,
        pageHeight
      }
      showDocViewer.value = true

      setTimeout(() => {
        renderPdfPage(blob, page)
      }, 300)
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
  temperature: 0.7,
  rerankThreshold: 60
})

const results = ref({
  facts: [],
  answer: '',
  rerank_results: [],
  prompts: {
    system: '',
    user: ''
  }
})

const showPromptModal = ref(false)

// Node Positions and Config
const nodes = ref([
  { id: 'n1', type: 'input', title: '用户输入 (Input)', icon: '📝', x: 50, y: 150, status: 'pending' },
  { id: 'n2', type: 'retrieval', title: '知识库检索 (Retrieval)', icon: '🔍', x: 350, y: 150, status: 'pending' },
  { id: 'n_rerank', type: 'rerank', title: 'LLM 相关性重排 (Rerank)', icon: '🃏', x: 650, y: 150, status: 'pending' },
  { id: 'n3', type: 'llm', title: '大模型推理 (LLM)', icon: '🧠', x: 950, y: 150, status: 'pending' },
  { id: 'n4', type: 'output', title: '结果输出 (Output)', icon: '✨', x: 1250, y: 150, status: 'pending' }
])

const connections = [
  { from: 'n1', to: 'n2' },
  { from: 'n2', to: 'n_rerank' },
  { from: 'n_rerank', to: 'n3' },
  { from: 'n3', to: 'n4' }
]

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

  const x1 = fromNode.x + 280
  const y1 = fromNode.y + 40
  const x2 = toNode.x
  const y2 = toNode.y + 40

  const dx = (x2 - x1) / 2
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`
}

const loadProjects = async () => {
  projectListLoading.value = true
  try {
    const res = await getProjectList()
    if (res.success) {
      projects.value = res.data
      const projectId = (!props.id?.startsWith('app_') && props.id !== 'default') ? props.id : null
      if (projectId) {
        const currentProj = projects.value.find(p => p.project_id === projectId)
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
  results.value = {
    facts: [],
    answer: '',
    rerank_results: [],
    prompts: { system: '', user: '' }
  }
  nodes.value.forEach(n => {
    n.status = 'pending'
    n.duration = null
  })
}

const getThresholdColor = (val) => {
  if (val < 40) return '#f56c6c'
  if (val < 70) return '#e6a23c'
  return '#67c23a'
}

const getThresholdClass = (val) => {
  if (val < 40) return 'low'
  if (val < 70) return 'mid'
  return 'high'
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
    const response = await fetch(`${window.location.origin}/api/graph/ai-qa`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: workflowData.value.query,
        graph_ids: workflowData.value.selectedGraphIds,
        temperature: workflowData.value.temperature,
        rerank_threshold: workflowData.value.rerankThreshold
      })
    })

    const reader = response.body.getReader()
    const decoder = new TextDecoder()

    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      const chunk = decoder.decode(value)
      const lines = chunk.split('\n')

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const event = JSON.parse(line.slice(6))
          const { type, data } = event

          if (type === 'retrieval_start') {
            nodes.value.find(n => n.id === 'n1').status = 'completed'
            nodes.value.find(n => n.id === 'n1').duration = '0.01'
            nodes.value.find(n => n.id === 'n2').status = 'running'
          } else if (type === 'retrieval_complete') {
            const node = nodes.value.find(n => n.id === 'n2')
            node.status = 'completed'
            node.duration = data.duration
            results.value.facts = data.facts
          } else if (type === 'rerank_complete') {
            const node = nodes.value.find(n => n.id === 'n_rerank')
            node.status = 'completed'
            node.duration = data.duration
            results.value.rerank_results = data.results
          } else if (type === 'prompts_ready') {
            results.value.prompts = data
          } else if (type === 'llm_start') {
            nodes.value.find(n => n.id === 'n3').status = 'running'
          } else if (type === 'llm_complete') {
            const node = nodes.value.find(n => n.id === 'n3')
            node.status = 'completed'
            node.duration = data.duration
            results.value.answer = data.answer

            // Output node
            const outNode = nodes.value.find(n => n.id === 'n4')
            outNode.status = 'completed'
            outNode.duration = data.total_duration
            nextTick(() => {
              renderEvidenceScreenshots('node')
            })
          }
        } catch (e) {
          console.error('Error parsing SSE event:', e)
        }
      }
    }
  } catch (err) {
    console.error('Workflow error:', err)
    alert('流程执行出错')
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
      workflow_data: workflowData.value,
      is_published: isPublished.value
    }
    const res = await saveApp(payload)
    if (res.success) {
      if (appId.value !== res.data.app_id) {
        appId.value = res.data.app_id
        router.replace({ name: 'AiQa', params: { id: appId.value } })
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

const handlePublish = async () => {
  if (!appId.value) {
    alert('请先保存应用再发布')
    return
  }

  publishing.value = true
  try {
    const res = await publishApp(appId.value, !isPublished.value)
    if (res.success) {
      isPublished.value = res.data.is_published
      if (isPublished.value) {
        showApiModal.value = true
      }
    } else {
      alert('发布失败: ' + res.error)
    }
  } catch (err) {
    console.error('Publish error:', err)
    alert('发布操作出错')
  } finally {
    publishing.value = false
  }
}

const runMockTest = async () => {
  if (!mockQuery.value.trim()) return
  mockLoading.value = true
  mockResult.value = null
  try {
    const res = await executeAppApi(appId.value, mockQuery.value)
    if (res.success) {
      mockResult.value = res.data
    } else {
      mockResult.value = { error: res.error }
    }
  } catch (err) {
    mockResult.value = { error: err.message }
  } finally {
    mockLoading.value = false
  }
}

const loadAppConfig = async (id) => {
  try {
    const res = await getApp(id)
    if (res.success) {
      const app = res.data
      appName.value = app.name
      isPublished.value = app.is_published || false
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

.publish-btn {
  background: #fff;
  border: 1px solid #409eff;
  color: #409eff;
}

.publish-btn:hover {
  background: #ecf5ff;
}

.publish-btn.published {
  background: #67c23a;
  border-color: #67c23a;
  color: #fff;
}

.publish-btn.published:hover {
  background: #85ce61;
}

.icon-btn {
  background: none;
  border: none;
  font-size: 18px;
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  transition: background 0.2s;
}

.icon-btn:hover {
  background: #f0f2f5;
}

.info-btn {
  color: #909399;
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
  background: #525659;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.pdf-render-container {
  flex: 1;
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.pdf-scroll-wrapper {
  flex: 1;
  overflow: auto;
  display: flex;
  justify-content: center;
  padding: 20px;
}

.pdf-page-wrapper {
  position: relative;
  box-shadow: 0 5px 15px rgba(0,0,0,0.3);
  background: white;
  height: fit-content;
}

.pdf-canvas {
  display: block;
  max-width: 100%;
}

.pdf-highlight-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 2;
}

.highlight-rect {
  fill: rgba(255, 165, 0, 0.35);
  stroke: #ff4500;
  stroke-width: 1.5px;
  stroke-dasharray: 2;
  animation: pulse-highlight 2s infinite;
}

@keyframes pulse-highlight {
  0% { fill: rgba(255, 165, 0, 0.25); }
  50% { fill: rgba(255, 165, 0, 0.45); }
  100% { fill: rgba(255, 165, 0, 0.25); }
}

.pdf-loading-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(255,255,255,0.8);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  z-index: 10;
  color: #666;
  font-size: 13px;
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

.node-duration {
  font-size: 11px;
  color: #909399;
  background: #f0f2f5;
  padding: 2px 6px;
  border-radius: 10px;
  font-family: monospace;
  margin-right: 5px;
}

.expand-btn {

  background: none;
  border: none;
  font-size: 16px;
  cursor: pointer;
  color: #666;
  padding: 2px 6px;
  border-radius: 4px;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.expand-btn:hover {
  background: #eef1f6;
  color: #409eff;
}

.node-content {
  padding: 15px;
  min-height: 100px;
}

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

.retrieval-done-hint-mini {
  font-size: 10px;
  color: #67c23a;
  margin-bottom: 8px;
  font-weight: 600;
}

.rerank-config {
  margin-bottom: 15px;
  padding-bottom: 15px;
  border-bottom: 1px dashed #e0e0e0;
}

.threshold-label {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 700;
}

.threshold-value {
  padding: 2px 8px;
  border-radius: 10px;
  color: #fff;
}

.threshold-value.low { background: #f56c6c; }
.threshold-value.mid { background: #e6a23c; }
.threshold-value.high { background: #67c23a; }

.thermometer-container {
  position: relative;
  height: 24px;
  display: flex;
  align-items: center;
}

.thermometer-input {
  position: absolute;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
  z-index: 2;
}

.thermometer-track {
  width: 100%;
  height: 10px;
  background: #f0f2f5;
  border-radius: 5px;
  overflow: hidden;
  position: relative;
  border: 1px solid #e0e0e0;
}

.thermometer-fill {
  height: 100%;
  transition: width 0.3s ease, background 0.3s ease;
}

.rerank-content {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.rerank-placeholder {
  color: #999;
  text-align: center;
  margin-top: 20px;
  font-size: 13px;
}

.rerank-results-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rerank-summary {
  font-size: 11px;
  font-weight: 700;
  color: #409eff;
  background: #ecf5ff;
  padding: 4px 8px;
  border-radius: 4px;
}

.rerank-scroll-area {
  max-height: 300px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rerank-item-card {
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 8px;
  transition: all 0.2s;
}

.rerank-item-card.high-score {
  border-left: 4px solid #67c23a;
  background: #f0f9eb;
}

.rerank-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.rerank-score {
  font-weight: 800;
  color: #f56c6c;
  font-size: 14px;
}

.rerank-index {
  color: #999;
  font-size: 10px;
}

.rerank-reason {
  font-size: 11px;
  font-weight: 600;
  color: #333;
  margin-bottom: 4px;
}

.rerank-text-snippet {
  font-size: 10px;
  color: #666;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
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

.qa-result-container {
  display: flex;
  flex-direction: column;
  gap: 15px;
  max-height: 500px;
  overflow-y: auto;
  padding-right: 5px;
}

.result-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.section-header {
  font-weight: 700;
  font-size: 12px;
  color: #666;
  background: #f0f2f5;
  padding: 4px 8px;
  border-radius: 4px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: default;
}

.source-evidence-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.evidence-item {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}

.evidence-meta {
  padding: 4px 8px;
  background: #fafafa;
  border-bottom: 1px solid #f0f0f0;
}

.source-tag {
  font-size: 10px;
  color: #909399;
  font-weight: 600;
}

.evidence-screenshot-box {
  padding: 5px;
  display: flex;
  flex-direction: column;
  align-items: center;
  background: #525659;
}

.evidence-canvas {
  max-width: 100%;
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
  background: #fff;
}

.no-bbox-hint {
  font-size: 11px;
  color: #999;
  font-style: italic;
  padding: 10px;
  background: #fff;
  width: 100%;
}

.thought-content {
  font-size: 12px;
  color: #606266;
  background: #fdf6ec;
  border-left: 3px solid #e6a23c;
  padding: 10px;
  white-space: pre-wrap;
  font-family: inherit;
}

.conclusion-text {
  font-size: 13px;
  color: #2c3e50;
  font-weight: 500;
  line-height: 1.6;
  white-space: pre-wrap;
  padding: 0 5px;
}

.toggle-icon {
  font-size: 10px;
  transition: transform 0.2s;
}

.output-placeholder {
  color: #999;
  text-align: center;
  margin-top: 20px;
}

.prompt-debug-entry {
  margin-top: 15px;
  padding-top: 10px;
  border-top: 1px dashed #e0e0e0;
}

.debug-btn {
  width: 100%;
  background: #f8f9fa;
  border: 1px solid #dcdfe6;
  color: #606266;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
}

.debug-btn:hover {
  background: #eef1f6;
  border-color: #409eff;
  color: #409eff;
}

.prompt-modal {
  background: #fff;
  width: 800px;
  max-width: 90vw;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(0,0,0,0.2);
  display: flex;
  flex-direction: column;
  max-height: 85vh;
}

.prompt-debug-body {
  padding: 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.section-title {
  font-weight: 700;
  font-size: 13px;
  color: #333;
  padding-left: 8px;
  border-left: 3px solid #409eff;
}

.prompt-pre {
  background: #f4f6f8;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 12px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  color: #444;
  margin: 0;
}

.full-result-modal {
  background: #f8f9fa;
  width: 1000px;
  max-width: 95vw;
  height: 90vh;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
}

.full-result-body {
  padding: 30px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 35px;
}

.full-section-title {
  font-size: 16px;
  font-weight: 800;
  color: #1a1a1a;
  margin-bottom: 15px;
  padding-bottom: 8px;
  border-bottom: 2px solid #e0e0e0;
  display: flex;
  align-items: center;
}

.full-evidence-grid {
  display: flex;
  flex-direction: column;
  gap: 25px;
}

.full-evidence-card {
  background: #fff;
  border-radius: 12px;
  border: 1px solid #e0e0e0;
  overflow: hidden;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

.evidence-header {
  padding: 10px 15px;
  background: #f1f3f5;
  border-bottom: 1px solid #e0e0e0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.evidence-idx {
  background: #409eff;
  color: #fff;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
}

.evidence-source {
  font-size: 13px;
  font-weight: 600;
  color: #495057;
}

.full-evidence-screenshot {
  padding: 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  background: #525659;
}

.full-evidence-canvas {
  max-width: 100%;
  box-shadow: 0 10px 25px rgba(0,0,0,0.3);
  border: 1px solid #000;
  background: #fff;
}

.full-thought-box {
  background: #fffbea;
  border-left: 5px solid #f6ad55;
  padding: 20px;
  font-size: 14px;
  line-height: 1.8;
  color: #4a5568;
  border-radius: 0 8px 8px 0;
  white-space: pre-wrap;
}

.full-conclusion-box {
  background: #fff;
  border: 1px solid #e2e8f0;
  padding: 25px;
  font-size: 16px;
  line-height: 1.7;
  color: #2d3748;
  border-radius: 12px;
  box-shadow: inset 0 2px 4px 0 rgba(0, 0, 0, 0.06);
  white-space: pre-wrap;
}

/* API Modal Styles */
.api-modal {
  background: #fff;
  width: 800px;
  max-width: 90vw;
  max-height: 85vh;
  border-radius: 16px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 40px rgba(0,0,0,0.2);
}

.api-modal-body {
  padding: 25px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 25px;
}

.api-section {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.api-info-card {
  background: #f8f9fa;
  border: 1px solid #e9ecef;
  border-radius: 8px;
  padding: 15px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.info-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.info-label {
  color: #6c757d;
  font-weight: 600;
  width: 100px;
}

.info-value {
  font-family: monospace;
  color: #333;
  word-break: break-all;
}

.method-tag {
  background: #e1f3ff;
  color: #409eff;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 700;
}

.code-block-wrapper {
  display: flex;
  flex-direction: column;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #333;
}

.code-header {
  background: #333;
  color: #fff;
  padding: 6px 12px;
  font-size: 11px;
  font-weight: 600;
}

.code-content {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 15px;
  margin: 0;
  font-family: 'Consolas', monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.mock-textarea {
  width: 100%;
  height: 80px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  padding: 12px;
  font-size: 13px;
  resize: none;
  outline: none;
  transition: border-color 0.2s;
}

.mock-textarea:focus {
  border-color: #409eff;
}

.mock-input-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-end;
}

.result-pre {
  max-height: 200px;
  overflow-y: auto;
}

.result-pre.error {
  color: #f56c6c;
}

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
