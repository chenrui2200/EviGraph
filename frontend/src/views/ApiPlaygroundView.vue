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
      <!-- Endpoint Card -->
      <div class="endpoint-card">
        <div class="endpoint-line">
          <span class="method-badge post">POST</span>
          <span class="endpoint-url">/api/report/public-query</span>
        </div>
        <p class="endpoint-desc">
          公共查询接口：无需认证，通过 app_id 获取应用配置并执行检索，返回条款文本、PDF 来源、bbox 位置等信息。
        </p>
      </div>

      <!-- Parameters Section -->
      <div class="section-card">
        <div class="section-title">📋 请求参数</div>
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
            <tr>
              <td><code>app_id</code></td>
              <td>string</td>
              <td><span class="required-tag">是</span></td>
              <td>应用 ID（已自动填充）</td>
            </tr>
            <tr>
              <td><code>query</code></td>
              <td>string</td>
              <td><span class="required-tag">是</span></td>
              <td>查询问题</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Try It Out Section -->
      <div class="section-card">
        <div class="section-title try-title">
          🚀 在线调试
          <span class="try-hint">填写参数后点击发送即可测试接口</span>
        </div>

        <div class="request-form">
          <div class="form-row">
            <label class="form-label">
              <code>app_id</code>
              <span class="required-dot">*</span>
            </label>
            <input
              type="text"
              v-model="form.app_id"
              class="form-input readonly"
              readonly
              title="从 URL 自动读取"
            />
          </div>

          <div class="form-row">
            <label class="form-label">
              <code>query</code>
              <span class="required-dot">*</span>
            </label>
            <textarea
              v-model="form.query"
              class="form-textarea"
              placeholder="请输入查询问题，例如：配电线路的敷设要求"
              rows="3"
            ></textarea>
          </div>

          <div class="form-actions">
            <button
              class="send-btn"
              :disabled="sending || !form.query.trim()"
              @click="handleSend"
            >
              <span v-if="!sending">▶ Send</span>
              <span v-else class="spinner-sm"></span>
            </button>
            <button class="clear-btn" @click="clearResponse">Clear</button>
          </div>
        </div>
      </div>

      <!-- Response Section -->
      <div v-if="responseShown" class="section-card response-card">
        <div class="section-title response-title">
          📤 响应结果
          <span v-if="statusCode" class="status-badge" :class="statusClass">{{ statusCode }}</span>
          <span v-if="responseTime" class="response-time">{{ responseTime }}ms</span>
        </div>

        <div v-if="errorMsg" class="error-block">
          <div class="error-title">请求失败</div>
          <div class="error-text">{{ errorMsg }}</div>
        </div>

        <div v-else class="code-block-wrapper">
          <div class="code-header">
            <span>Response Body</span>
            <div class="header-right-actions">
              <span class="content-type">application/json</span>
              <button class="copy-btn" @click="copyResponse" title="复制 Response Body">
                <span v-if="copied">已复制</span>
                <span v-else>复制</span>
              </button>
            </div>
          </div>
          <pre class="code-content" v-html="highlightedJson"></pre>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const appId = route.params.app_id || ''
const chatUrl = computed(() => `${window.location.origin}/chat/${appId}`)
const apiUrl = `${window.location.origin}/api/report/public-query`

const form = ref({
  app_id: appId,
  query: '',
})

const sending = ref(false)
const responseShown = ref(false)
const statusCode = ref(0)
const responseTime = ref(0)
const responseData = ref(null)
const errorMsg = ref('')
const copied = ref(false)

const statusClass = computed(() => {
  if (statusCode.value >= 200 && statusCode.value < 300) return 'success'
  if (statusCode.value >= 400) return 'error'
  return ''
})

const formattedJson = computed(() => {
  if (!responseData.value) return ''
  return JSON.stringify(responseData.value, null, 2)
})

const highlightedJson = computed(() => {
  const json = formattedJson.value
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
})

const handleSend = async () => {
  if (!form.value.query.trim()) return
  sending.value = true
  responseShown.value = true
  errorMsg.value = ''
  responseData.value = null
  statusCode.value = 0
  responseTime.value = 0

  const start = performance.now()
  try {
    const res = await fetch(apiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        app_id: form.value.app_id,
        query: form.value.query.trim(),
      }),
    })
    statusCode.value = res.status
    responseTime.value = Math.round(performance.now() - start)

    const data = await res.json()
    responseData.value = data
  } catch (err) {
    responseTime.value = Math.round(performance.now() - start)
    errorMsg.value = err.message || '网络请求失败'
    statusCode.value = 0
  } finally {
    sending.value = false
  }
}

const clearResponse = () => {
  responseShown.value = false
  responseData.value = null
  errorMsg.value = ''
  statusCode.value = 0
}

const copyResponse = async () => {
  if (!formattedJson.value) return
  try {
    await navigator.clipboard.writeText(formattedJson.value)
    copied.value = true
    setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch (err) {
    console.error('Copy failed:', err)
  }
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
  gap: 20px;
}

/* Endpoint Card */
.endpoint-card {
  background: #fff;
  border-radius: 12px;
  padding: 20px 24px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  border: 1px solid #e8e8e8;
}

.endpoint-line {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.method-badge {
  font-size: 12px;
  font-weight: 800;
  padding: 4px 10px;
  border-radius: 6px;
  color: #fff;
}

.method-badge.post {
  background: #49cc90;
}

.endpoint-url {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 15px;
  color: #1a1a1a;
  word-break: break-all;
}

.endpoint-desc {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  margin: 0;
}

/* Section Card */
.section-card {
  background: #fff;
  border-radius: 12px;
  padding: 20px 24px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  border: 1px solid #e8e8e8;
}

.section-title {
  font-size: 14px;
  font-weight: 800;
  color: #1a1a1a;
  margin-bottom: 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.try-title {
  justify-content: space-between;
}

.try-hint {
  font-size: 12px;
  font-weight: 400;
  color: #909399;
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
.response-card {
  border-left: 4px solid #409eff;
}

.response-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
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
