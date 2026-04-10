<template>
  <div class="chunk-analysis-view">
    <!-- Header -->
    <header class="ca-header">
      <div class="header-left">
        <div class="brand" @click="router.push('/')">Knowledge EviGraph</div>
      </div>

      <div class="header-right">
        <StepNavigator
          :projectId="currentProjectId"
          :projectStatus="analysisStatus"
          :currentStep="1"
        />
        <div class="step-divider"></div>
        <span class="status-indicator" :class="'status-' + analysisStatus">
          <span class="dot" :style="{ background: statusDotColor }"></span>
          {{ statusLabel }}
        </span>
        <div v-if="analysisStatus === 'graph_chunking'" class="progress-wrapper">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
          </div>
          <span class="progress-text">{{ progressPercent }}%</span>
        </div>
        <button
          v-if="analysisStatus === 'graph_chunked' || analysisStatus === 'graph_completed'"
          class="goto-build-btn"
          @click="goToGraphBuild">
          进入图谱构建 →
        </button>
      </div>
    </header>

    <!-- Main Content: Left PDF + Right Analysis -->
    <main class="ca-main">
      <!-- ========== LEFT: PDF Preview ========== -->
      <div class="pdf-panel">
        <!-- PDF 工具栏 -->
        <div class="pdf-toolbar">
          <div class="pdf-toolbar-title">
            <span v-if="pdfFileName">{{ pdfFileName }}</span>
            <span v-else class="pdf-placeholder">暂无PDF文件</span>
          </div>
          <div class="pdf-toolbar-actions">
            <!-- MinerU 标注开关 -->
            <button
              v-if="mineruChunks.length > 0"
              class="mineru-toggle"
              :class="{ active: mineruMode }"
              @click="toggleMineruMode"
              title="切换 MinerU 布局标注">
              {{ mineruMode ? '📊 分析模式' : '🔵 MinerU 标注' }}
            </button>
          </div>
          <div class="pdf-page-count" v-if="totalPages > 0">
            {{ totalPages }} 页
          </div>
        </div>

        <!-- PDF 渲染区域 -->
        <div class="pdf-body" ref="viewerContainer">
          <!-- 空白状态 -->
          <div v-if="!pdfUrl" class="pdf-empty">
            <div class="pdf-empty-icon">📄</div>
            <p>请等待PDF文件加载...</p>
            <p class="pdf-empty-hint">分析过程中的条文标注将实时显示在此区域</p>
          </div>

          <!-- 全量 PDF：每页一个 canvas + SVG 叠加层 -->
          <div v-if="pdfUrl && renderedPages.length > 0" class="pdf-scroll-container">
            <div
              v-for="rp in renderedPages"
              :key="rp.pageNum"
              class="pdf-page-wrapper"
              :data-page="rp.pageNum"
            >
              <canvas
                :ref="el => setCanvasRef(el, rp.pageNum)"
                class="pdf-canvas"
              ></canvas>

              <!-- SVG BBox 叠加层（每个页面独立） -->
              <svg
                v-if="getPageAnnotations(rp.pageNum).length > 0"
                class="bbox-overlay"
                :viewBox="`0 0 ${rp.pageWidth} ${rp.pageHeight}`"
                :style="{ width: rp.canvasWidth + 'px', height: rp.canvasHeight + 'px', top: '0', left: '0' }"
              >
                <template v-for="ann in getPageAnnotations(rp.pageNum)" :key="ann.clauseId">
                  <rect
                    v-if="ann.isMineru"
                    :x="ann.bbox[0]"
                    :y="ann.bbox[1]"
                    :width="ann.bbox[2] - ann.bbox[0]"
                    :height="ann.bbox[3] - ann.bbox[1]"
                    class="bbox-rect bbox-mineru"
                    :class="{ 'bbox-active': highlightedClauseId === ann.clauseId }"
                    :style="{ stroke: categoryIdColor(ann.categoryId) }"
                    @click="onMineruBboxClick(ann)"
                  />
                  <rect
                    v-else
                    :x="ann.bbox[0]"
                    :y="ann.bbox[1]"
                    :width="ann.bbox[2] - ann.bbox[0]"
                    :height="ann.bbox[3] - ann.bbox[1]"
                    class="bbox-rect"
                    :class="['bbox-' + ann.type, { 'bbox-active': highlightedClauseId === ann.clauseId }]"
                    @click="onBboxClick(ann)"
                  />
                </template>
              </svg>
            </div>
          </div>

          <!-- 加载状态 -->
          <div v-if="pdfLoading" class="pdf-loading">
            <div class="spinner"></div>
            <span>正在渲染全部 {{ totalPages }} 页...</span>
          </div>
        </div>

        <!-- 图例 -->
        <div class="bbox-legend" v-if="pdfUrl">
          <template v-if="!mineruMode">
            <span class="legend-item">
              <span class="legend-dot clause-dot"></span> Clause
            </span>
            <span class="legend-item">
              <span class="legend-dot element-dot"></span> Element
            </span>
            <span class="legend-item">
              <span class="legend-dot term-dot"></span> Term
            </span>
          </template>
          <template v-else>
            <span class="legend-item">
              <span class="legend-dot" style="border-color:#ea580c;background:#fff7ed"></span> 标题
            </span>
            <span class="legend-item">
              <span class="legend-dot" style="border-color:#2563eb;background:#eff6ff"></span> 正文
            </span>
            <span class="legend-item">
              <span class="legend-dot" style="border-color:#16a34a;background:#f0fdf4"></span> 表格
            </span>
            <span class="legend-item">
              <span class="legend-dot" style="border-color:#9333ea;background:#f3e8ff"></span> 图片
            </span>
            <span class="legend-item">
              <span class="legend-dot" style="border-color:#ca8a04;background:#fef9c3"></span> 公式
            </span>
            <span v-if="mineruSummary" class="legend-count">
              {{ mineruChunks.length }} 块
            </span>
          </template>
        </div>
      </div>

      <!-- ========== RIGHT: Analysis Panel ========== -->
      <div class="analysis-panel">
        <!-- ===== MinerU 解析结果面板 ===== -->
        <div v-if="mineruMode && mineruChunks.length" class="mineru-panel">
          <div class="mineru-panel-header">
            <div class="mineru-panel-title">
              <span>🧠 MinerU 布局解析</span>
              <span v-if="mineruSummary" class="mineru-summary-badges">
                <span class="badge">{{ mineruSummary.total_pages }} 页</span>
                <span class="badge">{{ mineruChunks.length }} 块</span>
              </span>
            </div>
            <button class="re-annotate-btn" @click="handleReAnnotate" :disabled="reAnnotating">
              {{ reAnnotating ? '标注中...' : '重新标注' }}
            </button>
            <button class="close-mineru-btn" @click="toggleMineruMode">×</button>
          </div>

          <!-- 布局统计 -->
          <div class="mineru-layout-info">
            <div class="layout-info-row">
              <span class="info-label">标题块:</span>
              <span class="info-value">{{ mineruChunks.filter(c => c.category_id === 0).length }}</span>
              <span class="info-label" style="margin-left:12px">正文块:</span>
              <span class="info-value">{{ mineruChunks.filter(c => c.category_id === 1).length }}</span>
            </div>
            <div class="layout-info-row">
              <span class="info-label">表格块:</span>
              <span class="info-value">{{ mineruChunks.filter(c => c.category_id === 2 || c.category_id === 4).length }}</span>
              <span class="info-label" style="margin-left:12px">图片块:</span>
              <span class="info-value">{{ mineruChunks.filter(c => c.category_id === 3 || c.category_id === 5).length }}</span>
            </div>
          </div>

          <!-- Chunks 列表（联动 PDF 标注） -->
          <div class="mineru-chunk-list" ref="mineruchunkListRef">
            <div
              v-for="chunk in mineruChunks"
              :key="chunk.chunk_id"
              class="mineru-chunk-item"
              :class="{ 'chunk-active': mineruSelectedChunk?.chunk_id === chunk.chunk_id }"
              :data-chunk-id="chunk.chunk_id"
              @click="onMineruChunkClick(chunk)"
            >
              <div class="chunk-item-header">
                <span
                  class="chunk-type-badge"
                  :style="{ background: categoryIdColor(chunk.category_id || 1) + '22', color: categoryIdColor(chunk.category_id || 1) }"
                >{{ categoryIdLabel(chunk.category_id) }}</span>
                <span class="chunk-block-type">{{ chunk.block_type || '' }}</span>
                <span class="chunk-page">P{{ (chunk.page_idx || 0) + 1 }}</span>
                <span class="chunk-id">{{ chunk.chunk_id }}</span>
              </div>
              <div class="chunk-item-content">{{ chunk.content }}</div>
            </div>
          </div>

          <!-- 选中块详情 -->
          <div v-if="mineruSelectedChunk" class="mineru-chunk-detail">
            <div class="detail-header">
              <span class="detail-type">{{ categoryIdLabel(mineruSelectedChunk.category_id) }}</span>
              <span class="detail-block-type">{{ mineruSelectedChunk.block_type || '' }}</span>
              <span class="detail-page">页 {{ (mineruSelectedChunk.page_idx || 0) + 1 }}</span>
            </div>
            <div class="detail-content">{{ mineruSelectedChunk.content }}</div>

            <div v-if="mineruSelectedChunk.type === 'table' && mineruSelectedChunk.table_caption" class="detail-table-caption">
              <span class="detail-table-caption-label">表头：</span>
              <span>{{ mineruSelectedChunk.table_caption }}</span>
            </div>
            <!-- 表格内容 -->
            <div v-if="mineruSelectedChunk.type === 'table' && mineruSelectedChunk.table_content" class="detail-table-content">
              <div class="detail-table-label">表格内容</div>
              <div class="detail-table-markdown" v-html="mineruSelectedChunk.table_content"></div>
            </div>
            <div v-if="mineruSelectedChunk.type === 'table' && mineruSelectedChunk.table_footnote" class="detail-table-footnote">
              <div class="detail-table-label">表注</div>
              <div class="detail-table-footnote-text">{{ mineruSelectedChunk.table_footnote }}</div>
            </div>
            <div v-if="mineruSelectedChunk.bbox_viewport" class="detail-bbox">
              <span class="detail-bbox-label">bbox_viewport:</span>
              <span class="detail-bbox-val">{{ mineruSelectedChunk.bbox_viewport.join(', ') }}</span>
            </div>
            <div v-if="mineruSelectedChunk.bbox_pdf" class="detail-bbox">
              <span class="detail-bbox-label">bbox_pdf:</span>
              <span class="detail-bbox-val">{{ mineruSelectedChunk.bbox_pdf.join(', ') }}</span>
            </div>
          </div>
        </div>

        <!-- 统计摘要 + 章节树（与 MinerU 面板互斥） -->
        <div v-if="!mineruMode" class="analysis-content">
          <div class="analysis-panel-header">
            <div class="analysis-panel-title">
              <span>🧠 智能分析</span>
            </div>
            <button class="re-analyse-btn" @click="handleResetChunking" :disabled="starting">
              🔄 重新分析
            </button>
          </div>

          <!-- 章节树 -->
          <div class="chapter-tree" v-if="analysisData">
          <div class="tree-header">
            <span>章节树</span>
            <div class="tree-actions">
              <button class="expand-all-btn" @click="toggleAllChapters">
                {{ allExpanded ? '全部收起' : '全部展开' }}
              </button>
            </div>
          </div>

          <div class="tree-body">
            <div v-for="chapter in analysisData.chapter_tree" :key="chapter.chapter.chapter_number"
                 class="chapter-item">
              <div class="chapter-header"
                   :class="{ expanded: expandedChapters[chapter.chapter.chapter_number] }"
                   @click="toggleChapter(chapter.chapter.chapter_number)">
                <span class="chapter-toggle">{{ expandedChapters[chapter.chapter.chapter_number] ? '▼' : '▶' }}</span>
                <span class="chapter-title">
                  {{ chapter.chapter.chapter_number }}. {{ chapter.chapter.title }}
                </span>
                <span class="chapter-meta">[{{ chapter.chapter.clause_count }}条]</span>
              </div>

              <div class="clause-list" v-show="expandedChapters[chapter.chapter.chapter_number]">
                <div v-for="clause in chapter.clauses" :key="clause.clause_id"
                     class="clause-item"
                     :class="{ 'clause-active': highlightedClauseId === clause.clause_id }"
                     @click="handleClauseClick(clause)">
                  <div class="clause-row">
                    <span class="clause-id">{{ clause.clause_id }}</span>
                    <span class="clause-title">{{ clause.clause_title || clause.content?.substring(0, 40) + '...' }}</span>
                  </div>

                  <!-- 条文详情 -->
                  <div class="clause-detail" v-if="expandedClauseId === clause.clause_id">
                    <!-- PDF 位置信息（支持多 bbox） -->
                    <div class="entity-row" v-if="clause.bboxs?.length || clause.page || clause.pdf_location?.page">
                      <span class="entity-label" style="color:#6b7280">📍 位置</span>
                      <template v-if="clause.bboxs?.length">
                        <span v-for="(item, idx) in clause.bboxs" :key="idx" class="entity-tag" style="background:#f3f4f6;color:#374151;border-color:#d1d5db">
                          P{{ item[0] }}: [
                          {{ item.slice(1,3).join(',') }},
                          {{ item.slice(3).join(',') }}
                          ]
                        </span>
                      </template>
                      <template v-else>
                        <span class="entity-tag" style="background:#f3f4f6;color:#374151;border-color:#d1d5db">
                          第 {{ clause.pdf_location?.page ?? clause.page }} 页
                        </span>
                        <span v-if="clause.pdf_location?.bbox || clause.bbox" class="entity-tag" style="background:#f3f4f6;color:#374151;border-color:#d1d5db;font-family:monospace;font-size:10px">
                          bbox: [
                          {{ (clause.pdf_location?.bbox ?? clause.bbox)?.slice(0,2).join(', ') }} ,
                          {{ (clause.pdf_location?.bbox ?? clause.bbox)?.slice(2).join(', ') }}
                          ]
                        </span>
                      </template>
                    </div>

                    <!-- Term -->
                    <div class="entity-row" v-if="clause.terms?.length || editingClauseId === clause.clause_id">
                      <span class="entity-label term-label">🔵 Term</span>
                      <div class="entity-tags">
                        <template v-for="(t, i) in (editingClauseId === clause.clause_id ? editingTerms : clause.terms)" :key="i">
                          <span v-if="typeof t === 'string'" class="entity-tag term-tag">{{ t }}</span>
                          <span v-else class="term-item" @click.stop="toggleTermDef(t.term_name)">
                            <span class="entity-tag term-tag" :class="{ active: expandedTermDefs.has(t.term_name) }">
                              {{ t.term_name }}
                            </span>
                            <span v-if="t.definition" class="term-def-arrow">{{ expandedTermDefs.has(t.term_name) ? '▲' : '▼' }}</span>
                            <div v-if="expandedTermDefs.has(t.term_name) && t.definition" class="term-definition">
                              <span class="def-connector">(解释)</span>
                              {{ t.definition }}
                            </div>
                          </span>
                        </template>
                      </div>
                      <button class="edit-btn" @click.stop="startEditEntity(clause, 'terms')">
                        {{ editingClauseId === clause.clause_id ? '取消' : '编辑' }}
                      </button>
                    </div>
                    <div v-if="editingClauseId === clause.clause_id && editingField === 'terms'" class="entity-editor">
                      <input v-model="editingValue" class="entity-input"
                             placeholder="输入Term，多个用逗号分隔" @keyup.enter="saveEntity(clause.clause_id, 'terms')"/>
                      <button class="save-btn" @click="saveEntity(clause.clause_id, 'terms')">保存</button>
                    </div>

                    <!-- Condition -->
                    <div class="entity-row" v-if="clause.conditions?.length || editingClauseId === clause.clause_id">
                      <span class="entity-label cond-label">🟡 Cond</span>
                      <div class="entity-tags">
                        <span v-for="(c, i) in (editingClauseId === clause.clause_id ? editingConditions : clause.conditions)"
                              :key="i" class="entity-tag cond-tag">{{ c }}</span>
                      </div>
                      <button class="edit-btn" @click.stop="startEditEntity(clause, 'conditions')">
                        {{ editingClauseId === clause.clause_id ? '取消' : '编辑' }}
                      </button>
                    </div>
                    <div v-if="editingClauseId === clause.clause_id && editingField === 'conditions'" class="entity-editor">
                      <input v-model="editingValue" class="entity-input"
                             placeholder="输入条件，多个用逗号分隔" @keyup.enter="saveEntity(clause.clause_id, 'conditions')"/>
                      <button class="save-btn" @click="saveEntity(clause.clause_id, 'conditions')">保存</button>
                    </div>

                    <!-- Action -->
                    <div class="entity-row" v-if="clause.actions?.length || editingClauseId === clause.clause_id">
                      <span class="entity-label action-label">🔷 Act</span>
                      <div class="entity-tags">
                        <span v-for="(a, i) in (editingClauseId === clause.clause_id ? editingActions : clause.actions)"
                              :key="i" class="entity-tag action-tag">{{ a }}</span>
                      </div>
                      <button class="edit-btn" @click.stop="startEditEntity(clause, 'actions')">
                        {{ editingClauseId === clause.clause_id ? '取消' : '编辑' }}
                      </button>
                    </div>
                    <div v-if="editingClauseId === clause.clause_id && editingField === 'actions'" class="entity-editor">
                      <input v-model="editingValue" class="entity-input"
                             placeholder="输入动作，多个用逗号分隔" @keyup.enter="saveEntity(clause.clause_id, 'actions')"/>
                      <button class="save-btn" @click="saveEntity(clause.clause_id, 'actions')">保存</button>
                    </div>

                    <!-- Component -->
                    <div class="entity-row" v-if="clause.components?.length">
                      <span class="entity-label comp-label">🟣 Comp</span>
                      <div class="entity-tags">
                        <span v-for="(c, i) in clause.components" :key="i" class="entity-tag comp-tag">{{ c }}</span>
                      </div>
                    </div>

                    <!-- 语义三元组 -->
                    <div class="triplets-section" v-if="clause.metadata?.triplets?.length">
                      <div class="triplet-label">📌 语义三元组</div>
                      <div v-for="(triplet, ti) in clause.metadata.triplets" :key="ti" class="triplet-row">
                        <span class="triplet-comp">{{ triplet.component || '—' }}</span>
                        <span class="triplet-arrow">—{{ triplet.requirement?.[0]?.toUpperCase() || 'M' }}→</span>
                        <span class="triplet-obj">{{ triplet.obj || '—' }}</span>
                        <span v-if="triplet.condition" class="triplet-cond">@ {{ triplet.condition }}</span>
                      </div>
                    </div>

                    <!-- 知识实体 -->
                    <div class="clause-entities-section" v-if="clause.topic || (clause.entities?.length || clause.related_elements?.length)">
                      <!-- 条款摘要 -->
                      <div v-if="clause.topic" class="clause-topic-row">
                        <span class="topic-label">📝 摘要</span>
                        <span class="topic-content">{{ clause.topic }}</span>
                      </div>
                      <!-- 实体列表 -->
                      <div v-if="clause.entities?.length || clause.related_elements?.length" class="entity-section-label">📎 知识实体</div>
                      <div class="entity-item-row" v-for="(elem, ei) in (clause.entities || clause.related_elements || [])" :key="ei">
                        <template v-if="typeof elem === 'string'">
                          <span class="entity-type-tag">noun</span>
                          <span class="entity-key">{{ elem }}</span>
                        </template>
                        <template v-else>
                          <span class="entity-type-tag">{{ elem.element_type || 'noun_entity' }}</span>
                          <span class="entity-key">{{ elem.key }}</span>
                          <span v-if="elem.value" class="entity-value">= {{ elem.value }}</span>
                          <span v-if="elem.unit" class="entity-unit">{{ elem.unit }}</span>
                        </template>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        </div>

      </div>
    </main>

    <!-- 实时日志抽屉 -->
    <div class="log-drawer" :class="{ open: logDrawerOpen }">
      <div class="log-drawer-header" @click="logDrawerOpen = !logDrawerOpen">
        <span>实时日志</span>
        <span class="log-toggle">{{ logDrawerOpen ? '▼' : '▲' }}</span>
      </div>
      <div class="log-drawer-body" v-if="logDrawerOpen" ref="logScrollEl">
        <div v-for="(log, i) in realtimeLogs" :key="i" class="log-line" :class="logClass(log)">
          {{ log }}
        </div>
        <div v-if="!realtimeLogs.length" class="log-empty">暂无日志</div>
      </div>
    </div>

    <!-- 开始分析按钮（首次） -->
    <div v-if="showStartButton" class="start-overlay">
      <div class="start-card">
        <div class="start-icon">📊</div>
        <h3>智能Chunks标注分析</h3>
        <p>项目 <strong>{{ projectName }}</strong> 已上传完成，开始执行 LLM 智能分块与知识实体标注。</p>
        <div class="start-actions">
          <button class="start-btn" @click="handleStartChunking" :disabled="starting">
            <span v-if="!starting">🚀 开始智能分析</span>
            <span v-else class="spinner-sm"></span>
          </button>
          <button class="reset-btn" @click="handleResetChunking" :disabled="starting">
            重置并重新分析
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  generateOntology,
  getProject,
  getChunkAnalysis,
  getChunkProgress,
  startChunking,
  updateClauseEntity,
  getTaskStatus,
  getMineruChunks,
  getTaskEventsURL,
  reAnnotateMineru
} from '../api/graph'
import StepNavigator from '../components/StepNavigator.vue'
import { getPendingUpload, clearPendingUpload } from '../store/pendingUpload'

