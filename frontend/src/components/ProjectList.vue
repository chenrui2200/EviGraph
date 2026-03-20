<template>
  <div class="project-list-container">
    <!-- Title section -->
    <div class="section-header">
      <div class="section-line"></div>
      <span class="section-title">Knowledge Base Projects</span>
      <div class="section-line"></div>
    </div>

    <!-- Loading state -->
    <div v-if="loading" class="loading-state">
      <span class="loading-spinner"></span>
      <span class="loading-text">Loading projects...</span>
    </div>

    <!-- Project list -->
    <div v-else-if="projects.length > 0" class="projects-grid">
      <div
        v-for="project in projects"
        :key="project.project_id"
        class="project-card"
        :class="getStatusClass(project.status)"
        @click="navigateToProject(project)"
      >
        <!-- Card header -->
        <div class="card-header">
          <span class="project-id">{{ formatProjectId(project.project_id) }}</span>
          <span class="status-badge" :class="getStatusClass(project.status)">
            {{ formatStatus(project.status) }}
          </span>
        </div>

        <!-- Project name -->
        <h3 class="project-name">{{ project.name || 'Unnamed Project' }}</h3>

        <!-- Project info -->
        <div class="project-info">
          <div class="info-row" v-if="project.ontology">
            <span class="info-label">Entities:</span>
            <span class="info-value">{{ project.ontology.entity_types?.length || 0 }}</span>
          </div>
          <div class="info-row" v-if="project.graph_id">
            <span class="info-label">Graph ID:</span>
            <span class="info-value code">{{ project.graph_id.slice(0, 12) }}...</span>
          </div>
          <div class="info-row">
            <span class="info-label">Created:</span>
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
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getProjectList } from '../api/graph'

const router = useRouter()
const projects = ref([])
const loading = ref(true)

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

// Navigate to project page
const navigateToProject = (project) => {
  router.push({
    name: 'Process',
    params: { projectId: project.project_id }
  })
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
    'created': 'Created',
    'ontology_generated': 'Ontology Ready',
    'graph_building': 'Building',
    'graph_completed': 'Completed',
    'failed': 'Failed'
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
  cursor: pointer;
  transition: all 0.2s ease;
}

.project-card:hover {
  border-color: #000000;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
  transform: translateY(-2px);
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

.project-id {
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

/* Project name */
.project-name {
  font-family: 'Inter', sans-serif;
  font-size: 0.95rem;
  font-weight: 600;
  color: #111827;
  margin: 0 0 12px 0;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
