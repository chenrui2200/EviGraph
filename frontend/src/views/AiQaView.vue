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
          <span v-if="!publishing">{{ isPublished ? '🌐 已发布' : '🚀 发布' }}</span>
          <span v-else class="spinner-sm"></span>
        </button>
        <button v-if="isPublished" class="icon-btn info-btn" @click="showApiModal = true" title="查看发布详情">
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

              <!-- 检索配置 (与 hit-test 对齐) -->
              <div class="search-options">
                <!-- 根节点类型 -->
                <div class="search-option-row">
                  <span class="option-label">根节点类型</span>
                  <div class="root-type-checks">
                    <label class="checkbox-label root-type-check">
                      <input type="checkbox" value="Entity" v-model="workflowData.rootTypes" />
                      <span>Entity</span>
                    </label>
                    <label class="checkbox-label root-type-check">
                      <input type="checkbox" value="Term" v-model="workflowData.rootTypes" />
                      <span>Term</span>
                    </label>
                  </div>
                </div>

                <!-- 相似度阈值：检索后预过滤，减少 reranking 数量 -->
                <div class="search-option-row sim-row">
                  <span class="option-label">相似度阈值</span>
                  <div class="sim-slider-wrap">
                    <input
                      type="range"
                      v-model.number="workflowData.similarityThreshold"
                      min="50"
                      max="100"
                      step="5"
                      class="sim-slider"
                    />
                    <span class="sim-value" :class="simValueClass">{{ workflowData.similarityThreshold }}</span>
                  </div>
                </div>

              </div>

              <!-- 检索分析摘要 (hit-test 风格) -->
              <div v-if="results.rows.length > 0" class="results-summary-card">
                <div class="summary-title">💡 检索分析</div>
                <div class="summary-content">
                  本次检索命中了 <strong>{{ results.rows.length }}</strong> 个根节点
                  <span v-if="results.rows.length > 0">
                    （Term: {{ results.rows.filter(r => r.object_node?.labels?.includes('Term')).length }},
                    Entity: {{ results.rows.filter(r => r.object_node?.labels?.includes('Entity') && !r.object_node?.labels?.includes('Term')).length }}），
                    共 <strong>{{ results.rows.reduce((s, r) => s + (r.facts?.length || 0), 0) }}</strong> 条关联事实。
                  </span>
                  <template v-if="results.searchTimings.object_s || results.searchTimings.term_s">
                    <br/>耗时：{{ (results.searchTimings.object_s || 0).toFixed(2) }}s + {{ (results.searchTimings.term_s || 0).toFixed(2) }}s
                  </template>
                </div>
              </div>

              <!-- DFS 检索结果 (hit-test 风格 ObjectFirstRow 展示) -->
              <div v-if="results.rows.length > 0" class="object-rows-list">
                <div
                  v-for="(row, rowIdx) in results.rows"
                  :key="rowIdx"
                  class="object-row-card"
                >
                  <!-- Root 节点标题 -->
                  <div class="object-row-header">
                    <div class="object-name">
                      <span class="object-badge" :class="{ term: row.object_node?.labels?.includes('Term') }">
                        {{ row.object_node?.labels?.find(l => l !== 'Entity' && l !== 'Node') || row.object_node?.labels?.[0] || 'Entity' }}
                      </span>
                      <strong>{{ row.object_node?.name || 'Unknown' }}</strong>
                    </div>
                    <div class="object-score">
                      <span class="score-tag">{{ (row.relevance_score || 0).toFixed(1) }}</span>
                    </div>
                  </div>

                  <!-- Object 摘要 -->
                  <div v-if="row.object_node?.summary" class="object-summary">
                    {{ row.object_node.summary }}
                  </div>

                  <!-- DFS 遍历路径 -->
                  <div v-if="row.traversal_paths?.length > 1" class="traversal-path">
                    <div class="path-header">🔱 DFS 遍历路径 (深度 {{ row.traversal_paths.length - 1 }})</div>
                    <div class="path-nodes">
                      <template v-for="(pNode, nIdx) in row.traversal_paths" :key="nIdx">
                        <span class="path-node" :class="`depth-${pNode.depth}`">
                          {{ pNode.name || pNode.uuid?.slice(0, 8) }}
                        </span>
                        <span v-if="nIdx < row.traversal_paths.length - 1" class="path-arrow">→</span>
                      </template>
                    </div>
                  </div>

                  <!-- 关联事实列表 -->
                  <div class="object-facts">
                    <div class="facts-header">📋 关联事实 ({{ row.facts?.length || 0 }})</div>
                    <div
                      v-for="(fact, fIdx) in row.facts"
                      :key="fIdx"
                      class="fact-item"
                    >
                      <div class="fact-text">{{ fact.text }}</div>
                      <div class="fact-meta">
                        <span class="source-tag">
                          📄 {{ fact.source || 'Unknown' }}
                          <span v-if="fact.page">(P{{ fact.page }})</span>
                        </span>
                        <span class="depth-tag" v-if="fact.traversal_depth !== undefined">深度{{ fact.traversal_depth }}</span>
                        <button
                          v-if="fact.source && fact.source !== 'Local Search' && fact.source !== 'Graph' && fact.source !== 'Knowledge Graph' && fact.source !== 'Graph Path Extension'"
                          class="locate-btn"
                          @click="viewDocument(fact)"
                        >
                          定位文档
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 无结果提示 -->
              <div v-else-if="results.facts.length > 0 && node.status === 'completed'" class="no-results-hint">
                未检索到符合条件的结果
              </div>
            </div>

            <!-- Rerank Node Content -->
            <div v-if="node.type === 'rerank'" class="rerank-content">
              <div class="rerank-config">
                <div class="rerank-config-row">
                  <div class="threshold-label">
                    <span>🎯 保留 Top K 样本:</span>
                    <input type="number" v-model.number="workflowData.topK" min="1" max="50" step="1" class="topk-input" />
                  </div>
                </div>
                <div class="threshold-label" style="margin-top: 6px;">
                  <span>🎯 重排得分阈值:</span>
                  <span class="threshold-value" :class="getThresholdClass(workflowData.rerankMinScore)">{{ workflowData.rerankMinScore }}分</span>
                </div>
                <div class="thermometer-container">
                  <input type="range" v-model.number="workflowData.rerankMinScore" min="0" max="100" step="5" class="thermometer-input" />
                  <div class="thermometer-track">
                    <div class="thermometer-fill" :style="{ width: (workflowData.rerankMinScore / 100 * 100) + '%', background: getThresholdColor(workflowData.rerankMinScore) }"></div>
                  </div>
                </div>
                <p class="config-hint">过滤掉 bge-reranker-v2-m3 打分低于阈值的不相关事实，再取 Top K</p>
              </div>

              <div v-if="results.rerank_results.length === 0" class="rerank-placeholder">
                等待运行...
              </div>
              <div v-else class="rerank-results-list">
                <div class="rerank-summary">
                  bge-reranker-v2-m3 已完成精排，共 {{ results.facts.length }} 条（Top {{ workflowData.topK }}，最低 {{ workflowData.rerankMinScore }}分）
                </div>
                <div class="rerank-scroll-area">
                  <div v-for="(fact, idx) in results.facts" :key="idx" class="rerank-item-card" :class="{ 'high-score': idx < workflowData.topK, 'below-threshold': idx >= workflowData.topK }">
                    <div class="rerank-item-header">
                      <span class="rerank-score">{{ fact.relevance_score }}分</span>
                      <span class="rerank-index">Rank #{{ idx + 1 }}</span>
                    </div>
                    <div v-if="fact.relevance_reasoning" class="rerank-reason">理由: {{ fact.relevance_reasoning }}</div>
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
                  <div class="section-header">
                    📚 检索依据原文
                  </div>
                  <div v-if="results.facts.length === 0" class="no-evidence-hint">
                    暂无有效知识出处
                  </div>
                  <div v-else class="source-evidence-list">
                    <div v-for="(fact, idx) in results.facts" :key="idx" class="evidence-item">
                      <div class="evidence-meta">
                        <span class="source-tag">来源 {{ idx + 1 }}: {{ fact.source }} <template v-if="fact.page">(P{{ fact.page }})</template></span>
                        <span v-if="fact.relevance_score" class="evidence-score-badge" :style="{ background: getThresholdColor(fact.relevance_score) }">
                          {{ fact.relevance_score }}分
                        </span>
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
      <PdfViewer
        v-model="showDocViewer"
        class="document-viewer"
        :filename="currentDoc.filename"
        :url="currentDoc.url"
        :page="currentDoc.page"
        :bbox="currentDoc.bbox"
        :page-width="currentDoc.pageWidth"
        :page-height="currentDoc.pageHeight"
        :pdf-bboxes="currentDoc.pdfBboxes"
      />
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
            <div class="full-section-title">
              📚 检索知识出处 (Knowledge Evidence)
            </div>
            <div v-if="results.facts.length === 0" class="no-evidence-hint full-no-evidence">
              暂无有效知识出处
            </div>
            <div v-else class="full-evidence-grid">
              <div v-for="(fact, idx) in results.facts" :key="idx" class="full-evidence-card">
                <div class="evidence-header">
                  <span class="evidence-idx">#{{ idx + 1 }}</span>
                  <span class="evidence-source">{{ fact.source }} <template v-if="fact.page">(第 {{ fact.page }} 页)</template></span>
                  <span v-if="fact.relevance_score" class="full-evidence-score" :style="{ color: getThresholdColor(fact.relevance_score) }">
                    得分: {{ fact.relevance_score }}
                  </span>
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
          <h3><span class="header-icon">🌐</span> 应用发布详情</h3>
          <button class="close-btn" @click="showApiModal = false">×</button>
        </div>
        <div class="modal-body api-modal-body">
          <div class="api-tabs">
            <div class="api-section">
              <div class="section-title">方式 1: Web 应用嵌入 (Iframe)</div>
              <div class="api-info-card">
                <div class="info-row">
                  <span class="info-label">访问地址:</span>
                  <a :href="publicChatUrl" target="_blank" class="info-value link">{{ publicChatUrl }}</a>
                </div>
              </div>
              <div class="code-block-wrapper">
                <div class="code-header">嵌入代码 (HTML)</div>
                <pre class="code-content">{{ iframeCode }}</pre>
              </div>
            </div>

            <div class="api-section">
              <div class="section-title">方式 2: API 接口调用</div>
              <div class="api-info-card">
                <div class="info-row">
                  <span class="info-label">接口地址:</span>
                  <code class="info-value">{{ apiBaseUrl }}</code>
                </div>
                <div class="info-row">
                  <span class="info-label">请求方法:</span>
                  <span class="info-value method-tag">POST</span>
                </div>
              </div>

              <div class="code-block-wrapper">
                <div class="code-header">Curl 调用示例</div>
                <pre class="code-content">curl -X POST {{ apiBaseUrl }} \
     -H "Content-Type: application/json" \
     -d '{"query": "您的问题"}'</pre>
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
import { ref, onMounted, onUnmounted, computed, nextTick, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getProjectList, updateProject, rerankFacts, llmAnswer } from '../api/graph'
import { hitTestSearch } from '../composables/useHitTestSearch'
import { saveApp, getApp, publishApp, executeAppApi } from '../api/ai_app'
import PdfViewer from '../components/PdfViewer.vue'

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
const publicChatUrl = computed(() => `${window.location.origin}/chat/${appId.value}`)
const iframeCode = computed(() => `<iframe src="${publicChatUrl.value}" width="100%" height="600px" frameborder="0"></iframe>`)

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

