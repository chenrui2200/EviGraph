<template>
  <div class="kb-pipeline-list-container">
    <!-- Title section -->
    <div class="section-header">
      <div class="section-line"></div>
      <span class="section-title">KB Pipeline 流水线</span>
      <div class="section-line"></div>
    </div>

    <!-- Loading state -->
    <div v-if="loading" class="loading-state">
      <span class="loading-spinner"></span>
      <span class="loading-text">加载流水线...</span>
    </div>

    <!-- Pipeline list -->
    <div v-else-if="pipelines.length > 0" class="pipelines-grid">
      <div
        v-for="pipeline in pipelines"
        :key="pipeline.pipeline_id"
        class="pipeline-card"
        :class="'status-' + pipeline.status"
        @click="goToTrack(pipeline)"
      >
        <div class="card-header">
          <span class="pipeline-id">{{ formatPipelineId(pipeline.pipeline_id) }}</span>
          <span class="status-badge" :class="'status-' + pipeline.status">
            {{ formatStatus(pipeline.status) }}
          </span>
        </div>

        <h3 class="pipeline-name">{{ pipeline.minio_object || '未命名文档' }}</h3>

        <div class="pipeline-info">
          <div class="info-row">
            <span class="info-label">阶段进度:</span>
            <span class="info-value">{{ computeProgress(pipeline) }}%</span>
          </div>
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: computeProgress(pipeline) + '%', background: progressColor(pipeline.status) }"></div>
          </div>
          <div class="info-row">
            <span class="info-label">创建时间:</span>
            <span class="info-value">{{ formatDate(pipeline.created_at) }}</span>
          </div>
          <div class="info-row" v-if="pipeline.project_id">
            <span class="info-label">项目 ID:</span>
            <span class="info-value code">{{ formatId(pipeline.project_id) }}</span>
          </div>
        </div>

        <div v-if="pipeline.status === 'failed' && pipeline.error" class="error-message">
          {{ truncateText(pipeline.error, 60) }}
        </div>

        <div class="card-footer">
          <button class="track-btn" @click.stop="goToTrack(pipeline)">
            {{ pipeline.status === 'completed' ? '查看结果' : pipeline.status === 'failed' ? '查看详情' : '追踪进度' }}
            <span>➝</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else class="empty-state">
      <span class="empty-icon">📭</span>
      <p class="empty-text">暂无 KB Pipeline 记录</p>
      <p class="empty-hint">点击下方按钮启动你的第一条流水线</p>
      <button class="launch-btn" @click="goToLaunch">
        <span>🚀</span>
        <span>启动流水线</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getKbPipelineList } from '../api/graph'

const router = useRouter()
const pipelines = ref([])
const loading = ref(true)

const loadPipelines = async () => {
  try {
    loading.value = true
    const res = await getKbPipelineList(20)
    if (res.success) {
      pipelines.value = res.data || []
    }
  } catch (err) {
    console.error('Failed to load KB pipelines:', err)
    pipelines.value = []
  } finally {
    loading.value = false
  }
}

const goToTrack = (pipeline) => {
  router.push({
    name: 'KbPipelineTrack',
    params: { pipelineId: pipeline.pipeline_id }
  })
}

const goToLaunch = () => {
  router.push({ name: 'KbPipelineLaunch' })
}

const computeProgress = (pipeline) => {
  const stages = pipeline.stages || []
  if (!stages.length) return 0
  const completed = stages.filter(s => s.status === 'completed').length
  return Math.round((completed / stages.length) * 100)
}

const progressColor = (status) => {
  const map = {
    pending: '#999',
    processing: '#FF4500',
    completed: '#00C853',
    failed: '#FF1744',
    skipped: '#9CA3AF'
  }
  return map[status] || '#999'
}

const formatStatus = (status) => {
  const map = {
    pending: '等待中',
    processing: '执行中',
    completed: '已完成',
    failed: '失败',
    skipped: '已跳过'
  }
  return map[status] || status
}

const formatPipelineId = (id) => {
  if (!id) return 'PIPE_UNKNOWN'
  return id.slice(0, 14).toUpperCase()
}

