<template>
  <div class="public-report-container">
    <header class="report-header">
      <div class="app-info">
        <span class="app-icon">📚</span>
        <h1 class="app-title">{{ appName }}</h1>
      </div>
      <div class="header-status" v-if="loading">
        <div class="spinner-sm"></div>
        <span>正在分析中...</span>
      </div>
    </header>

    <main class="report-body" ref="scrollContainer">
      <!-- Welcome State -->
      <div v-if="!loading && !reportData.answer" class="welcome-screen">
        <div class="empty-icon">🔎</div>
        <h2>知识库分析助手</h2>
        <p>请在下方输入您的问题，我将为您检索图谱并生成详细的技术报告</p>
      </div>

      <!-- Report Content -->
      <div v-if="reportData.answer" class="report-content">
        <!-- 1. Knowledge Sources with Screenshots -->
        <section class="report-section">
          <h2 class="section-title">📚 检索依据原文 (Knowledge Evidence)</h2>
          <div class="evidence-grid">
            <div v-for="(fact, idx) in reportData.facts" :key="idx" class="evidence-card">
              <div class="evidence-header">
                <span class="evidence-idx">证据 #{{ idx + 1 }}</span>
                <span class="evidence-source">{{ fact.source }} <template v-if="fact.page">(第 {{ fact.page }} 页)</template></span>
                <span v-if="fact.relevance_score" class="evidence-score" :style="{ color: getThresholdColor(fact.relevance_score) }">
                  {{ fact.relevance_score }}分
                </span>
              </div>
              <div class="evidence-screenshot">
                <canvas :ref="el => setEvidenceRef(el, idx)" class="evidence-canvas"></canvas>
                <div v-if="!fact.bbox" class="no-bbox-fallback">
                  <p class="fact-text-direct">{{ fact.text }}</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- 2. Reasoning Process -->
        <section v-if="parsedResult.thought" class="report-section thought-section">
          <h2 class="section-title">🧠 深度推理过程 (Thinking Process)</h2>
          <div class="thought-box">
            {{ parsedResult.thought }}
          </div>
        </section>

        <!-- 3. Final Conclusion -->
        <section class="report-section conclusion-section">
          <h2 class="section-title">✨ 最终结论 (Final Conclusion)</h2>
          <div class="conclusion-box">
            {{ parsedResult.conclusion }}
          </div>
        </section>
      </div>

      <!-- Loading Placeholder at the end -->
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
import { ref, onMounted, computed, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { getApp } from '../api/ai_app'

const route = useRoute()
const appId = route.params.id
const appName = ref('加载中...')
const userInput = ref('')
const loading = ref(false)
const scrollContainer = ref(null)
const textareaRef = ref(null)

const reportData = ref({
  facts: [],
  answer: ''
})

const evidenceCanvasRefs = ref({})

const setEvidenceRef = (el, idx) => {
  if (el) evidenceCanvasRefs.value[idx] = el
}

const getThresholdColor = (val) => {
  if (val < 40) return '#f56c6c'
  if (val < 70) return '#e6a23c'
  return '#67c23a'
}

const loadApp = async () => {
  const res = await getApp(appId)
  if (res.success) {
    appName.value = res.data.name
  }
}

const handleSearch = async () => {
  if (!userInput.value.trim() || loading.value) return

  const query = userInput.value.trim()
  loading.value = true
  userInput.value = ''

  // Clear previous report
  reportData.value = { facts: [], answer: '' }

  try {
    const response = await fetch(`${window.location.origin}/api/ai-app/execute/${appId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query })
    })

    const result = await response.json()
    if (result.success) {
      const data = result.data
      reportData.value = {
        answer: data.answer,
        facts: data.retrieved_facts || []
      }

      console.log('Received facts:', reportData.value.facts.length)

      // Render screenshots after report is ready
      nextTick(() => {
        renderAllScreenshots()
        scrollToTop()
      })
    }
  } catch (err) {
    console.error('Report generation error:', err)
    alert('报告生成失败')
  } finally {
    loading.value = false
  }
}

const scrollToTop = () => {
  if (scrollContainer.value) {
    scrollContainer.value.scrollTop = 0
  }
}

const parsedResult = computed(() => {
  const text = reportData.value.answer || ''
  let thought = ''
  let conclusion = text

  const thoughtMatch = text.match(/<(thought|think)>([\s\S]*?)<\/\1>/i)
  if (thoughtMatch) {
    thought = thoughtMatch[2].trim()
    conclusion = text.replace(thoughtMatch[0], '').trim()
  }
  return { thought, conclusion }
})

// PDF.js Logic
let pdfjsLibInstance = null
const initPdfJs = async () => {
  if (window.pdfjsLib) {
    pdfjsLibInstance = window.pdfjsLib
    return
  }
  return new Promise((resolve) => {
    const script = document.createElement('script')
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js'
    script.onload = () => {
      pdfjsLibInstance = window['pdfjs-dist/build/pdf']
      pdfjsLibInstance.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js'
      resolve()
    }
    document.head.appendChild(script)
  })
}

const renderAllScreenshots = async () => {
  if (!pdfjsLibInstance) await initPdfJs()
  const pdfDocCache = {}

  for (let i = 0; i < reportData.value.facts.length; i++) {
    const fact = reportData.value.facts[i]
    const canvas = evidenceCanvasRefs.value[i]

    // Check if canvas exists and we have necessary data
    if (!canvas) continue
    if (!fact.bbox || !fact.graph_id || !fact.source) {
      console.log(`Skipping canvas ${i} due to missing data:`, fact.source)
      continue
    }

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

      // Use parent clientWidth for responsive sizing
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

.report-body {
  flex: 1;
  overflow-y: auto;
  padding: 30px 20px 100px 20px;
  scroll-behavior: smooth;
}

.report-content {
  max-width: 900px; margin: 0 auto;
  display: flex; flex-direction: column; gap: 30px;
}

.report-section {
  background: #fff; border-radius: 12px; padding: 25px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.03); border: 1px solid #eef2f5;
}

.section-title {
  font-size: 15px; font-weight: 800; margin-top: 0; margin-bottom: 20px;
  padding-bottom: 10px; border-bottom: 2px solid #f0f4f8;
  display: flex; align-items: center; color: #1a1a1a;
}

.evidence-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 20px; }
@media (max-width: 600px) {
  .evidence-grid { grid-template-columns: 1fr; }
}

.evidence-card { border: 1px solid #eee; border-radius: 10px; overflow: hidden; background: #fff; }
.evidence-header {
  padding: 8px 12px; background: #f8f9fa; display: flex;
  justify-content: space-between; font-size: 12px; font-weight: 600;
  color: #666; border-bottom: 1px solid #eee;
}
.evidence-idx { color: #409eff; }
.evidence-source { flex: 1; margin: 0 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.evidence-score { font-weight: 800; }

.evidence-screenshot { background: #525659; padding: 10px; display: flex; justify-content: center; min-height: 100px; }
.evidence-canvas { max-width: 100%; box-shadow: 0 4px 12px rgba(0,0,0,0.2); background: #fff; }
.no-bbox-fallback { background: #fff; padding: 15px; width: 100%; }
.fact-text-direct { font-size: 13px; line-height: 1.6; color: #333; margin: 0; }

.thought-box { background: #fffbea; border-left: 4px solid #f6ad55; padding: 15px; font-size: 14px; line-height: 1.8; color: #5a6371; white-space: pre-wrap; }
.conclusion-box { font-size: 16px; line-height: 1.7; color: #2c3e50; font-weight: 500; white-space: pre-wrap; }

.report-footer {
  position: fixed; bottom: 0; left: 0; right: 0;
  background: #fff; padding: 15px 20px;
  border-top: 1px solid #e0e0e0;
  box-shadow: 0 -4px 15px rgba(0,0,0,0.05);
  display: flex; justify-content: center;
}

.search-container {
  max-width: 900px; width: 100%;
  display: flex; gap: 12px; align-items: flex-end;
}

.search-container textarea {
  flex: 1; padding: 12px 15px; border: 1px solid #ddd;
  border-radius: 10px; outline: none; transition: border-color 0.2s;
  font-family: inherit; font-size: 14px; resize: none;
  max-height: 120px; min-height: 44px;
}
.search-container textarea:focus { border-color: #409eff; }

.search-btn {
  background: #000; color: #fff; border: none;
  height: 44px; padding: 0 24px; border-radius: 10px;
  font-weight: 600; cursor: pointer; transition: background 0.2s;
}
.search-btn:hover:not(:disabled) { background: #333; }
.search-btn:disabled { background: #ccc; cursor: not-allowed; }

.loading-screen, .welcome-screen { text-align: center; margin-top: 10vh; color: #999; }
.spinner { width: 40px; height: 40px; border: 4px solid #f3f3f3; border-top: 4px solid #000; border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 20px; }
.empty-icon { font-size: 60px; margin-bottom: 20px; opacity: 0.3; }

.report-loading-placeholder { max-width: 900px; margin: 0 auto; padding: 20px; }
.skeleton-line { background: #eef2f5; border-radius: 4px; margin-bottom: 12px; animation: pulse 1.5s infinite; }
.skeleton-line.title { width: 30%; height: 20px; }
.skeleton-line.content { width: 100%; height: 15px; }

@keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }
@keyframes spin { to { transform: rotate(360deg); } }
.spinner-sm { width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3); border-top: 2px solid #fff; border-radius: 50%; animation: spin 1s linear infinite; display: inline-block; }
</style>
