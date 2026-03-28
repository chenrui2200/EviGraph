<template>
  <div class="project-list-container">
    <!-- Title section -->
    <div class="section-header">
      <div class="section-line"></div>
      <span class="section-title">知识库项目列表</span>
      <div class="section-line"></div>
    </div>

    <!-- Loading state -->
    <div v-if="loading" class="loading-state">
      <span class="loading-spinner"></span>
      <span class="loading-text">正在加载项目...</span>
    </div>

    <!-- Project list -->
    <div v-else-if="projects.length > 0" class="projects-grid">
      <div
        v-for="project in projects"
        :key="project.project_id"
        class="project-card"
        :class="getStatusClass(project.status)"
      >
        <!-- Step Navigator -->
        <StepNavigator
          :projectId="project.project_id"
          :projectStatus="project.status"
          :currentStep="0"
        />
        <!-- Card header -->
        <div class="card-header">
          <span class="project-id">{{ formatProjectId(project.project_id) }}</span>
          <div class="card-actions">
            <span class="status-badge" :class="getStatusClass(project.status)">
              {{ formatStatus(project.status) }}
            </span>
            <span
              v-if="project.referencing_apps?.length > 0"
              class="constraint-hint"
              title="该知识库被 AI 应用使用"
            >
              ⚠️
            </span>
            <button
              class="delete-btn"
              :class="{ 'constrained': project.referencing_apps?.length > 0 }"
              @click.stop="confirmDelete($event, project)"
              :title="project.referencing_apps?.length > 0 ? '该知识库被 AI 应用使用，无法删除' : '删除项目'"
            >
              🗑️
            </button>
          </div>
        </div>

        <!-- Project name -->
        <div class="project-name-container" @click.stop>
          <div v-if="editingProjectId === project.project_id" class="edit-name-form">
            <input
              v-model="editingName"
              ref="nameInput"
              class="edit-name-input"
              @keyup.enter="saveProjectName($event, project)"
              @keyup.esc="cancelEdit($event)"
              @blur="cancelEdit($event)"
            />
          </div>
          <h3 v-else class="project-name" @click.stop="toggleEdit($event, project)">
            {{ project.name || 'Unnamed Project' }}
            <span class="edit-icon">✏️</span>
          </h3>
        </div>

        <!-- Project info -->
        <div class="project-info">
          <div class="info-row" v-if="project.ontology">
            <span class="info-label">实体类型:</span>
            <span class="info-value">{{ project.ontology.entity_types?.length || 0 }}</span>
          </div>
          <div class="info-row" v-if="project.graph_id">
            <span class="info-label">图谱 ID:</span>
            <span class="info-value code">{{ project.graph_id.slice(0, 12) }}...</span>
          </div>
          <div class="info-row">
            <span class="info-label">创建日期:</span>
            <span class="info-value">{{ formatDate(project.created_at) }}</span>
          </div>
        </div>

        <!-- Error message if failed -->
        <div v-if="project.status === 'failed' && project.error" class="error-message">
          {{ truncateText(project.error, 60) }}
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else class="empty-state">
      <span class="empty-icon">◇</span>
      <span class="empty-text">No projects yet</span>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { getProjectList, updateProject, deleteProject } from '../api/graph'
import StepNavigator from './StepNavigator.vue'

const projects = ref([])
const loading = ref(true)

// Edit State
const editingProjectId = ref(null)
const editingName = ref('')
const nameInput = ref(null)

const toggleEdit = (event, project) => {
  event.stopPropagation()
  editingProjectId.value = project.project_id
  editingName.value = project.name || ''
  nextTick(() => {
    if (nameInput.value && nameInput.value[0]) {
      nameInput.value[0].focus()
    }
  })
}

const saveProjectName = async (event, project) => {
  event.stopPropagation()
  if (!editingName.value || editingName.value === project.name) {
    editingProjectId.value = null
    return
  }

  try {
    const response = await updateProject(project.project_id, { name: editingName.value })
    if (response.success) {
      project.name = editingName.value
    }
  } catch (err) {
    console.error('Failed to update project name:', err)
  } finally {
    editingProjectId.value = null
  }
}

const cancelEdit = (event) => {
  if (event) event.stopPropagation()
  // Add delay to allow enter key to trigger save
  setTimeout(() => {
    editingProjectId.value = null
  }, 100)
}

// Delete project
const confirmDelete = async (event, project) => {
  event.stopPropagation()

  const appNames = project.referencing_apps?.map(a => a.name).join(', ')

  let message = `确定要删除项目 "${project.name || project.project_id}" 吗？`
  if (appNames) {
    message = `无法删除！该项目正被以下 AI 应用使用：\n${appNames}\n\n请先在 AI 应用中移除该知识库的关联。`
    alert(message)
    return
  }
  message += '\n\n此操作不可恢复。'

  if (!confirm(message)) {
    return
  }

  try {
    const response = await deleteProject(project.project_id)
    if (response.success) {
      // Remove from list
      projects.value = projects.value.filter(p => p.project_id !== project.project_id)
    } else {
      // Check if there are referencing apps
      if (response.referencing_apps && response.referencing_apps.length > 0) {
        const names = response.referencing_apps.map(a => a.name).join(', ')
        alert(`无法删除！该项目正被以下 AI 应用使用：\n${names}\n\n请先在 AI 应用中移除该知识库的关联。`)
      } else {
        alert(`删除失败: ${response.error || '未知错误'}`)
      }
    }
  } catch (err) {
    console.error('Failed to delete project:', err)
    alert('删除失败，请重试')
  }
}

