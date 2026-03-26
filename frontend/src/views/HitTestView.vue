<template>
  <div class="hit-test-view">
    <!-- Header -->
    <header class="view-header">
      <div class="header-left">
        <button class="back-btn" @click="router.back()">←</button>
        <h2 class="view-title">知识召回命中测试 (Hit Test)</h2>
      </div>
      <div class="header-right">
        <span class="project-info" v-if="projectName">
          当前项目: <strong>{{ projectName }}</strong>
        </span>
        <button class="supplement-btn" @click="startSupplement">➕ 补充知识</button>
      </div>
    </header>

    <main class="hit-test-main" :class="{ 'viewer-open': showDocViewer || isSupplementMode }">
      <!-- Left: Graph Visualization -->
      <div class="graph-section">
        <GraphPanel
          ref="graphPanelRef"
          :graph-data="filteredGraphData"
          :loading="graphLoading"
          :highlight-node-id="highlightedNodeId"
          :show-legend="false"
          @refresh="loadFullGraph"
          @node-click="handleNodeClick"
        />
      </div>

      <!-- Right Sub-container for Search Results and PDF Viewer -->
      <div class="right-panels-container">
        <!-- Search and Results Section -->
        <div class="search-section">
          <div class="search-card">
            <div class="search-box">
              <input
                v-model="searchQuery"
                placeholder="输入关键词或问题进行检索测试..."
                @keyup.enter="handleSearch"
                :disabled="searching"
              />
              <button class="search-btn" @click="handleSearch" :disabled="searching || !searchQuery">
                <span v-if="!searching">🔍 查询</span>
                <span v-else class="spinner-sm"></span>
              </button>
            </div>

            <div class="search-options">
              <label class="checkbox-label">
                <input type="checkbox" v-model="filterGraph" />
                <span>自动过滤关联图结构</span>
              </label>
            </div>
          </div>

          <div class="results-container">
            <div v-if="!results.facts.length && !searching" class="empty-results">
              <div class="empty-icon">🔎</div>
              <p>在上方输入内容并点击查询，测试知识召回效果</p>
            </div>

            <div v-if="searching" class="searching-state">
              <div class="spinner"></div>
              <p>正在从图谱中检索相关事实...</p>
            </div>

            <div v-if="results.facts.length > 0" class="results-list">
              <div class="results-header">
                <span>检索命中汇总 (Found {{ results.facts.length }} items)</span>
                <button class="reset-filter-btn" @click="resetFilter">重置视图</button>
              </div>

              <!-- Summary Section (Optional but good) -->
              <div class="results-summary-card">
                <div class="summary-title">💡 检索分析</div>
                <p class="summary-content">
                  本次检索命中了 {{ results.nodes.length }} 个实体节点和 {{ results.edges.length }} 条关系事实。
                  左侧图谱已过滤展示相关知识路径。
                </p>
              </div>

            <div class="facts-items-list">
              <div
                v-for="(fact, idx) in results.facts"
                :key="idx"
                :id="`fact-item-${idx}`"
                class="fact-item"
                :class="{ 'expanded': expandedFacts.has(idx), 'active': highlightedNodeId === (fact.source_node_uuid || fact.uuid) }"
                @mouseenter="highlightInGraph(fact)"
                @mouseleave="clearHighlight"
                @click="selectFact(fact, idx)"
              >
                <div class="fact-body">
                  <div class="fact-main-row">
                    <div class="fact-text">{{ fact.text }}</div>
                    <button class="expand-toggle" @click.stop="toggleExpand(idx)">
                      {{ expandedFacts.has(idx) ? '▲' : '▼' }}
                    </button>
                  </div>

                  <!-- Expanded Associated Info -->
                  <div v-if="expandedFacts.has(idx) && getAssociatedInfo(fact).length > 0" class="associated-info-box">
                    <div class="associated-header">🔗 关联知识扩展 (图谱关系描述)</div>
                    <div class="associated-list">
                      <div v-for="(assoc, aIdx) in getAssociatedInfo(fact)" :key="aIdx" class="assoc-item">
                        <p class="assoc-description">{{ assoc.description }}</p>
                      </div>
                    </div>
                  </div>

                  <div class="fact-meta">
                    <span class="source-tag">
                      📄 {{ fact.source }}
                      <span v-if="fact.page">(P{{ fact.page }})</span>
                    </span>
                    <button
                      v-if="fact.page && fact.source !== 'Local Search'"
                      class="locate-btn"
                      @click.stop="viewDocument(fact)"
                    >
                      定位文档
                    </button>
                  </div>
                </div>
              </div>
            </div>
            </div>
          </div>
        </div>

        <!-- Document Viewer Panel (Flex-in) -->
        <div class="doc-viewer-panel" :class="{ 'open': showDocViewer || isSupplementMode, 'supplement-mode': isSupplementMode }">
          <div class="viewer-header">
            <template v-if="!isSupplementMode">
              <span class="viewer-filename">{{ currentDoc.filename }}</span>
              <button class="close-viewer" @click="showDocViewer = false">✕</button>
            </template>
            <template v-else>
              <div class="supplement-tools">
                <span class="tool-title">知识补录模式: 请在下方 PDF 区域圈选缺失内容</span>
                <div class="tool-actions">
                  <span class="selection-count">已选 {{ selectedRegions.length }} 个区域</span>
                  <button class="clear-btn" @click="clearSelections" :disabled="!selectedRegions.length">清空</button>
                  <button class="submit-btn" @click="submitSupplement" :disabled="!selectedRegions.length || supplementing">
                    <span v-if="!supplementing">✅ 完成补录</span>
                    <span v-else class="spinner-sm"></span>
                  </button>
                  <button class="exit-btn" @click="exitSupplement">取消退出</button>
                </div>
              </div>
            </template>
          </div>
          <div class="viewer-body" ref="viewerContainer">
            <div class="pdf-render-wrapper"
                 @mousedown="handleSelectionStart"
                 @mousemove="handleSelectionMove"
                 @mouseup="handleSelectionEnd"
                 :class="{ 'crosshair-cursor': isSupplementMode }">
              <canvas ref="pdfCanvas" class="pdf-canvas"></canvas>

              <!-- Highlight SVG Overlay (Locate mode only) -->
              <svg v-if="!isSupplementMode && currentDoc.bbox && currentDoc.bbox.length === 4" class="pdf-highlight-overlay" :viewBox="`0 0 ${currentDoc.pageWidth || 600} ${currentDoc.pageHeight || 800}`">
                <rect
                  :x="currentDoc.bbox[0]"
                  :y="currentDoc.bbox[1]"
                  :width="currentDoc.bbox[2] - currentDoc.bbox[0]"
                  :height="currentDoc.bbox[3] - currentDoc.bbox[1]"
                  class="highlight-rect"
                />
              </svg>

              <!-- Active Drawing Rect -->
              <div v-if="isDraggingSelection" class="drawing-rect" :style="drawingRectStyle"></div>

              <!-- Finished Selection Rects -->
              <div v-for="(region, ridx) in selectedRegions" :key="ridx"
                   v-show="region.page === currentDoc.page"
                   class="saved-rect"
                   :style="getSavedRectStyle(region)">
                <span class="rect-idx">{{ ridx + 1 }}</span>
                <button class="remove-rect" @click.stop="removeRegion(ridx)">×</button>
              </div>
            </div>

            <!-- Page Navigation for Supplement Mode -->
            <div v-if="isSupplementMode" class="page-nav-floating">
              <button @click="changePage(-1)" :disabled="currentDoc.page <= 1">◀</button>
              <div class="page-jump">
                <span>第 </span>
                <input
                  type="number"
                  v-model.number="jumpPage"
                  @keyup.enter="handleJumpPage"
                  @blur="handleJumpPage"
                  min="1"
                  :max="totalDocPages"
                  class="page-input"
                />
                <span> / {{ totalDocPages }} 页</span>
              </div>
              <button @click="changePage(1)" :disabled="currentDoc.page >= totalDocPages">▶</button>
            </div>

            <div v-if="pdfLoading" class="viewer-loading">
              <div class="spinner-sm"></div>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted, computed, nextTick, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GraphPanel from '../components/GraphPanel.vue'