const props = defineProps({
  projectId: { type: String, required: true }
})

const router = useRouter()

// ============================================================================
// 状态
// ============================================================================

// 项目信息
const projectName = ref('')
const currentProjectId = ref('')
const analysisStatus = ref('')
const taskId = ref(null)

// PDF 渲染
const pdfUrl = ref('')
const pdfFileName = ref('')

const pdfjsLib = ref(null)
const viewerContainer = ref(null)
const pdfLoading = ref(false)
const totalPages = ref(0)
const pdfDoc = ref(null)
const pdfDocUrl = ref('')
const renderedPages = ref([])  // [{pageNum, pageWidth, pageHeight, canvasWidth, canvasHeight}]
const pageCanvasMap = ref({}) // pageNum -> canvas element

// BBox 标注
const allAnnotations = ref([])
const highlightedClauseId = ref(null)

// UI 状态
const expandedChapters = ref({})
const expandedClauseId = ref(null)
const allExpanded = ref(false)
const logDrawerOpen = ref(true)
const realtimeLogs = ref([])
const showStartButton = ref(false)
const starting = ref(false)
const reAnnotating = ref(false)
const waitingForReAnnotate = ref(false)
const progressPercent = ref(0)
const hasAutoExpanded = ref(false)
let pollInterval = null
let taskSource = null
const logScrollEl = ref(null)

