<template>
  <div class="intent-match-view">
    <!-- Header -->
    <header class="view-header">
      <div class="header-left">
        <button class="back-btn" @click="router.push({ name: 'GraphBuild', params: { projectId } })">←</button>
        <h2 class="view-title">问题意图摘要匹配</h2>
      </div>
      <div class="header-right">
        <span class="project-info" v-if="projectName">
          当前项目: <strong>{{ projectName }}</strong>
        </span>
      </div>
    </header>

    <main class="intent-match-main">
      <!-- Search Section -->
      <div class="search-section">
        <div class="search-card">
          <div class="search-box">
            <input
              v-model="searchQuery"
              placeholder="输入问题，系统将检索最相关的 Topic 及关联知识实体..."
              @keyup.enter="handleSearch"
              :disabled="searching"
            />
            <button class="search-btn" @click="handleSearch" :disabled="searching || !searchQuery.trim()">
              <span v-if="!searching">🔍 匹配</span>
              <span v-else class="spinner-sm"></span>
            </button>
          </div>
          <div class="search-controls">
            <div class="threshold-control">
              <span class="threshold-label">重排阈值</span>
              <input
                type="range"
                v-model.number="rerankMinScore"
                min="0"
                max="100"
                step="5"
                class="threshold-slider"
              />
              <span class="threshold-value">{{ rerankMinScore }}</span>
              <span class="threshold-hint" v-if="rerankMinScore > 0">低于 {{ rerankMinScore }} 分的 Topic 将被过滤</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Content Split: Left Results + Right Retrieval Process -->
      <div class="content-split">
        <!-- Left: Results -->
        <div class="left-panel">
          <!-- Empty State -->
          <div v-if="!results.topics.length && !searching" class="empty-state">
            <div class="empty-icon">🎯</div>
            <p>输入问题后点击匹配，查看 Topic 召回与关联实体结果</p>
          </div>

          <!-- Searching State -->
          <div v-if="searching" class="searching-state">
            <div class="spinner"></div>
            <p>正在检索 Topic 并匹配关联实体...</p>
          </div>

          <!-- Results Summary -->
          <div v-if="!searching && results.topics.length > 0" class="results-summary">
            <span class="summary-badge">
              召回 {{ results.total_topics }} 个 Topic，去重后 {{ totalUniqueEntities }} 个实体
            </span>
            <span v-if="durationMs > 0" class="time-badge">{{ durationMs }}ms</span>
          </div>

          <!-- Topic Cards -->
          <div v-if="!searching && results.topics.length > 0" class="topic-list">
            <div
              v-for="(topic, tIdx) in results.topics"
              :key="topic.uuid || tIdx"
              class="topic-card"
            >
              <!-- Topic Header -->
              <div class="topic-header" @click="toggleTopic(tIdx)">
                <div class="topic-rank">{{ tIdx + 1 }}</div>
                <div class="topic-info">
                  <div class="topic-name-row">
                    <span class="topic-name">{{ topic.name || topic.topic || 'Unknown Topic' }}</span>
                    <span class="topic-labels">
                      <span v-for="lbl in (topic.labels || []).filter(l => l !== 'Topic')" :key="lbl" class="label-tag">{{ lbl }}</span>
                    </span>
                  </div>
                  <div class="topic-meta">
                    <span class="score-tag rerank" title="bge-reranker 相关性分数">
                      rerank {{ topic.relevance_score?.toFixed(1) || 0 }}
                    </span>
                    <span class="score-tag hybrid" title="hybrid 检索原始分数">
                      hybrid {{ topic.hybrid_score?.toFixed(3) || 0 }}
                    </span>
                    <span class="entity-count">{{ topic.entity_count || 0 }} 个关联实体</span>
                  </div>
                </div>
                <div class="expand-icon">{{ expandedTopics.has(tIdx) ? '▲' : '▼' }}</div>
              </div>

              <!-- Topic Summary -->
              <div v-if="topic.summary" class="topic-summary">
                {{ topic.summary }}
              </div>

              <!-- Associated Entities -->
              <div v-if="expandedTopics.has(tIdx)" class="entity-list">
                <div class="entity-list-header">关联知识实体</div>
                <div
                  v-for="(ent, eIdx) in topic.associated_entities"
                  :key="ent.uuid || eIdx"
                  class="entity-item"
                  :class="{ 'is-term': (ent.labels || []).includes('Term') }"
                >
                  <div class="entity-main">
                    <div class="entity-name-row">
                      <span class="entity-type-badge" :class="(ent.labels || []).includes('Term') ? 'term' : 'entity'">
                        {{ (ent.labels || []).includes('Term') ? 'Term' : 'Entity' }}
                      </span>
                      <span class="entity-name">{{ ent.name || 'Unknown' }}</span>
                      <span class="entity-score" title="bge-reranker 相关性分数">
                        {{ ent.relevance_score?.toFixed(1) || 0 }}
                      </span>
                    </div>
                    <div v-if="ent.summary" class="entity-summary-text">
                      {{ ent.summary }}
                    </div>
                  </div>
                </div>
                <div v-if="!topic.associated_entities?.length" class="no-entity-hint">
                  该 Topic 暂无关联实体
                </div>
              </div>

              <!-- Associated Clauses -->
              <div v-if="expandedTopics.has(tIdx)" class="clause-list">
                <div class="clause-list-header">📄 关联条款 ({{ topic.clause_count || 0 }})</div>
                <div
                  v-for="(clause, cIdx) in topic.associated_clauses"
                  :key="clause.uuid || cIdx"
                  class="clause-item"
                >
                  <div class="clause-main">
                    <div class="clause-name-row">
                      <span class="clause-type-badge">Clause</span>
                      <span v-if="clause.clause_id" class="clause-id">{{ clause.clause_id }}</span>
                      <span v-if="clause.name" class="clause-name">{{ clause.name }}</span>
                      <button
                        v-if="clause.source && clause.source !== 'Unknown'"
                        class="clause-locate-btn"
                        @click.stop="viewDocument(clause)"
                      >
                        📄 定位文档
                      </button>
                    </div>
                    <div v-if="clause.summary" class="clause-summary-text">
                      {{ clause.summary }}
                    </div>
                  </div>
                </div>
                <div v-if="!topic.associated_clauses?.length" class="no-clause-hint">
                  该 Topic 暂无关联条款
                </div>
              </div>
            </div>
          </div>

          <!-- Unique Entities Panel -->
          <div v-if="!searching && allUniqueEntities.length > 0" class="unique-entities-panel">
            <div class="panel-header">
              <span>🧩 去重实体聚合 ({{ allUniqueEntities.length }} 个)</span>
            </div>
            <div class="unique-entity-list">
              <div
                v-for="(ent, idx) in allUniqueEntities"
                :key="ent.uuid || idx"
                class="unique-entity-item"
                :class="{ 'is-term': (ent.labels || []).includes('Term') }"
              >
                <span class="ue-type">{{ (ent.labels || []).includes('Term') ? 'Term' : 'Entity' }}</span>
                <span class="ue-name">{{ ent.name }}</span>
                <span class="ue-score">{{ ent.relevance_score?.toFixed(1) || 0 }}</span>
                <span v-if="ent.topic_sources" class="ue-sources">来自 {{ ent.topic_sources }} 个 Topic</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Right: Retrieval Process Panel -->
        <div class="right-panel" v-if="hasRetrievalProcess || searching">
          <div class="retrieval-panel-header">🧠 检索过程</div>

          <!-- Searching state in panel -->
          <div v-if="searching && !hasRetrievalProcess" class="retrieval-searching">
            <div class="rp-step">
              <div class="rp-step-dot pulse"></div>
              <div class="rp-step-content">
                <div class="rp-step-title">正在分析查询并检索...</div>
              </div>
            </div>
          </div>

          <!-- Retrieval Flow -->
          <div v-if="hasRetrievalProcess" class="retrieval-flow">
            <!-- Step 0: Original Query -->
            <div class="rp-step">
              <div class="rp-step-dot start">Q</div>
              <div class="rp-step-content">
                <div class="rp-step-label">原始查询</div>
                <div class="rp-query-text">{{ results.query || searchQuery }}</div>
              </div>
            </div>

            <div class="rp-arrow">↓</div>

            <!-- Step 1: Keyword Extraction -->
            <div class="rp-step">
              <div class="rp-step-dot">1</div>
              <div class="rp-step-content">
                <div class="rp-step-label">LLM 提取关键词</div>
                <div class="rp-keywords">
                  <span
                    v-for="(kw, kIdx) in results.retrieval_process.keywords"
                    :key="kIdx"
                    class="rp-keyword-tag"
                  >{{ kw }}</span>
                </div>
              </div>
            </div>

            <div class="rp-arrow">↓</div>

            <!-- Step 2: Parallel Search -->
            <div class="rp-step">
              <div class="rp-step-dot">2</div>
              <div class="rp-step-content">
                <div class="rp-step-label">
                  并行 {{ results.retrieval_process.search_steps?.length || 0 }} 路搜索 Topic
                </div>
                <div class="rp-search-steps">
                  <div
                    v-for="(step, sIdx) in results.retrieval_process.search_steps"
                    :key="sIdx"
                    class="rp-search-step"
                  >
                    <div class="rp-step-num">#{{ step.step }}</div>
                    <div class="rp-step-detail">
                      <div class="rp-step-query" :title="step.query">{{ step.query }}</div>
                      <div class="rp-step-meta">
                        <span class="rp-method">{{ step.search_method }}</span>
                        <span class="rp-count">召回 {{ step.results_count }} 个</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="rp-arrow">↓</div>

            <!-- Step 3: Merge & Rerank -->
            <div class="rp-step">
              <div class="rp-step-dot">3</div>
              <div class="rp-step-content">
                <div class="rp-step-label">合并去重 + bge-reranker 重排</div>
                <div class="rp-step-desc">
                  按原始查询对所有候选 Topic 进行相关性重排序
                </div>
              </div>
            </div>

            <div class="rp-arrow">↓</div>

            <!-- Step 4: Final Result -->
            <div class="rp-step">
              <div class="rp-step-dot end">✓</div>
              <div class="rp-step-content">
                <div class="rp-step-label">最终输出</div>
                <div class="rp-stats">
                  <div class="rp-stat">
                    <span class="rp-stat-val">{{ results.retrieval_process.merged_candidates }}</span>
                    <span class="rp-stat-label">合并候选</span>
                  </div>
                  <div class="rp-stat">
                    <span class="rp-stat-val">{{ results.retrieval_process.reranked_topics }}</span>
                    <span class="rp-stat-label">重排 Topic</span>
                  </div>
                  <div class="rp-stat">
                    <span class="rp-stat-val">{{ results.total_topics }}</span>
                    <span class="rp-stat-label">最终结果</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Retrieval Path Info -->
          <div v-if="hasRetrievalProcess" class="retrieval-path-info">
            <div class="rp-path-title">检索路径</div>
            <div class="rp-path">
              <span class="rp-path-node">Entity/Term</span>
              <span class="rp-path-arrow">←MENTIONS—</span>
              <span class="rp-path-node highlight">Topic</span>
              <span class="rp-path-arrow">—HAS_TOPIC→</span>
              <span class="rp-path-node">Clause</span>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- PDF Document Viewer Overlay -->
    <div class="pdf-viewer-overlay" v-show="showDocViewer">
      <div class="pdf-viewer-backdrop" @click="showDocViewer = false"></div>
      <div class="pdf-viewer-drawer">
        <PdfViewer
          v-model="showDocViewer"
          :filename="currentDoc.filename"
          :url="currentDoc.url"
          :page="currentDoc.page"
          :bbox="currentDoc.bbox"
          :page-width="currentDoc.pageWidth"
          :page-height="currentDoc.pageHeight"
          :pdf-bboxes="currentDoc.pdfBboxes"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getProject } from '../api/graph'