// Load project list
const loadProjects = async () => {
  try {
    loading.value = true
    const response = await getProjectList(20)
    if (response.success) {
      projects.value = response.data || []
    }
  } catch (error) {
    console.error('Failed to load projects:', error)
    projects.value = []
  } finally {
    loading.value = false
  }
}

// Format project ID
const formatProjectId = (projectId) => {
  if (!projectId) return 'PROJ_UNKNOWN'
  const prefix = projectId.replace('proj_', '').slice(0, 8)
  return `PROJ_${prefix.toUpperCase()}`
}

// Format status
const formatStatus = (status) => {
  const statusMap = {
    'created': '已创建',
    'ontology_generated': '本体已就绪',
    'graph_building': '图谱构建中',
    'graph_completed': '构建完成',
    'failed': '失败'
  }
  return statusMap[status] || status
}

// Get status class
const getStatusClass = (status) => {
  return `status-${status?.replace('_', '-')}`
}

// Format date
const formatDate = (dateStr) => {
  if (!dateStr) return ''
  try {
    const date = new Date(dateStr)
    return date.toISOString().slice(0, 10)
  } catch {
    return dateStr?.slice(0, 10) || ''
  }
}

// Truncate text
const truncateText = (text, maxLength) => {
  if (!text) return ''
  return text.length > maxLength ? text.slice(0, maxLength) + '...' : text
}

onMounted(() => {
  loadProjects()
})
</script>

<style scoped>
.project-list-container {
  position: relative;
  width: 100%;
  margin-top: 60px;
  padding: 35px 40px 40px;
}

/* Title section */
.section-header {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
  margin-bottom: 32px;
  font-family: 'JetBrains Mono', 'SF Mono', monospace;
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

/* Loading state */
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 48px;
  color: #9CA3AF;
}

.loading-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid #E5E7EB;
  border-top-color: #6B7280;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.loading-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
}

/* Projects grid */
.projects-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 20px;
}

/* Project card */
.project-card {
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 8px;
  padding: 16px;
  transition: all 0.2s ease;
}

.project-card:hover {
  border-color: #D1D5DB;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.06);
}

/* Status-based border colors */
.project-card.status-graph-completed {
  border-left: 3px solid #10B981;
}

.project-card.status-graph-building {
  border-left: 3px solid #F59E0B;
}

.project-card.status-ontology-generated {
  border-left: 3px solid #3B82F6;
}

.project-card.status-failed {
  border-left: 3px solid #EF4444;
}

/* Card header */
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #F3F4F6;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.project-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: #6B7280;
  letter-spacing: 0.5px;
  font-weight: 500;
}

.delete-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px 6px;
  font-size: 0.9rem;
  opacity: 0.5;
  transition: opacity 0.2s, transform 0.2s;
  border-radius: 4px;
}

.delete-btn:hover {
  opacity: 1;
  transform: scale(1.1);
  background: #FEE2E2;
}

.delete-btn.constrained {
  cursor: not-allowed;
  opacity: 0.3;
}

.delete-btn.constrained:hover {
  opacity: 0.3;
  transform: none;
  background: none;
}

.constraint-hint {
  font-size: 0.9rem;
  cursor: help;
}

.status-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.65rem;
  padding: 3px 8px;
  border-radius: 3px;
  font-weight: 600;
  letter-spacing: 0.3px;
}

.status-badge.status-graph-completed {
  background: rgba(16, 185, 129, 0.1);
  color: #10B981;
}

.status-badge.status-graph-building {
  background: rgba(245, 158, 11, 0.1);
  color: #F59E0B;
}

.status-badge.status-ontology-generated {
  background: rgba(59, 130, 246, 0.1);
  color: #3B82F6;
}

.status-badge.status-failed {
  background: rgba(239, 68, 68, 0.1);
  color: #EF4444;
}

.status-badge.status-created {
  background: #F3F4F6;
  color: #9CA3AF;
}

.project-name {
  font-family: 'Inter', sans-serif;
  font-size: 0.95rem;
  font-weight: 600;
  color: #111827;
  margin: 0;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  position: relative;
  padding-right: 24px;
}

.edit-icon {
  opacity: 0;
  font-size: 0.8rem;
  transition: opacity 0.2s;
  position: absolute;
  right: 0;
}

.project-name:hover .edit-icon {
  opacity: 1;
}

.project-name-container {
  margin-bottom: 12px;
}

.edit-name-form {
  width: 100%;
}

.edit-name-input {
  width: 100%;
  padding: 4px 8px;
  font-size: 0.95rem;
  font-weight: 600;
  border: 1px solid #000;
  border-radius: 4px;
  outline: none;
  font-family: inherit;
}

/* Project info */
.project-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.75rem;
}

.info-label {
  color: #9CA3AF;
  font-family: 'JetBrains Mono', monospace;
}

.info-value {
  color: #4B5563;
  font-family: 'Inter', sans-serif;
}

.info-value.code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
}

/* Error message */
.error-message {
  margin-top: 12px;
  padding: 8px;
  background: rgba(239, 68, 68, 0.05);
  border: 1px solid rgba(239, 68, 68, 0.2);
  border-radius: 4px;
  font-size: 0.7rem;
  color: #EF4444;
  line-height: 1.4;
}

/* Empty state */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 48px;
  color: #9CA3AF;
}

.empty-icon {
  font-size: 2rem;
  opacity: 0.5;
}

.empty-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  letter-spacing: 0.5px;
}

/* Responsive */
@media (max-width: 768px) {
  .projects-grid {
    grid-template-columns: 1fr;
  }
}
</style>
