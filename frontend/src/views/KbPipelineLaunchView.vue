<template>
  <div class="kb-pipeline-launch">
    <header class="kpl-header">
      <div class="header-left">
        <div class="brand" @click="router.push('/')">
          <span class="brand-icon">◈</span>
          <span>Knowledge EviGraph</span>
        </div>
      </div>
      <div class="header-right">
        <span class="page-title">KB Pipeline 启动台</span>
      </div>
    </header>

    <main class="kpl-main">
      <div class="kpl-hero">
        <h1 class="kpl-headline">知识库构建流水线</h1>
        <p class="kpl-subtitle">选择 MinIO 中的 PDF 文档，一键启动全自动知识抽取与图谱构建</p>
      </div>

      <div class="kpl-card">
        <div class="card-header">
          <div class="card-icon">📦</div>
          <div>
            <h2 class="kpl-title">选择文档</h2>
            <p class="kpl-desc">从已上传的 MinIO 文件中选择一份 PDF 启动流水线</p>
          </div>
        </div>

        <div v-if="loading" class="kpl-loading">
          <div class="spinner"></div>
          <span>正在加载文件列表...</span>
        </div>

        <div v-else-if="files.length === 0" class="kpl-empty">
          <div class="empty-icon">📭</div>
          <div class="empty-title">暂无 PDF 文件</div>
          <div class="empty-desc">请检查 MinIO 配置与存储桶内容后刷新页面</div>
        </div>

        <div v-else class="file-list">
          <div
            v-for="file in files"
            :key="file.name"
            class="file-item"
            :class="{ selected: selectedFile === file.name }"
            @click="selectedFile = file.name"
          >
            <div class="file-radio">
              <div class="radio-inner"></div>
            </div>
            <div class="file-icon">📄</div>
            <div class="file-info">
              <div class="file-name">{{ file.name }}</div>
              <div class="file-meta">
                <span class="meta-badge size">{{ formatSize(file.size) }}</span>
                <span class="meta-badge time">{{ formatTime(file.last_modified) }}</span>
              </div>
            </div>
            <div v-if="selectedFile === file.name" class="file-check">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            </div>
          </div>
        </div>

        <div class="kpl-actions">
          <button
            class="btn-start"
            :disabled="!selectedFile || starting"
            @click="handleStart"
          >
            <span v-if="starting" class="btn-spinner"></span>
            <span v-else class="btn-icon">🚀</span>
            <span>{{ starting ? '启动中...' : '启动流水线' }}</span>
          </button>
        </div>
      </div>
    </main>

    <div class="bg-blobs">
      <div class="blob blob-1"></div>
      <div class="blob blob-2"></div>
      <div class="blob blob-3"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getKbPipelineMinioFiles, startKbPipeline } from '../api/graph'

const router = useRouter()
const files = ref([])
const loading = ref(false)
const selectedFile = ref(null)
const starting = ref(false)

const fetchFiles = async () => {
  loading.value = true
  try {
    const res = await getKbPipelineMinioFiles()
    files.value = res.data || []
  } catch (e) {
    console.error('获取 MinIO 文件失败:', e)
    files.value = []
  } finally {
    loading.value = false
  }
}

const formatSize = (bytes) => {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

const formatTime = (iso) => {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleString()
}

const handleStart = async () => {
  if (!selectedFile.value || starting.value) return
  starting.value = true
  try {
    const res = await startKbPipeline(selectedFile.value)
    const pipelineId = res.data?.pipeline_id
    if (pipelineId) {
      router.push({ name: 'KbPipelineTrack', params: { pipelineId } })
    }
  } catch (e) {
    console.error('启动流水线失败:', e)
    alert('启动失败: ' + (e.message || '未知错误'))
  } finally {
    starting.value = false
  }
}

onMounted(() => {
  fetchFiles()
})
</script>

<style scoped>
.kb-pipeline-launch {
  min-height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #e4e8f0 50%, #f0f4f8 100%);
  position: relative;
  overflow-x: hidden;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.bg-blobs {
  position: fixed;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
  z-index: 0;
}
.blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.45;
}
.blob-1 {
  width: 500px;
  height: 500px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  top: -150px;
  right: -150px;
  animation: float 8s ease-in-out infinite;
}
.blob-2 {
  width: 400px;
  height: 400px;
  background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
  bottom: -100px;
  left: -100px;
  animation: float 10s ease-in-out infinite reverse;
}
.blob-3 {
  width: 300px;
  height: 300px;
  background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  animation: float 12s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33% { transform: translate(30px, -30px) scale(1.05); }
  66% { transform: translate(-20px, 20px) scale(0.95); }
}

.kpl-header {
  position: relative;
  z-index: 10;
  height: 72px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.6);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 48px;
}
.header-left .brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 1.15rem;
  color: #1a1a2e;
  cursor: pointer;
  transition: opacity 0.2s;
}
.header-left .brand:hover { opacity: 0.8; }
.brand-icon {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  font-size: 1.3rem;
}
.header-right .page-title {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.9rem;
  color: #4a5568;
  font-weight: 500;
  letter-spacing: 0.5px;
}