import { getProject, searchGraph, getGraphData } from '../api/graph'

const route = useRoute()
const router = useRouter()

const projectId = route.params.projectId
const projectName = ref('')
const graphId = ref('')

// State
const graphLoading = ref(false)
const searching = ref(false)
const searchQuery = ref('')
const filterGraph = ref(true)
const fullGraphData = ref({ nodes: [], edges: [] })
const results = ref({ facts: [], nodes: [], edges: [] })
const showDocViewer = ref(false)
const pdfLoading = ref(false)
const expandedFacts = ref(new Set())
const highlightedNodeId = ref(null)
const selectedNodeId = ref(null) // Persistent selection from click
const graphPanelRef = ref(null)

// --- Knowledge Supplement Mode ---
const isSupplementMode = ref(false)
const supplementing = ref(false)
const selectedRegions = ref([]) // [{page, bbox: [x0,y0,x1,y1], screenRect: {left, top, width, height}}]
const totalDocPages = ref(0)
const fullPdfBuffer = ref(null)
const jumpPage = ref(1)

const isDraggingSelection = ref(false)

const selectionStart = ref({ x: 0, y: 0 })
const currentDragPos = ref({ x: 0, y: 0 })

const startSupplement = async () => {
  try {
    // 1. Determine which file to use: current viewed or first available
    let fileToLoad = currentDoc.value.filename
    let initialPage = currentDoc.value.page || 1

    if (!fileToLoad) {
      const projectRes = await getProject(projectId)
      if (!projectRes.success || !projectRes.data.files.length) {
        alert('项目中没有可用文档')
        return
      }
      fileToLoad = projectRes.data.files[0].filename
      initialPage = 1
    }

    isSupplementMode.value = true
    showDocViewer.value = false

    // Wait for DOM to expand the panel
    nextTick(async () => {
      await loadFullDocumentForSupplement(fileToLoad, initialPage)
    })
  } catch (err) {
    console.error('Failed to start supplement:', err)
  }
}

