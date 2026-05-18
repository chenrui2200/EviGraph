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
    <div v-else-if="pipelines.length > 0" class="pipelines-table-wrapper">
      <table class="pipelines-table">
        <thead>
          <tr>
            <th class="col-id">Pipeline ID</th>
            <th class="col-name">文档名称</th>
            <th class="col-status">状态</th>
            <th class="col-progress">阶段进度</th>
            <th class="col-time">创建时间</th>
            <th class="col-action">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="pipeline in pipelines"
            :key="pipeline.pipeline_id"
            class="pipeline-row"
            :class="'status-' + statusCategory(pipeline.status)"
            @click="goToTrack(pipeline)"
          >
            <td class="col-id">
              <span class="pipeline-id">{{ formatPipelineId(pipeline.pipeline_id) }}</span>
            </td>
            <td class="col-name">
              <span class="pipeline-name">{{ pipeline.minio_object || '未命名文档' }}</span>
            </td>
            <td class="col-status">
              <span class="status-badge" :class="'status-' + statusCategory(pipeline.status)">
                {{ formatStatus(pipeline.status) }}
              </span>
            </td>
            <td class="col-progress">
              <div class="progress-cell">
                <span class="progress-text">{{ computeProgress(pipeline) }}%</span>
                <div class="progress-bar">
                  <div class="progress-fill" :style="{ width: computeProgress(pipeline) + '%', background: progressColor(statusCategory(pipeline.status)) }"></div>
                </div>
              </div>
            </td>
            <td class="col-time">{{ formatDate(pipeline.created_at) }}</td>
            <td class="col-action">
              <div class="action-btns">
                <button class="track-btn" @click.stop="goToTrack(pipeline)">
                  {{ actionLabel(pipeline.status) }}
                </button>
                <button class="delete-btn" @click.stop="handleDelete(pipeline)">
                  删除
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
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
import { getKbPipelineList, deleteKbPipeline } from '../api/graph'

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

const handleDelete = async (pipeline) => {
  const confirmed = window.confirm(
    `确定要删除 Pipeline ${formatPipelineId(pipeline.pipeline_id)} 吗？\n\n仅删除 Pipeline 自身记录，不会删除关联的项目和应用。`
  )
  if (!confirmed) return
  try {
    const res = await deleteKbPipeline(pipeline.pipeline_id)
    if (res.success) {
      pipelines.value = pipelines.value.filter(p => p.pipeline_id !== pipeline.pipeline_id)
    } else {
      alert('删除失败: ' + (res.error || '未知错误'))
    }
  } catch (e) {
    console.error('删除 Pipeline 失败:', e)
    alert('删除失败: ' + (e.message || '未知错误'))
  }
}

const statusCategory = (status) => {
  if (!status) return 'pending'
  if (status.endsWith('_processing')) return 'processing'
  if (status.endsWith('_completed') || status === 'total_completed') return 'completed'
  if (status.endsWith('_failed')) return 'failed'
  return 'pending'
}

const computeProgress = (pipeline) => {
  const stages = pipeline.stages || []
  if (!stages.length) return 0
  const completed = stages.filter(s => {
    const st = s.status || ''
    return st.endsWith('_completed') || st === 'total_completed'
  }).length
  return Math.round((completed / stages.length) * 100)
}

const progressColor = (category) => {
  const map = {
    pending: '#999',
    processing: '#FF4500',
    completed: '#00C853',
    failed: '#FF1744'
  }
  return map[category] || '#999'
}

const formatStatus = (status) => {
  const map = {
    pending: '等待中',
    total_completed: '全部完成',
    project_creation_processing: '创建项目中',
    project_creation_completed: '创建项目完成',
    project_creation_failed: '创建项目失败',
    mineru_annotation_processing: 'PDF解析中',
    mineru_annotation_completed: 'PDF解析完成',
    mineru_annotation_failed: 'PDF解析失败',
    chapter_analysis_processing: '章节分析中',
    chapter_analysis_completed: '章节分析完成',
    chapter_analysis_failed: '章节分析失败',
    intelligent_analysis_processing: '智能分析中',
    intelligent_analysis_completed: '智能分析完成',
    intelligent_analysis_failed: '智能分析失败',
    graph_building_processing: '图谱构建中',
    graph_building_completed: '图谱构建完成',
    graph_building_failed: '图谱构建失败',
    app_creation_processing: '创建应用中',
    app_creation_completed: '创建应用完成',
    app_creation_failed: '创建应用失败'
  }
  return map[status] || status
}

const actionLabel = (status) => {
  const cat = statusCategory(status)
  if (cat === 'completed') return '查看结果'
  if (cat === 'failed') return '查看详情'
  return '追踪进度'
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

.pipelines-table-wrapper {
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 12px;
  overflow: hidden;
  overflow-x: auto;
}

.pipelines-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.pipelines-table thead {
  background: #F9FAFB;
  border-bottom: 1px solid #E5E7EB;
}

.pipelines-table th {
  padding: 12px 16px;
  text-align: left;
  font-weight: 600;
  color: #374151;
  font-size: 12px;
  letter-spacing: 0.3px;
  white-space: nowrap;
}

.pipelines-table td {
  padding: 14px 16px;
  border-bottom: 1px solid #F3F4F6;
  vertical-align: middle;
}

.pipeline-row {
  cursor: pointer;
  transition: background 0.15s ease;
}

.pipeline-row:hover {
  background: #FEF7F4;
}

.pipeline-row.status-completed {
  border-left: 3px solid #00C853;
}

.pipeline-row.status-processing {
  border-left: 3px solid #FF4500;
}

.pipeline-row.status-failed {
  border-left: 3px solid #FF1744;
}

.pipeline-row.status-pending {
  border-left: 3px solid #9CA3AF;
}

.col-id { width: 120px; }
.col-name { min-width: 200px; }
.col-status { width: 120px; }
.col-progress { width: 160px; }
.col-time { width: 150px; white-space: nowrap; }
.col-action { width: 100px; text-align: center; }

.action-btns {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
}

.pipeline-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: #6B7280;
  letter-spacing: 0.5px;
  font-weight: 500;
}

.pipeline-name {
  font-weight: 600;
  color: #111827;
  word-break: break-all;
}

.status-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.65rem;
  padding: 3px 8px;
  border-radius: 3px;
  font-weight: 600;
  letter-spacing: 0.3px;
  white-space: nowrap;
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

.status-badge.status-pending {
  background: #F3F4F6;
  color: #9CA3AF;
}

.progress-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.progress-text {
  font-size: 12px;
  font-weight: 600;
  color: #374151;
  min-width: 32px;
  text-align: right;
}

.progress-bar {
  flex: 1;
  height: 6px;
  background: #F3F4F6;
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  transition: width 0.4s ease;
}

.track-btn {
  background: #000;
  color: #fff;
  border: none;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
  white-space: nowrap;
}

.track-btn:hover {
  background: #333;
}

.delete-btn {
  background: transparent;
  color: #FF1744;
  border: 1px solid #FF1744;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.delete-btn:hover {
  background: #FF1744;
  color: #fff;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
</style>