.kpl-main {
  position: relative;
  z-index: 5;
  max-width: 880px;
  margin: 0 auto;
  padding: 48px 24px 80px;
}

.kpl-hero {
  text-align: center;
  margin-bottom: 36px;
}
.kpl-headline {
  font-size: 2.4rem;
  font-weight: 800;
  margin: 0 0 12px;
  background: linear-gradient(135deg, #1a1a2e 0%, #4a5568 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  letter-spacing: -0.5px;
}
.kpl-subtitle {
  font-size: 1.05rem;
  color: #718096;
  max-width: 520px;
  margin: 0 auto;
  line-height: 1.6;
}

.kpl-card {
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-radius: 24px;
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow:
    0 4px 6px -1px rgba(0, 0, 0, 0.05),
    0 20px 40px -10px rgba(0, 0, 0, 0.08),
    inset 0 1px 0 rgba(255, 255, 255, 0.6);
  padding: 40px;
  transition: transform 0.3s ease, box-shadow 0.3s ease;
}
.kpl-card:hover {
  box-shadow:
    0 4px 6px -1px rgba(0, 0, 0, 0.05),
    0 24px 48px -12px rgba(0, 0, 0, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.6);
}

.card-header {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 28px;
}
.card-icon {
  width: 52px;
  height: 52px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.6rem;
  box-shadow: 0 8px 20px rgba(102, 126, 234, 0.3);
}
.kpl-title {
  font-size: 1.35rem;
  font-weight: 700;
  margin: 0 0 6px;
  color: #1a202c;
}
.kpl-desc {
  color: #718096;
  margin: 0;
  font-size: 0.95rem;
}

.kpl-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 56px 0;
  color: #718096;
  font-size: 0.95rem;
}
.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid #e2e8f0;
  border-top-color: #667eea;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.kpl-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 56px 24px;
  background: #f8fafc;
  border-radius: 16px;
  border: 2px dashed #e2e8f0;
}
.empty-icon {
  font-size: 3rem;
  margin-bottom: 12px;
  opacity: 0.7;
}
.empty-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: #4a5568;
  margin-bottom: 6px;
}
.empty-desc {
  font-size: 0.9rem;
  color: #a0aec0;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 32px;
}
.file-item {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  background: #fff;
  border: 2px solid #edf2f7;
  border-radius: 16px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.file-item:hover {
  border-color: #cbd5e0;
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.06);
}
.file-item.selected {
  border-color: #667eea;
  background: linear-gradient(135deg, rgba(102,126,234,0.06) 0%, rgba(118,75,162,0.06) 100%);
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.15);
}

.file-radio {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid #cbd5e0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  flex-shrink: 0;
}
.file-item.selected .file-radio {
  border-color: #667eea;
  background: #667eea;
}
.radio-inner {
  width: 8px;
  height: 8px;
  background: #fff;
  border-radius: 50%;
  transform: scale(0);
  transition: transform 0.2s;
}
.file-item.selected .radio-inner {
  transform: scale(1);
}

.file-icon {
  font-size: 1.5rem;
  flex-shrink: 0;
}
.file-info {
  flex: 1;
  min-width: 0;
}
.file-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.92rem;
  font-weight: 500;
  color: #2d3748;
  margin-bottom: 6px;
  word-break: break-all;
}
.file-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.meta-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: 0.75rem;
  font-weight: 500;
}
.meta-badge.size {
  background: #edf2f7;
  color: #4a5568;
}
.meta-badge.time {
  background: #e6fffa;
  color: #2c7a7b;
}

.file-check {
  width: 28px;
  height: 28px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
  animation: popIn 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}
.file-check svg {
  width: 14px;
  height: 14px;
}
@keyframes popIn {
  from { transform: scale(0); }
  to { transform: scale(1); }
}

.kpl-actions {
  display: flex;
  justify-content: flex-end;
}
.btn-start {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  border: none;
  padding: 14px 32px;
  font-size: 1rem;
  font-weight: 600;
  border-radius: 14px;
  cursor: pointer;
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.35);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.btn-start:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(102, 126, 234, 0.45);
}
.btn-start:active:not(:disabled) {
  transform: translateY(0);
}
.btn-start:disabled {
  background: #a0aec0;
  box-shadow: none;
  cursor: not-allowed;
}
.btn-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}
.btn-icon {
  font-size: 1.1rem;
}

@media (max-width: 640px) {
  .kpl-header { padding: 0 24px; height: 64px; }
  .kpl-main { padding: 28px 16px 48px; }
  .kpl-headline { font-size: 1.8rem; }
  .kpl-card { padding: 24px; }
  .card-icon { width: 44px; height: 44px; font-size: 1.3rem; }
  .file-item { padding: 14px 16px; }
  .btn-start { width: 100%; justify-content: center; }
}
</style>