const loadFullDocumentForSupplement = async (filename, pageNum = 1) => {
  try {
    pdfLoading.value = true
    const identifier = graphId.value || projectId
    const apiUrl = `${window.location.origin}/api/graph/project/${identifier}/document/${encodeURIComponent(filename)}?t=${Date.now()}`

    if (!pdfjsLib.value) await initPdfJs()

    currentDoc.value = {
      filename,
      page: pageNum,
      bbox: null,
      pageWidth: 0,
      pageHeight: 0,
      url: apiUrl
    }
    jumpPage.value = pageNum

    // Direct render using URL is more reliable for 304/caching
    setTimeout(() => {
      renderPdfPage(apiUrl, pageNum)
    }, 600)
  } catch (err) {
    console.error('Supplement load error:', err)
    alert('无法加载补录文档: ' + err.message)
    pdfLoading.value = false
  }
}

const changePage = (delta) => {
  const newPage = currentDoc.value.page + delta
  if (newPage >= 1 && newPage <= totalDocPages.value) {
    currentDoc.value.page = newPage
    jumpPage.value = newPage
    // Use the URL from currentDoc
    renderPdfPage(currentDoc.value.url, newPage)
  }
}

const handleJumpPage = () => {
  let page = parseInt(jumpPage.value)
  if (isNaN(page)) {
    jumpPage.value = currentDoc.value.page
    return
  }
  if (page < 1) page = 1
  if (page > totalDocPages.value) page = totalDocPages.value

  jumpPage.value = page
  if (page !== currentDoc.value.page) {
    currentDoc.value.page = page
    renderPdfPage(currentDoc.value.url, page)
  }
}

// --- Drawing Logic ---
const drawingRectStyle = computed(() => {
  const x = Math.min(selectionStart.value.x, currentDragPos.value.x)
  const y = Math.min(selectionStart.value.y, currentDragPos.value.y)
  const width = Math.abs(selectionStart.value.x - currentDragPos.value.x)
  const height = Math.abs(selectionStart.value.y - currentDragPos.value.y)

  return {
    left: `${x}px`,
    top: `${y}px`,
    width: `${width}px`,
    height: `${height}px`
  }
})

