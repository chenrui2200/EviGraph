<template>
  <div class="public-report-container">
    <header class="report-header">
      <div class="app-info">
        <span class="app-icon">📚</span>
        <h1 class="app-title">{{ appName }}</h1>
      </div>
      <div class="header-query" v-if="currentQuery">
        <div class="query-bubble">
          <span class="query-label">问</span>
          <span class="query-text">{{ currentQuery }}</span>
        </div>
      </div>
      <div class="header-status" v-if="loading">
        <div class="spinner-sm"></div>
        <span>{{ loadingMessage }}</span>
      </div>
    </header>

    <main class="report-body" ref="scrollContainer">
      <!-- Welcome State -->
      <div v-if="!loading && results.facts.length === 0 && !results.searched" class="welcome-screen">
        <div class="empty-icon">🔎</div>
        <h2>知识库分析助手</h2>
        <p>请在下方输入您的问题，我将为您检索图谱知识出处</p>
      </div>

      <!-- No Results State -->
      <div v-if="!loading && results.facts.length === 0 && results.searched" class="qa-result-container no-results">
        <div class="empty-icon">📭</div>
        <h2>未检索到相关结果</h2>
        <p>当前知识库中未找到与"{{ currentQuery }}"相关的内容，请尝试更换关键词或调整检索范围</p>
      </div>

      <!-- QA Result Report (与 /ai-qa Output 节点一致) -->
      <div v-if="results.facts.length > 0" class="qa-result-container">
        <!-- Knowledge Sources -->
        <div class="result-section">
          <div class="section-header">📚 检索依据原文</div>
          <div class="source-evidence-list">
            <div
              v-for="(group, sourceName) in groupedFacts"
              :key="sourceName"
              class="evidence-section"
            >
              <div class="evidence-section-header" @click="toggleSection(sourceName)">
                <span class="section-toggle">{{ isSectionOpen(sourceName) ? '▼' : '▶' }}</span>
                <span class="section-title">📄 {{ sourceName }} ({{ group.length }}条)</span>
              </div>
              <div v-show="isSectionOpen(sourceName)" class="evidence-section-body">
                <div
                  v-for="fact in group"
                  :key="fact._idx"
                  class="evidence-item"
                >
                  <div class="evidence-text-row">
                    <span v-if="fact.relevance_score" class="evidence-score-badge-small" :style="{ background: getThresholdColor(fact.relevance_score) }">
                      {{ fact.relevance_score }}分
                    </span>
                    <span class="evidence-text">{{ fact.text }}</span>
                    <span v-if="getFactPageBboxes(fact).length" class="evidence-page">{{ formatPageRange(getFactPageBboxes(fact)) }}</span>
                  </div>
                  <!-- 关联表格图片 -->
                  <div v-if="fact.related_tables?.length" class="evidence-tables">
                    <div
                      v-for="(tbl, tIdx) in fact.related_tables.filter(t => t.table_image_base64_content)"
                      :key="tIdx"
                      class="table-image-item"
                    >
                      <div v-if="tbl.caption" class="table-caption">{{ tbl.caption }}</div>
                      <img
                        :src="tbl.table_image_base64_content.startsWith('data:') ? tbl.table_image_base64_content : 'data:image/png;base64,' + tbl.table_image_base64_content"
                        :alt="tbl.caption || '表格图片'"
                        class="table-image"
                      />
                    </div>
                  </div>
                  <div v-if="fact.topic" class="evidence-topics">
                    <span class="topics-label">关联 Topic:</span>
                    <span class="topic-tag">{{ fact.topic }}</span>
                  </div>
                  <div class="evidence-pdf-fold">
                    <button class="fold-btn" @click.stop="togglePdf(fact._idx)">
                      {{ isPdfOpen(fact._idx) ? '收起PDF位置 ▲' : '查看PDF位置 ▼' }}
                    </button>
                    <div v-show="isPdfOpen(fact._idx)" class="evidence-pdf-content">
                      <template v-if="getFactPageBboxes(fact).length">
                        <div
                          v-for="(pb, pi) in getFactPageBboxes(fact)"
                          :key="pi"
                          class="page-screenshot"
                        >
                          <div class="page-label">第 {{ pb.page }} 页</div>
                          <canvas :ref="el => setEvidenceRef(el, fact._idx + '-' + pi)" class="evidence-canvas"></canvas>
                        </div>
                      </template>
                      <div v-else class="no-bbox-hint">（无位置信息，展示文本）: {{ fact.text }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
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
    <footer v-if="!route.query.question" class="report-footer">
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
import { getProjectList, rerankFacts } from '../api/graph'
import { hitTestSearch } from '../composables/useHitTestSearch'

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
const isRenderingEvidence = ref(false)

// Results state
const results = ref({
  rows: [],
  facts: [],
  rerank_results: [],
  searched: false,
})

// Pending URL params for auto-open evidence
const pendingUrlParams = ref(null)

// Evidence canvas refs
const evidenceCanvasRefs = ref({})

// ============ Fold State ============
const expandedSections = ref(new Set())
const expandedPdfs = ref(new Set())

const groupedFacts = computed(() => {
  const groups = {}
  for (let i = 0; i < results.value.facts.length; i++) {
    const fact = results.value.facts[i]
    const key = fact.source || 'Unknown'
    if (!groups[key]) groups[key] = []
    groups[key].push({ ...fact, _idx: i })
  }
  return groups
})

const isSectionOpen = (source) => expandedSections.value.has(source)
const isPdfOpen = (idx) => expandedPdfs.value.has(idx)

const toggleSection = (source) => {
  const s = expandedSections.value
  if (s.has(source)) s.delete(source)
  else s.add(source)
}

const togglePdf = (idx) => {
  const s = expandedPdfs.value
  if (s.has(idx)) s.delete(idx)
  else {
    s.add(idx)
    nextTick(() => renderEvidenceScreenshots())
  }
}

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

// 组件级 PDF 缓存和 tempCanvas 池（避免重复下载和频繁创建 canvas）
const _pdfDocCache = {}
const _tempCanvasPool = {}

function _getTempCanvas(width, height) {
  const key = `${width}:${height}`
  let canvas = _tempCanvasPool[key]
  if (canvas) {
    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, width, height)
    return { canvas, ctx }
  }
  canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  _tempCanvasPool[key] = canvas
  return { canvas, ctx: canvas.getContext('2d') }
}

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