// 实体编辑
const editingClauseId = ref(null)
const editingField = ref('')
const editingValue = ref('')
const editingTerms = ref([])
const expandedTermDefs = ref(new Set())  // 展开的术语定义
const editingConditions = ref([])
const editingActions = ref([])

// MinerU 解析状态
const mineruMode = ref(false)
const mineruChunks = ref([])
const mineruSelectedChunk = ref(null)
const mineruSummary = ref(null)
const mineruchunkListRef = ref(null)

// 分析数据
const analysisData = ref(null)

// ============================================================================
// 计算属性
// ============================================================================

const statusLabel = computed(() => {
  const map = {
    'created': '待上传',
    'ontology_generation': '生成中',
    'ontology_generated': '已生成',
    'graph_chunking': '分析中',
    'graph_chunked': '已完成',
    'graph_building': '建图中',
    'graph_completed': '已完成',
    'failed': '失败'
  }
  return map[analysisStatus.value] || analysisStatus.value || '未知'
})

const statusDotColor = computed(() => {
  const colorMap = {
    'graph_chunking': '#FF5722',
    'graph_chunked': '#4CAF50',
    'graph_completed': '#4CAF50',
    'graph_building': '#FF5722',
    'failed': '#F44336',
    'ontology_generation': '#FF5722',
    'ontology_generated': '#4CAF50',
    'created': '#CCC'
  }
  return colorMap[analysisStatus.value] || '#CCC'
})

