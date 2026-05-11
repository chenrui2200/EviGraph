<template>
  <div class="pdf-preview-container">
    <header class="preview-header">
      <div class="header-left">
        <span class="header-icon">🔎</span>
        <div class="header-meta">
          <span class="source-tag">📄 {{ source }}</span>
        </div>
      </div>
    </header>

    <main class="preview-body">
      <div class="preview-info-bar" v-if="clauseInfo || bboxPages.length">
        <span class="info-item" v-if="clauseInfo">📖 条款: {{ clauseInfo }}</span>
        <span class="info-item" v-if="bboxPages.length">
          📄 共 {{ bboxPages.length }} 页 / {{ totalBboxCount }} 处高亮
        </span>
      </div>

      <div class="canvas-wrapper" ref="canvasWrapper">
        <div v-for="p in bboxPages" :key="p" class="page-block">
          <div class="page-label">第 {{ p }} 页</div>
          <canvas :ref="el => setCanvasRef(el, p)" class="pdf-canvas"></canvas>
        </div>

        <!-- 加载中遮罩 -->
        <div v-if="loading" class="loading-overlay">
          <div class="spinner"></div>
          <span>{{ loadingMessage }}</span>
        </div>
        <!-- 错误提示 -->
        <div v-if="errorMsg" class="error-overlay">
          <div class="error-icon">⚠️</div>
          <p>{{ errorMsg }}</p>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { getApp } from '../api/ai_app'

const route = useRoute()
const appId = route.params.app_id

// Query params
const source = ref('')
const pdfBboxes = ref([])
const graphId = ref('')

// UI state
const appName = ref('知识库分析助手')
const loading = ref(true)
const loadingMessage = ref('正在加载 PDF...')
const errorMsg = ref('')
const clauseInfo = ref('')
const chatLink = ref('')

// Refs
const canvasWrapper = ref(null)
const pageCanvasRefs = ref({})

// Computed
const bboxPages = computed(() => {
  const pages = new Set()
  for (const b of pdfBboxes.value) {
    if (b.length >= 5) pages.add(b[0])
  }
  return Array.from(pages).sort((a, b) => a - b)
})

const totalBboxCount = computed(() =>
  pdfBboxes.value.filter(b => b.length >= 5).length
)

let pdfjsLibInstance = null
let pdfDoc = null

// ============ PDF.js init ============
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

// ============ Load App Info ============
const loadApp = async () => {
  try {
    const res = await getApp(appId)
    if (res.success && res.data) {
      appName.value = res.data.name || appName.value
    }
  } catch (err) {
    console.warn('[PdfPreview] loadApp error:', err)
  }
}

const setCanvasRef = (el, pageNum) => {
  if (el) pageCanvasRefs.value[pageNum] = el
}

// ============ Render Single Page ============
const renderPdfPage = async (pageNum) => {
  if (!pdfjsLibInstance || !pdfDoc) return

  const canvas = pageCanvasRefs.value[pageNum]
  if (!canvas) return

  try {
    const ctx = canvas.getContext('2d')
    const pdfPage = await pdfDoc.getPage(pageNum)

    // 计算缩放比例：适配容器宽度，最大不超过 2.0
    const wrapperWidth = canvasWrapper.value?.clientWidth || 800
    const unscaledViewport = pdfPage.getViewport({ scale: 1 })
    const scale = Math.min(wrapperWidth / unscaledViewport.width, 2.0)
    const viewport = pdfPage.getViewport({ scale })

    canvas.width = viewport.width
    canvas.height = viewport.height
    canvas.style.width = viewport.width + 'px'
    canvas.style.height = viewport.height + 'px'

    await pdfPage.render({ canvasContext: ctx, viewport }).promise

    // 绘制当前页所有匹配的 bbox 高亮框
    const currentPageBboxes = pdfBboxes.value.filter(b => b.length >= 5 && b[0] === pageNum)
    for (const b of currentPageBboxes) {
      drawBboxHighlight(ctx, b.slice(1, 5), scale)
    }
  } catch (err) {
    console.error(`[PdfPreview] render page ${pageNum} error:`, err)
  }
}

const drawBboxHighlight = (ctx, bboxArr, scale) => {
  const [x1, y1, x2, y2] = bboxArr

  // 红色虚线边框
  ctx.save()
  ctx.strokeStyle = '#ff4500'
  ctx.lineWidth = 2.5 * scale
  ctx.setLineDash([8 * scale, 5 * scale])
  ctx.lineJoin = 'round'
  ctx.strokeRect(x1 * scale, y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale)

  // 半透明填充，突出显示区域
  ctx.fillStyle = 'rgba(255, 69, 0, 0.08)'
  ctx.fillRect(x1 * scale, y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale)

  // 四角标记，增强视觉引导
  const cornerLen = 12 * scale
  ctx.setLineDash([])
  ctx.lineWidth = 3 * scale
  const corners = [
    [x1, y1, 1, 1],
    [x2, y1, -1, 1],
    [x1, y2, 1, -1],
    [x2, y2, -1, -1],
  ]
  for (const [cx, cy, dx, dy] of corners) {
    ctx.beginPath()
    ctx.moveTo((cx + cornerLen * dx) * scale, cy * scale)
    ctx.lineTo(cx * scale, cy * scale)
    ctx.lineTo(cx * scale, (cy + cornerLen * dy) * scale)
    ctx.stroke()
  }

  ctx.restore()
}