// 将 fact 的 pdf_bboxes / bbox 统一为 [{ page, bbox }, ...]
const getFactPageBboxes = (fact) => {
  if (fact.pdf_bboxes && Array.isArray(fact.pdf_bboxes) && fact.pdf_bboxes.length > 0) {
    return fact.pdf_bboxes
      .filter(b => b && b.length >= 5)
      .map(b => ({ page: b[0], bbox: b.slice(1, 5) }))
  }
  if (fact.bbox && fact.bbox.length === 4 && fact.page) {
    return [{ page: fact.page, bbox: fact.bbox }]
  }
  return []
}

const formatPageRange = (pageBboxes) => {
  if (!pageBboxes || pageBboxes.length === 0) return ''
  const pages = pageBboxes.map(pb => pb.page).sort((a, b) => a - b)
  if (pages.length === 1) return `P${pages[0]}`
  // 合并连续页码
  const ranges = []
  let start = pages[0]
  let end = pages[0]
  for (let i = 1; i < pages.length; i++) {
    if (pages[i] === end + 1) {
      end = pages[i]
    } else {
      ranges.push(start === end ? `P${start}` : `P${start}~P${end}`)
      start = end = pages[i]
    }
  }
  ranges.push(start === end ? `P${start}` : `P${start}~P${end}`)
  return ranges.join(', ')
}

