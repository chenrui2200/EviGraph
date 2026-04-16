<template>
  <div class="pdf-viewer-panel" :class="{ open: modelValue }">
    <div class="viewer-header">
      <slot name="header">
        <span class="viewer-filename">{{ filename }}</span>
        <button class="close-viewer" @click="closeViewer">✕</button>
      </slot>
    </div>
    <div class="viewer-body" ref="viewerContainer">
      <template v-if="url">
        <div
          class="pdf-render-wrapper"
          :class="wrapperClass"
          @mousedown="e => $emit('mousedown', e)"
          @mousemove="e => $emit('mousemove', e)"
          @mouseup="e => $emit('mouseup', e)"
        >
          <!-- 多页连续渲染（或单页） -->
          <div
            v-for="p in renderedPages"
            :key="p"
            class="pdf-page-wrapper"
          >
            <canvas :ref="el => setCanvasRef(el, p)" class="pdf-canvas"></canvas>

            <!-- 默认高亮 overlay -->
            <svg
              v-if="!$slots.overlay && (getPageBbox(p) || getPagePdfBboxes(p).length)"
              class="pdf-highlight-overlay"
              :viewBox="`0 0 ${pageDimensions[p]?.width || pageWidth || 600} ${pageDimensions[p]?.height || pageHeight || 800}`"
            >
              <rect
                v-if="getPageBbox(p) && getPageBbox(p).length === 4"
                :x="getPageBbox(p)[0]"
                :y="getPageBbox(p)[1]"
                :width="getPageBbox(p)[2] - getPageBbox(p)[0]"
                :height="getPageBbox(p)[3] - getPageBbox(p)[1]"
                class="highlight-rect"
              />
              <rect
                v-for="(bb, idx) in getPagePdfBboxes(p)"
                :key="'multi-bbox-' + idx"
                :x="bb[1]"
                :y="bb[2]"
                :width="bb[3] - bb[1]"
                :height="bb[4] - bb[2]"
                class="highlight-rect multi-page-highlight"
              />
            </svg>

            <!-- 自定义 overlay 插槽（带当前页号作用域参数） -->
            <slot name="overlay" :page="p"></slot>
          </div>
        </div>
      </template>

      <div v-else class="viewer-empty">
        <slot name="empty">
          <span class="empty-icon">📂</span>
          <p>点击"定位文档"查看源文件</p>
        </slot>
      </div>

      <slot name="extra"></slot>

      <div v-if="pdfLoading" class="viewer-loading">
        <div class="spinner-sm"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  filename: { type: String, default: '' },
  url: { type: String, default: '' },
  page: { type: Number, default: 1 },
  bbox: { type: Array, default: null },
  pageWidth: { type: Number, default: 0 },
  pageHeight: { type: Number, default: 0 },
  pdfBboxes: { type: Array, default: null },
  wrapperClass: { type: [String, Object], default: '' }
})

const emit = defineEmits(['update:modelValue', 'mousedown', 'mousemove', 'mouseup'])

const viewerContainer = ref(null)
const pdfjsLib = ref(null)
let pdfDoc = null
let renderLock = false
const pdfLoading = ref(false)
const pdfBboxesBasePage = ref(props.page)
const pageDimensions = ref({})
const pageCanvasRefs = {}

const getBboxOffset = () => {
  if (!props.pdfBboxes || !Array.isArray(props.pdfBboxes) || props.pdfBboxes.length === 0) return 0
  return props.pdfBboxes[0][0] - pdfBboxesBasePage.value
}

const renderedPages = computed(() => {
  if (!props.pdfBboxes || !Array.isArray(props.pdfBboxes) || props.pdfBboxes.length === 0) {
    return [props.page]
  }
  const offset = getBboxOffset()
  const pages = new Set(props.pdfBboxes.map(b => b && b.length >= 5 ? b[0] - offset : props.page))
  return Array.from(pages).sort((a, b) => a - b)
})

const getPagePdfBboxes = (pageNum) => {
  if (!props.pdfBboxes || !Array.isArray(props.pdfBboxes)) return []
  const offset = getBboxOffset()
  return props.pdfBboxes.filter(b => b && b.length >= 5 && b[0] - offset === pageNum)
}

const getPageBbox = (pageNum) => {
  if (pageNum === props.page) return props.bbox
  return null
}

const setCanvasRef = (el, pageNum) => {
  if (el) pageCanvasRefs[pageNum] = el
}