const handleSelectionStart = (e) => {
  if (!isSupplementMode.value) return
  const rect = e.currentTarget.getBoundingClientRect()
  isDraggingSelection.value = true
  selectionStart.value = {
    x: e.clientX - rect.left,
    y: e.clientY - rect.top
  }
  currentDragPos.value = { ...selectionStart.value }
}

const handleSelectionMove = (e) => {
  if (!isDraggingSelection.value) return
  const rect = e.currentTarget.getBoundingClientRect()
  currentDragPos.value = {
    x: e.clientX - rect.left,
    y: e.clientY - rect.top
  }
}

const handleSelectionEnd = (e) => {
  if (!isDraggingSelection.value) return
  isDraggingSelection.value = false

  const rect = e.currentTarget.getBoundingClientRect()
  const endPos = {
    x: e.clientX - rect.left,
    y: e.clientY - rect.top
  }

  const canvas = pdfCanvas.value
  if (!canvas) return

  const x0 = Math.min(selectionStart.value.x, endPos.x)
  const y0 = Math.min(selectionStart.value.y, endPos.y)
  const x1 = Math.max(selectionStart.value.x, endPos.x)
  const y1 = Math.max(selectionStart.value.y, endPos.y)

  if (x1 - x0 < 10 && y1 - y0 < 10) return

  // Calculate current scale from canvas width vs original PDF width
  const currentScale = canvas.width / (currentDoc.value.pageWidth || 600)

  const region = {
    page: currentDoc.value.page,
    bbox: [
      x0 / currentScale,
      y0 / currentScale,
      x1 / currentScale,
      y1 / currentScale
    ],
    screenRect: {
      left: x0,
      top: y0,
      width: x1 - x0,
      height: y1 - y0
    }
  }

  selectedRegions.value.push(region)
}

const getSavedRectStyle = (region) => {
  return {
    left: `${region.screenRect.left}px`,
    top: `${region.screenRect.top}px`,
    width: `${region.screenRect.width}px`,
    height: `${region.screenRect.height}px`
  }
}

const removeRegion = (idx) => {
  selectedRegions.value.splice(idx, 1)
}

const clearSelections = () => {
  selectedRegions.value = []
}

const exitSupplement = () => {
  isSupplementMode.value = false
  selectedRegions.value = []
  fullPdfBuffer.value = null
}

const submitSupplement = async () => {
  if (!selectedRegions.value.length) return

  supplementing.value = true
  try {
    const payload = {
      project_id: projectId,
      filename: currentDoc.value.filename,
      regions: selectedRegions.value.map(r => ({
        page: r.page,
        bbox: r.bbox
      }))
    }

    const res = await fetch(`${window.location.origin}/api/graph/supplement`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })

    const data = await res.json()
    if (data.success) {
      alert(data.message)
      exitSupplement()
      loadFullGraph()
    } else {
      alert('补录失败: ' + data.error)
    }
  } catch (err) {
    console.error('Supplement submit error:', err)
    alert('提交补录时出错')
  } finally {
    supplementing.value = false
  }
}
// --- End Supplement Logic ---

// Graph Filtering
const filteredGraphData = computed(() => {
  if (!filterGraph.value || !results.value.facts.length) {
    return fullGraphData.value
  }

  const resultNodeIds = new Set()
  const resultEdgeIds = new Set()

  results.value.nodes.forEach(n => resultNodeIds.add(n.uuid))
  results.value.edges.forEach(e => {
    resultEdgeIds.add(e.uuid)
    resultNodeIds.add(e.source_node_uuid)
    resultNodeIds.add(e.target_node_uuid)
  })

  results.value.facts.forEach(f => {
    if (f.source_node_uuid) resultNodeIds.add(f.source_node_uuid)
    if (f.target_node_uuid) resultNodeIds.add(f.target_node_uuid)
    if (f.uuid) resultNodeIds.add(f.uuid)
  })

  if (fullGraphData.value.edges) {
    fullGraphData.value.edges.forEach(e => {
      if (resultNodeIds.has(e.source_node_uuid) || resultNodeIds.has(e.target_node_uuid)) {
        resultEdgeIds.add(e.uuid)
        resultNodeIds.add(e.source_node_uuid)
        resultNodeIds.add(e.target_node_uuid)
      }
    })
  }

  return {
    nodes: fullGraphData.value.nodes.filter(n => resultNodeIds.has(n.uuid)),
    edges: (fullGraphData.value.edges || []).filter(e => resultEdgeIds.has(e.uuid))
  }
})