import { queryIntentMatch } from '../api/graph'
import PdfViewer from '../components/PdfViewer.vue'

const route = useRoute()
const router = useRouter()

const projectId = route.params.projectId
const projectName = ref('')
const graphId = ref('')

const searchQuery = ref('')
const searching = ref(false)
const durationMs = ref(0)
const results = ref({ topics: [], total_topics: 0 })
const expandedTopics = ref(new Set())
const graphIdLoading = ref(true)
const rerankMinScore = ref(0)

// PDF document viewer state
const showDocViewer = ref(false)
const currentDoc = ref({
  filename: '',
  url: '',
  page: 1,
  bbox: null,
  pageWidth: 0,
  pageHeight: 0,
  pdfBboxes: null,
})

onMounted(async () => {
  try {
    const res = await getProject(projectId)
    console.log('[IntentMatch] getProject res:', res)
    if (res.success) {
      projectName.value = res.data.name
      graphId.value = res.data.graph_id
      console.log('[IntentMatch] graphId loaded:', graphId.value)
    }
  } catch (err) {
    console.error('Failed to init IntentMatch:', err)
  } finally {
    graphIdLoading.value = false
  }
})

const handleSearch = async () => {
  const q = searchQuery.value.trim()
  const gid = graphId.value || projectId
  console.log('[IntentMatch] handleSearch called, query:', q, 'graphId:', gid)
  if (!q) {
    alert('请输入查询问题')
    return
  }
  if (!gid) {
    alert('项目信息加载中，请稍后重试')
    return
  }
  searching.value = true
  const start = Date.now()
  try {
    const res = await queryIntentMatch({
      graph_id: gid,
      query: q,
      topic_limit: 50,
      entity_limit: 50,
      rerank_min_score: rerankMinScore.value,
    })
    if (res?.success) {
      results.value = res.data || { topics: [], total_topics: 0 }
      // 默认展开前 3 个 Topic
      expandedTopics.value = new Set([0, 1, 2].filter(i => i < results.value.topics.length))
    } else {
      alert(res?.error || '检索失败')
    }
  } catch (err) {
    console.error('Intent match failed:', err)
    alert('请求失败: ' + err.message)
  } finally {
    durationMs.value = Date.now() - start
    searching.value = false
  }
}

