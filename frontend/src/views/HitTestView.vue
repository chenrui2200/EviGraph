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
      </div>
    </header>

    <main class="hit-test-main" :class="{ 'viewer-open': showDocViewer }">
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
                  <div v-if="expandedFacts.has(idx)" class="associated-info-box">
                    <div class="associated-header">🔗 关联知识扩展 (图谱关系描述)</div>
                    <div v-if="getAssociatedInfo(fact).length > 0" class="associated-list">
                      <div v-for="(assoc, aIdx) in getAssociatedInfo(fact)" :key="aIdx" class="assoc-item">
                        <p class="assoc-description">{{ assoc.description }}</p>
                      </div>
                    </div>
                    <div v-else class="no-assoc-hint">暂无直接关联的详细扩展信息</div>
                  </div>

                  <div class="fact-meta">
                    <span class="source-tag">
                      📄 {{ fact.source }}
                      <span v-if="fact.page">(P{{ fact.page }})</span>
                    </span>
                    <button class="locate-btn" @click.stop="viewDocument(fact)">定位文档</button>
                  </div>
                </div>
              </div>
            </div>
            </div>
          </div>
        </div>

        <!-- Document Viewer Panel (Flex-in) -->
        <div class="doc-viewer-panel" :class="{ 'open': showDocViewer }">
          <div class="viewer-header">
            <span class="viewer-filename">{{ currentDoc.filename }}</span>
            <button class="close-viewer" @click="showDocViewer = false">✕</button>
          </div>
          <div class="viewer-body" ref="viewerContainer">
            <canvas ref="pdfCanvas" class="pdf-canvas"></canvas>
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

// Graph Filtering
const filteredGraphData = computed(() => {
  if (!filterGraph.value || !results.value.facts.length) {
    return fullGraphData.value
  }

  const resultNodeIds = new Set()
  const resultEdgeIds = new Set()

  // 1. Core nodes and edges from results
  // Make sure we handle both UUIDs and specific node/edge objects
  results.value.nodes.forEach(n => resultNodeIds.add(n.uuid))
  results.value.edges.forEach(e => {
    resultEdgeIds.add(e.uuid)
    resultNodeIds.add(e.source_node_uuid)
    resultNodeIds.add(e.target_node_uuid)
  })

  // Also include the facts themselves if they represent nodes or have source info
  results.value.facts.forEach(f => {
    if (f.source_node_uuid) resultNodeIds.add(f.source_node_uuid)
    if (f.target_node_uuid) resultNodeIds.add(f.target_node_uuid)
    if (f.uuid) resultNodeIds.add(f.uuid)
  })

  // 2. Path extension: include neighbors of the result nodes
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

  // Case 1: The fact itself represents a direct relation (has both source and target)
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

  // Case 2: The fact is tied to a node, find its neighbors
  const factNodeId = fact.source_node_uuid || fact.uuid
  if (!factNodeId) return []

  const factNode = fullGraphData.value.nodes.find(n => n.uuid === factNodeId)
  if (!factNode) return []

  const factNodeName = factNode.name

  // Find nodes connected to the node that produced this fact
  const associated = []
  fullGraphData.value.edges.forEach(e => {
    if (e.source_node_uuid === factNodeId) {
      const targetNode = fullGraphData.value.nodes.find(n => n.uuid === e.target_node_uuid)
      if (targetNode && targetNode.uuid !== factNodeId) { // Avoid self-loops in extension
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

  // Deduplicate by description
  const seen = new Set()
  return associated.filter(item => {
    if (seen.has(item.description)) return false
    seen.add(item.description)
    return true
  })
}

// PDF Viewer
const pdfCanvas = ref(null)
const viewerContainer = ref(null)
const pdfjsLib = ref(null)
const currentDoc = ref({ filename: '', url: '', page: 1, bbox: null })

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
  // Reset to the persistently selected one if it exists, otherwise null
  highlightedNodeId.value = selectedNodeId.value
}

const selectFact = (fact, idx) => {
  const nodeId = fact.source_node_uuid || fact.uuid
  if (nodeId) {
    // If clicking the same one, toggle off
    if (selectedNodeId.value === nodeId) {
      selectedNodeId.value = null
      highlightedNodeId.value = null
    } else {
      selectedNodeId.value = nodeId
      highlightedNodeId.value = nodeId
      // Focus node in graph panel
      if (graphPanelRef.value) {
        graphPanelRef.value.focusNode(nodeId)
      }
    }
  }
}

// Logic to handle clicking nodes in GraphPanel
const handleNodeClick = (nodeId) => {
  if (!nodeId) {
    selectedNodeId.value = null
    highlightedNodeId.value = null
    return
  }

  // Update selection
  selectedNodeId.value = nodeId
  highlightedNodeId.value = nodeId

  // Find corresponding fact index
  const factIdx = results.value.facts.findIndex(f => (f.source_node_uuid || f.uuid) === nodeId)
  if (factIdx !== -1) {
    // Auto expand the fact
    expandedFacts.value.add(factIdx)

    // Scroll to the fact item
    nextTick(() => {
      const el = document.getElementById(`fact-item-${factIdx}`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    })
  }
}

// PDF Utilities (Same as AiQaView)
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

  try {
    showDocViewer.value = true
    pdfLoading.value = true

    const apiUrl = `${window.location.origin}/api/graph/project/${projectId}/document/${encodeURIComponent(fact.source)}`
    const response = await fetch(apiUrl)
    if (!response.ok) throw new Error('Failed to fetch document')
    const blob = await response.blob()
    const arrayBuffer = await blob.arrayBuffer()

    if (!pdfjsLib.value) await initPdfJs()

    const loadingTask = pdfjsLib.value.getDocument({ data: arrayBuffer })
    const pdf = await loadingTask.promise
    const page = await pdf.getPage(fact.page || 1)

    const canvas = pdfCanvas.value
    const context = canvas.getContext('2d')
    const viewport = page.getViewport({ scale: 1.5 })

    canvas.height = viewport.height
    canvas.width = viewport.width

    await page.render({ canvasContext: context, viewport }).promise

    currentDoc.value = {
      filename: fact.source,
      page: fact.page || 1
    }
  } catch (err) {
    console.error('PDF error:', err)
    alert('无法加载文档')
  } finally {
    pdfLoading.value = false
  }
}

onMounted(async () => {
  try {
    const res = await getProject(projectId)
    if (res.success) {
      projectName.value = res.data.name
      graphId.value = res.data.graph_id
      if (graphId.value) {
        loadFullGraph()
      }
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

.hit-test-main {
  flex: 1;
  width: 100%;
  display: flex;
  overflow: hidden;
  position: relative;
  background: #fff;
}

.graph-section {
  flex: 7; /* Default 70% */
  min-width: 300px;
  height: 100%;
  border-right: 1px solid #e0e0e0;
  background: #fff;
  transition: flex 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}

.hit-test-main.viewer-open .graph-section {
  flex: 4; /* 40% when viewer open */
}

.right-panels-container {
  flex: 3; /* Default 30% */
  display: flex;
  height: 100%;
  overflow: hidden;
  background: #fff;
  transition: flex 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}

.hit-test-main.viewer-open .right-panels-container {
  flex: 6; /* 60% (30% search + 30% doc) */
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

.pdf-canvas {
  box-shadow: 0 5px 15px rgba(0,0,0,0.3);
  background: #fff;
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