const renderEvidenceScreenshots = async () => {
  if (!pdfjsLibInstance) await initPdfJs()

  const facts = results.value.facts
  const validFacts = facts.filter(f => getFactPageBboxes(f).length > 0 && f.graph_id && f.source)
  if (validFacts.length === 0) return

  // 按 PDF 分组：不同 PDF 之间并行，同一 PDF 内串行
  const factsByPdf = new Map()
  for (const fact of validFacts) {
    const cacheKey = `${fact.graph_id}:${fact.source}`
    if (!factsByPdf.has(cacheKey)) factsByPdf.set(cacheKey, [])
    factsByPdf.get(cacheKey).push(fact)
  }

  // 渲染单条 fact 的某一页截图
  const renderSinglePage = async (pageNum, bbox, refKey, pdfDoc) => {
    const canvas = evidenceCanvasRefs.value[refKey]
    if (!canvas) return

    try {
      const page = await pdfDoc.getPage(pageNum)
      const context = canvas.getContext('2d')

      const unscaledViewport = page.getViewport({ scale: 1 })
      const pageW = unscaledViewport.width
      const pageH = unscaledViewport.height

      const vPadding = 240
      const rawCropY = bbox[1] - vPadding
      const rawCropH = (bbox[3] - bbox[1]) + vPadding * 2

      const cropX = 0
      const cropW = pageW
      const cropY = Math.max(0, Math.min(rawCropY, pageH - rawCropH))
      const cropH = Math.min(rawCropH, pageH - cropY)

      const scale = 2.5
      const viewport = page.getViewport({ scale })

      const xRatio = viewport.width / pageW
      const yRatio = viewport.height / pageH

      // 复用 tempCanvas
      const { canvas: tempCanvas, ctx: tempCtx } = _getTempCanvas(viewport.width, viewport.height)
      await page.render({ canvasContext: tempCtx, viewport }).promise

      const sX = cropX * xRatio
      const sY = cropY * yRatio
      const sW = cropW * xRatio
      const sH = cropH * yRatio
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

  // 并行处理不同 PDF，同一 PDF 内串行渲染
  await Promise.all(
    Array.from(factsByPdf.entries()).map(async ([cacheKey, facts]) => {
      let pdfDoc = _pdfDocCache[cacheKey]
      if (!pdfDoc) {
        const fact = facts[0]
        const apiUrl = `${window.location.origin}/api/graph/project/${fact.graph_id}/document/${encodeURIComponent(fact.source)}`
        try {
          const response = await fetch(apiUrl)
          if (!response.ok) {
            console.warn(`[Evidence] PDF fetch failed: ${response.status} ${cacheKey}`)
            return
          }
          const blob = await response.blob()
          const arrayBuffer = await blob.arrayBuffer()
          pdfDoc = await pdfjsLibInstance.getDocument({ data: new Uint8Array(arrayBuffer) }).promise
          _pdfDocCache[cacheKey] = pdfDoc
        } catch (err) {
          console.error(`[Evidence] Failed to load PDF ${cacheKey}:`, err)
          return
        }
      }
      for (const fact of facts) {
        const pageBboxes = getFactPageBboxes(fact)
        const baseIdx = results.value.facts.indexOf(fact)
        for (let pi = 0; pi < pageBboxes.length; pi++) {
          const { page: pageNum, bbox } = pageBboxes[pi]
          const refKey = `${baseIdx}-${pi}`
          await renderSinglePage(pageNum, bbox, refKey, pdfDoc)
        }
      }
    })
  )
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

      // Resolve selectedGraphIds from selectedProjectIds (source of truth)
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
        // Backward compatibility: migrate from selectedGraphIds
        const legacyGraphIds = wf.selectedGraphIds || []
        resolvedGraphIds = legacyGraphIds
          .map(gid => {
            const proj = projects.value.find(p => p.graph_id === gid)
            return proj?.graph_id
          })
          .filter(Boolean)
      }

      appConfig.value = {
        selectedGraphIds: resolvedGraphIds,
        rootTypes: wf.rootTypes || ['Entity', 'Term'],
        similarityThreshold: wf.similarityThreshold ?? 0,
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
  results.value = { rows: [], facts: [], rerank_results: [], searched: false }
  evidenceCanvasRefs.value = {}
  expandedSections.value = new Set()
  expandedPdfs.value = new Set()

  try {
    // ===== Stage 1: 检索 =====
    loadingMessage.value = '正在知识库检索...'
    const { rows } = await hitTestSearch({
      graphId: appConfig.value.selectedGraphIds,
      query,
      limit: 15,
      similarityThreshold: appConfig.value.similarityThreshold,
      rootTypes: appConfig.value.rootTypes,
    })
    results.value.rows = rows
    results.value.facts = rows.flatMap(r => r.facts || [])

    if (rows.length === 0) {
      loadingMessage.value = '未检索到结果'
      results.value.searched = true
      loading.value = false
      return
    }

    // ===== Stage 2: 相关性重排 =====
    loadingMessage.value = '正在重排...'
    const rerankRes = await rerankFacts({
      rows: rows,
      query: query,
      top_k: appConfig.value.topK,
      rerank_min_score: appConfig.value.rerankMinScore,
    })

    if (rerankRes.success && rerankRes.data) {
      results.value.rerank_results = rerankRes.data.scored_facts || []
      results.value.facts = rerankRes.data.filtered_facts || rerankRes.data.scored_facts || []
      if (rerankRes.data.rows && rerankRes.data.rows.length > 0) {
        results.value.rows = rerankRes.data.rows
      }
    }

    // 默认展开所有 section
    const sources = new Set(results.value.facts.map(f => f.source || 'Unknown'))
    sources.forEach(s => expandedSections.value.add(s))

    // 渲染截图
    isRenderingEvidence.value = true
    nextTick(async () => {
      await renderEvidenceScreenshots()
      isRenderingEvidence.value = false
      scrollToTop()
      // URL 参数自动定位
      autoOpenEvidence()
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

const autoOpenEvidence = () => {
  if (!pendingUrlParams.value) return
  const { source, page, bbox } = pendingUrlParams.value
  const facts = results.value.facts
  for (let i = 0; i < facts.length; i++) {
    const f = facts[i]
    const matchSource = f.source === source
    const matchPage = !page || (f.page === page)
    if (matchSource && matchPage) {
      expandedSections.value.add(source)
      expandedPdfs.value.add(i)
      nextTick(() => {
        renderEvidenceScreenshots()
        const canvas = evidenceCanvasRefs.value[i]
        const el = canvas?.closest?.('.evidence-item')
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }
      })
      pendingUrlParams.value = null
      return
    }
  }
  // 未匹配到，清除标记避免影响后续搜索
  pendingUrlParams.value = null
}

onMounted(async () => {
  await loadApp()

  // 解析 URL 参数用于自动定位 PDF
  const { source, page, bbox } = route.query
  if (source) {
    pendingUrlParams.value = {
      source,
      page: page ? parseInt(page, 10) : null,
      bbox: bbox ? String(bbox).split(',').map(Number) : null,
    }
  }

  const presetQuestion = route.query.question
  if (presetQuestion && typeof presetQuestion === 'string' && presetQuestion.trim()) {
    userInput.value = presetQuestion.trim()
    await handleSearch()
  }
})
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
  justify-content: center;
  margin: 0 16px;
  overflow: hidden;
}
.query-bubble {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  background: #f0f7ff;
  border: 1px solid #c6e2ff;
  border-radius: 10px;
  padding: 8px 14px;
  max-width: 100%;
}
.query-label {
  font-size: 12px;
  color: #409eff;
  font-weight: 800;
  background: #fff;
  padding: 2px 6px;
  border-radius: 6px;
  border: 1px solid #c6e2ff;
  flex-shrink: 0;
  margin-top: 2px;
}
.query-text {
  font-size: 14px;
  color: #1a1a1a;
  font-weight: 600;
  line-height: 1.5;
  word-break: break-all;
}

.report-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  padding-bottom: 90px;
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
  gap: 12px;
}

.evidence-section {
  border: 1px solid #eee;
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
}

.evidence-section-header {
  padding: 10px 16px;
  background: #f5f7fa;
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s;
}

.evidence-section-header:hover {
  background: #e8ecf1;
}

.evidence-section-header .section-toggle {
  font-size: 11px;
  color: #909399;
}

.evidence-section-header .section-title {
  font-size: 14px;
  font-weight: 700;
  color: #333;
}

.evidence-section-body {
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: #fafafa;
}

.evidence-item {
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
}

.evidence-text-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 8px;
}

.evidence-score-badge-small {
  font-size: 11px;
  color: #fff;
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 700;
  white-space: nowrap;
  flex-shrink: 0;
  margin-top: 2px;
}

.evidence-text {
  font-size: 14px;
  color: #333;
  line-height: 1.7;
  flex: 1;
}

.evidence-tables {
  margin: 8px 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.table-image-item {
  border: 1px dashed #dcdfe6;
  border-radius: 8px;
  padding: 8px;
  background: #f8f9fa;
}

.table-caption {
  font-size: 12px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 6px;
}

.table-image {
  max-width: 100%;
  border-radius: 4px;
  display: block;
}

.evidence-page {
  font-size: 11px;
  color: #909399;
  white-space: nowrap;
  flex-shrink: 0;
}

.evidence-topics {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
  padding-left: 2px;
}

.evidence-topics .topics-label {
  font-size: 10px;
  color: #909399;
  font-weight: 600;
  margin-right: 2px;
}

.evidence-topics .topic-tag {
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  background: #f6ffed;
  color: #389e0d;
  border: 1px solid #b7eb8f;
  white-space: nowrap;
}

.evidence-pdf-fold {
  margin-top: 4px;
}

.fold-btn {
  background: #f0f2f5;
  border: 1px solid #dcdfe6;
  color: #606266;
  padding: 4px 12px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.fold-btn:hover {
  background: #e6f7ff;
  border-color: #409eff;
  color: #409eff;
}

.evidence-pdf-content {
  margin-top: 8px;
  padding: 6px;
  background: #525659;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.evidence-canvas {
  max-width: 100%;
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
  background: #fff;
}

.page-screenshot {
  margin-bottom: 12px;
}

.page-screenshot:last-child {
  margin-bottom: 0;
}

.page-label {
  font-size: 12px;
  color: #666;
  padding: 4px 8px;
  background: #f0f0f0;
  border-radius: 4px 4px 0 0;
  display: inline-block;
}

.no-bbox-hint {
  background: #fff;
  padding: 15px;
  width: 100%;
  font-size: 13px;
  color: #666;
  text-align: left;
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
.loading-screen, .welcome-screen, .qa-result-container.no-results {
  text-align: center;
  margin-top: 10vh;
  color: #999;
}

.qa-result-container.no-results h2 {
  color: #c0392b;
}

.qa-result-container.no-results p {
  color: #7f8c8d;
  max-width: 500px;
  margin: 0 auto;
  line-height: 1.6;
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

.rendering-hint {
  text-align: center;
  font-size: 13px;
  color: #409eff;
  font-weight: 600;
  padding: 16px 0;
}

@keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }
@keyframes spin { to { transform: rotate(360deg); } }
.spinner-sm { width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3); border-top: 2px solid #fff; border-radius: 50%; animation: spin 1s linear infinite; display: inline-block; }

@media (max-width: 600px) {
  .evidence-grid { grid-template-columns: 1fr; }
}
</style>
