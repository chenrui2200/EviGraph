<template>
  <div class="public-report-container">
    <header class="report-header">
      <div class="app-info">
        <span class="app-icon">📚</span>
        <h1 class="app-title">{{ appName }}</h1>
      </div>
      <div class="header-query" v-if="currentQuery">
        <span class="query-label">问：</span>
        <span class="query-text">{{ currentQuery }}</span>
      </div>
      <div class="header-status" v-if="loading">
        <div class="spinner-sm"></div>
        <span>{{ loadingMessage }}</span>
      </div>
    </header>

    <main class="report-body" ref="scrollContainer">
      <!-- Welcome State -->
      <div v-if="!loading && !results.answer" class="welcome-screen">
        <div class="empty-icon">🔎</div>
        <h2>知识库分析助手</h2>
        <p>请在下方输入您的问题，我将为您检索图谱并生成详细的技术报告</p>
      </div>

      <!-- QA Result Report (与 /ai-qa Output 节点一致) -->
      <div v-if="results.answer" class="qa-result-container">
        <!-- 1. Knowledge Sources -->
        <div class="result-section">
          <div class="section-header">📚 检索依据原文</div>
          <div v-if="results.facts.length === 0" class="no-evidence-hint">暂无有效知识出处</div>
          <div v-else class="source-evidence-list">
            <div v-for="(fact, idx) in results.facts" :key="idx" class="evidence-item">
              <div class="evidence-meta">
                <span class="source-tag">来源 {{ idx + 1 }}: {{ fact.source }} <template v-if="fact.page">(P{{ fact.page }})</template></span>
                <span v-if="fact.relevance_score" class="evidence-score-badge" :style="{ background: getThresholdColor(fact.relevance_score) }">
                  {{ fact.relevance_score }}分
                </span>
              </div>
              <div class="evidence-screenshot-box">
                <canvas :ref="el => setEvidenceRef(el, idx)" class="evidence-canvas"></canvas>
                <div v-if="!fact.bbox" class="no-bbox-hint">（无位置信息，展示文本）: {{ fact.text }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- 2. Thinking Process -->
        <div v-if="parsedResult.thought" class="result-section">
          <div class="section-header">🧠 推理过程 (Thinking Process)</div>
          <div class="thought-content">{{ parsedResult.thought }}</div>
        </div>

        <!-- 3. Final Conclusion -->
        <div class="result-section">
          <div class="section-header">✨ 最终结论</div>
          <div class="conclusion-text">{{ parsedResult.conclusion }}</div>
        </div>
      </div>

      <!-- Loading Placeholder -->
      <div v-if="loading" class="report-loading-placeholder">
        <div class="skeleton-line title"></div>
        <div class="skeleton-line content"></div>
        <div class="skeleton-line content"></div>
      </div>
    </main>

    <!-- Bottom Search Area -->
    <footer class="report-footer">
      <div class="search-container">
        <textarea
          v-model="userInput"
          placeholder="请输入您的问题进行检索分析..."
          @keyup.enter.exact.prevent="handleSearch"
          :disabled="loading"
          rows="1"
          ref="textareaRef"
        ></textarea>
        <button class="search-btn" @click="handleSearch" :disabled="loading || !userInput.trim()">
          <span v-if="!loading">🔍 运行检索</span>
          <span v-else class="spinner-sm"></span>
        </button>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { getApp } from '../api/ai_app'
import { getProjectList, searchEntityTopicClause, rerankFacts, llmAnswer } from '../api/graph'

const route = useRoute()
const appId = route.params.id

// Projects list (for resolving latest graph_id)
const projects = ref([])

// App info
const appName = ref('加载中...')
const appConfig = ref({
  selectedGraphIds: [],
  rootTypes: ['Entity', 'Term'],
  similarityThreshold: 0,
  topK: 5,
  rerankMinScore: 0,
  temperature: 0.7,
})

// UI state
const userInput = ref('')
const loading = ref(false)
const loadingMessage = ref('正在分析中...')
const scrollContainer = ref(null)
const textareaRef = ref(null)
const currentQuery = ref('')

// Results state
const results = ref({
  rows: [],
  facts: [],
  rerank_results: [],
  answer: '',
})

// Evidence canvas refs
const evidenceCanvasRefs = ref({})

// ============ Computed ============
const parsedResult = computed(() => {
  const text = results.value.answer || ''
  let thought = ''
  let conclusion = text

  const thoughtMatch = text.match(/<(thought|think)>([\s\S]*?)<\/\1>/i)
  if (thoughtMatch) {
    thought = thoughtMatch[2].trim()
    conclusion = text.replace(thoughtMatch[0], '').trim()
  }
  return { thought, conclusion }
})

// ============ Threshold Colors ============
const getThresholdColor = (val) => {
  if (val < 40) return '#f56c6c'
  if (val < 70) return '#e6a23c'
  return '#67c23a'
}

// ============ Canvas Refs ============
const setEvidenceRef = (el, idx) => {
  if (el) evidenceCanvasRefs.value[idx] = el
}

// ============ PDF.js ============
let pdfjsLibInstance = null

const initPdfJs = async () => {
  if (window.pdfjsLib) {
    pdfjsLibInstance = window.pdfjsLib
    return
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js'
    script.onload = () => {
      pdfjsLibInstance = window['pdfjs-dist/build/pdf']
      pdfjsLibInstance.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js'
      resolve()
    }
    script.onerror = reject
    document.head.appendChild(script)
  })
}