// ============ Load and Render ============
const loadAndRender = async () => {
  loading.value = true
  errorMsg.value = ''

  try {
    await initPdfJs()

    if (!graphId.value || !source.value) {
      errorMsg.value = '缺少必要的参数 (graph_id 或 source)'
      loading.value = false
      return
    }

    const apiUrl = `${window.location.origin}/api/graph/project/${graphId.value}/document/${encodeURIComponent(source.value)}`
    const response = await fetch(apiUrl)
    if (!response.ok) {
      throw new Error(`PDF 加载失败: ${response.status}`)
    }

    const blob = await response.blob()
    const arrayBuffer = await blob.arrayBuffer()
    pdfDoc = await pdfjsLibInstance.getDocument({ data: new Uint8Array(arrayBuffer) }).promise

    // 等待 DOM 更新（canvas 创建）
    await nextTick()

    // 顺序渲染所有 page
    for (const p of bboxPages.value) {
      await renderPdfPage(p)
    }
  } catch (err) {
    console.error('[PdfPreview] load error:', err)
    errorMsg.value = err.message || '加载失败'
  } finally {
    loading.value = false
  }
}

// ============ Parse URL Params ============
const parseParams = () => {
  const q = route.query
  source.value = q.source || ''
  graphId.value = q.graph_id || ''
  clauseInfo.value = q.clause || ''

  // 解析 pdf_bboxes: [[page, x1, y1, x2, y2], ...]
  if (q.pdf_bboxes) {
    try {
      const parsed = JSON.parse(decodeURIComponent(q.pdf_bboxes))
      if (Array.isArray(parsed) && parsed.length > 0) {
        pdfBboxes.value = parsed
      }
    } catch (e) {
      console.warn('[PdfPreview] failed to parse pdf_bboxes:', e)
    }
  }

  // 兼容旧格式: 单个 bbox 参数
  if (pdfBboxes.value.length === 0 && q.bbox) {
    const parts = String(q.bbox).split(',').map(Number)
    const page = q.page ? parseInt(q.page, 10) : 1
    if (parts.length >= 4 && parts.every(n => !isNaN(n))) {
      pdfBboxes.value = [[page, ...parts]]
    }
  }

  // 生成返回问答页的链接
  chatLink.value = `${window.location.origin}/chat/${appId}`
}

// ============ Lifecycle ============
onMounted(async () => {
  parseParams()
  await loadApp()
  await loadAndRender()
})
</script>

<style scoped>
.pdf-preview-container {
  width: 100%;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #1a1a2e;
  color: #fff;
  overflow: hidden;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 24px;
  background: #16213e;
  border-bottom: 1px solid #2a2a4a;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-icon {
  font-size: 22px;
}

.header-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.app-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  color: #fff;
}

.source-tag {
  font-size: 12px;
  color: #a0a0c0;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.back-btn {
  padding: 8px 16px;
  background: #0f3460;
  color: #e0e0ff;
  border-radius: 6px;
  text-decoration: none;
  font-size: 13px;
  transition: background 0.2s;
}

.back-btn:hover {
  background: #1a4a7a;
}

.preview-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.preview-info-bar {
  display: flex;
  gap: 20px;
  padding: 10px 24px;
  background: #16213e;
  border-bottom: 1px solid #2a2a4a;
  font-size: 13px;
  color: #c0c0e0;
  flex-shrink: 0;
}

.info-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.canvas-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px;
  overflow: auto;
  background: #0f0f1a;
  position: relative;
  gap: 24px;
}

.page-block {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.page-block .page-label {
  font-size: 12px;
  color: #a0a0c0;
  padding: 4px 8px;
  background: #16213e;
  border-radius: 4px 4px 0 0;
  margin-bottom: 0;
}

.pdf-canvas {
  background: #fff;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
  border-radius: 0 0 4px 4px;
  display: block;
}

.loading-overlay,
.error-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(15, 15, 26, 0.85);
  gap: 12px;
  z-index: 10;
}

.spinner {
  width: 36px;
  height: 36px;
  border: 3px solid #333;
  border-top-color: #4a9eff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.error-icon {
  font-size: 36px;
}

.error-overlay p {
  color: #ff6b6b;
  font-size: 14px;
  max-width: 400px;
  text-align: center;
}
</style>