const toggleTopic = (idx) => {
  const newSet = new Set(expandedTopics.value)
  if (newSet.has(idx)) {
    newSet.delete(idx)
  } else {
    newSet.add(idx)
  }
  expandedTopics.value = newSet
}

// View clause in PDF document
const viewDocument = (clause) => {
  if (!clause.source || clause.source === 'Unknown') return
  const identifier = graphId.value || projectId
  const filename = clause.source
  const page = clause.page || 1
  const bbox = clause.bbox || null
  const pageWidth = clause.page_width || 0
  const pageHeight = clause.page_height || 0
  const pdfBboxes = clause.pdf_bboxes || null

  currentDoc.value = {
    filename,
    url: `${window.location.origin}/api/graph/project/${identifier}/document/${encodeURIComponent(filename)}?t=${Date.now()}`,
    page,
    bbox,
    pageWidth,
    pageHeight,
    pdfBboxes,
  }
  showDocViewer.value = true
}

// 是否有检索过程数据
const hasRetrievalProcess = computed(() => {
  return !!results.value.retrieval_process
})

// 统计去重后的实体总数
const totalUniqueEntities = computed(() => {
  const seen = new Set()
  let count = 0
  for (const topic of results.value.topics || []) {
    for (const ent of topic.associated_entities || []) {
      if (ent.uuid && !seen.has(ent.uuid)) {
        seen.add(ent.uuid)
        count++
      }
    }
  }
  return count
})