const renderEvidenceScreenshots = async () => {
  if (!pdfjsLibInstance) await initPdfJs()
  const pdfDocCache = {}

  // 使用 top_k 截取后的 facts（已由后端过滤）
  const facts = results.value.facts

  for (let i = 0; i < facts.length; i++) {
    const fact = facts[i]
    const canvas = evidenceCanvasRefs.value[i]
    if (!canvas) continue
    if (!fact.bbox || !fact.graph_id || !fact.source) continue

    try {
      const cacheKey = `${fact.graph_id}:${fact.source}`
      let pdfDoc = pdfDocCache[cacheKey]

      if (!pdfDoc) {
        const apiUrl = `${window.location.origin}/api/graph/project/${fact.graph_id}/document/${encodeURIComponent(fact.source)}`
        const response = await fetch(apiUrl)
        if (!response.ok) continue
        const blob = await response.blob()
        const arrayBuffer = await blob.arrayBuffer()
        pdfDoc = await pdfjsLibInstance.getDocument({ data: new Uint8Array(arrayBuffer) }).promise
        pdfDocCache[cacheKey] = pdfDoc
      }

      const page = await pdfDoc.getPage(fact.page || 1)
      const context = canvas.getContext('2d')
      const bbox = fact.bbox
      const padding = 30
      const cropX = Math.max(0, bbox[0] - padding)
      const cropY = Math.max(0, bbox[1] - padding)
      const cropW = (bbox[2] - bbox[0]) + padding * 2
      const cropH = (bbox[3] - bbox[1]) + padding * 2
      const scale = 2.5
      const viewport = page.getViewport({ scale })

      const tempCanvas = document.createElement('canvas')
      tempCanvas.width = viewport.width
      tempCanvas.height = viewport.height
      await page.render({ canvasContext: tempCanvas.getContext('2d'), viewport }).promise

      const sX = cropX * scale, sY = cropY * scale, sW = cropW * scale, sH = cropH * scale
      const targetWidth = canvas.parentElement.clientWidth || 400
      canvas.width = targetWidth
      canvas.height = (sH / sW) * targetWidth
      context.drawImage(tempCanvas, sX, sY, sW, sH, 0, 0, canvas.width, canvas.height)

      context.strokeStyle = 'rgba(255, 69, 0, 0.7)'
      context.lineWidth = 3
      context.setLineDash([5, 3])
      const hX = (bbox[0] - cropX) * (canvas.width / cropW)
      const hY = (bbox[1] - cropY) * (canvas.height / cropH)
      const hW = (bbox[2] - bbox[0]) * (canvas.width / cropW)
      const hH = (bbox[3] - bbox[1]) * (canvas.height / cropH)
      context.strokeRect(hX, hY, hW, hH)
    } catch (err) {
      console.error('Error rendering screenshot:', err)
    }
  }
}

// ============ Load Projects (for resolving latest graph_id) ============
const loadProjects = async () => {
  try {
    const res = await getProjectList()
    if (res.success) {
      projects.value = res.data?.projects || []
    }
  } catch (err) {
    console.error('[PublicChat] loadProjects error:', err)
  }
}