const closeViewer = () => {
  emit('update:modelValue', false)
}

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

const renderPage = async (doc, pageNum) => {
  if (!doc) return
  const canvas = pageCanvasRefs[pageNum]
  if (!canvas) return

  try {
    const page = await doc.getPage(pageNum)
    const context = canvas.getContext('2d')
    const containerWidth = viewerContainer.value?.clientWidth || 600
    const unscaledViewport = page.getViewport({ scale: 1 })
    const scale = (containerWidth - 40) / unscaledViewport.width
    const viewport = page.getViewport({ scale })

    pageDimensions.value[pageNum] = {
      width: unscaledViewport.width,
      height: unscaledViewport.height
    }

    canvas.height = viewport.height
    canvas.width = viewport.width

    await page.render({ canvasContext: context, viewport }).promise
  } catch (err) {
    console.error(`PDF render error (page ${pageNum}):`, err)
  }
}

const renderAllPages = async (pdfSource) => {
  if (!pdfjsLib.value || renderLock) return
  renderLock = true
  pdfLoading.value = true
  pageDimensions.value = {}
  try {
    const loadingTask = pdfjsLib.value.getDocument(
      typeof pdfSource === 'string' ? pdfSource : { data: new Uint8Array(pdfSource) }
    )
    pdfDoc = await loadingTask.promise
    const doc = pdfDoc

    // 等待 DOM 更新后再渲染
    await nextTick()
    const pages = renderedPages.value
    for (const p of pages) {
      await renderPage(doc, p)
    }
  } catch (err) {
    console.error('PDF load error:', err)
  } finally {
    pdfLoading.value = false
    renderLock = false
  }
}

watch(() => props.url, (newUrl, oldUrl) => {
  if (newUrl && newUrl !== oldUrl) {
    pdfBboxesBasePage.value = props.page
    // 清空旧 canvas refs
    Object.keys(pageCanvasRefs).forEach(k => delete pageCanvasRefs[k])
  }
}, { immediate: true })

watch(() => [props.url, props.page], async ([newUrl]) => {
  if (props.modelValue && newUrl) {
    await initPdfJs()
    nextTick(() => {
      setTimeout(() => renderAllPages(newUrl), 300)
    })
  }
}, { immediate: true })

// 兼容旧 expose：pdfCanvas 指向第一页 canvas
const pdfCanvas = computed(() => pageCanvasRefs[renderedPages.value[0]] || null)

// 兼容旧 renderPdfPage 签名：渲染指定页（单页场景）
const renderPdfPage = async (pdfSource, pageNum) => {
  if (!pdfjsLib.value) await initPdfJs()
  if (renderLock) return
  renderLock = true
  pdfLoading.value = true
  try {
    const loadingTask = pdfjsLib.value.getDocument(
      typeof pdfSource === 'string' ? pdfSource : { data: new Uint8Array(pdfSource) }
    )
    pdfDoc = await loadingTask.promise
    const doc = pdfDoc
    await nextTick()
    await renderPage(doc, pageNum)
  } catch (err) {
    console.error('PDF render error:', err)
  } finally {
    pdfLoading.value = false
    renderLock = false
  }
}

defineExpose({
  initPdfJs,
  renderPdfPage,
  renderAllPages,
  viewerContainer,
  pdfCanvas,
  pdfjsLib,
  get pdfDoc() { return pdfDoc },
  pageCanvasRefs
})
</script>

<style scoped>
.pdf-viewer-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: #fff;
}

.viewer-header {
  height: 50px;
  padding: 0 20px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #f8f9fa;
  flex-shrink: 0;
}

.viewer-filename {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

.close-viewer {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  color: #999;
}

.viewer-body {
  flex: 1;
  overflow: auto;
  background: #525659;
  position: relative;
  display: flex;
  justify-content: center;
  padding: 20px;
}

.pdf-render-wrapper {
  position: relative;
  height: fit-content;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.pdf-page-wrapper {
  position: relative;
  box-shadow: 0 5px 15px rgba(0,0,0,0.3);
  background: #fff;
  height: fit-content;
}

.pdf-canvas {
  display: block;
  background: #fff;
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

.viewer-loading {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(255,255,255,0.8);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
}

.viewer-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #999;
}

.empty-icon {
  font-size: 40px;
  margin-bottom: 10px;
  opacity: 0.3;
}

.spinner-sm {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  display: inline-block;
}

@keyframes spin { to { transform: rotate(360deg); } }
</style>