const renderEvidenceScreenshots = async (type = 'node') => {
  const targetFacts = results.value.facts
  // 保留索引以便匹配 canvas ref
  const validFacts = targetFacts.reduce((acc, f, idx) => {
    if (f.bbox && f.bbox.length === 4 && f.graph_id && f.source) {
      acc.push({ ...f, _renderIdx: idx })
    }
    return acc
  }, [])

  if (validFacts.length === 0) return

  // Ensure PDF.js is ready
  if (!pdfjsLib.value) await initPdfJs()

  // Cache for PDF documents
  const pdfDocCache = {}

  for (const fact of validFacts) {
    const renderIdx = fact._renderIdx
    const canvas = evidenceCanvasRefs.value[type][renderIdx]
    if (!canvas) continue

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

      // 使用 pdf.js 实际解析的页面尺寸作为比例基准（和 PdfViewer 完全一致）
      const unscaledViewport = page.getViewport({ scale: 1 })
      const pageW = unscaledViewport.width
      const pageH = unscaledViewport.height

      // Logic: 横向截取整页宽度，纵向截取 bbox 附近区域
      const bbox = fact.bbox
      const vPadding = type === 'modal' ? 360 : 180
      const rawCropY = bbox[1] - vPadding
      const rawCropH = (bbox[3] - bbox[1]) + vPadding * 2

      // 横向：完整页面宽度，确保 PDF 左右不截断
      const cropX = 0
      const cropW = pageW
      // 纵向：限制在页面边界内
      const cropY = Math.max(0, Math.min(rawCropY, pageH - rawCropH))
      const cropH = Math.min(rawCropH, pageH - cropY)

      const scale = type === 'modal' ? 3.0 : 2.0
      const viewport = page.getViewport({ scale })

      // 用 page_width 计算实际比例，处理 pdf.js 解析宽度与 MinerU 报告值不一致的情况
      const xRatio = viewport.width / pageW
      const yRatio = viewport.height / pageH

      const tempCanvas = document.createElement('canvas')
      tempCanvas.width = viewport.width
      tempCanvas.height = viewport.height
      const tempCtx = tempCanvas.getContext('2d')

      await page.render({ canvasContext: tempCtx, viewport }).promise

      const sX = cropX * xRatio
      const sY = cropY * yRatio
      const sW = cropW * xRatio
      const sH = cropH * yRatio

      const targetWidth = type === 'modal' ? 1100 : 480
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
      console.error(`Failed to render screenshot for fact ${renderIdx}:`, err)
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

const viewDocument = async (fact) => {
  if (!fact.source || fact.source === 'Unknown') return

  const identifier = fact.graph_id
  const filename = fact.source
  const page = fact.page || 1
  const bbox = fact.bbox || null
  const pageWidth = fact.page_width || 0
  const pageHeight = fact.page_height || 0
  const pdfBboxes = fact.pdf_bboxes || null

  currentDoc.value = {
    filename,
    url: `${window.location.origin}/api/graph/project/${identifier}/document/${encodeURIComponent(filename)}?t=${Date.now()}`,
    page,
    bbox,
    pageWidth,
    pageHeight,
    pdfBboxes
  }
  showDocViewer.value = true
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
  similarityThreshold: 50,   // 相似度阈值：检索后预过滤，减少 reranking 数量
  topK: 5,                 // top_k：bge-reranker-v2-m3 精排后保留得分最高的 K 条
  rerankMinScore: 0,        // 重排分数阈值：低于此分数的 facts 会被过滤（默认0）
  rootTypes: ['Entity', 'Term'],  // 根节点类型（与 hit-test 对齐）
})

const results = ref({
  facts: [],
  rows: [],      // ObjectFirstRow structure from DFS flow
  searchTimings: { object_s: 0, term_s: 0, total_s: 0 },  // 与 hit-test 对齐
  similarityThreshold: 0,   // 相似度阈值（retrieval 用）
  topK: 5,               // top_k（rerank 用）
  rerankMinScore: 0,      // 重排分数阈值（rerank 用）
  answer: '',
  rerank_results: [],
  prompts: {
    system: '',
    user: ''
  }
})

// Evidence Canvas management

// Evidence Canvas management
const evidenceCanvasRefs = ref({ node: {}, modal: {} })
// canvas refs 使用原始 fact 索引作为 key，确保阈值变化时 ref 稳定
const setEvidenceRef = (el, factIndex, type = 'node') => {
  if (el) evidenceCanvasRefs.value[type][factIndex] = el
}

const showPromptModal = ref(false)

// Node Positions and Config
const nodes = ref([
  { id: 'n1', type: 'input', title: '用户输入 (Input)', icon: '📝', x: 50, y: 150, status: 'pending' },
  { id: 'n2', type: 'retrieval', title: '知识库检索 (Retrieval)', icon: '🔍', x: 350, y: 150, status: 'pending' },
  { id: 'n_rerank', type: 'rerank', title: 'bge-reranker-v2-m3 精排', icon: '🃏', x: 650, y: 150, status: 'pending' },
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
      projects.value = res.data?.projects || []
      const projectId = (!props.id?.startsWith('app_') && props.id !== 'default' && props.id !== 'new') ? props.id : null
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
    rows: [],
    searchTimings: { object_s: 0, term_s: 0, total_s: 0 },
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

// 相似度阈值颜色（中=橙，高=绿），与 hit-test 对齐
const simValueClass = computed(() => {
  if (workflowData.value.similarityThreshold >= 75) return 'high'
  return 'mid'
})

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
  const workflowStart = Date.now()

  try {
    // ===== Stage 1: 知识库检索 =====
    const retrievalNode = nodes.value.find(n => n.type === 'retrieval')
    if (retrievalNode) retrievalNode.status = 'running'

    const { rows, durationMs } = await hitTestSearch({
      graphId: workflowData.value.selectedGraphIds,
      query: workflowData.value.query,
      limit: 15,
      similarityThreshold: workflowData.value.similarityThreshold,
      rootTypes: workflowData.value.rootTypes,
    })

    const retrievalDuration = (durationMs / 1000).toFixed(2)
    if (retrievalNode) {
      retrievalNode.status = 'completed'
      retrievalNode.duration = retrievalDuration
    }

    results.value.rows = rows
    results.value.facts = rows.flatMap(r => r.facts || [])

    // ===== Stage 2: 相关性重排 =====
    const rerankNode = nodes.value.find(n => n.type === 'rerank')
    if (rerankNode) rerankNode.status = 'running'

    const rerankStart = Date.now()
    const rerankRes = await rerankFacts({
      rows: rows,
      query: workflowData.value.query,
      top_k: workflowData.value.topK,
      rerank_min_score: workflowData.value.rerankMinScore,
    })

    // fallback: 使用原始 facts
    let filteredFacts = results.value.facts

    if (rerankRes.success && rerankRes.data) {
      const rerankDuration = ((Date.now() - rerankStart) / 1000).toFixed(2)
      if (rerankNode) {
        rerankNode.status = 'completed'
        rerankNode.duration = rerankDuration
      }
      results.value.rerank_results = rerankRes.data.scored_facts || []
      results.value.facts = rerankRes.data.filtered_facts || rerankRes.data.scored_facts || []
      filteredFacts = rerankRes.data.filtered_facts || rerankRes.data.scored_facts || []
      if (rerankRes.data.rows && rerankRes.data.rows.length > 0) {
        results.value.rows = rerankRes.data.rows
      }
    } else {
      if (rerankNode) {
        rerankNode.status = 'completed'
        rerankNode.duration = ((Date.now() - rerankStart) / 1000).toFixed(2)
      }
    }

    // ===== Stage 3: LLM 推理 =====
    const llmNode = nodes.value.find(n => n.type === 'llm')
    if (llmNode) llmNode.status = 'running'

    const llmStart = Date.now()
    const llmRes = await llmAnswer({
      facts: filteredFacts,
      query: workflowData.value.query,
      temperature: workflowData.value.temperature,
    })

    if (llmRes.success && llmRes.data) {
      const llmDuration = ((Date.now() - llmStart) / 1000).toFixed(2)
      if (llmNode) {
        llmNode.status = 'completed'
        llmNode.duration = llmDuration
      }
      results.value.answer = llmRes.data.answer || ''
      if (llmRes.data.prompts) {
        results.value.prompts = llmRes.data.prompts
      }
    } else {
      if (llmNode) {
        llmNode.status = 'completed'
        llmNode.duration = ((Date.now() - llmStart) / 1000).toFixed(2)
      }
    }

    // ===== Stage 4: 输出 =====
    const outputNode = nodes.value.find(n => n.type === 'output')
    if (outputNode) {
      outputNode.status = 'completed'
      outputNode.duration = ((Date.now() - workflowStart) / 1000).toFixed(2)
      nextTick(() => {
        renderEvidenceScreenshots('node')
      })
    }
  } catch (err) {
    console.error('Workflow error:', err)
    alert('流程执行出错: ' + (err.message || err))
    nodes.value.forEach(n => { if (n.status === 'running') n.status = 'failed' })
  } finally {
    running.value = false
  }
}

const saveWorkflowApp = async () => {
  saving.value = true
  try {
    // 保存时同步 selectedProjectIds（用于加载时映射最新 graph_id）
    const selectedProjectIds = workflowData.value.selectedGraphIds
      .map(gid => {
        const p = projects.value.find(p => p.graph_id === gid)
        return p?.project_id
      })
      .filter(Boolean)
    const payload = {
      app_id: appId.value,
      name: appName.value,
      nodes: nodes.value,
      workflow_data: {
        ...workflowData.value,
        selectedProjectIds,
      },
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
      // 同步 selectedGraphIds：优先用 selectedProjectIds 映射最新 graph_id
      const savedProjectIds = workflowData.value.selectedProjectIds || []
      if (savedProjectIds.length > 0) {
        // 用保存的 project_id 查找当前最新 graph_id
        const syncedGraphIds = savedProjectIds
          .map(pid => {
            const proj = projects.value.find(p => p.project_id === pid)
            return proj?.graph_id
          })
          .filter(Boolean)
        workflowData.value.selectedGraphIds = syncedGraphIds
      } else {
        // 兼容旧数据：没有 selectedProjectIds，移除已失效的 graph_id
        const savedGraphIds = workflowData.value.selectedGraphIds || []
        workflowData.value.selectedGraphIds = savedGraphIds.filter(gid =>
          projects.value.some(p => p.graph_id === gid)
        )
      }

      // 额外检查：如果路由带了 projectId，确保该项目最新 graph_id 被选中
      const projectId = (!props.id?.startsWith('app_') && props.id !== 'default' && props.id !== 'new') ? props.id : null
      if (projectId) {
        const proj = projects.value.find(p => p.project_id === projectId)
        if (proj?.graph_id && !workflowData.value.selectedGraphIds.includes(proj.graph_id)) {
          workflowData.value.selectedGraphIds.push(proj.graph_id)
        }
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
  font-size: small;
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
  min-height: 200px;
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

.topk-input {
  width: 60px;
  padding: 4px 8px;
  border: 1.5px solid #dcdfe6;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  text-align: center;
  color: #409eff;
  background: #f5f7fa;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.topk-input:focus {
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.15);
  background: #fff;
}
.topk-input::-webkit-inner-spin-button,
.topk-input::-webkit-outer-spin-button {
  opacity: 1;
  height: 20px;
}

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
.rerank-item-card.below-threshold {
  opacity: 0.5;
  border-left: 4px solid #909399;
  background: #f4f4f5;
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
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.evidence-score-badge {
  font-size: 10px;
  color: #fff;
  padding: 1px 6px;
  border-radius: 8px;
  font-weight: 700;
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

.no-evidence-hint {
  font-size: 12px;
  color: #e6a23c;
  text-align: center;
  padding: 20px;
  background: #fdf6ec;
  border-radius: 6px;
  margin: 10px 0;
}

.full-no-evidence {
  padding: 40px;
  font-size: 14px;
}

.filter-summary {
  font-size: 11px;
  color: #909399;
  margin-left: 8px;
  font-weight: normal;
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
  flex: 1;
}

.full-evidence-score {
  font-size: 13px;
  font-weight: 800;
  background: #f8f9fa;
  padding: 2px 10px;
  border-radius: 12px;
  border: 1px solid #dee2e6;
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

/* ===== hit-test 对齐样式 ===== */

/* 检索配置区域 */
.search-options {
  padding: 8px 12px;
  border-top: 1px dashed #e8e8e8;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.search-option-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.option-label {
  font-size: 11px;
  color: #606266;
  font-weight: 600;
  min-width: 60px;
}

.root-type-checks {
  display: flex;
  gap: 8px;
}

.root-type-check {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  cursor: pointer;
}

.root-type-check input {
  cursor: pointer;
}

/* 深度选择 */
.depth-pills {
  display: flex;
  gap: 4px;
}

.depth-pill {
  padding: 2px 10px;
  border: 1px solid #dcdfe6;
  border-radius: 12px;
  background: #fff;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
  color: #606266;
}

.depth-pill.active {
  background: #409eff;
  color: #fff;
  border-color: #409eff;
}

/* 相似度阈值滑块 */
.sim-slider-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.sim-slider {
  flex: 1;
  height: 4px;
  cursor: pointer;
  accent-color: #409eff;
}

.sim-value {
  font-size: 12px;
  font-weight: 700;
  min-width: 28px;
  text-align: center;
}

.sim-value.mid { color: #e6a23c; }
.sim-value.high { color: #67c23a; }

/* 检索分析摘要卡片 */
.results-summary-card {
  margin: 8px 12px;
  padding: 10px 12px;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 8px;
}

.summary-title {
  font-size: 12px;
  font-weight: 700;
  color: #d48806;
  margin-bottom: 6px;
}

.summary-content {
  font-size: 12px;
  color: #5c3d00;
  line-height: 1.6;
}

/* ObjectFirstRow DFS 结果列表 */
.object-rows-list {
  padding: 0 8px 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 350px;
  overflow-y: auto;
}

.object-row-card {
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  padding: 10px;
  transition: border-color 0.2s;
}

.object-row-card:hover {
  border-color: #409eff;
}

.object-row-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.object-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.object-badge {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  background: #e6f7ff;
  color: #0969da;
}

.object-badge.term {
  background: #fff0f6;
  color: #cf3385;
}

.object-score {
  display: flex;
  align-items: center;
  gap: 6px;
}

.score-tag {
  background: #ecf5ff;
  color: #409eff;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
}

.object-summary {
  font-size: 11px;
  color: #909399;
  margin-bottom: 6px;
  line-height: 1.4;
  padding-left: 4px;
}

/* DFS 遍历路径 */
.traversal-path {
  background: #f8f9fa;
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 6px;
}

.path-header {
  font-size: 10px;
  font-weight: 700;
  color: #606266;
  margin-bottom: 4px;
}

.path-nodes {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.path-node {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}

.path-node.depth-0 { background: #dbeafe; color: #1d4ed8; }
.path-node.depth-1 { background: #dcfce7; color: #15803d; }
.path-node.depth-2 { background: #fef3c7; color: #b45309; }
.path-node.depth-3 { background: #fce7f3; color: #be185d; }

.path-arrow {
  color: #c0c4cc;
  font-size: 10px;
}

/* 关联事实列表 */
.object-facts {
  border-top: 1px dashed #f0f0f0;
  padding-top: 6px;
}

.facts-header {
  font-size: 11px;
  font-weight: 700;
  color: #409eff;
  margin-bottom: 4px;
}

.fact-item {
  padding: 6px 0;
  border-bottom: 1px solid #f5f5f5;
}

.fact-item:last-child {
  border-bottom: none;
}

.depth-tag {
  background: #f0f0f0;
  color: #909399;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 10px;
}

.no-results-hint {
  text-align: center;
  color: #999;
  font-size: 12px;
  padding: 12px;
}

.locate-btn {
  padding: 2px 8px;
  background: #f0f7ff;
  color: #409eff;
  border: 1px solid #c6e2ff;
  border-radius: 4px;
  font-size: 10px;
  cursor: pointer;
  transition: all 0.2s;
}

.locate-btn:hover {
  background: #409eff;
  color: #fff;
}

.source-tag {
  font-size: 11px;
  color: #909399;
}

.retrieval-content {
  display: flex;
  flex-direction: column;
  gap: 0;
  max-height: 700px;
  overflow-y: auto;
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
