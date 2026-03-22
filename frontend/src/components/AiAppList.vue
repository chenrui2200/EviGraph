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

    <!-- App list -->
    <div v-else-if="apps.length > 0" class="apps-grid">
      <div
        v-for="app in apps"
        :key="app.app_id"
        class="app-card"
        @click="navigateToApp(app)"
      >
        <div class="card-header">
          <span class="app-icon">🤖</span>
          <span class="app-date">{{ formatDate(app.created_at) }}</span>
        </div>

        <h3 class="app-name">{{ app.name }}</h3>

        <div class="app-info">
          <div class="info-row">
            <span class="info-label">关联知识库:</span>
            <span class="info-value">{{ app.workflow_data?.selectedGraphIds?.length || 0 }} 个</span>
          </div>
          <div class="info-row">
            <span class="info-label">工作流节点:</span>
            <span class="info-value">{{ app.nodes?.length || 0 }} 个</span>
          </div>
        </div>

        <div class="card-footer">
          <button class="edit-btn" @click.stop="navigateToApp(app)">进入工作流 ➝</button>
          <button class="delete-btn" @click.stop="confirmDelete(app)">×</button>
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else class="empty-state">
      <span class="empty-icon">📂</span>
      <p class="empty-text">暂无保存的 AI 应用</p>
      <p class="empty-hint">在项目构建完成后点击“创建 AI 知识库应用”即可开始</p>
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
    params: { projectId: app.project_id || 'default' },
    query: { appId: app.app_id }
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

.apps-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 20px;
}

.app-card {
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

.app-card:hover {
  border-color: #FF4500;
  box-shadow: 0 10px 20px rgba(0,0,0,0.05);
  transform: translateY(-4px);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.app-icon {
  font-size: 24px;
}

.app-date {
  font-size: 10px;
  color: #999;
  font-family: 'JetBrains Mono', monospace;
}

.app-name {
  font-size: 1.1rem;
  font-weight: 700;
  margin: 0 0 15px 0;
  color: #000;
}

.app-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 20px;
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

.card-footer {
  display: flex;
  gap: 10px;
}

.edit-btn {
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
}

.edit-btn:hover {
  background: #333;
}

.delete-btn {
  width: 36px;
  height: 36px;
  background: #fff;
  border: 1px solid #E5E7EB;
  border-radius: 6px;
  color: #999;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
}

.delete-btn:hover {
  background: #FEE2E2;
  color: #EF4444;
  border-color: #FECACA;
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
}

@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
</style>