// 页码 -> 标注列表 的缓存（两种模式互斥）
const pageAnnotationsCache = computed(() => {
  const cache = {}
  if (mineruMode.value) {
    // MinerU 模式：使用 chunks.json 的 bbox_viewport
    if (mineruChunks.value.length) {
      for (const c of mineruChunks.value) {
        const bbox = c.bbox_viewport || c.bbox_pdf
        if (!bbox || bbox.length < 4) continue
        const pageNum = (c.page_idx || 0) + 1
        if (!cache[pageNum]) cache[pageNum] = []
        cache[pageNum].push({
          clauseId: c.chunk_id,
          page: pageNum,
          bbox,
          type: c.type || 'text',
          categoryId: c.category_id || 1,
          isMineru: true
        })
      }
    }
  } else {
    // 智能分析模式：使用 intelligent_chunks.json 的 clause bbox
    for (const ann of allAnnotations.value) {
      if (!cache[ann.page]) cache[ann.page] = []
      cache[ann.page].push(ann)
    }
  }
  return cache
})

function getPageAnnotations(pageNum) {
  return pageAnnotationsCache.value[pageNum] || []
}

// 设置 canvas ref（用于渲染时获取 canvas 元素）
function setCanvasRef(el, pageNum) {
  if (el) {
    pageCanvasMap.value[pageNum] = el
  }
}

// ============================================================================
// 生命周期
// ============================================================================

onMounted(async () => {
  await initPdfJs()

  if (props.projectId === 'new') {
    await handleNewProject()
  } else {
    currentProjectId.value = props.projectId
    await loadExistingProject()
  }
})

watch(() => props.projectId, async (newId) => {
  if (newId && newId !== 'new' && newId !== currentProjectId.value) {
    currentProjectId.value = newId
    resetState()
    await loadExistingProject()
  }
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
  if (taskSource) taskSource.close()
})

function resetState() {
  hasAutoExpanded.value = false
  analysisData.value = null
  allAnnotations.value = []
  expandedChapters.value = {}
  expandedClauseId.value = null
  highlightedClauseId.value = null
  pdfUrl.value = ''
  pdfFileName.value = ''
  pdfDoc.value = null
  pdfDocUrl.value = ''
  totalPages.value = 0
  renderedPages.value = []
  mineruMode.value = false
  mineruChunks.value = []
  mineruSummary.value = null
  mineruSelectedChunk.value = null
}

// ============================================================================
// 新建项目流程
// ============================================================================

async function handleNewProject() {
  const pending = getPendingUpload()
  if (!pending.files.length) {
    router.push('/')
    return
  }

  try {
    realtimeLogs.value.push('开始上传文档...')

    const formData = new FormData()
    pending.files.forEach(f => formData.append('files', f))
    formData.append('simulation_requirement', pending.simulationRequirement)

    const res = await generateOntology(formData)
    if (res.success) {
      clearPendingUpload()
      currentProjectId.value = res.data.project_id
      projectName.value = res.data.name || res.data.project_id
      taskId.value = res.data.task_id

      router.replace({ name: 'ChunkAnalysis', params: { projectId: currentProjectId.value } })
      realtimeLogs.value.push(`项目创建成功: ${currentProjectId.value}`)

      // 加载项目（会触发 MinerU 自动解析）
      await loadExistingProject()
    } else {
      realtimeLogs.value.push(`❌ 上传失败: ${res.error}`)
    }
  } catch (err) {
    realtimeLogs.value.push(`❌ 异常: ${err.message}`)
  }
}

async function loadExistingProject() {
  try {
    const res = await getProject(currentProjectId.value)
    if (!res.success) {
      realtimeLogs.value.push(`❌ 加载项目失败: ${res.error}`)
      return
    }

    projectName.value = res.data.name || res.data.project_id
    analysisStatus.value = res.data.status
    taskId.value = res.data.graph_build_task_id

    // 查找 PDF 文件
    const files = res.data.files || []
    for (const f of files) {
      const fname = f.saved_filename || f.filename || ''
      if (fname.toLowerCase().endsWith('.pdf')) {
        pdfFileName.value = fname
        break
      }
    }

    // 并行加载 PDF 和分析数据（MinerU + 智能分析）
    const tasks = []
    if (pdfFileName.value) {
      tasks.push(loadPdf())
    }
    tasks.push(loadMineruResults())

    if (res.data.status === 'graph_chunked' || res.data.status === 'graph_completed') {
      tasks.push(loadAnalysis())
    } else if (res.data.status === 'graph_chunking') {
      tasks.push(loadAnalysis())
      startTaskSSE()
      startProgressPolling()
    } else if (res.data.status === 'ontology_generated' || res.data.status === 'created') {
      // ontology_generation 状态下也可能 MinerU 正在解析，需要建立 SSE 接收日志
      if (res.data.ontology_task_id) {
        taskId.value = res.data.ontology_task_id
        startTaskSSE()
      }
      showStartButton.value = true
    }

    await Promise.allSettled(tasks)
  } catch (err) {
    realtimeLogs.value.push(`❌ 加载项目失败: ${err.message}`)
  }
}

// ============================================================================
// MinerU 解析
// ============================================================================

async function loadMineruResults() {
  if (!currentProjectId.value) return
  try {
    const res = await getMineruChunks(currentProjectId.value)
    if (res && Array.isArray(res.data?.chunks)) {
      mineruChunks.value = res.data.chunks
      mineruSummary.value = res.data.summary
      mineruMode.value = mineruChunks.value.length > 0
      realtimeLogs.value.push(`✅ MinerU 解析结果已加载: ${mineruChunks.value.length} 个布局块`)
    }
  } catch (e) {
    // 静默忽略
  }
}

function toggleMineruMode() {
  mineruMode.value = !mineruMode.value
  mineruSelectedChunk.value = null
}

function onMineruBboxClick(ann) {
  highlightedClauseId.value = ann.clauseId
  const chunk = mineruChunks.value.find(c => c.chunk_id === ann.clauseId)
  mineruSelectedChunk.value = chunk
  if (chunk) {
    scrollChunkListToItem(chunk.chunk_id)
  }
}

function onMineruChunkClick(chunk) {
  if (mineruSelectedChunk.value?.chunk_id === chunk.chunk_id) {
    mineruSelectedChunk.value = null
    highlightedClauseId.value = null
    return
  }
  mineruSelectedChunk.value = chunk
  highlightedClauseId.value = chunk.chunk_id
  // 滚动 PDF 到对应页面
  const pageNum = (chunk.page_idx || 0) + 1
  scrollToPage(pageNum)
}