// UI Helpers
const toggleExpand = (idx) => {
  if (expandedFacts.value.has(idx)) {
    expandedFacts.value.delete(idx)
  } else {
    expandedFacts.value.add(idx)
  }
}

const getAssociatedInfo = (fact) => {
  if (!fullGraphData.value.nodes || !fullGraphData.value.edges) return []

  if (fact.source_node_uuid && fact.target_node_uuid) {
    const sourceNode = fullGraphData.value.nodes.find(n => n.uuid === fact.source_node_uuid)
    const targetNode = fullGraphData.value.nodes.find(n => n.uuid === fact.target_node_uuid)
    if (sourceNode && targetNode) {
      return [{
        description: `直接关联: 【${sourceNode.name}】 --(${fact.fact_type || '关系'})--> 【${targetNode.name}】`,
        node: targetNode.name
      }]
    }
  }

  const factNodeId = fact.source_node_uuid || fact.uuid
  if (!factNodeId) return []

  const factNode = fullGraphData.value.nodes.find(n => n.uuid === factNodeId)
  if (!factNode) return []

  const factNodeName = factNode.name
  const associated = []
  fullGraphData.value.edges.forEach(e => {
    if (e.source_node_uuid === factNodeId) {
      const targetNode = fullGraphData.value.nodes.find(n => n.uuid === e.target_node_uuid)
      if (targetNode && targetNode.uuid !== factNodeId) {
        associated.push({
          description: `【${factNodeName}】 --(${e.name || e.fact_type || '关联'})--> 【${targetNode.name}】: ${targetNode.summary || '暂无摘要'}`,
          node: targetNode.name
        })
      }
    } else if (e.target_node_uuid === factNodeId) {
      const sourceNode = fullGraphData.value.nodes.find(n => n.uuid === e.source_node_uuid)
      if (sourceNode && sourceNode.uuid !== factNodeId) {
        associated.push({
          description: `【${sourceNode.name}】 --(${e.name || e.fact_type || '关联'})--> 【${factNodeName}】: ${sourceNode.summary || '暂无摘要'}`,
          node: sourceNode.name
        })
      }
    }
  })

  const seen = new Set()
  return associated.filter(item => {
    if (seen.has(item.description)) return false
    seen.add(item.description)
    return true
  })
}

// PDF Viewer Refs
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

const loadFullGraph = async () => {
  if (!graphId.value) return
  graphLoading.value = true
  try {
    const res = await getGraphData(graphId.value)
    if (res.success) {
      fullGraphData.value = res.data
    }
  } catch (err) {
    console.error('Failed to load graph:', err)
  } finally {
    graphLoading.value = false
  }
}

const handleSearch = async () => {
  if (!searchQuery.value.trim() || !graphId.value) return
  searching.value = true
  try {
    const res = await searchGraph({
      graph_id: graphId.value,
      query: searchQuery.value,
      limit: 15,
      scope: 'both'
    })
    if (res.success) {
      results.value = {
        facts: res.data.facts || [],
        nodes: res.data.nodes || [],
        edges: res.data.edges || []
      }
    }
  } catch (err) {
    console.error('Search failed:', err)
    alert('检索失败，请重试')
  } finally {
    searching.value = false
  }
}

const resetFilter = () => {
  results.value = { facts: [], nodes: [], edges: [] }
}

const highlightInGraph = (fact) => {
  const nodeId = fact.source_node_uuid || fact.uuid
  if (nodeId) {
    highlightedNodeId.value = nodeId
  }
}