// ============ Load App ============
const loadApp = async () => {
  try {
    // Load projects first to resolve latest graph_ids
    await loadProjects()

    const res = await getApp(appId)
    if (res.success) {
      const data = res.data
      appName.value = data.name
      const wf = data.workflow_data || {}

      // Resolve selectedGraphIds from selectedProjectIds (like AiQaView)
      // This ensures we use the latest graph_id for each project
      const savedProjectIds = wf.selectedProjectIds || []
      let resolvedGraphIds = []

      if (savedProjectIds.length > 0) {
        // Map project_ids to latest graph_ids
        resolvedGraphIds = savedProjectIds
          .map(pid => {
            const proj = projects.value.find(p => p.project_id === pid)
            return proj?.graph_id
          })
          .filter(Boolean)
      } else {
        // Backward compatibility: use selectedGraphIds directly
        resolvedGraphIds = wf.selectedGraphIds || []
      }

      appConfig.value = {
        selectedGraphIds: resolvedGraphIds,
        rootTypes: wf.rootTypes || ['Entity', 'Term'],
        similarityThreshold: wf.similarityThreshold ?? 50,
        topK: wf.topK ?? 5,
        rerankMinScore: wf.rerankMinScore ?? 0,
        temperature: wf.temperature || 0.7,
      }
    } else {
      console.error('[PublicChat] getApp failed:', res.error)
      alert('加载应用失败: ' + (res.error || '应用不存在'))
    }
  } catch (err) {
    console.error('[PublicChat] loadApp error:', err)
    alert('加载应用失败: ' + err.message)
  }
}

// ============ Main Search Flow ============
const handleSearch = async () => {
  if (!userInput.value.trim() || loading.value) return
  if (appConfig.value.selectedGraphIds.length === 0) {
    alert('该应用未配置知识库')
    return
  }

  const query = userInput.value.trim()
  currentQuery.value = query
  loading.value = true
  loadingMessage.value = '正在检索...'
  userInput.value = ''

  // Reset results
  results.value = { rows: [], facts: [], rerank_results: [], answer: '' }
  evidenceCanvasRefs.value = {}

  try {
    // ===== Stage 1: 检索 =====
    loadingMessage.value = '正在知识库检索...'
    const allRows = []
    for (const graphId of appConfig.value.selectedGraphIds) {
      for (const rootType of appConfig.value.rootTypes) {
        const res = await searchEntityTopicClause({
          graph_id: graphId,
          query: query,
          limit: 15,
          root_type: rootType,
        })
        if (res.success && res.data.rows) {
          allRows.push(...res.data.rows.map(r => ({ ...r, root_type: rootType })))
        }
      }
    }

    // 按相似度阈值过滤
    const filteredRows = allRows.filter(r => (r.relevance_score || 0) >= appConfig.value.similarityThreshold)
    results.value.rows = filteredRows
    results.value.facts = filteredRows.flatMap(r => r.facts || [])

    if (filteredRows.length === 0) {
      loadingMessage.value = '未检索到结果'
      loading.value = false
      return
    }

    // ===== Stage 2: 相关性重排 =====
    loadingMessage.value = '正在重排...'
    const rerankRes = await rerankFacts({
      rows: filteredRows,
      query: query,
      top_k: appConfig.value.topK,
      rerank_min_score: appConfig.value.rerankMinScore,
    })

    if (rerankRes.success && rerankRes.data) {
      results.value.rerank_results = rerankRes.data.scored_facts || []
      // facts = top_k 截取后的 facts（用于 LLM 推理和证据展示）
      results.value.facts = rerankRes.data.filtered_facts || rerankRes.data.scored_facts || []
      if (rerankRes.data.rows && rerankRes.data.rows.length > 0) {
        results.value.rows = rerankRes.data.rows
      }
    }

    // ===== Stage 3: LLM 生成答案 =====
    loadingMessage.value = '正在生成答案...'
    // filtered_facts 已由后端按 top_k 截取，直接使用
    const llmRes = await llmAnswer({
      facts: results.value.facts,
      query: query,
      temperature: appConfig.value.temperature,
    })

    if (llmRes.success && llmRes.data) {
      results.value.answer = llmRes.data.answer || ''
    }

    // Render evidence screenshots
    nextTick(() => {
      renderEvidenceScreenshots()
      scrollToTop()
    })
  } catch (err) {
    console.error('Report generation error:', err)
    alert('报告生成失败: ' + err.message)
  } finally {
    loading.value = false
    loadingMessage.value = '正在分析中...'
  }
}

const scrollToTop = () => {
  if (scrollContainer.value) {
    scrollContainer.value.scrollTop = 0
  }
}

onMounted(loadApp)
</script>

<style scoped>
.public-report-container {
  width: 100%; height: 100vh;
  display: flex; flex-direction: column;
  background: #f4f7f9;
  font-family: 'Inter', -apple-system, sans-serif;
  overflow: hidden;
}