// 聚合所有去重实体，按 relevance_score 降序
const allUniqueEntities = computed(() => {
  const map = new Map()
  for (const topic of results.value.topics || []) {
    for (const ent of topic.associated_entities || []) {
      if (!ent.uuid) continue
      const existing = map.get(ent.uuid)
      if (!existing) {
        map.set(ent.uuid, { ...ent, topic_sources: 1 })
      } else {
        existing.topic_sources = (existing.topic_sources || 1) + 1
        // 保留更高分
        if ((ent.relevance_score || 0) > (existing.relevance_score || 0)) {
          existing.relevance_score = ent.relevance_score
        }
      }
    }
  }
  return Array.from(map.values()).sort((a, b) => (b.relevance_score || 0) - (a.relevance_score || 0))
})
</script>

<style scoped>
.intent-match-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
  background: #f5f7fa;
  overflow: hidden;
}

.view-header {
  height: 56px;
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
  font-size: 13px;
  color: #666;
}

.intent-match-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Content Split */
.content-split {
  flex: 1;
  display: flex;
  flex-direction: row;
  overflow: hidden;
}

.left-panel {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  min-width: 0;
}

.right-panel {
  width: 340px;
  flex-shrink: 0;
  background: #fff;
  border-left: 1px solid #e0e0e0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

/* Search Section */
.search-section {
  padding: 20px 24px 12px;
  background: #fff;
  border-bottom: 1px solid #e8e8e8;
  flex-shrink: 0;
}

.search-card {
  max-width: 800px;
}

.search-box {
  display: flex;
  gap: 12px;
}

.search-box input {
  flex: 1;
  height: 44px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  padding: 0 16px;
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s;
}

.search-box input:focus {
  border-color: #409eff;
}

.search-btn {
  padding: 0 28px;
  background: #000;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  font-size: 14px;
  cursor: pointer;
  transition: opacity 0.2s;
  display: flex;
  align-items: center;
  gap: 6px;
}

.search-btn:hover:not(:disabled) {
  opacity: 0.8;
}

.search-btn:disabled {
  background: #ccc;
  cursor: not-allowed;
}

/* Search Controls */
.search-controls {
  margin-top: 10px;
  display: flex;
  align-items: center;
}

.threshold-control {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: #606266;
}

.threshold-label {
  font-weight: 600;
  color: #1a1a1a;
  white-space: nowrap;
}

.threshold-slider {
  width: 160px;
  cursor: pointer;
}

.threshold-value {
  font-weight: 700;
  color: #409eff;
  min-width: 28px;
  text-align: center;
}

.threshold-hint {
  font-size: 11px;
  color: #e6a23c;
  background: #fdf6ec;
  padding: 2px 8px;
  border-radius: 10px;
}

/* Empty / Searching states inside left panel */
.left-panel .empty-state,
.left-panel .searching-state {
  margin-top: 40px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 300px;
  color: #999;
}

.empty-icon {
  font-size: 48px;
  margin-bottom: 12px;
  opacity: 0.4;
}

.searching-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-top: 60px;
  color: #666;
}