const clearHighlight = () => {
  highlightedNodeId.value = selectedNodeId.value
}

const selectFact = (fact, idx) => {
  const nodeId = fact.source_node_uuid || fact.uuid
  if (nodeId) {
    if (selectedNodeId.value === nodeId) {
      selectedNodeId.value = null
      highlightedNodeId.value = null
    } else {
      selectedNodeId.value = nodeId
      highlightedNodeId.value = nodeId
      if (graphPanelRef.value) {
        graphPanelRef.value.focusNode(nodeId)
      }
      // If fact has PDF location info, open document viewer
      if (fact.source && fact.source !== 'Unknown') {
        viewDocument(fact)
      }
    }
  }
}

const handleNodeClick = (nodeId) => {
  if (!nodeId) {
    selectedNodeId.value = null
    highlightedNodeId.value = null
    return
  }
  selectedNodeId.value = nodeId
  highlightedNodeId.value = nodeId

  // Helper function to find node with PDF info
  const findNodeWithPdfInfo = (nodeId) => {
    // Check in search results first (nodes from search have pdf_info)
    const searchNode = results.value?.nodes?.find(n => n.uuid === nodeId)
    if (searchNode?.pdf_info?.source) return searchNode
    // Check in full graph data (also has pdf_info after our backend update)
    const fullNode = fullGraphData.value?.nodes?.find(n => n.uuid === nodeId)
    if (fullNode?.pdf_info?.source) return fullNode
    return null
  }

  const node = findNodeWithPdfInfo(nodeId)
  if (node?.pdf_info?.source) {
    // Node has PDF location info - open document viewer
    const fact = {
      uuid: nodeId,
      source: node.pdf_info.source,
      page: node.pdf_info.page || 1,
      bbox: node.pdf_info.bbox,
      page_width: node.pdf_info.page_width,
      page_height: node.pdf_info.page_height,
      graph_id: graphId.value || projectId
    }
    viewDocument(fact)
  } else {
    // No PDF info - expand the fact if found
    const factIdx = results.value.facts.findIndex(f => (f.source_node_uuid || f.uuid) === nodeId)
    if (factIdx !== -1) {
      expandedFacts.value.add(factIdx)
      nextTick(() => {
        const el = document.getElementById(`fact-item-${factIdx}`)
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }
      })
    }
  }
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
    totalDocPages.value = pdf.numPages
    const page = await pdf.getPage(pageNum)

    const canvas = pdfCanvas.value
    const context = canvas.getContext('2d')
    const containerWidth = viewerContainer.value?.clientWidth || 600
    const unscaledViewport = page.getViewport({ scale: 1 })
    const scale = (containerWidth - 40) / unscaledViewport.width
    const viewport = page.getViewport({ scale })

    currentDoc.value.pageWidth = unscaledViewport.width
    currentDoc.value.pageHeight = unscaledViewport.height

    canvas.height = viewport.height
    canvas.width = viewport.width

    await page.render({ canvasContext: context, viewport }).promise
    console.log(`Rendered page ${pageNum}`)
  } catch (err) {
    console.error('PDF render error:', err)
  } finally {
    pdfLoading.value = false
  }
}

const viewDocument = async (fact) => {
  if (!fact.source || fact.source === 'Unknown') return
  const identifier = fact.graph_id || graphId.value || projectId
  const filename = fact.source
  const page = fact.page || 1
  const bbox = fact.bbox || null
  const pageWidth = fact.page_width || 0
  const pageHeight = fact.page_height || 0

  try {
    showDocViewer.value = true
    isSupplementMode.value = false
    pdfLoading.value = true

    const apiUrl = `${window.location.origin}/api/graph/project/${identifier}/document/${encodeURIComponent(filename)}?t=${Date.now()}`
    if (!pdfjsLib.value) await initPdfJs()

    currentDoc.value = {
      filename,
      page,
      bbox,
      url: apiUrl,
      pageWidth,
      pageHeight
    }

    nextTick(() => {
      setTimeout(() => { renderPdfPage(apiUrl, page) }, 500)
    })
  } catch (err) {
    console.error('viewDocument error:', err)
    alert('无法加载文档')
    pdfLoading.value = false
  }
}

