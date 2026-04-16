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
          <canvas ref="pdfCanvas" class="pdf-canvas"></canvas>

          <!-- 默认高亮 overlay -->
          <svg
            v-if="!$slots.overlay && (bbox || pdfBboxes)"
            class="pdf-highlight-overlay"
            :viewBox="`0 0 ${pageWidth || 600} ${pageHeight || 800}`"
          >
            <rect
              v-if="bbox && bbox.length === 4"
              :x="bbox[0]"
              :y="bbox[1]"
              :width="bbox[2] - bbox[0]"
              :height="bbox[3] - bbox[1]"
              class="highlight-rect"
            />
            <template v-if="pdfBboxes && Array.isArray(pdfBboxes)">
              <rect
                v-for="(bb, idx) in currentPageBboxes"
                :key="'multi-bbox-' + idx"
                :x="bb[1]"
                :y="bb[2]"
                :width="bb[3] - bb[1]"
                :height="bb[4] - bb[2]"
                class="highlight-rect multi-page-highlight"
              />
            </template>
          </svg>

          <!-- 自定义 overlay 插槽 -->
          <slot name="overlay"></slot>
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

const pdfCanvas = ref(null)
const viewerContainer = ref(null)
const pdfjsLib = ref(null)
const pdfLoading = ref(false)

const currentPageBboxes = computed(() => {
  if (!props.pdfBboxes || !Array.isArray(props.pdfBboxes)) return []
  return props.pdfBboxes.filter(b => b && b.length >= 5 && b[0] === props.page)
})

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

const renderPdfPage = async (pdfSource, pageNum) => {
  if (!pdfjsLib.value || !pdfCanvas.value) {
    console.warn('PDF.js lib or canvas not ready')
    return
  }

  pdfLoading.value = true
  try {
    const loadingTask = pdfjsLib.value.getDocument(
      typeof pdfSource === 'string' ? pdfSource : { data: new Uint8Array(pdfSource) }
    )
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

    await page.render({ canvasContext: context, viewport }).promise
  } catch (err) {
    console.error('PDF render error:', err)
  } finally {
    pdfLoading.value = false
  }
}

watch(() => [props.url, props.page], async ([newUrl, newPage]) => {
  if (props.modelValue && newUrl) {
    await initPdfJs()
    nextTick(() => {
      setTimeout(() => renderPdfPage(newUrl, newPage || 1), 300)
    })
  }
}, { immediate: true })

defineExpose({
  initPdfJs,
  renderPdfPage,
  viewerContainer,
  pdfCanvas,
  pdfjsLib
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
  box-shadow: 0 5px 15px rgba(0,0,0,0.3);
  background: #fff;
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

.multi-page-highlight {
  fill: rgba(0, 200, 255, 0.25);
  stroke: #00aaff;
  stroke-width: 2px;
  stroke-dasharray: 4 2;
  animation: pulse-highlight-multi 2s infinite;
}

@keyframes pulse-highlight {
  0% { fill: rgba(255, 165, 0, 0.25); }
  50% { fill: rgba(255, 165, 0, 0.45); }
  100% { fill: rgba(255, 165, 0, 0.25); }
}

@keyframes pulse-highlight-multi {
  0% { fill: rgba(0, 200, 255, 0.2); }
  50% { fill: rgba(0, 200, 255, 0.4); }
  100% { fill: rgba(0, 200, 255, 0.2); }
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