.spinner {
  width: 30px;
  height: 30px;
  border: 3px solid #f3f3f3;
  border-top: 3px solid #409eff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 12px;
}

.spinner-sm {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  display: inline-block;
}

@keyframes spin { to { transform: rotate(360deg); } }

.results-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.summary-badge {
  font-size: 13px;
  color: #409eff;
  background: #ecf5ff;
  padding: 4px 12px;
  border-radius: 4px;
  font-weight: 600;
}

.time-badge {
  font-size: 12px;
  color: #909399;
}

/* Topic Cards */
.topic-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 900px;
}

.topic-card {
  background: #fff;
  border-radius: 10px;
  border: 1px solid #e0e0e0;
  overflow: hidden;
  transition: box-shadow 0.2s;
}

.topic-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}

.topic-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  cursor: pointer;
  background: #fafafa;
}

.topic-rank {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.topic-info {
  flex: 1;
  min-width: 0;
}

.topic-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}

.topic-name {
  font-size: 15px;
  font-weight: 700;
  color: #1a1a1a;
}

.topic-labels {
  display: flex;
  gap: 4px;
}

.label-tag {
  font-size: 10px;
  background: #e6f7ff;
  color: #1890ff;
  padding: 1px 6px;
  border-radius: 4px;
}

.topic-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.score-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
}

.score-tag.rerank {
  background: #f0f9eb;
  color: #67c23a;
}

.score-tag.hybrid {
  background: #fdf6ec;
  color: #e6a23c;
}

.entity-count {
  font-size: 12px;
  color: #909399;
}