onMounted(async () => {
  try {
    const res = await getProject(projectId)
    if (res.success) {
      projectName.value = res.data.name
      graphId.value = res.data.graph_id
      if (graphId.value) loadFullGraph()
    }
  } catch (err) {
    console.error('Failed to init HitTest:', err)
  }
})
</script>

<style scoped>
.hit-test-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
  background: #f5f7fa;
  overflow: hidden;
}

.view-header {
  height: 60px;
  background: #fff;
  border-bottom: 1px solid #e0e0e0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.back-btn {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  color: #666;
}

.view-title {
  font-size: 18px;
  font-weight: 700;
  margin: 0;
}

.project-info {
  font-size: 14px;
  color: #666;
}

.supplement-btn {
  padding: 6px 16px;
  background: #409eff;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-weight: 600;
  cursor: pointer;
  margin-left: 16px;
}

.supplement-btn:hover {
  background: #66b1ff;
}

.hit-test-main {
  flex: 1;
  width: 100%;
  display: flex;
  overflow: hidden;
  position: relative;
  background: #fff;
}

.graph-section {
  flex: 7;
  min-width: 300px;
  height: 100%;
  border-right: 1px solid #e0e0e0;
  background: #fff;
  transition: flex 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}

.hit-test-main.viewer-open .graph-section {
  flex: 4;
}

.right-panels-container {
  flex: 3;
  display: flex;
  height: 100%;
  overflow: hidden;
  background: #fff;
  transition: flex 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}

.hit-test-main.viewer-open .right-panels-container {
  flex: 6;
}

.search-section {
  flex: 1;
  min-width: 320px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #fff;
  overflow: hidden;
  border-right: 1px solid #f0f0f0;
}

.search-card {
  padding: 24px;
  border-bottom: 1px solid #f0f0f0;
}

.search-box {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}

.search-box input {
  flex: 1;
  height: 40px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  padding: 0 12px;
  outline: none;
  transition: border-color 0.2s;
}

.search-box input:focus {
  border-color: #409eff;
}

.search-btn {
  padding: 0 20px;
  background: #000;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
}

.search-btn:disabled {
  background: #ccc;
  cursor: not-allowed;
}

.search-options {
  font-size: 13px;
  color: #666;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.results-container {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: #fafafa;
}

.empty-results {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #999;
}

.empty-icon {
  font-size: 48px;
  margin-bottom: 16px;
  opacity: 0.3;
}

.searching-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-top: 40px;
  color: #666;
}

.results-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.results-header {
  font-weight: 700;
  font-size: 14px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.reset-filter-btn {
  font-size: 12px;
  color: #409eff;
  background: none;
  border: none;
  cursor: pointer;
}

.results-summary-card {
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 16px;
}

.summary-title {
  font-size: 12px;
  font-weight: 700;
  color: #67c23a;
  margin-bottom: 4px;
}

.summary-content {
  font-size: 13px;
  color: #5e6d82;
  margin: 0;
  line-height: 1.4;
}

.facts-items-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.fact-item {
  padding: 16px;
  border: 1px solid #e0e0e0;
  border-radius: 10px;
  transition: all 0.2s;
  background: #fff;
}

.fact-item.active {
  border-color: #ff5722;
  background: #fff9f7;
  box-shadow: 0 4px 12px rgba(255, 87, 34, 0.15);
  transform: translateY(-2px);
}

.fact-main-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 8px;
}

.fact-text {
  flex: 1;
  font-size: 14px;
  line-height: 1.6;
  color: #333;
}

.expand-toggle {
  background: none;
  border: none;
  color: #999;
  cursor: pointer;
  padding: 4px;
  font-size: 12px;
}

.associated-info-box {
  background: #f8f9fa;
  border-radius: 6px;
  padding: 12px;
  margin: 12px 0;
  border-left: 3px solid #409eff;
}

