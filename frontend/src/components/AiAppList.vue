<template>
  <div class="ai-app-list-container">
    <!-- Title section -->
    <div class="section-header">
      <div class="section-line"></div>
      <span class="section-title">AI 知识库应用</span>
      <div class="section-line"></div>
    </div>

    <!-- Loading state -->
    <div v-if="loading" class="loading-state">
      <span class="loading-spinner"></span>
      <span class="loading-text">加载应用中...</span>
    </div>

    <!-- App table -->
    <div v-else-if="apps.length > 0" class="table-wrapper">
      <table class="data-table">
        <thead>
          <tr>
            <th>App ID</th>
            <th>应用名称</th>
            <th>关联知识库</th>
            <th>工作流节点</th>
            <th>创建日期</th>
            <th style="width: 140px;">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="app in apps"
            :key="app.app_id"
            @click="navigateToApp(app)"
            class="app-row"
          >
            <td>
              <code class="id-badge">{{ app.app_id }}</code>
            </td>
            <td>
              <span class="app-name">{{ app.name }}</span>
            </td>
            <td>
              <span class="count-badge">{{ app.workflow_data?.selectedGraphIds?.length || 0 }}</span>
            </td>
            <td>
              <span class="count-badge">{{ app.nodes?.length || 0 }}</span>
            </td>
            <td>{{ formatDate(app.created_at) }}</td>
            <td>
              <div class="action-btns" @click.stop>
                <button class="enter-btn" @click="navigateToApp(app)">进入工作流 ➝</button>
                <button class="delete-btn" @click="confirmDelete(app)">×</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Empty state -->
    <div v-else class="empty-state">
      <span class="empty-icon">📂</span>
      <p class="empty-text">暂无保存的 AI 应用</p>
      <p class="empty-hint">在项目构建完成后点击"创建 AI 知识库应用"即可开始</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getAppList, deleteApp } from '../api/ai_app'

const router = useRouter()
const apps = ref([])
const loading = ref(true)

const loadApps = async () => {
  try {
    loading.value = true
    const res = await getAppList()
    if (res.success) {
      apps.value = res.data || []
    }
  } catch (err) {
    console.error('Failed to load AI apps:', err)
  } finally {
    loading.value = false
  }
}

const navigateToApp = (app) => {
  router.push({
    name: 'AiQa',
    params: { id: app.app_id }
  })
}

const confirmDelete = async (app) => {
  if (confirm(`确定要删除应用 "${app.name}" 吗？`)) {
    try {
      const res = await deleteApp(app.app_id)
      if (res.success) {
        apps.value = apps.value.filter(a => a.app_id !== app.app_id)
      }
    } catch (err) {
      alert('删除失败')
    }
  }
}

const formatDate = (dateStr) => {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

onMounted(() => {
  loadApps()
})
</script>

<style scoped>
.ai-app-list-container {
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

/* App row clickable */
.app-row {
  cursor: pointer;
  transition: background 0.15s;
}

/* ID badge */
.id-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: #6B7280;
  background: #F3F4F6;
  padding: 2px 6px;
  border-radius: 4px;
}

/* App name */
.app-name {
  font-weight: 600;
  color: #111827;
}

/* Count badge */
.count-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  font-weight: 600;
  color: #6B7280;
  background: #F3F4F6;
  padding: 2px 8px;
  border-radius: 10px;
}

/* Action buttons */
.action-btns {
  display: flex;
  align-items: center;
  gap: 8px;
}

.enter-btn {
  background: #000;
  color: #fff;
  border: none;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 0.75rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
  font-family: 'JetBrains Mono', monospace;
}

.enter-btn:hover {
  background: #333;
}

.delete-btn {
  width: 32px;
  height: 32px;
  background: #fff;
  border: 1px solid #E5E7EB;
  border-radius: 6px;
  color: #999;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  transition: all 0.2s;
}

.delete-btn:hover {
  background: #FEE2E2;
  color: #EF4444;
  border-color: #FECACA;
}

/* Empty state */
.empty-state {
  text-align: center;
  padding: 60px;
  color: #999;
  background: #fff;
  border-radius: 12px;
  border: 1px dashed #E5E7EB;
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
