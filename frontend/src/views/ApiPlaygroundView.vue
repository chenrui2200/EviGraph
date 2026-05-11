<template>
  <div class="api-playground">
    <header class="playground-header">
      <div class="header-left">
        <span class="header-icon">⚡</span>
        <h1>API 在线调试</h1>
      </div>
      <div class="header-meta">
        <span class="app-id-tag">App: {{ appId }}</span>
        <a :href="chatUrl" target="_blank" class="chat-link">打开 Chat 页面 →</a>
      </div>
    </header>

    <main class="playground-body">
      <div
        v-for="api in apiList"
        :key="api.id"
        class="api-block"
        :class="{ expanded: api.expanded }"
      >
        <!-- API 头部：点击展开/折叠 -->
        <div class="api-header" @click="toggleApi(api.id)">
          <span class="api-toggle">{{ api.expanded ? '▼' : '▶' }}</span>
          <span class="method-badge" :class="api.method.toLowerCase()">{{ api.method }}</span>
          <span class="api-path">{{ api.path }}</span>
          <span class="api-summary">{{ api.summary }}</span>
        </div>

        <!-- API 内容：展开后显示 -->
        <div v-show="api.expanded" class="api-content">
          <!-- 描述 -->
          <p class="api-desc">{{ api.description }}</p>

          <!-- 参数 -->
          <div class="api-section">
            <div class="api-section-title">📋 请求参数</div>
            <table class="params-table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>类型</th>
                  <th>必填</th>
                  <th>说明</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="param in api.parameters" :key="param.name">
                  <td><code>{{ param.name }}</code></td>
                  <td>{{ param.type }}</td>
                  <td>
                    <span v-if="param.required" class="required-tag">是</span>
                    <span v-else class="optional-tag">否</span>
                  </td>
                  <td>{{ param.description }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 调试 -->
          <div class="api-section">
            <div class="api-section-title">🚀 在线调试</div>
            <div class="request-form">
              <div
                v-for="param in api.parameters.filter(p => p.in === 'body' || p.in === 'query')"
                :key="param.name"
                class="form-row"
              >
                <label class="form-label">
                  <code>{{ param.name }}</code>
                  <span v-if="param.required" class="required-dot">*</span>
                  <span v-if="param.in === 'query'" class="param-location">Query</span>
                </label>
                <input
                  v-if="param.type === 'string' && !param.multiline"
                  v-model="api.form[param.name]"
                  type="text"
                  class="form-input"
                  :class="{ readonly: param.readonly }"
                  :readonly="param.readonly"
                  :placeholder="param.placeholder"
                />
                <textarea
                  v-else-if="param.type === 'string' && param.multiline"
                  v-model="api.form[param.name]"
                  class="form-textarea"
                  :placeholder="param.placeholder"
                  rows="3"
                ></textarea>
                <div v-else-if="param.type === 'number'" class="slider-row">
                  <input
                    type="range"
                    v-model.number="api.form[param.name]"
                    :min="param.min"
                    :max="param.max"
                    :step="param.step"
                    class="threshold-slider"
                  />
                  <span class="slider-value">{{ api.form[param.name] }}</span>
                </div>
              </div>

              <div class="form-actions">
                <button
                  class="send-btn"
                  :disabled="api.sending || !canSend(api)"
                  @click="handleSend(api)"
                >
                  <span v-if="!api.sending">▶ Send</span>
                  <span v-else class="spinner-sm"></span>
                </button>
                <button class="clear-btn" @click="clearResponse(api)">Clear</button>
              </div>
            </div>
          </div>

          <!-- 响应 -->
          <div v-if="api.responseShown" class="api-section response-section">
            <div class="api-section-title response-title">
              📤 响应结果
              <span v-if="api.statusCode" class="status-badge" :class="statusClass(api)">
                {{ api.statusCode }}
              </span>
              <span v-if="api.responseTime" class="response-time">{{ api.responseTime }}ms</span>
              <button
                v-if="api.results.length > 0"
                class="toggle-all-btn"
                @click="toggleAllResults(api)"
              >
                {{ api.allExpanded ? '全部折叠' : '全部展开' }}
              </button>
            </div>

            <div v-if="api.errorMsg" class="error-block">
              <div class="error-title">请求失败</div>
              <div class="error-text">{{ api.errorMsg }}</div>
            </div>

            <!-- public-query 响应视图 -->
            <template v-else-if="api.responseType === 'clause-list'">
              <div v-if="api.results.length === 0" class="empty-results">
                未返回任何结果
              </div>

              <div v-else class="results-list">
                <div
                  v-for="(item, idx) in api.results"
                  :key="idx"
                  class="result-item"
                >
                  <div class="result-header" @click.stop="toggleResult(api, idx)">
                    <span class="result-toggle">{{ api.collapsed[idx] ? '▶' : '▼' }}</span>
                    <span class="result-clause">{{ item.clause_id || '无编号' }}</span>
                    <span class="result-source">{{ item.source || '' }}</span>
                    <span class="result-score">
                      {{ item.relevance_score != null ? item.relevance_score + '分' : '' }}
                    </span>
                  </div>
                  <div v-show="!api.collapsed[idx]" class="result-body">
                    <pre class="result-json" v-html="highlightJson(JSON.stringify(item, null, 2))"
                    ></pre>
                  </div>
                </div>
              </div>
            </template>

            <!-- intent-match 响应视图 -->
            <template v-else-if="api.responseType === 'intent-match'">
              <div v-if="api.results.length === 0" class="empty-results">
                未返回任何结果
              </div>

              <div v-else class="intent-match-layout">
                <!-- 左侧：Topic 列表 -->
                <div class="intent-left">
                  <div class="intent-summary">
                    召回 {{ api.responseData?.data?.total_topics || api.results.length }} 个 Topic
                  </div>
                  <div class="topic-list">
                    <div
                      v-for="(topic, tIdx) in api.results"
                      :key="topic.uuid || tIdx"
                      class="topic-card"
                    >
                      <div class="topic-header" @click.stop="toggleTopic(api, tIdx)">
                        <span class="topic-rank">{{ tIdx + 1 }}</span>
                        <span class="topic-name">{{ topic.name || topic.topic || 'Unknown Topic' }}</span>
                        <span class="topic-score rerank">{{ topic.relevance_score?.toFixed(1) || 0 }}</span>
                      </div>
                      <div v-show="api.collapsed[tIdx]" class="topic-detail">
                        <div v-if="topic.summary" class="topic-summary-text">{{ topic.summary }}</div>
                        <div v-if="topic.associated_entities?.length" class="detail-section">
                          <div class="detail-title">关联实体 ({{ topic.associated_entities.length }})</div>
                          <div
                            v-for="(ent, eIdx) in topic.associated_entities"
                            :key="eIdx"
                            class="detail-item"
                          >
                            <span class="detail-badge" :class="(ent.labels || []).includes('Term') ? 'term' : 'entity'">
                              {{ (ent.labels || []).includes('Term') ? 'Term' : 'Entity' }}
                            </span>
                            <span class="detail-name">{{ ent.name }}</span>
                            <span class="detail-score">{{ ent.relevance_score?.toFixed(1) || 0 }}</span>
                          </div>
                        </div>
                        <div v-if="topic.associated_clauses?.length" class="detail-section">
                          <div class="detail-title">关联条款 ({{ topic.associated_clauses.length }})</div>
                          <div
                            v-for="(clause, cIdx) in topic.associated_clauses"
                            :key="cIdx"
                            class="detail-item"
                          >
                            <span class="detail-badge clause">Clause</span>
                            <span class="detail-name">{{ clause.clause_id || clause.name || '—' }}</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- 右侧：检索过程 -->
                <div v-if="hasRetrievalProcess(api)" class="intent-right">
                  <div class="rp-panel-title">🧠 检索过程</div>
                  <div class="rp-flow">
                    <div class="rp-step">
                      <div class="rp-dot start">Q</div>
                      <div class="rp-content">
                        <div class="rp-label">原始查询</div>
                        <div class="rp-text">{{ api.responseData?.data?.query || api.form.query }}</div>
                      </div>
                    </div>
                    <div class="rp-arrow">↓</div>
                    <div class="rp-step">
                      <div class="rp-dot">1</div>
                      <div class="rp-content">
                        <div class="rp-label">Hybrid 检索 Topic（向量 + BM25）</div>
                      </div>
                    </div>
                    <div class="rp-arrow">↓</div>
                    <div class="rp-step">
                      <div class="rp-dot">2</div>
                      <div class="rp-content">
                        <div class="rp-label">合并去重 + bge-reranker 重排</div>
                      </div>
                    </div>
                    <div class="rp-arrow">↓</div>
                    <div class="rp-step">
                      <div class="rp-dot end">✓</div>
                      <div class="rp-content">
                        <div class="rp-label">最终输出</div>
                        <div class="rp-stats">
                          <div class="rp-stat">
                            <span class="rp-val">{{ api.responseData?.data?.retrieval_process?.merged_candidates || 0 }}</span>
                            <span class="rp-stat-label">合并候选</span>
                          </div>
                          <div class="rp-stat">
                            <span class="rp-val">{{ api.responseData?.data?.retrieval_process?.reranked_topics || 0 }}</span>
                            <span class="rp-stat-label">重排 Topic</span>
                          </div>
                          <div class="rp-stat">
                            <span class="rp-val">{{ api.responseData?.data?.total_topics || 0 }}</span>
                            <span class="rp-stat-label">最终结果</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </template>

            <!-- text/plain 响应视图 -->
            <template v-else-if="api.responseType === 'text-plain'">
              <div v-if="!api.responseData" class="empty-results">未返回任何内容</div>
            </template>

            <div class="code-block-wrapper" style="margin-top: 16px;">
              <div class="code-header">
                <span>完整 Response</span>
                <div class="header-right-actions">
                  <span class="content-type">{{ api.responseType === 'text-plain' ? 'text/plain' : 'application/json' }}</span>
                  <button class="copy-btn" @click="copyResponse(api)" title="复制 Response Body">
                    <span v-if="api.copied">已复制</span>
                    <span v-else>复制</span>
                  </button>
                </div>
              </div>
              <pre v-if="api.responseType === 'text-plain' && typeof api.responseData === 'string'" class="code-content">{{ api.responseData }}</pre>
              <pre v-else class="code-content" v-html="highlightJson(JSON.stringify(api.responseData, null, 2))"
              ></pre>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const appId = route.params.app_id || ''
const chatUrl = `${window.location.origin}/chat/${appId}`
const baseUrl = window.location.origin

function createApiState(config) {
  return reactive({
    ...config,
    expanded: false,
    sending: false,
    responseShown: false,
    statusCode: 0,
    responseTime: 0,
    responseData: null,
    errorMsg: '',
    copied: false,
    collapsed: [],
    get results() {
      if (!this.responseData || !this.responseData.data) return []
      if (this.responseType === 'intent-match') {
        return this.responseData.data.topics || []
      }
      return this.responseData.data.results || []
    },
    get allExpanded() {
      return this.collapsed.length > 0 && this.collapsed.every(c => !c)
    },
  })
}

const apiList = ref([
  createApiState({
    id: 'public-query',
    method: 'POST',
    path: '/api/report/public-query',
    summary: '精确命中',
    description: '无需认证，通过 app_id 获取应用配置并执行检索，返回条款文本、PDF 来源、bbox 位置等信息。',
    url: `${baseUrl}/api/report/public-query`,
    responseType: 'clause-list',
    parameters: [
      { name: 'app_id', type: 'string', required: true, in: 'body', description: '应用 ID（已自动填充）', readonly: true },
      { name: 'query', type: 'string', required: true, in: 'body', description: '查询问题', multiline: true, placeholder: '请输入查询问题，例如：配电线路的敷设要求' },
    ],
    form: { app_id: appId, query: '' },
  }),
  createApiState({
    id: 'kb-words-pool',
    method: 'GET',
    path: '/api/report/kb-words-pool',
    summary: '知识实体词池',
    description: '返回项目知识实体词池 (kb_words_pool.json) 的 JSON 内容，用于调试和查看项目提取的 Topic、实体等信息。结构：{pdf名称: {topics: [{topic, entities}]}}',
    url: `${baseUrl}/api/report/kb-words-pool`,
    responseType: 'text-plain',
    parameters: [
      { name: 'app_id', type: 'string', required: true, in: 'query', description: '应用 ID（已自动填充）', readonly: true },
    ],
    form: { app_id: appId },
  }),
  createApiState({
    id: 'query-topic',
    method: 'POST',
    path: '/api/report/query-topic',
    summary: '全量捕获',
    description: 'hybrid 检索 Topic 节点（向量 + BM25），bge-reranker 重排，获取每个 Top Topic 关联的 Entity / Term 及 Clause。',
    url: `${baseUrl}/api/report/query-topic`,
    responseType: 'intent-match',
    parameters: [
      { name: 'app_id', type: 'string', required: true, in: 'body', description: '应用 ID（已自动填充）', readonly: true },
      { name: 'query', type: 'string', required: true, in: 'body', description: '查询问题', multiline: true, placeholder: '例如：导体应该如何选择' },
      { name: 'topic_limit', type: 'number', required: false, in: 'body', description: 'Topic 召回上限', min: 5, max: 100, step: 5 },
      { name: 'entity_limit', type: 'number', required: false, in: 'body', description: 'Entity 召回上限', min: 5, max: 100, step: 5 },
      { name: 'rerank_min_score', type: 'number', required: false, in: 'body', description: '重排阈值（低于此分数过滤）', min: 0, max: 100, step: 5 },
    ],
    form: { app_id: appId, query: '', topic_limit: 50, entity_limit: 50, rerank_min_score: 0 },
  }),
])

function toggleApi(id) {
  const api = apiList.value.find(a => a.id === id)
  if (api) api.expanded = !api.expanded
}

function canSend(api) {
  return api.parameters.every(p => !p.required || (api.form[p.name] != null && api.form[p.name].toString().trim()))
}

function buildQueryString(form) {
  const params = new URLSearchParams()
  for (const [key, val] of Object.entries(form)) {
    if (val != null && val !== '') params.append(key, val)
  }
  return params.toString()
}

function statusClass(api) {
  if (api.statusCode >= 200 && api.statusCode < 300) return 'success'
  if (api.statusCode >= 400) return 'error'
  return ''
}

const handleSend = async (api) => {
  if (!canSend(api)) return
  api.sending = true
  api.responseShown = true
  api.errorMsg = ''
  api.responseData = null
  api.statusCode = 0
  api.responseTime = 0

  const start = performance.now()
  try {
    let url = api.url
    const options = { method: api.method }

    if (api.method === 'GET') {
      const qs = buildQueryString(api.form)
      if (qs) url += (url.includes('?') ? '&' : '?') + qs
    } else {
      options.headers = { 'Content-Type': 'application/json' }
      options.body = JSON.stringify(api.form)
    }

    const res = await fetch(url, options)
    api.statusCode = res.status
    api.responseTime = Math.round(performance.now() - start)

    const contentType = res.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
      const data = await res.json()
      api.responseData = data
      const resultCount = api.results.length
      api.collapsed = new Array(resultCount).fill(true)
    } else {
      const text = await res.text()
      api.responseData = text
      api.collapsed = []
    }
  } catch (err) {
    api.responseTime = Math.round(performance.now() - start)
    api.errorMsg = err.message || '网络请求失败'
    api.statusCode = 0
  } finally {
    api.sending = false
  }
}