.associated-header {
  font-size: 11px;
  font-weight: 700;
  color: #409eff;
  margin-bottom: 8px;
  text-transform: uppercase;
}

.assoc-item {
  font-size: 12px;
  margin-bottom: 6px;
  line-height: 1.4;
}

.assoc-rel {
  color: #909399;
  margin-right: 6px;
  font-weight: 600;
}

.assoc-node {
  font-weight: 700;
  margin-right: 6px;
}

.assoc-info {
  color: #606266;
}

.no-assoc-hint {
  font-size: 11px;
  color: #999;
  font-style: italic;
}

.assoc-description {
  margin: 0;
  color: #444;
  word-break: break-all;
}

.fact-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.source-tag {
  font-size: 12px;
  color: #909399;
}

.locate-btn {
  padding: 4px 12px;
  background: #f0f7ff;
  color: #409eff;
  border: 1px solid #c6e2ff;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
}

.doc-viewer-panel {
  width: 0;
  flex: 0;
  height: 100%;
  background: #fff;
  border-left: 1px solid #e0e0e0;
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  opacity: 0;
}

.doc-viewer-panel.open {
  flex: 1;
  width: auto;
  opacity: 1;
}

.doc-viewer-panel.supplement-mode {
  flex: 10;
}

.supplement-tools {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.tool-title {
  font-size: 14px;
  font-weight: 700;
  color: #409eff;
}

.tool-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.selection-count {
  font-size: 12px;
  color: #666;
}

.clear-btn, .exit-btn {
  background: #f0f2f5;
  border: 1px solid #dcdfe6;
  padding: 4px 12px;
  border-radius: 4px;
  cursor: pointer;
}

.submit-btn {
  background: #67c23a;
  color: #fff;
  border: none;
  padding: 4px 16px;
  border-radius: 4px;
  font-weight: 600;
  cursor: pointer;
}

.submit-btn:disabled {
  background: #c2e7b0;
  cursor: not-allowed;
}

.viewer-header {
  height: 50px;
  padding: 0 20px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #f8f9fa;
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
}

.pdf-render-wrapper.crosshair-cursor {
  cursor: crosshair;
}

.drawing-rect {
  position: absolute;
  border: 2px dashed #409eff;
  background: rgba(64, 158, 255, 0.2);
  pointer-events: none;
  z-index: 5;
}

.saved-rect {
  position: absolute;
  border: 2px solid #67c23a;
  background: rgba(103, 194, 58, 0.15);
  z-index: 4;
}

.rect-idx {
  position: absolute;
  top: -20px;
  left: 0;
  background: #67c23a;
  color: #fff;
  font-size: 10px;
  padding: 0 4px;
  border-radius: 2px;
}

.remove-rect {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 16px;
  height: 16px;
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid #67c23a;
  border-radius: 50%;
  color: #67c23a;
  font-size: 12px;
  line-height: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.page-nav-floating {
  position: absolute;
  bottom: -430px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  padding: 8px 16px;
  border-radius: 24px;
  display: flex;
  align-items: center;
  gap: 15px;
  z-index: 100;
}

.page-nav-floating button {
  background: none;
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: #fff;
  padding: 2px 8px;
  border-radius: 4px;
  cursor: pointer;
}

.page-nav-floating button:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.page-jump {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
}

.page-input {
  width: 45px;
  height: 24px;
  background: rgba(255, 255, 255, 0.15);
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 4px;
  color: #fff;
  text-align: center;
  font-size: 13px;
  outline: none;
}

.page-input::-webkit-inner-spin-button,
.page-input::-webkit-outer-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

.pdf-canvas {

  box-shadow: 0 5px 15px rgba(0,0,0,0.3);
  background: #fff;
  display: block;
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

.close-viewer {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  color: #999;
}

.viewer-loading {
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(255,255,255,0.8);
  display: flex;
  align-items: center;
  justify-content: center;
}

.spinner {
  width: 30px; height: 30px;
  border: 3px solid #f3f3f3;
  border-top: 3px solid #000;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 12px;
}

.spinner-sm {
  width: 16px; height: 16px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top: 2px solid #fff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }
</style>