.report-header {
  background: #fff; padding: 12px 24px;
  border-bottom: 1px solid #e0e0e0;
  display: flex; justify-content: space-between; align-items: center;
  flex-shrink: 0; box-shadow: 0 2px 8px rgba(0,0,0,0.05);
  z-index: 10;
}

.app-info { display: flex; align-items: center; gap: 12px; }
.app-icon { font-size: 24px; }
.app-title { font-size: 16px; font-weight: 800; margin: 0; color: #333; }

.header-status { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #409eff; font-weight: 600; }

.header-query {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 24px;
  overflow: hidden;
}
.query-label { font-size: 13px; color: #909399; font-weight: 600; flex-shrink: 0; }
.query-text {
  font-size: 13px;
  color: #303133;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.report-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  scroll-behavior: smooth;
}

/* ========== QA Result Container ========== */
.qa-result-container {
  max-width: 900px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.result-section {
  background: #fff;
  border-radius: 12px;
  padding: 25px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.03);
  border: 1px solid #eef2f5;
}

.section-header {
  font-size: 15px;
  font-weight: 800;
  margin-top: 0;
  margin-bottom: 16px;
  padding-bottom: 10px;
  border-bottom: 2px solid #f0f4f8;
  display: flex;
  align-items: center;
  color: #1a1a1a;
}

.no-evidence-hint {
  font-size: 13px;
  color: #999;
  text-align: center;
  padding: 20px;
}

.source-evidence-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.evidence-item {
  border: 1px solid #eee;
  border-radius: 8px;
  overflow: hidden;
}

.evidence-meta {
  padding: 8px 12px;
  background: #f8f9fa;
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 600;
  color: #666;
  border-bottom: 1px solid #eee;
}

.source-tag { color: #409eff; }
.evidence-score-badge {
  color: #fff;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 700;
}

.evidence-screenshot-box {
  background: #525659;
  padding: 10px;
  display: flex;
  justify-content: center;
}

.evidence-canvas {
  max-width: 100%;
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
  background: #fff;
}

.no-bbox-hint {
  background: #fff;
  padding: 15px;
  width: 100%;
  font-size: 13px;
  color: #666;
  text-align: left;
}

.thought-content {
  background: #fffbea;
  border-left: 4px solid #f6ad55;
  padding: 15px;
  font-size: 14px;
  line-height: 1.8;
  color: #5a6371;
  white-space: pre-wrap;
}

.conclusion-text {
  font-size: 16px;
  line-height: 1.8;
  color: #2c3e50;
  font-weight: 500;
  white-space: pre-wrap;
}

/* ========== Footer ========== */
.report-footer {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: #fff;
  padding: 15px 20px;
  border-top: 1px solid #e0e0e0;
  box-shadow: 0 -4px 15px rgba(0,0,0,0.05);
  display: flex;
  justify-content: center;
}

.search-container {
  max-width: 900px;
  width: 100%;
  display: flex;
  gap: 12px;
  align-items: flex-end;
}

.search-container textarea {
  flex: 1;
  padding: 12px 15px;
  border: 1px solid #ddd;
  border-radius: 10px;
  outline: none;
  transition: border-color 0.2s;
  font-family: inherit;
  font-size: 14px;
  resize: none;
  max-height: 120px;
  min-height: 44px;
}

.search-container textarea:focus {
  border-color: #409eff;
}

.search-btn {
  background: #000;
  color: #fff;
  border: none;
  height: 44px;
  padding: 0 24px;
  border-radius: 10px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}

.search-btn:hover:not(:disabled) {
  background: #333;
}

.search-btn:disabled {
  background: #ccc;
  cursor: not-allowed;
}

/* ========== Misc ========== */
.loading-screen, .welcome-screen {
  text-align: center;
  margin-top: 10vh;
  color: #999;
}

.empty-icon {
  font-size: 60px;
  margin-bottom: 20px;
  opacity: 0.3;
}

.report-loading-placeholder {
  max-width: 900px;
  margin: 0 auto;
  padding: 20px;
}

.skeleton-line {
  background: #eef2f5;
  border-radius: 4px;
  margin-bottom: 12px;
  animation: pulse 1.5s infinite;
}

.skeleton-line.title { width: 30%; height: 20px; }
.skeleton-line.content { width: 100%; height: 15px; }

@keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }
@keyframes spin { to { transform: rotate(360deg); } }
.spinner-sm { width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3); border-top: 2px solid #fff; border-radius: 50%; animation: spin 1s linear infinite; display: inline-block; }

@media (max-width: 600px) {
  .evidence-grid { grid-template-columns: 1fr; }
}
</style>