.expand-icon {
  color: #999;
  font-size: 12px;
  flex-shrink: 0;
}

.topic-summary {
  padding: 10px 16px;
  font-size: 13px;
  color: #606266;
  line-height: 1.5;
  border-bottom: 1px solid #f0f0f0;
}

/* Entity List */
.entity-list {
  padding: 12px 16px;
  background: #fff;
}

.entity-list-header {
  font-size: 12px;
  font-weight: 700;
  color: #409eff;
  margin-bottom: 10px;
  padding-bottom: 6px;
  border-bottom: 1px dashed #e0e0e0;
}

.entity-item {
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8f9fa;
  margin-bottom: 8px;
  border-left: 3px solid #409eff;
}

.entity-item.is-term {
  border-left-color: #9c27b0;
}

.entity-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.entity-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.entity-type-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 10px;
  color: #fff;
}

.entity-type-badge.entity {
  background: #409eff;
}

.entity-type-badge.term {
  background: #9c27b0;
}

.entity-name {
  font-size: 14px;
  font-weight: 600;
  color: #1a1a1a;
}

.entity-score {
  font-size: 11px;
  font-weight: 700;
  color: #67c23a;
  background: #f0f9eb;
  padding: 2px 8px;
  border-radius: 10px;
  margin-left: auto;
}

.entity-summary-text {
  font-size: 12px;
  color: #606266;
  line-height: 1.4;
}

.no-entity-hint {
  font-size: 12px;
  color: #999;
  font-style: italic;
  padding: 8px;
  text-align: center;
}

/* Clause List */
.clause-list {
  padding: 12px 16px;
  background: #fff;
  border-top: 1px dashed #e0e0e0;
}

.clause-list-header {
  font-size: 12px;
  font-weight: 700;
  color: #e6a23c;
  margin-bottom: 10px;
  padding-bottom: 6px;
  border-bottom: 1px dashed #e0e0e0;
}

.clause-item {
  padding: 10px 12px;
  border-radius: 8px;
  background: #fdf6ec;
  margin-bottom: 8px;
  border-left: 3px solid #e6a23c;
}

.clause-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.clause-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.clause-type-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 10px;
  color: #fff;
  background: #e6a23c;
}

.clause-id {
  font-size: 11px;
  font-weight: 700;
  color: #e6a23c;
  background: #fdf6ec;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid #faecd8;
}

.clause-name {
  font-size: 14px;
  font-weight: 600;
  color: #1a1a1a;
}

.clause-locate-btn {
  font-size: 11px;
  padding: 2px 8px;
  border: 1px solid #e6a23c;
  background: #fdf6ec;
  color: #e6a23c;
  border-radius: 10px;
  cursor: pointer;
  font-weight: 600;
  transition: all 0.2s;
  margin-left: auto;
}

.clause-locate-btn:hover {
  background: #e6a23c;
  color: #fff;
}

.clause-summary-text {
  font-size: 12px;
  color: #606266;
  line-height: 1.4;
}

.no-clause-hint {
  font-size: 12px;
  color: #999;
  font-style: italic;
  padding: 8px;
  text-align: center;
}

/* Unique Entities Panel */
.unique-entities-panel {
  margin-top: 24px;
  max-width: 900px;
  background: #fff;
  border-radius: 10px;
  border: 1px solid #e0e0e0;
  overflow: hidden;
}

.panel-header {
  padding: 12px 16px;
  background: #fafafa;
  font-size: 14px;
  font-weight: 700;
  color: #1a1a1a;
  border-bottom: 1px solid #f0f0f0;
}

.unique-entity-list {
  padding: 12px 16px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.unique-entity-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border-radius: 10px;
  background: #f8f9fa;
  font-size: 12px;
  border: 1px solid #e8e8e8;
  max-width: 100%;
}

.unique-entity-item.is-term {
  background: #f9f0ff;
  border-color: #e0c3f0;
}

.ue-type {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 8px;
  color: #fff;
  background: #409eff;
  flex-shrink: 0;
}

.unique-entity-item.is-term .ue-type {
  background: #9c27b0;
}