const clearResponse = (api) => {
  api.responseShown = false
  api.responseData = null
  api.errorMsg = ''
  api.statusCode = 0
  api.collapsed = []
}

const toggleResult = (api, idx) => {
  api.collapsed.splice(idx, 1, !api.collapsed[idx])
}

const toggleAllResults = (api) => {
  const target = !api.allExpanded
  api.collapsed = api.collapsed.map(() => !target)
}

const toggleTopic = (api, tIdx) => {
  api.collapsed.splice(tIdx, 1, !api.collapsed[tIdx])
}

const hasRetrievalProcess = (api) => {
  return !!api.responseData?.data?.retrieval_process
}

const copyResponse = async (api) => {
  const text = typeof api.responseData === 'string'
    ? api.responseData
    : JSON.stringify(api.responseData, null, 2)
  if (!text) return
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      // fallback for non-secure contexts
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.left = '-9999px'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    api.copied = true
    setTimeout(() => { api.copied = false }, 2000)
  } catch (err) {
    console.error('Copy failed:', err)
    alert('复制失败，请手动复制')
  }
}

function highlightJson(json) {
  if (!json) return ''
  return json
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/(".*?")(\s*:)/g, '<span class="json-key">$1</span>$2')
    .replace(/: (".*?")/g, ': <span class="json-string">$1</span>')
    .replace(/: (true|false)/g, ': <span class="json-bool">$1</span>')
    .replace(/: (null)/g, ': <span class="json-null">$1</span>')
    .replace(/: (\d+(?:\.\d+)?)/g, ': <span class="json-number">$1</span>')
}
</script>

