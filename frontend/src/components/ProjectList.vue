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

    <!-- Project table -->
    <div v-else-if="projects.length > 0" class="table-wrapper">
      <table class="data-table">
        <thead>
          <tr>
            <th>项目 ID</th>
            <th>项目名称</th>
            <th>状态</th>
            <th>实体类型</th>
            <th>图谱 ID</th>
            <th>创建日期</th>
            <th style="width: 80px;">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="project in projects"
            :key="project.project_id"
            :class="getStatusClass(project.status)"
          >
            <td>
              <span class="id-badge">{{ formatProjectId(project.project_id) }}</span>
            </td>
            <td>
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
              <div v-else class="name-cell" @click="toggleEdit($event, project)">
                <StepNavigator
                  :projectId="project.project_id"
                  :projectStatus="project.status"
                  :currentStep="0"
                />
                <span class="project-name">{{ project.name || 'Unnamed Project' }}</span>
                <span class="edit-icon">✏️</span>
              </div>
            </td>
            <td>
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
            </td>
            <td>{{ project.ontology?.entity_types?.length || 0 }}</td>
            <td>
              <code v-if="project.graph_id" class="code-value">{{ project.graph_id.slice(0, 12) }}...</code>
              <span v-else class="empty-value">-</span>
            </td>
            <td>{{ formatDate(project.created_at) }}</td>
            <td>
              <button
                class="delete-btn"
                :class="{ 'constrained': project.referencing_apps?.length > 0 }"
                @click.stop="confirmDelete($event, project)"
                :title="project.referencing_apps?.length > 0 ? '该知识库被 AI 应用使用，无法删除' : '删除项目'"
              >
                🗑️
              </button>
            </td>
          </tr>
        </tbody>
      </table>
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
      projects.value = projects.value.filter(p => p.project_id !== project.project_id)
    } else {
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
      projects.value = response.data?.projects || []
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
  return projectId
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

/* Table */
.table-wrapper {
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  border-radius: 12px;
  overflow: hidden;
  overflow-x: auto;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.data-table thead {
  background: #F9FAFB;
  border-bottom: 1px solid #E5E7EB;
}

.data-table th {
  padding: 12px 16px;
  text-align: left;
  font-weight: 600;
  color: #6B7280;
  font-size: 0.75rem;
  letter-spacing: 0.3px;
  text-transform: uppercase;
  font-family: 'JetBrains Mono', monospace;
  white-space: nowrap;
}

.data-table td {
  padding: 14px 16px;
  border-bottom: 1px solid #F3F4F6;
  color: #374151;
  vertical-align: middle;
}

.data-table tbody tr:hover {
  background: #F9FAFB;
}

.data-table tbody tr:last-child td {
  border-bottom: none;
}

/* Status-based left border */
.data-table tbody tr.status-graph-completed {
  border-left: 3px solid #10B981;
}
.data-table tbody tr.status-graph-building {
  border-left: 3px solid #F59E0B;
}
.data-table tbody tr.status-ontology-generated {
  border-left: 3px solid #3B82F6;
}
.data-table tbody tr.status-failed {
  border-left: 3px solid #EF4444;
}

/* ID badge */
.id-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: #6B7280;
  font-weight: 500;
  letter-spacing: 0.5px;
}

/* Name cell */
.name-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}

.project-name {
  font-weight: 600;
  color: #111827;
}

.edit-icon {
  opacity: 0;
  font-size: 0.75rem;
  transition: opacity 0.2s;
}

.name-cell:hover .edit-icon {
  opacity: 1;
}

/* Edit input */
.edit-name-input {
  width: 100%;
  max-width: 240px;
  padding: 4px 8px;
  font-size: 0.85rem;
  font-weight: 600;
  border: 1px solid #000;
  border-radius: 4px;
  outline: none;
  font-family: inherit;
}

/* Status badge */
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

.constraint-hint {
  font-size: 0.9rem;
  cursor: help;
  margin-left: 6px;
}

/* Code value */
.code-value {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: #6B7280;
  background: #F3F4F6;
  padding: 2px 6px;
  border-radius: 4px;
}

.empty-value {
  color: #D1D5DB;
}

/* Delete button */
.delete-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 6px;
  font-size: 0.9rem;
  opacity: 0.5;
  transition: opacity 0.2s, transform 0.2s;
  border-radius: 6px;
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

/* Empty state */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 48px;
  color: #9CA3AF;
  background: #FFFFFF;
  border: 1px dashed #E5E7EB;
  border-radius: 12px;
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
@media (max-width: 900px) {
  .data-table th,
  .data-table td {
    padding: 10px 12px;
    font-size: 0.8rem;
  }
}
</style>