.ue-name {
  font-weight: 600;
  color: #1a1a1a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ue-score {
  font-size: 10px;
  font-weight: 700;
  color: #67c23a;
  background: #f0f9eb;
  padding: 1px 5px;
  border-radius: 8px;
  flex-shrink: 0;
}

.ue-sources {
  font-size: 10px;
  color: #909399;
  flex-shrink: 0;
  white-space: nowrap;
}

/* Retrieval Process Panel */
.retrieval-panel-header {
  padding: 14px 16px;
  font-size: 14px;
  font-weight: 700;
  color: #1a1a1a;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
  flex-shrink: 0;
}

.retrieval-searching {
  padding: 20px 16px;
}

.retrieval-flow {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.rp-step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.rp-step-dot {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
}

.rp-step-dot.start {
  background: #67c23a;
}

.rp-step-dot.end {
  background: #67c23a;
  font-size: 14px;
}

.rp-step-dot.pulse {
  background: #409eff;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.rp-step-content {
  flex: 1;
  min-width: 0;
}

.rp-step-label {
  font-size: 12px;
  font-weight: 700;
  color: #1a1a1a;
  margin-bottom: 6px;
}

.rp-step-title {
  font-size: 13px;
  color: #409eff;
  font-weight: 600;
}

.rp-query-text {
  font-size: 13px;
  color: #606266;
  background: #f5f7fa;
  padding: 8px 10px;
  border-radius: 6px;
  line-height: 1.4;
  word-break: break-all;
}

.rp-keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.rp-keyword-tag {
  font-size: 11px;
  background: #ecf5ff;
  color: #409eff;
  padding: 3px 8px;
  border-radius: 10px;
  font-weight: 600;
}

.rp-arrow {
  font-size: 14px;
  color: #c0c4cc;
  padding-left: 32px;
  line-height: 1.4;
}

.rp-search-steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rp-search-step {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px;
  background: #f8f9fa;
  border-radius: 6px;
}

.rp-step-num {
  font-size: 10px;
  font-weight: 700;
  color: #fff;
  background: #909399;
  padding: 1px 5px;
  border-radius: 4px;
  flex-shrink: 0;
}

.rp-step-detail {
  flex: 1;
  min-width: 0;
}

.rp-step-query {
  font-size: 12px;
  color: #1a1a1a;
  font-weight: 600;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rp-step-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.rp-method {
  font-size: 10px;
  background: #fdf6ec;
  color: #e6a23c;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 600;
}

.rp-count {
  font-size: 11px;
  color: #909399;
}

.rp-step-desc {
  font-size: 12px;
  color: #606266;
  line-height: 1.4;
}

.rp-stats {
  display: flex;
  gap: 12px;
}

.rp-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  background: #f5f7fa;
  padding: 8px 12px;
  border-radius: 6px;
  min-width: 60px;
}

.rp-stat-val {
  font-size: 16px;
  font-weight: 700;
  color: #409eff;
}

.rp-stat-label {
  font-size: 10px;
  color: #909399;
}

/* Retrieval Path Info */
.retrieval-path-info {
  margin: 8px 16px 16px;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 8px;
  border: 1px dashed #dcdfe6;
}

.rp-path-title {
  font-size: 11px;
  font-weight: 700;
  color: #606266;
  margin-bottom: 8px;
}

.rp-path {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  font-size: 11px;
}

.rp-path-node {
  background: #e6f7ff;
  color: #1890ff;
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 600;
}

.rp-path-node.highlight {
  background: #409eff;
  color: #fff;
}

.rp-path-arrow {
  color: #909399;
  font-weight: 600;
}

/* PDF Viewer Overlay */
.pdf-viewer-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1000;
  display: flex;
  justify-content: flex-end;
}

.pdf-viewer-backdrop {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.3);
}

.pdf-viewer-drawer {
  position: relative;
  width: 55%;
  height: 100%;
  background: #fff;
  box-shadow: -4px 0 20px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  z-index: 1;
}

.pdf-viewer-drawer .pdf-viewer-panel {
  height: 100%;
}
</style>