const formatId = (id) => {
  if (!id) return '-'
  return id.slice(0, 12) + '...'
}

const formatDate = (dateStr) => {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

const truncateText = (text, maxLength) => {
  if (!text) return ''
  return text.length > maxLength ? text.slice(0, maxLength) + '...' : text
}

onMounted(() => {
  loadPipelines()
})
</script>

<style scoped>
.kb-pipeline-list-container {
  margin-top: 60px;
  padding: 0 40px 40px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
  margin-bottom: 32px;
}

.section-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(90deg, transparent, #E5E7EB, transparent);
  max-width: 300px;
}

.section-title {
  font-size: 0.8rem;
  font-weight: 500;
  color: #9CA3AF;
  letter-spacing: 3px;
  text-transform: uppercase;
}

.loading-state, .empty-state {
  text-align: center;
  padding: 60px;
  color: #999;
  background: #fff;
  border-radius: 12px;
  border: 1px dashed #E5E7EB;
}

.loading-spinner {
  width: 30px;
  height: 30px;
  border: 3px solid #f3f3f3;
  border-top: 3px solid #000;
  border-radius: 50%;
  display: inline-block;
  animation: spin 1s linear infinite;
  margin-bottom: 15px;
}

.empty-icon {
  font-size: 40px;
  margin-bottom: 15px;
  display: block;
}

.empty-text {
  font-weight: 600;
  color: #333;
  margin-bottom: 5px;
}

.empty-hint {
  font-size: 12px;
  margin-bottom: 16px;
}

.launch-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: #000;
  color: #fff;
  border: none;
  padding: 10px 20px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}

.launch-btn:hover {
  background: #333;
}

.pipelines-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 20px;
}

.pipeline-card {
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 12px;
  padding: 20px;
  cursor: pointer;
  transition: all 0.2s ease;
  display: flex;
  flex-direction: column;
  position: relative;
}

.pipeline-card:hover {
  border-color: #FF4500;
  box-shadow: 0 10px 20px rgba(0,0,0,0.05);
  transform: translateY(-4px);
}

.pipeline-card.status-completed {
  border-left: 3px solid #00C853;
}

.pipeline-card.status-processing {
  border-left: 3px solid #FF4500;
}

.pipeline-card.status-failed {
  border-left: 3px solid #FF1744;
}

.pipeline-card.status-pending {
  border-left: 3px solid #9CA3AF;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.pipeline-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: #6B7280;
  letter-spacing: 0.5px;
  font-weight: 500;
}

.status-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.65rem;
  padding: 3px 8px;
  border-radius: 3px;
  font-weight: 600;
  letter-spacing: 0.3px;
}

.status-badge.status-completed {
  background: rgba(0, 200, 83, 0.1);
  color: #00C853;
}

.status-badge.status-processing {
  background: rgba(255, 69, 0, 0.1);
  color: #FF4500;
}

.status-badge.status-failed {
  background: rgba(255, 23, 68, 0.1);
  color: #FF1744;
}

.status-badge.status-pending,
.status-badge.status-skipped {
  background: #F3F4F6;
  color: #9CA3AF;
}

.pipeline-name {
  font-size: 1rem;
  font-weight: 700;
  margin: 0 0 15px 0;
  color: #000;
  word-break: break-all;
}

.pipeline-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
}

.info-label {
  color: #666;
}

.info-value {
  font-weight: 600;
  color: #333;
}

.info-value.code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
}

.progress-bar {
  width: 100%;
  height: 6px;
  background: #F3F4F6;
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  transition: width 0.4s ease;
}

.error-message {
  margin-bottom: 12px;
  padding: 8px;
  background: rgba(255, 23, 68, 0.05);
  border: 1px solid rgba(255, 23, 68, 0.2);
  border-radius: 4px;
  font-size: 0.75rem;
  color: #FF1744;
  line-height: 1.4;
}

.card-footer {
  display: flex;
  gap: 10px;
}

.track-btn {
  flex: 1;
  background: #000;
  color: #fff;
  border: none;
  padding: 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.track-btn:hover {
  background: #333;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
</style>