<style scoped>
.api-playground {
  min-height: 100vh;
  background: #f4f7f9;
  font-family: 'Inter', -apple-system, sans-serif;
  display: flex;
  flex-direction: column;
}

.playground-header {
  background: #fff;
  border-bottom: 1px solid #e0e0e0;
  padding: 16px 32px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: sticky;
  top: 0;
  z-index: 10;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-left h1 {
  font-size: 18px;
  font-weight: 800;
  margin: 0;
  color: #1a1a1a;
}

.header-icon {
  font-size: 22px;
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 16px;
}

.app-id-tag {
  font-family: monospace;
  font-size: 12px;
  background: #f0f2f5;
  padding: 4px 10px;
  border-radius: 6px;
  color: #606266;
}

.chat-link {
  font-size: 13px;
  color: #409eff;
  text-decoration: none;
  font-weight: 600;
}

.chat-link:hover {
  text-decoration: underline;
}

.playground-body {
  flex: 1;
  max-width: 960px;
  width: 100%;
  margin: 0 auto;
  padding: 24px 16px 60px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* API Block */
.api-block {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  border: 1px solid #e8e8e8;
  overflow: hidden;
}

.api-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s;
}

.api-header:hover {
  background: #fafafa;
}

.api-toggle {
  font-size: 10px;
  color: #909399;
  width: 16px;
  text-align: center;
}

.method-badge {
  font-size: 12px;
  font-weight: 800;
  padding: 4px 10px;
  border-radius: 6px;
  color: #fff;
  flex-shrink: 0;
}

.method-badge.post {
  background: #49cc90;
}

.method-badge.get {
  background: #61affe;
}

.api-path {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 14px;
  color: #1a1a1a;
  word-break: break-all;
}

.api-summary {
  font-size: 13px;
  color: #909399;
  margin-left: auto;
  flex-shrink: 0;
}

.api-content {
  padding: 0 20px 20px;
  border-top: 1px solid #f0f0f0;
}

.api-desc {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  margin: 16px 0;
}

/* Section */
.api-section {
  margin-top: 20px;
}

.api-section-title {
  font-size: 14px;
  font-weight: 800;
  color: #1a1a1a;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Params Table */
.params-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.params-table th,
.params-table td {
  text-align: left;
  padding: 10px 12px;
  border-bottom: 1px solid #f0f0f0;
}

.params-table th {
  font-weight: 700;
  color: #606266;
  background: #fafafa;
}

.params-table code {
  font-family: 'Consolas', monospace;
  font-size: 12px;
  background: #f4f6f8;
  padding: 2px 6px;
  border-radius: 4px;
  color: #d73a49;
}

.required-tag {
  background: #fff0f0;
  color: #d93026;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
}

.optional-tag {
  background: #f4f4f5;
  color: #909399;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
}

/* Form */
.request-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-label {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  display: flex;
  align-items: center;
  gap: 4px;
}

.form-label code {
  font-family: 'Consolas', monospace;
  font-size: 13px;
  background: #f4f6f8;
  padding: 2px 6px;
  border-radius: 4px;
}

.required-dot {
  color: #d93026;
  font-weight: 800;
}

.param-location {
  font-size: 10px;
  color: #909399;
  background: #f0f2f5;
  padding: 1px 6px;
  border-radius: 4px;
  margin-left: 4px;
  font-weight: 500;
}

.form-input,
.form-textarea {
  padding: 10px 14px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  font-size: 13px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
  background: #fff;
}

.form-input:focus,
.form-textarea:focus {
  border-color: #409eff;
  box-shadow: 0 0 0 3px rgba(64, 158, 255, 0.1);
}

.form-input.readonly {
  background: #f5f7fa;
  color: #909399;
  cursor: not-allowed;
}

.form-textarea {
  resize: vertical;
  min-height: 80px;
  line-height: 1.6;
}

.slider-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.threshold-slider {
  width: 200px;
  cursor: pointer;
}

.slider-value {
  font-weight: 700;
  color: #409eff;
  min-width: 32px;
  text-align: center;
  font-size: 13px;
}

.form-actions {
  display: flex;
  gap: 12px;
  margin-top: 4px;
}

.send-btn {
  background: #49cc90;
  color: #fff;
  border: none;
  padding: 10px 24px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.2s;
  display: flex;
  align-items: center;
  gap: 6px;
}

.send-btn:hover:not(:disabled) {
  background: #3bb87d;
}

.send-btn:disabled {
  background: #b0e0c8;
  cursor: not-allowed;
}

.clear-btn {
  background: #fff;
  border: 1px solid #dcdfe6;
  color: #606266;
  padding: 10px 20px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.clear-btn:hover {
  border-color: #409eff;
  color: #409eff;
}

/* Response */
.response-section {
  border-left: 3px solid #409eff;
  padding-left: 16px;
}

.response-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.toggle-all-btn {
  margin-left: auto;
  background: #f0f2f5;
  border: 1px solid #dcdfe6;
  color: #606266;
  padding: 4px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.toggle-all-btn:hover {
  background: #e4e7ed;
  border-color: #c0c4cc;
}

.status-badge {
  font-size: 12px;
  font-weight: 800;
  padding: 2px 10px;
  border-radius: 6px;
}

.status-badge.success {
  background: #f0f9eb;
  color: #67c23a;
}

.status-badge.error {
  background: #fef0f0;
  color: #f56c6c;
}

.response-time {
  font-size: 12px;
  color: #909399;
  font-weight: 400;
}

.empty-results {
  font-size: 13px;
  color: #909399;
  padding: 16px;
  text-align: center;
}

.error-block {
  background: #fef0f0;
  border: 1px solid #fde2e2;
  border-radius: 8px;
  padding: 16px;
}

.error-title {
  font-size: 13px;
  font-weight: 700;
  color: #c0392b;
  margin-bottom: 6px;
}

.error-text {
  font-size: 13px;
  color: #666;
  font-family: monospace;
  word-break: break-all;
}

/* Results List (clause-list) */
.results-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.result-item {
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  overflow: hidden;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: #fafafa;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s;
}

.result-header:hover {
  background: #f0f2f5;
}

.result-toggle {
  font-size: 10px;
  color: #909399;
  width: 14px;
  text-align: center;
}

.result-clause {
  font-family: 'Consolas', monospace;
  font-size: 13px;
  font-weight: 700;
  color: #409eff;
  background: #ecf5ff;
  padding: 2px 8px;
  border-radius: 4px;
}

.result-source {
  font-size: 12px;
  color: #606266;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-score {
  font-size: 12px;
  font-weight: 600;
  color: #67c23a;
  background: #f0f9eb;
  padding: 2px 8px;
  border-radius: 4px;
}

.result-body {
  border-top: 1px solid #f0f0f0;
}

.result-json {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 14px;
  margin: 0;
  font-family: 'Consolas', 'Monaco', 'Menlo', monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
}

/* Intent Match Layout */
.intent-match-layout {
  display: flex;
  gap: 16px;
}

.intent-left {
  flex: 1;
  min-width: 0;
}

.intent-right {
  width: 280px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  overflow: hidden;
}

.intent-summary {
  font-size: 13px;
  color: #409eff;
  background: #ecf5ff;
  padding: 6px 12px;
  border-radius: 6px;
  margin-bottom: 12px;
  font-weight: 600;
}

/* Topic Cards */
.topic-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.topic-card {
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  overflow: hidden;
}

.topic-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: #fafafa;
  cursor: pointer;
  user-select: none;
  transition: background 0.2s;
}

.topic-header:hover {
  background: #f0f2f5;
}

.topic-rank {
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
}

.topic-name {
  font-size: 14px;
  font-weight: 600;
  color: #1a1a1a;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topic-score {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 10px;
  flex-shrink: 0;
}

.topic-score.rerank {
  background: #f0f9eb;
  color: #67c23a;
}

.topic-detail {
  padding: 12px 14px;
  border-top: 1px solid #f0f0f0;
}

.topic-summary-text {
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
  margin-bottom: 12px;
}

.detail-section {
  margin-top: 10px;
}

.detail-title {
  font-size: 11px;
  font-weight: 700;
  color: #409eff;
  margin-bottom: 6px;
  padding-bottom: 4px;
  border-bottom: 1px dashed #e0e0e0;
}

.detail-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  background: #f8f9fa;
  border-radius: 6px;
  margin-bottom: 4px;
  font-size: 12px;
}

.detail-badge {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 8px;
  color: #fff;
  flex-shrink: 0;
}

.detail-badge.entity {
  background: #409eff;
}

.detail-badge.term {
  background: #9c27b0;
}

.detail-badge.clause {
  background: #e6a23c;
}

.detail-name {
  font-weight: 600;
  color: #1a1a1a;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-score {
  font-size: 10px;
  font-weight: 700;
  color: #67c23a;
  background: #f0f9eb;
  padding: 1px 5px;
  border-radius: 8px;
  flex-shrink: 0;
}

/* Retrieval Process */
.rp-panel-title {
  padding: 12px 14px;
  font-size: 13px;
  font-weight: 700;
  color: #1a1a1a;
  background: #fafafa;
  border-bottom: 1px solid #f0f0f0;
}

.rp-flow {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.rp-step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.rp-dot {
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

.rp-dot.start {
  background: #67c23a;
}

.rp-dot.end {
  background: #67c23a;
  font-size: 14px;
}

.rp-content {
  flex: 1;
  min-width: 0;
}

.rp-label {
  font-size: 11px;
  font-weight: 700;
  color: #1a1a1a;
  margin-bottom: 4px;
}

.rp-text {
  font-size: 12px;
  color: #606266;
  background: #f5f7fa;
  padding: 6px 8px;
  border-radius: 6px;
  line-height: 1.4;
  word-break: break-all;
}

.rp-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.rp-tag {
  font-size: 10px;
  background: #ecf5ff;
  color: #409eff;
  padding: 2px 6px;
  border-radius: 8px;
  font-weight: 600;
}

.rp-arrow {
  font-size: 12px;
  color: #c0c4cc;
  padding-left: 30px;
  line-height: 1.2;
}

.rp-stats {
  display: flex;
  gap: 8px;
}

.rp-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  background: #f5f7fa;
  padding: 6px 10px;
  border-radius: 6px;
  min-width: 50px;
}

.rp-val {
  font-size: 14px;
  font-weight: 700;
  color: #409eff;
}

.rp-stat-label {
  font-size: 9px;
  color: #909399;
}

/* Code Block */
.code-block-wrapper {
  display: flex;
  flex-direction: column;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #333;
}

.code-header {
  background: #333;
  color: #fff;
  padding: 8px 14px;
  font-size: 11px;
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-right-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.content-type {
  font-size: 10px;
  color: #aaa;
  font-weight: 400;
}

.copy-btn {
  background: #444;
  color: #fff;
  border: 1px solid #555;
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
  font-weight: 600;
}

.copy-btn:hover {
  background: #555;
  border-color: #666;
}

.copy-btn:active {
  background: #333;
}

.code-content {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 16px;
  margin: 0;
  font-family: 'Consolas', 'Monaco', 'Menlo', monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  overflow-x: auto;
  max-height: 500px;
  overflow-y: auto;
}

/* JSON Highlight */
:deep(.json-key) {
  color: #9cdcfe;
}

:deep(.json-string) {
  color: #ce9178;
}

:deep(.json-number) {
  color: #b5cea8;
}

:deep(.json-bool) {
  color: #569cd6;
}

:deep(.json-null) {
  color: #569cd6;
}

/* Text Plain Content */
.text-plain-content {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 16px;
  margin: 0;
  font-family: 'Consolas', 'Monaco', 'Menlo', monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  overflow-x: auto;
  max-height: 600px;
  overflow-y: auto;
  border-radius: 8px;
}

/* Spinner */
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
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
</style>
