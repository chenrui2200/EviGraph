<template>
  <div class="graph-search-view">
    <!-- Header -->
    <header class="view-header">
      <div class="header-left">
        <button class="back-btn" @click="router.back()">←</button>
        <h2 class="view-title">图谱检索测试</h2>
      </div>
      <div class="header-right">
        <span class="project-info" v-if="projectName">
          当前项目: <strong>{{ projectName }}</strong>
        </span>
      </div>
    </header>

    <!-- Main Content -->
    <main class="view-main">
      <!-- Left: Graph Visualization -->
      <div class="graph-section">
        <GraphPanel
          ref="graphPanelRef"
          :graph-data="graphData"
          :loading="graphLoading"
          :highlight-node-id="highlightedNodeId"
          :show-legend="true"
          @node-click="handleNodeClick"
        />
        <div v-if="!hasSearched" class="graph-empty-hint">
          <div class="empty-icon">🔎</div>
          <p>请在右侧搜索节点，查看图谱局部结构</p>
        </div>
      </div>

      <!-- Right: Search Panel -->
      <div class="search-panel-section">
        <!-- Search Toolbar -->
        <div class="search-toolbar">
          <div class="search-row">
            <input
              v-model="searchQuery"
              placeholder="输入节点名称、条款编号..."
              @keyup.enter="handleSearch"
              class="search-input"
            />
            <button class="search-btn" @click="handleSearch" :disabled="searching || !searchQuery.trim() || selectedTypes.length === 0">
              <span v-if="!searching">🔍</span>
              <span v-else class="spinner-sm"></span>
            </button>
          </div>
          <div class="type-checkboxes">
            <label
              v-for="t in TYPE_OPTIONS"
              :key="t"
              class="type-checkbox"
              :class="{ active: selectedTypes.includes(t) }"
            >
              <input type="checkbox" :value="t" v-model="selectedTypes" />
              <span class="checkbox-label">{{ t }}</span>
            </label>
          </div>
        </div>

        <!-- Node Detail Panel -->
        <div v-if="selectedNode" class="node-detail-panel">
          <div class="detail-header">
            <span class="detail-title">节点详情</span>
            <button class="close-detail" @click="selectedNode = null">✕</button>
          </div>
          <div class="detail-content">
            <div
              v-for="prop in displayedProperties"
              :key="prop.key"
              class="detail-item"
              :class="{ 'detail-long': prop.isLong, 'detail-object': prop.isObject }"
            >
              <span class="detail-label">{{ prop.label }}</span>
              <span v-if="prop.key === 'type'" class="detail-value">
                <span class="node-type-tag" :class="String(prop.value).toLowerCase()">
                  {{ prop.value }}
                </span>
              </span>
              <pre v-else-if="prop.isObject" class="detail-value detail-pre">{{ formatPropertyValue(prop.value) }}</pre>
              <span v-else class="detail-value" :class="{ 'detail-uuid': prop.key === 'uuid' }">
                {{ formatPropertyValue(prop.value) }}
              </span>
            </div>
          </div>

          <!-- 关联节点：动态展开链路 -->
          <div v-if="currentNeighbors.length" class="neighbors-section">
            <div class="neighbors-header">
              <span class="neighbors-title">关联节点 ({{ currentNeighbors.length }})</span>
              <span class="neighbors-hint">点击展开到图谱</span>
            </div>
            <div class="neighbors-list">
              <div
                v-for="n in currentNeighbors"
                :key="n.uuid"
                class="neighbor-item"
                :class="{ expanded: expandedNodeIds.has(n.uuid || n.id) }"
                @click="expandNode(n)"
              >
                <span class="neighbor-badge" :class="String(n.type || n.labels?.[0] || '').toLowerCase()">
                  {{ n.type || n.labels?.[0] || '?' }}
                </span>
                <span class="neighbor-name">{{ n.name || 'Unnamed' }}</span>
                <span v-if="expandedNodeIds.has(n.uuid || n.id)" class="neighbor-state">已展开</span>
                <span v-else class="neighbor-expand-icon">+</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Search Results -->
        <div class="results-panel">
          <div class="results-header">
            <span>搜索结果 ({{ searchResults.length }})</span>
            <button v-if="searchResults.length" class="clear-btn" @click="clearResults">清空</button>
          </div>

          <div v-if="!searchResults.length && !searching && hasSearched" class="no-results">
            未找到匹配的节点
          </div>

          <div v-if="searching" class="searching-state">
            <div class="spinner-sm"></div>
            <span>搜索中...</span>
          </div>

          <div class="results-list">
            <div
              v-for="node in searchResults"
              :key="node.uuid"
              class="result-item"
              :class="{ active: highlightedNodeId === node.uuid }"
              @click="selectNode(node)"
            >
              <div class="result-name">
                <span class="result-badge" :class="(node.type || node.labels?.[0] || '').toLowerCase()">
                  {{ node.type || node.labels?.[0] || '?' }}
                </span>
                <span class="result-title">{{ node.name || node.topic || 'Unnamed' }}</span>
              </div>
              <div v-if="node.summary || node.topic" class="result-summary">
                {{ node.summary || node.topic }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'

const EXCLUDED_DETAIL_KEYS = new Set([
  'id', 'embedding', 'name_lower', 'fact_embedding',
  'x', 'y', 'vx', 'vy', 'fx', 'fy', 'index', 'radius'
])
import { useRouter, useRoute } from 'vue-router'
import GraphPanel from '../components/GraphPanel.vue'
import { searchNodes, getNodeNeighborhood, getProject } from '../api/graph'

const props = defineProps({
  projectId: String
})

const router = useRouter()
const route = useRoute()

const TYPE_OPTIONS = ['Entity', 'Term', 'Topic', 'Clause']

// Project info
const projectName = ref('')
const graphId = ref('')

// Search state
const searchQuery = ref('')
const selectedTypes = ref(['Entity', 'Term', 'Topic', 'Clause'])
const searching = ref(false)
const searchResults = ref([])
const hasSearched = ref(false)

// Graph state
const graphPanelRef = ref(null)
const graphData = ref({ nodes: [], edges: [] })
const graphLoading = ref(false)
const highlightedNodeId = ref(null)
const selectedNode = ref(null)
const currentNeighbors = ref([]) // 当前选中节点的邻居列表
const expandedNodeIds = ref(new Set()) // 已展开到图谱的节点ID集合

// Load project info
const loadProject = async () => {
  try {
    const res = await getProject(props.projectId)
    if (res.success) {
      projectName.value = res.data.name
      graphId.value = res.data.graph_id
    }
  } catch (err) {
    console.error('Failed to load project:', err)
  }
}

// Search nodes
const handleSearch = async () => {
  if (!searchQuery.value.trim() || !graphId.value || selectedTypes.value.length === 0) return

  searching.value = true
  hasSearched.value = true
  searchResults.value = []
  selectedNode.value = null
  highlightedNodeId.value = null
  currentNeighbors.value = []
  expandedNodeIds.value = new Set()

  try {
    const res = await searchNodes({
      graph_id: graphId.value,
      query: searchQuery.value.trim(),
      node_types: selectedTypes.value,
      limit: 20
    })

    if (res.success && res.data.nodes) {
      searchResults.value = res.data.nodes

      // Load neighborhood for all found nodes
      await loadNeighborhoods(res.data.nodes)
    }
  } catch (err) {
    console.error('Search failed:', err)
  } finally {
    searching.value = false
  }
}

// Load neighborhoods for all found nodes and merge into graphData
const loadNeighborhoods = async (nodes) => {
  graphLoading.value = true
  const allNodes = new Map()
  const allEdges = new Map()

  for (const node of nodes) {
    // Add center node
    allNodes.set(node.uuid, normalizeNode(node))

    try {
      const res = await getNodeNeighborhood({
        graph_id: graphId.value,
        node_uuid: node.uuid
      })

      if (res.success && res.data) {
        // Add neighbor nodes
        for (const n of res.data.nodes || []) {
          if (!allNodes.has(n.uuid)) {
            allNodes.set(n.uuid, normalizeNode(n))
          }
        }

        // Add edges
        for (const e of res.data.edges || []) {
          const edgeKey = `${e.uuid || e.source_uuid}-${e.target_uuid}-${e.name}`
          if (!allEdges.has(edgeKey)) {
            allEdges.set(edgeKey, normalizeEdge(e))
          }
        }
      }
    } catch (err) {
      console.error(`Failed to load neighborhood for ${node.uuid}:`, err)
    }
  }

  graphData.value = {
    nodes: Array.from(allNodes.values()),
    edges: Array.from(allEdges.values())
  }
  graphLoading.value = false
}

// Normalize node for GraphPanel
const normalizeNode = (node) => {
  const labels = node.labels || []
  let type = node.type
  if (!type) {
    if (labels.includes('Term')) type = 'Term'
    else if (labels.includes('Topic')) type = 'Topic'
    else if (labels.includes('Clause')) type = 'Clause'
    else if (labels.includes('Entity')) type = 'Entity'
    else type = labels[0] || 'Unknown'
  }
  return {
    id: node.uuid,
    uuid: node.uuid,
    name: node.name || node.topic || 'Unnamed',
    type: type,
    labels: labels,
    summary: node.summary || '',
    ...node
  }
}

// Normalize edge for GraphPanel
const normalizeEdge = (edge) => {
  return {
    id: edge.uuid || `${edge.source_uuid}-${edge.target_uuid}`,
    source: edge.source_uuid,
    target: edge.target_uuid,
    name: edge.name || edge.relation_name || 'RELATION',
    ...edge
  }
}

// Select a node from results list
const selectNode = async (node) => {
  highlightedNodeId.value = node.uuid
  selectedNode.value = node

  // Focus on graph
  if (graphPanelRef.value) {
    graphPanelRef.value.focusNode(node.uuid)
  }

  // Load neighbors
  await loadNodeNeighbors(node.uuid)
}

// Load neighbors for a specific node (for detail panel display)
const loadNodeNeighbors = async (nodeUuid) => {
  if (!graphId.value) return
  try {
    const res = await getNodeNeighborhood({
      graph_id: graphId.value,
      node_uuid: nodeUuid
    })
    if (res.success && res.data) {
      currentNeighbors.value = (res.data.nodes || [])
        .filter(n => n.uuid !== nodeUuid)
        .map(normalizeNode)
    }
  } catch (err) {
    console.error('Failed to load neighbors:', err)
    currentNeighbors.value = []
  }
}

// Expand a neighbor node into the graph (load its neighborhood and merge)
const expandNode = async (node) => {
  const uuid = node.uuid || node.id

  // Always highlight and focus on the graph first
  highlightedNodeId.value = uuid
  if (graphPanelRef.value) {
    graphPanelRef.value.focusNode(uuid)
  }

  // If already expanded, just keep it highlighted
  if (expandedNodeIds.value.has(uuid)) return

  graphLoading.value = true
  try {
    const res = await getNodeNeighborhood({
      graph_id: graphId.value,
      node_uuid: uuid
    })

    if (res.success && res.data) {
      const existingNodeIds = new Set(graphData.value.nodes.map(n => n.uuid || n.id))
      const existingEdgeKeys = new Set(graphData.value.edges.map(e => {
        const sid = e.source_uuid || e.source
        const tid = e.target_uuid || e.target
        return `${e.uuid || e.id || ''}-${sid}-${tid}-${e.name || e.relation_name || ''}`
      }))

      const newNodes = []
      for (const n of res.data.nodes || []) {
        const nid = n.uuid
        if (!existingNodeIds.has(nid)) {
          newNodes.push(normalizeNode(n))
          existingNodeIds.add(nid)
        }
      }

      const newEdges = []
      for (const e of res.data.edges || []) {
        const sid = e.source_uuid || e.source
        const tid = e.target_uuid || e.target
        const ekey = `${e.uuid || e.id || ''}-${sid}-${tid}-${e.name || e.relation_name || ''}`
        if (!existingEdgeKeys.has(ekey)) {
          newEdges.push(normalizeEdge(e))
          existingEdgeKeys.add(ekey)
        }
      }

      if (newNodes.length || newEdges.length) {
        graphData.value = {
          nodes: [...graphData.value.nodes, ...newNodes],
          edges: [...graphData.value.edges, ...newEdges]
        }
      }

      expandedNodeIds.value.add(uuid)
    }
  } catch (err) {
    console.error('Expand node failed:', err)
  } finally {
    graphLoading.value = false
  }
}

// Handle node click from GraphPanel (nodeId is a string UUID, null when clicking blank)
const handleNodeClick = async (nodeId) => {
  if (!nodeId) {
    selectedNode.value = null
    currentNeighbors.value = []
    highlightedNodeId.value = null
    return
  }
  const uuid = typeof nodeId === 'string' ? nodeId : (nodeId.uuid || nodeId.id)
  highlightedNodeId.value = uuid

  // Find full node data
  const found = searchResults.value.find(n => n.uuid === uuid)
  if (found) {
    selectedNode.value = found
  } else {
    const graphNode = graphData.value.nodes.find(n => n.uuid === uuid || n.id === uuid)
    if (graphNode) {
      selectedNode.value = graphNode
    }
  }

  // Load neighbors for this node
  await loadNodeNeighbors(uuid)
}

// 格式化属性值为展示字符串
const formatPropertyValue = (value) => {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

// 属性标签中文映射
const PROPERTY_LABEL_MAP = {
  uuid: 'UUID',
  name: '名称',
  labels: '标签',
  type: '类型',
  summary: '摘要',
  topic: '主题',
  data: '内容',
  definition: '定义',
  attributes: '属性',
  clause_id: '条款编号',
  pdf_source: 'PDF 来源',
  pdf_page: 'PDF 页码',
  pdf_bbox: 'PDF 位置',
  pdf_bboxes: 'PDF 多页位置',
  pdf_page_width: 'PDF 页宽',
  pdf_page_height: 'PDF 页高',
  created_at: '创建时间',
  graph_id: '图谱ID'
}

// 动态计算要展示的节点属性（排除内部字段，按优先级排序）
const displayedProperties = computed(() => {
  if (!selectedNode.value) return []
  const priorityOrder = [
    'name', 'type', 'labels', 'uuid',
    'summary', 'topic', 'data', 'definition',
    'attributes', 'clause_id',
    'pdf_source', 'pdf_page', 'pdf_bbox', 'pdf_bboxes',
    'pdf_page_width', 'pdf_page_height',
    'created_at', 'graph_id'
  ]
  const entries = Object.entries(selectedNode.value).filter(([key, value]) => {
    if (EXCLUDED_DETAIL_KEYS.has(key)) return false
    if (value === null || value === undefined) return false
    if (typeof value === 'string' && value.trim() === '') return false
    if (Array.isArray(value) && value.length === 0) return false
    if (typeof value === 'object' && Object.keys(value).length === 0) return false
    return true
  })
  // 按优先级排序，未定义优先级的放最后
  entries.sort((a, b) => {
    const pa = priorityOrder.indexOf(a[0])
    const pb = priorityOrder.indexOf(b[0])
    if (pa !== -1 && pb !== -1) return pa - pb
    if (pa !== -1) return -1
    if (pb !== -1) return 1
    return a[0].localeCompare(b[0])
  })
  return entries.map(([key, value]) => ({
    key,
    label: PROPERTY_LABEL_MAP[key] || key,
    value,
    isLong: typeof value === 'string' && value.length > 60,
    isObject: typeof value === 'object'
  }))
})

const clearResults = () => {
  searchResults.value = []
  graphData.value = { nodes: [], edges: [] }
  highlightedNodeId.value = null
  selectedNode.value = null
  currentNeighbors.value = []
  expandedNodeIds.value = new Set()
  hasSearched.value = false
}

onMounted(() => {
  loadProject()
})
</script>

<style scoped>
.graph-search-view {
  width: 100%;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f4f7f9;
  font-family: 'Inter', -apple-system, sans-serif;
  overflow: hidden;
}

.view-header {
  height: 50px;
  background: #fff;
  border-bottom: 1px solid #e0e0e0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.back-btn {
  background: none;
  border: none;
  font-size: 18px;
  cursor: pointer;
  color: #666;
  padding: 4px 8px;
}

.view-title {
  font-size: 16px;
  font-weight: 700;
  margin: 0;
  color: #333;
}

.header-right {
  font-size: 13px;
  color: #666;
}

.project-info strong {
  color: #333;
}

.view-main {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.graph-section {
  flex: 1;
  position: relative;
  background: #fff;
  border-right: 1px solid #e0e0e0;
}

.graph-empty-hint {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  color: #999;
  pointer-events: none;
}

.graph-empty-hint .empty-icon {
  font-size: 48px;
  margin-bottom: 12px;
  opacity: 0.4;
}

.graph-empty-hint p {
  font-size: 14px;
}

.search-panel-section {
  width: 600px;
  display: flex;
  flex-direction: column;
  background: #fff;
  overflow: hidden;
}

/* Search Toolbar */
.search-toolbar {
  padding: 16px;
  border-bottom: 1px solid #f0f0f0;
  flex-shrink: 0;
}

.type-checkboxes {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.type-checkbox {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid #dcdfe6;
  cursor: pointer;
  font-size: 12px;
  color: #606266;
  background: #fff;
  transition: all 0.15s;
  user-select: none;
}

.type-checkbox:hover {
  border-color: #409eff;
}

.type-checkbox.active {
  background: #e6f7ff;
  border-color: #409eff;
  color: #0969da;
}

.type-checkbox input {
  cursor: pointer;
  margin: 0;
}

.search-row {
  display: flex;
  gap: 8px;
}

.search-input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
  transition: border-color 0.2s;
}

.search-input:focus {
  border-color: #409eff;
}

.search-btn {
  width: 40px;
  height: 36px;
  background: #000;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  transition: opacity 0.2s;
}

.search-btn:hover:not(:disabled) {
  opacity: 0.8;
}

.search-btn:disabled {
  background: #ccc;
  cursor: not-allowed;
}

/* Node Detail Panel */
.node-detail-panel {
  margin: 12px;
  padding: 12px;
  background: #f8f9fa;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  flex-shrink: 0;
  max-height: 45vh;
  overflow-y: auto;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.detail-title {
  font-size: 13px;
  font-weight: 700;
  color: #333;
}

.close-detail {
  background: none;
  border: none;
  font-size: 16px;
  color: #999;
  cursor: pointer;
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-item {
  display: flex;
  gap: 8px;
  font-size: 12px;
}

.detail-label {
  color: #909399;
  min-width: 50px;
  flex-shrink: 0;
}

.detail-value {
  color: #333;
  word-break: break-all;
}

.detail-value.uuid {
  font-family: monospace;
  font-size: 11px;
  color: #666;
}

.detail-value.summary {
  color: #606266;
  line-height: 1.5;
}

.detail-value.detail-uuid {
  font-family: monospace;
  font-size: 11px;
  color: #666;
}

.detail-item.detail-long {
  flex-direction: column;
  gap: 4px;
}

.detail-item.detail-long .detail-label {
  min-width: auto;
}

.detail-item.detail-object {
  flex-direction: column;
  gap: 4px;
}

.detail-item.detail-object .detail-label {
  min-width: auto;
}

.detail-pre {
  margin: 0;
  padding: 6px 8px;
  background: #f0f0f0;
  border-radius: 4px;
  font-size: 11px;
  line-height: 1.4;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: 'JetBrains Mono', monospace;
}

/* Node Type Tags */
.node-type-tag,
.result-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
}

.node-type-tag.entity,
.result-badge.entity {
  background: #e6f7ff;
  color: #0969da;
}

.node-type-tag.term,
.result-badge.term {
  background: #fff0f6;
  color: #cf3385;
}

.node-type-tag.topic,
.result-badge.topic {
  background: #f6ffed;
  color: #389e0d;
}

.node-type-tag.clause,
.result-badge.clause {
  background: #fff7e6;
  color: #d48806;
}

/* Results Panel */
.results-panel {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.results-header {
  padding: 10px 16px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  color: #666;
  flex-shrink: 0;
}

.clear-btn {
  background: none;
  border: none;
  font-size: 12px;
  color: #909399;
  cursor: pointer;
}

.clear-btn:hover {
  color: #409eff;
}

.results-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.result-item {
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s;
  margin-bottom: 4px;
}

.result-item:hover {
  background: #f0f7ff;
}

.result-item.active {
  background: #e6f7ff;
  border-left: 3px solid #409eff;
}

.result-name {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.result-title {
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.result-summary {
  font-size: 11px;
  color: #909399;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.no-results {
  text-align: center;
  padding: 40px 20px;
  color: #999;
  font-size: 13px;
}

.searching-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px;
  color: #409eff;
  font-size: 13px;
}

/* Neighbors Section */
.neighbors-section {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed #e0e0e0;
}

.neighbors-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.neighbors-title {
  font-size: 12px;
  font-weight: 700;
  color: #333;
}

.neighbors-hint {
  font-size: 11px;
  color: #909399;
}

.neighbors-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 240px;
  overflow-y: auto;
}

.neighbor-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
  font-size: 12px;
}

.neighbor-item:hover {
  background: #f0f7ff;
}

.neighbor-item.expanded {
  background: #f6ffed;
  cursor: default;
}

.neighbor-badge {
  display: inline-block;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 700;
  flex-shrink: 0;
}

.neighbor-badge.entity {
  background: #e6f7ff;
  color: #0969da;
}

.neighbor-badge.term {
  background: #fff0f6;
  color: #cf3385;
}

.neighbor-badge.topic {
  background: #f6ffed;
  color: #389e0d;
}

.neighbor-badge.clause {
  background: #fff7e6;
  color: #d48806;
}

.neighbor-name {
  flex: 1;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.neighbor-expand-icon {
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #409eff;
  color: #fff;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}

.neighbor-state {
  font-size: 10px;
  color: #52c41a;
  font-weight: 600;
  flex-shrink: 0;
}

.spinner-sm {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(64, 158, 255, 0.3);
  border-top-color: #409eff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>