function scrollChunkListToItem(chunkId) {
  nextTick(() => {
    const el = mineruchunkListRef.value?.querySelector(`[data-chunk-id="${chunkId}"]`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  })
}

function categoryIdLabel(catId) {
  const labels = { 0: '标题', 1: '正文', 2: '表格', 3: '图片', 4: '表格', 5: '图片', 6: '公式' }
  return labels[catId] || '正文'
}

function categoryIdColor(catId) {
  const colors = { 0: '#ea580c', 1: '#2563eb', 2: '#16a34a', 3: '#9333ea', 4: '#16a34a', 5: '#9333ea', 6: '#ca8a04' }
  return colors[catId] || '#2563eb'
}

// ============================================================================
// PDF 渲染
// ============================================================================

async function initPdfJs() {
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

async function loadPdf() {
  if (!pdfFileName.value || !pdfjsLib.value) return
  pdfLoading.value = true
  renderedPages.value = []
  pageCanvasMap.value = {}
  try {
    const stableUrl = `${window.location.origin}/api/graph/project/${currentProjectId.value}/document/${encodeURIComponent(pdfFileName.value)}`
    pdfUrl.value = stableUrl + `?t=${Date.now()}`

    await nextTick()

    // 加载 PDF document
    let pdf
    if (pdfDoc.value && pdfDocUrl.value === stableUrl) {
      pdf = pdfDoc.value
    } else {
      const loadingTask = pdfjsLib.value.getDocument(stableUrl)
      pdf = await loadingTask.promise
      pdfDoc.value = pdf
      pdfDocUrl.value = stableUrl
      totalPages.value = pdf.numPages
    }

    // 渲染所有页面
    await renderAllPages(pdf)
  } catch (err) {
    console.error('loadPdf error:', err)
  } finally {
    pdfLoading.value = false
  }
}

async function renderAllPages(pdf) {
  const containerWidth = viewerContainer.value?.clientWidth || 600

  // 并行获取所有页面元数据（getPage 内部解析内容，较耗时）
  const pageMetas = await Promise.all(
    Array.from({ length: pdf.numPages }, async (_, i) => {
      const page = await pdf.getPage(i + 1)
      const unscaledViewport = page.getViewport({ scale: 1 })
      const scale = (containerWidth - 20) / unscaledViewport.width
      const viewport = page.getViewport({ scale })
      return {
        pageNum: i + 1,
        page,
        pageWidth: unscaledViewport.width,
        pageHeight: unscaledViewport.height,
        canvasWidth: viewport.width,
        canvasHeight: viewport.height,
        viewport
      }
    })
  )

  renderedPages.value = pageMetas

  // 等 canvas ref 绑定后再渲染
  await nextTick()

  // 串行渲染（PDF.js 渲染本身是 GPU 操作，并行反而可能冲突）
  for (const rp of pageMetas) {
    const canvas = pageCanvasMap.value[rp.pageNum]
    if (!canvas) continue
    canvas.height = rp.canvasHeight
    canvas.width = rp.canvasWidth
    const context = canvas.getContext('2d')
    await rp.page.render({ canvasContext: context, viewport: rp.viewport }).promise
  }
}

// ============================================================================
// 智能分析控制
// ============================================================================

async function handleStartChunking() {
  if (starting.value) return
  starting.value = true
  showStartButton.value = false
  realtimeLogs.value.push('🚀 启动智能Chunks标注分析...')

  try {
    const res = await startChunking({ project_id: currentProjectId.value, reset: false })
    if (res.success) {
      taskId.value = res.data.task_id
      analysisStatus.value = 'graph_chunking'
      realtimeLogs.value.push(`任务已启动: ${res.data.message}`)
      startTaskSSE()
      startProgressPolling()
    } else {
      realtimeLogs.value.push(`❌ 启动失败: ${res.error}`)
      showStartButton.value = true
    }
  } catch (err) {
    realtimeLogs.value.push(`❌ 异常: ${err.message}`)
    showStartButton.value = true
  } finally {
    starting.value = false
  }
}

async function handleReAnnotate() {
  if (reAnnotating.value) return
  if (!confirm('确认重新标注？将从 mineru_parsed.json 重新生成 chunks.json。')) return

  reAnnotating.value = true
  realtimeLogs.value.push('🔄 开始重新标注...')

  try {
    const res = await reAnnotateMineru(currentProjectId.value)
    if (res.success) {
      taskId.value = res.data.task_id
      waitingForReAnnotate.value = true
      startTaskSSE()
    } else {
      realtimeLogs.value.push(`❌ 重新标注失败: ${res.error}`)
      reAnnotating.value = false
    }
  } catch (err) {
    realtimeLogs.value.push(`❌ 异常: ${err.message}`)
    reAnnotating.value = false
  }
}

async function handleResetChunking() {
  if (starting.value) return
  if (!confirm('确认重置？所有已有的分析数据将被清除。')) return

  starting.value = true
  hasAutoExpanded.value = false
  realtimeLogs.value.push('🔄 重置并重新分析...')

  try {
    const res = await startChunking({ project_id: currentProjectId.value, reset: true })
    if (res.success) {
      taskId.value = res.data.task_id
      analysisStatus.value = 'graph_chunking'
      analysisData.value = null
      allAnnotations.value = []
      expandedChapters.value = {}
      expandedClauseId.value = null
      highlightedClauseId.value = null
      startTaskSSE()
      startProgressPolling()
    } else {
      realtimeLogs.value.push(`❌ 重置失败: ${res.error}`)
    }
  } catch (err) {
    realtimeLogs.value.push(`❌ 异常: ${err.message}`)
  } finally {
    starting.value = false
  }
}

let pollCount = 0

// ============================================================================
// SSE 实时日志流
// ============================================================================

function startTaskSSE() {
  if (!taskId.value) return
  if (taskSource) taskSource.close()

  const url = getTaskEventsURL(taskId.value)
  taskSource = new EventSource(url)

  taskSource.onmessage = async (event) => {
    try {
      const { type: msgType, data } = JSON.parse(event.data)

      if (msgType === 'init') {
        // 初始化：加载已有日志
        if (data.logs?.length) {
          data.logs.forEach(l => {
            if (!realtimeLogs.value.find(existing => existing === l.message)) {
              realtimeLogs.value.push(l.message)
            }
          })
        }
        return
      }

      if (msgType === 'update') {
        const payload = data
        if (payload.new_logs?.length) {
          payload.new_logs.forEach(l => {
            if (!realtimeLogs.value.find(existing => existing === l.message)) {
              realtimeLogs.value.push(l.message)
            }
          })
        }
        if (payload.status === 'completed' || payload.status === 'failed') {
          taskSource.close()
          taskSource = null
          // re-annotate 任务完成时刷新 MinerU 结果
          if (waitingForReAnnotate.value) {
            waitingForReAnnotate.value = false
            reAnnotating.value = false
            if (payload.status === 'completed') {
              await loadMineruResults()
              realtimeLogs.value.push(`✅ 重新标注完成`)
            } else {
              realtimeLogs.value.push(`❌ 重新标注失败: ${payload.error || '未知错误'}`)
            }
          }
        }
      }
    } catch (err) {
      console.error('SSE 解析错误:', err)
    }
  }

  taskSource.onerror = () => {
    taskSource.close()
    taskSource = null
  }
}

// ============================================================================
// 进度轮询
// ============================================================================

function startProgressPolling() {
  if (pollInterval) clearInterval(pollInterval)
  pollInterval = setInterval(async () => {
    try {
      // 优先使用 getChunkProgress 的章节级精度进度（智能分块阶段）
      const progRes = await getChunkProgress(currentProjectId.value)
      if (progRes.success && progRes.data) {
        const prog = progRes.data
        progressPercent.value = Math.round((prog.progress_ratio || 0) * 100)
        // if (prog.completed_clauses_count !== undefined) {
        //   realtimeLogs.value.push(`📊 已完成 ${prog.completed_clauses_count} 条文`)
        // }
      }

      // getTaskStatus 仅用于状态判断（completed/failed），不覆盖进度值
      if (taskId.value) {
        const res = await getTaskStatus(taskId.value)
        if (res.success) {
          const task = res.data
          if (task.status === 'completed') {
            analysisStatus.value = 'graph_chunked'
            clearInterval(pollInterval)
            realtimeLogs.value.push('✅ 分析完成！')
            await loadAnalysis()
          } else if (task.status === 'failed') {
            analysisStatus.value = 'failed'
            clearInterval(pollInterval)
            realtimeLogs.value.push(`❌ 分析失败: ${task.error}`)
          }
        }
      }

      pollCount++
      if (pollCount % 2 === 0) {
        try { await loadAnalysis() } catch (e) { /* 忽略 */ }
      }
    } catch (err) {
      // 忽略轮询错误
    }
  }, 3000)
}

// ============================================================================
// 加载分析数据
// ============================================================================

async function loadAnalysis() {
  try {
    const res = await getChunkAnalysis(currentProjectId.value)
    if (res.success) {
      analysisData.value = res.data
      analysisStatus.value = res.data.status || 'graph_chunked'

      if (!pdfFileName.value && res.data.pdf_file) {
        pdfFileName.value = res.data.pdf_file
        await loadPdf()
      }

      buildAnnotations()

      if (!hasAutoExpanded.value && analysisData.value.chapter_tree?.length) {
        hasAutoExpanded.value = true
        const firstChapter = analysisData.value.chapter_tree[0].chapter.chapter_number
        expandedChapters.value[firstChapter] = true
      }

      if (res.data.status === 'graph_chunked') {
        realtimeLogs.value.push(`✅ 分析完成: ${analysisData.value.summary.total_clauses} 条文`)
      }
    }
  } catch (err) {
    // 404 或无数据时静默忽略
  }
}

function buildAnnotations() {
  const anns = []
  for (const c of analysisData.value?.clauses || []) {
    // 优先使用 bboxs（聚合多 bbox），其次使用单 bbox，最后回退到 pdf_location
    const bboxsList = c.bboxs || []
    const loc = c.pdf_location

    if (bboxsList.length > 0) {
      // 使用聚合的 bboxs [[page, x0,y0,x1,y1], ...]
      for (const item of bboxsList) {
        if (item.length >= 5) {
          anns.push({
            clauseId: c.clause_id,
            page: item[0],
            bbox: item.slice(1),
            type: 'clause'
          })
        }
      }
    } else {
      // 回退到单 bbox
      const page = loc?.page ?? c.page ?? ((c.page_idx != null) ? c.page_idx + 1 : null)
      const bbox = loc?.bbox ?? c.bbox
      if (page && bbox && bbox.length >= 4) {
        anns.push({
          clauseId: c.clause_id,
          page,
          bbox,
          type: 'clause'
        })
      }
    }
  }
  allAnnotations.value = anns
}

// ============================================================================
// 交互
// ============================================================================

function toggleChapter(chapterNum) {
  expandedChapters.value[chapterNum] = !expandedChapters.value[chapterNum]
}

function toggleAllChapters() {
  if (allExpanded.value) {
    expandedChapters.value = {}
  } else {
    const all = {}
    analysisData.value?.chapter_tree?.forEach(ch => {
      all[ch.chapter.chapter_number] = true
    })
    expandedChapters.value = all
  }
  allExpanded.value = !allExpanded.value
}

async function handleClauseClick(clause) {
  if (expandedClauseId.value === clause.clause_id) {
    expandedClauseId.value = null
    highlightedClauseId.value = null
    return
  }
  expandedClauseId.value = clause.clause_id
  highlightedClauseId.value = clause.clause_id

  // 优先使用 bboxs（多 bbox），回退到单 bbox 或 pdf_location
  const bboxs = clause.bboxs || []
  if (bboxs.length > 0) {
    scrollToPage(bboxs[0][0])
  } else {
    const loc = clause.pdf_location
    const page = loc?.page ?? clause.page ?? ((clause.page_idx != null) ? clause.page_idx + 1 : null)
    if (page) scrollToPage(page)
  }
}

function onBboxClick(ann) {
  highlightedClauseId.value = ann.clauseId
  const clause = analysisData.value?.clauses?.find(c => c.clause_id === ann.clauseId)
  if (clause) {
    expandedClauseId.value = clause.clause_id
  }
}

function scrollToPage(pageNum) {
  const wrapper = viewerContainer.value
  if (!wrapper) return
  const rp = renderedPages.value.find(p => p.pageNum === pageNum)
  if (!rp) return
  const pageEl = wrapper.querySelector(`[data-page="${pageNum}"]`)
  if (pageEl) {
    pageEl.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

function handleEntityClick(entity, clauseId) {
  highlightedClauseId.value = clauseId
  const clause = analysisData.value?.clauses?.find(c => c.clause_id === clauseId)
  if (clause) {
    expandedClauseId.value = clauseId
  }
}

// ============================================================================
// 实体编辑
// ============================================================================

function startEditEntity(clause, field) {
  if (editingClauseId.value === clause.clause_id) {
    editingClauseId.value = null
    editingField.value = ''
    editingValue.value = ''
    return
  }

  editingClauseId.value = clause.clause_id
  editingField.value = field

  if (field === 'terms') {
    editingTerms.value = clause.terms || []
    editingValue.value = (clause.terms || []).map(t => typeof t === 'string' ? t : t.term_name).join('，')
  } else if (field === 'conditions') {
    editingConditions.value = clause.conditions || []
    editingValue.value = (clause.conditions || []).join('，')
  } else if (field === 'actions') {
    editingActions.value = clause.actions || []
    editingValue.value = (clause.actions || []).join('，')
  }
}

async function saveEntity(clauseId, field) {
  const value = editingValue.value.split('，').map(v => v.trim()).filter(v => v)
  try {
    const res = await updateClauseEntity(currentProjectId.value, { clause_id: clauseId, [field]: value })
    if (res.success) {
      const clause = analysisData.value?.clauses?.find(c => c.clause_id === clauseId)
      // 使用 API 返回的完整数据（含 definition 等完整字段）
      if (clause && res.data) {
        clause[field] = res.data[field]
      }
      editingClauseId.value = null
      editingField.value = ''
      editingValue.value = ''
      realtimeLogs.value.push(`✅ ${clauseId} ${field} 已更新`)
    } else {
      realtimeLogs.value.push(`❌ 更新失败: ${res.error}`)
    }
  } catch (err) {
    realtimeLogs.value.push(`❌ 保存失败: ${err.message}`)
  }
}

function toggleTermDef(termName) {
  if (expandedTermDefs.value.has(termName)) {
    expandedTermDefs.value.delete(termName)
  } else {
    expandedTermDefs.value.add(termName)
  }
  // 触发响应式更新
  expandedTermDefs.value = new Set(expandedTermDefs.value)
}

// ============================================================================
// 日志自动滚动
// ============================================================================

watch(() => realtimeLogs.value.length, () => {
  nextTick(() => {
    if (logScrollEl.value) {
      logScrollEl.value.scrollTop = logScrollEl.value.scrollHeight
    }
  })
})

// ============================================================================
// 日志 & 导航
// ============================================================================

function logClass(msg) {
  if (msg.startsWith('❌')) return 'log-error'
  if (msg.startsWith('✅')) return 'log-success'
  if (msg.startsWith('📊')) return 'log-info'
  return 'log-normal'
}

function goToGraphBuild() {
  router.push({ name: 'GraphBuild', params: { projectId: currentProjectId.value } })
}
</script>

<style scoped>
.chunk-analysis-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #f8f9fa;
  color: #1a1a2e;
  overflow: hidden;
}

/* Header */
.header-center {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
}
.brand {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 18px;
  letter-spacing: 1px;
  cursor: pointer;
}
.view-switcher {
  display: flex;
  background: #F5F5F5;
  padding: 4px;
  border-radius: 6px;
  gap: 4px;
}
.switch-btn {
  border: none;
  background: transparent;
  padding: 6px 16px;
  font-size: 12px;
  font-weight: 600;
  color: #666;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}
.switch-btn.active {
  background: #FFF;
  color: #000;
  box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  justify-content: flex-end;
}
.step-divider {
  width: 1px;
  height: 14px;
  background-color: #E0E0E0;
}
.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #666;
  font-weight: 500;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #CCC;
}
.status-indicator .dot { }

header.ca-header {
  height: 60px;
  border-bottom: 1px solid #EAEAEA;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: #FFF;
  z-index: 100;
  position: relative;
  flex-shrink: 0;
}
.progress-wrapper { display: flex; align-items: center; gap: 8px; }
.progress-bar { width: 120px; height: 6px; background: #e5e7eb; border-radius: 3px; overflow: hidden; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #2563eb, #3b82f6); border-radius: 3px; transition: width 0.5s ease; }
.progress-text { font-size: 12px; color: #6b7280; min-width: 36px; }

.goto-build-btn { background: #2563eb; color: white; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.goto-build-btn:hover { background: #1d4ed8; }

/* Main Layout */
.ca-main { display: flex; flex: 1; overflow: hidden; }

/* LEFT: PDF Panel */
.pdf-panel {
  width: 48%;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #e0e0e0;
  background: #ffffff;
}
.pdf-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #f9fafb;
  border-bottom: 1px solid #e0e0e0;
  flex-shrink: 0;
}
.pdf-toolbar-title { font-size: 13px; color: #6b7280; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
.pdf-placeholder { color: #9ca3af; }
.pdf-toolbar-actions { display: flex; align-items: center; gap: 6px; }
.pdf-page-count { font-size: 12px; color: #6b7280; min-width: 40px; text-align: right; }

.pdf-body {
  flex: 1;
  overflow-y: auto;
  position: relative;
  padding: 10px;
}
.pdf-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 12px; color: #9ca3af; }
.pdf-empty-icon { font-size: 48px; opacity: 0.4; }
.pdf-empty p { margin: 0; font-size: 14px; }
.pdf-empty-hint { font-size: 12px; color: #9ca3af; }

.pdf-scroll-container { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.pdf-page-wrapper { position: relative; display: inline-block; }
.pdf-canvas { display: block; max-width: 100%; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }

.bbox-overlay { position: absolute; pointer-events: auto; overflow: visible; }
.bbox-rect { fill: transparent; stroke-width: 2; cursor: pointer; transition: all 0.2s; }
.bbox-clause { stroke: #ea580c; fill: transparent; }
.bbox-element { stroke: #2563eb; fill: transparent; }
.bbox-term { stroke: #16a34a; fill: transparent; }
.bbox-active { stroke-width: 2.5; stroke-dasharray: 6 3; fill: transparent; filter: drop-shadow(0 0 6px #eab308); }
.bbox-mineru { stroke-width: 1.5; fill-opacity: 0.15; }
.bbox-mineru:hover { fill-opacity: 0.35; stroke-width: 2.5; }

.pdf-loading {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  display: flex; flex-direction: column; align-items: center; gap: 8px; color: #6b7280;
}

.bbox-legend { display: flex; gap: 16px; padding: 6px 12px; background: #f9fafb; border-top: 1px solid #e0e0e0; font-size: 11px; }
.legend-item { display: flex; align-items: center; gap: 4px; color: #6b7280; }
.legend-dot { width: 10px; height: 10px; border-radius: 2px; border: 2px solid; }
.clause-dot { border-color: #ea580c; background: #fff7ed; }
.element-dot { border-color: #2563eb; background: #eff6ff; }
.term-dot { border-color: #16a34a; background: #f0fdf4; }
.legend-count { font-size: 10px; color: #9ca3af; margin-left: 8px; }

/* RIGHT: Analysis Panel */
.analysis-panel {
  width: 52%;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  background: #f8f9fa;
}

.analysis-content {
  display: flex;
  flex-direction: column;
  flex: 1;
}

/* 智能分析面板表头 */
.analysis-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: linear-gradient(135deg, #0d9488 0%, #0369a1 100%);
  color: white;
  border-bottom: 1px solid rgba(255,255,255,0.1);
}
.analysis-panel-title { display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 600; }
.analysis-summary-badges { display: flex; gap: 6px; }
.re-analyse-btn { background: rgba(255,255,255,0.2); border: none; color: white; padding: 4px 12px; border-radius: 12px; cursor: pointer; font-size: 12px; font-weight: 500; }
.re-analyse-btn:hover { background: rgba(255,255,255,0.35); }
.re-analyse-btn:disabled { background: rgba(255,255,255,0.1); cursor: not-allowed; }

/* MinerU 面板 */
.mineru-panel {
  border-bottom: 1px solid #e0e0e0;
  flex-shrink: 0;
}
.mineru-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}
.mineru-panel-title { display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 600; }
.mineru-summary-badges { display: flex; gap: 6px; }
.badge { padding: 2px 8px; border-radius: 10px; font-size: 10px; background: rgba(255,255,255,0.25); font-weight: 500; }
.close-mineru-btn { background: rgba(255,255,255,0.2); border: none; color: white; width: 22px; height: 22px; border-radius: 50%; cursor: pointer; font-size: 16px; line-height: 1; display: flex; align-items: center; justify-content: center; }
.close-mineru-btn:hover { background: rgba(255,255,255,0.35); }
.re-annotate-btn { background: #4f46e5; border: none; color: white; padding: 4px 12px; border-radius: 12px; cursor: pointer; font-size: 12px; font-weight: 500; }
.re-annotate-btn:hover { background: #4338ca; }
.re-annotate-btn:disabled { background: #a5b4fc; cursor: not-allowed; }

.mineru-layout-info { padding: 10px 16px; background: #f9fafb; }
.layout-info-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 12px; }
.info-label { color: #6b7280; min-width: 60px; }
.info-value { color: #1a1a2e; font-weight: 600; }

/* Chunks 列表 */
.mineru-chunk-list { max-height: 240px; overflow-y: auto; border-top: 1px solid #e0e0e0; }
.mineru-chunk-item {
  padding: 8px 16px;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  transition: background 0.15s;
}
.mineru-chunk-item:hover { background: #f5f5ff; }
.chunk-active { background: #eff6ff; border-left: 3px solid #2563eb; }
.chunk-item-header { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.chunk-type-badge { padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; }
.chunk-block-type { padding: 1px 6px; border-radius: 4px; font-size: 10px; color: #6b7280; background: #f3f4f6; }
.chunk-page { font-size: 10px; color: #9ca3af; }
.chunk-id { font-size: 9px; color: #d1d5db; font-family: monospace; margin-left: auto; }
.chunk-item-content { font-size: 11px; color: #6b7280; line-height: 1.4; white-space: pre-wrap; word-break: break-all; }

/* 选中块详情 */
.mineru-chunk-detail { border-top: 1px solid #e0e0e0; padding: 10px 16px; background: #fafafa; overflow-y: auto; }
.detail-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.detail-type { padding: 2px 8px; background: #667eea22; color: #667eea; border-radius: 8px; font-size: 11px; font-weight: 600; }
.detail-block-type { padding: 2px 8px; background: #f3f4f6; color: #6b7280; border-radius: 8px; font-size: 11px; }
.detail-page { font-size: 11px; color: #9ca3af; }
.detail-content { font-size: 11px; color: #374151; line-height: 1.5; margin-bottom: 8px; white-space: pre-wrap; word-break: break-all; }
.detail-table-content { margin: 8px 0; }
.detail-table-label { font-size: 10px; color: #9ca3af; margin-bottom: 4px; font-weight: 500; }
.detail-table-markdown { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 10px; font-size: 11px; line-height: 1.6; color: #374151; overflow-x: auto; }
.detail-table-markdown table { width: auto; max-width: 100%; border-collapse: collapse; font-size: 11px; }
.detail-table-markdown td, .detail-table-markdown th { border: 1px solid #d1d5db; padding: 4px 8px; text-align: left; vertical-align: top; }
.detail-table-markdown th { background: #f3f4f6; font-weight: 600; }
.detail-table-markdown tr:nth-child(even) td { background: #f9fafb; }
.detail-table-markdown tr:hover td { background: #eff6ff; }
.detail-table-caption { font-size: 10px; color: #6b7280; margin-top: 6px; }
.detail-table-caption-label { color: #9ca3af; }
.detail-table-footnote { margin-top: 8px; }
.detail-table-footnote-text { font-size: 11px; color: #374151; background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; padding: 8px 10px; line-height: 1.6; white-space: pre-wrap; }
.detail-bbox { display: flex; align-items: center; gap: 6px; font-size: 10px; }
.detail-bbox-label { color: #9ca3af; }
.detail-bbox-val { color: #6b7280; font-family: monospace; }

/* 章节树 */
.chapter-tree { border-bottom: 1px solid #e0e0e0; flex-shrink: 0; }
.tree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: #ffffff;
  font-size: 13px;
  font-weight: 600;
  color: #1a1a2e;
  border-bottom: 1px solid #e0e0e0;
}
.tree-actions { display: flex; align-items: center; gap: 6px; }
.expand-all-btn { background: none; border: 1px solid #d0d7de; color: #6b7280; padding: 2px 8px; border-radius: 4px; cursor: pointer; font-size: 11px; }
.expand-all-btn:hover { background: #f0f0f0; color: #1a1a2e; }
.reset-chunks-btn { background: none; border: 1px solid #d97706; color: #d97706; padding: 2px 8px; border-radius: 4px; cursor: pointer; font-size: 11px; }
.reset-chunks-btn:hover:not(:disabled) { background: #fff7ed; }
.reset-chunks-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.tree-body { overflow-y: auto; }

.chapter-item { border-bottom: 1px solid #f0f0f0; }
.chapter-header { display: flex; align-items: center; gap: 8px; padding: 8px 16px; cursor: pointer; font-size: 13px; color: #1a1a2e; }
.chapter-header:hover { background: #f0f7ff; }
.chapter-toggle { color: #9ca3af; font-size: 10px; }
.chapter-title { font-weight: 600; }
.chapter-meta { color: #9ca3af; font-size: 11px; margin-left: auto; }

.clause-list { padding: 4px 0; }
.clause-item { padding: 4px 16px 4px 32px; cursor: pointer; border-left: 2px solid transparent; }
.clause-item:hover { background: #f9fafb; }
.clause-active { border-left-color: #2563eb; background: #eff6ff; }

.clause-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; }
.clause-id { font-size: 11px; color: #2563eb; font-family: monospace; min-width: 40px; }
.clause-title { font-size: 12px; color: #6b7280; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }

.clause-detail { margin: 4px 0 6px; padding: 6px 0; border-top: 1px solid #f0f0f0; }
.entity-row { display: flex; align-items: center; gap: 6px; margin: 3px 0; min-height: 22px; }
.entity-label { font-size: 11px; font-weight: 600; min-width: 48px; }
.term-label { color: #16a34a; }
.cond-label { color: #ca8a04; }
.action-label { color: #2563eb; }
.comp-label { color: #9333ea; }

.entity-tags { display: flex; flex-wrap: wrap; gap: 4px; flex: 1; align-items: center; }
.entity-tag { padding: 2px 7px; border-radius: 10px; font-size: 11px; cursor: pointer; transition: all 0.15s; }
.term-tag { background: #dcfce7; color: #16a34a; border: 1px solid #bbf7d0; }
.term-tag:hover { background: #bbf7d0; }
.term-tag.active { background: #16a34a; color: #fff; }
.cond-tag { background: #fef9c3; color: #ca8a04; border: 1px solid #fde68a; }
.cond-tag:hover { background: #fde68a; }
.action-tag { background: #dbeafe; color: #2563eb; border: 1px solid #bfdbfe; }
.action-tag:hover { background: #bfdbfe; }
.comp-tag { background: #f3e8ff; color: #9333ea; border: 1px solid #e9d5ff; }
.comp-tag:hover { background: #e9d5ff; }

.term-item { display: flex; flex-direction: column; gap: 2px; }
.term-def-arrow { font-size: 8px; color: #16a34a; text-align: center; line-height: 1; margin-top: -2px; }
.term-definition { font-size: 11px; color: #374151; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 4px 8px; margin-top: 2px; line-height: 1.4; }
.def-connector { color: #16a34a; font-weight: 600; margin-right: 4px; }

.edit-btn { background: none; border: 1px solid #d0d7de; color: #6b7280; padding: 1px 6px; border-radius: 4px; cursor: pointer; font-size: 10px; flex-shrink: 0; }
.edit-btn:hover { background: #f0f0f0; color: #1a1a2e; }
.entity-editor { display: flex; gap: 6px; margin: 4px 0; }
.entity-input { flex: 1; background: #ffffff; border: 1px solid #d0d7de; color: #1a1a2e; padding: 3px 8px; border-radius: 4px; font-size: 12px; }
.entity-input:focus { outline: none; border-color: #2563eb; }
.save-btn { background: #2563eb; border: none; color: white; padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 11px; }
.save-btn:hover { background: #1d4ed8; }

.triplets-section { margin: 6px 0; }
.triplet-label { font-size: 11px; color: #6b7280; margin-bottom: 4px; }
.triplet-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; font-size: 11px; flex-wrap: wrap; }
.triplet-comp { color: #9333ea; }
.triplet-arrow { color: #2563eb; }
.triplet-obj { color: #16a34a; }
.triplet-cond { color: #ca8a04; font-size: 10px; }

.clause-entities-section { margin: 6px 0; }
.clause-topic-row { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 6px; padding: 6px 8px; background: #f0f9ff; border: 1px solid #e0f2fe; border-radius: 6px; }
.topic-label { font-size: 11px; font-weight: 600; color: #0369a1; flex-shrink: 0; }
.topic-content { font-size: 12px; color: #1e40af; line-height: 1.4; }
.entity-section-label { font-size: 11px; color: #6b7280; margin-bottom: 4px; }
.entity-item-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; font-size: 11px; }
.entity-type-tag { padding: 1px 6px; border-radius: 4px; font-size: 10px; background: #e5e7eb; color: #6b7280; font-weight: 600; }
.entity-key { color: #1a1a2e; font-weight: 500; }
.entity-value { color: #2563eb; }
.entity-unit { color: #9ca3af; font-size: 10px; }

/* 日志抽屉 */
.log-drawer { background: #ffffff; border-top: 1px solid #e0e0e0; flex-shrink: 0; }
.log-drawer-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 16px; cursor: pointer; font-size: 12px; color: #6b7280; }
.log-drawer-header:hover { color: #1a1a2e; }
.log-toggle { font-size: 10px; }
.log-drawer-body { max-height: 100px; overflow-y: auto; padding: 0 16px 8px; font-family: 'Consolas', 'Monaco', monospace; font-size: 11px; background: #f9fafb; }
.log-line { padding: 1px 0; color: #6b7280; }
.log-error { color: #dc2626; }
.log-success { color: #16a34a; }
.log-info { color: #2563eb; }
.log-empty { color: #9ca3af; font-size: 11px; }

/* 开始分析弹窗 */
.start-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center;
  z-index: 100;
}
.start-card {
  background: #ffffff; border: 1px solid #e0e0e0; border-radius: 12px;
  padding: 32px 40px; text-align: center; max-width: 440px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
}
.start-icon { font-size: 48px; margin-bottom: 12px; }
.start-card h3 { font-size: 18px; margin: 0 0 12px; color: #1a1a2e; }
.start-card p { font-size: 14px; color: #6b7280; margin: 0 0 24px; line-height: 1.6; }
.start-actions { display: flex; gap: 12px; justify-content: center; }
.start-btn { background: #2563eb; color: white; border: none; padding: 10px 24px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; }
.start-btn:hover:not(:disabled) { background: #1d4ed8; }
.start-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.reset-btn { background: none; border: 1px solid #d0d7de; color: #6b7280; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 14px; }
.reset-btn:hover:not(:disabled) { background: #f0f0f0; color: #1a1a2e; }

/* Spinner */
.spinner { width: 24px; height: 24px; border: 2px solid #e5e7eb; border-top-color: #2563eb; border-radius: 50%; animation: spin 0.7s linear infinite; margin: 0 auto; }
.spinner-sm { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.4); border-top-color: white; border-radius: 50%; animation: spin 0.7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* MinerU toggle button */
.mineru-toggle { background: none; border: 1px solid #d0d7de; padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 11px; color: #6b7280; }
.mineru-toggle:hover { background: #f0f0f0; }
.mineru-toggle.active { background: #667eea; color: white; border-color: #667eea; }
</style>
