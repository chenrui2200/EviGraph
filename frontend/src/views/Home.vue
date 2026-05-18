<template>
  <div class="home-container">
    <!-- Top Navigation Bar -->
    <nav class="navbar" :style="s.navbar">
      <div class="nav-brand" :style="s.navBrand">Knowledge EviGraph</div>
    </nav>

    <div class="main-content" :style="s.mainContent">
      <!-- Hero Section -->
      <section class="hero-section" :style="s.heroSection">
        <div class="hero-left" :style="s.heroLeft">
          <div class="tag-row" :style="s.tagRow">
            <span class="orange-tag" :style="s.orangeTag">知识图谱自动构建索引引擎</span>
            <span class="version-text" :style="s.versionText">/ v0.1-预览版</span>
          </div>

          <h1 class="main-title" :style="s.mainTitle">
            上传任何文档<br>
            <span class="gradient-text" :style="s.gradientText">探索您想知道的一切</span>
          </h1>

          <div class="hero-desc" :style="s.heroDesc">
            <p :style="s.heroDescP">
              通过单一文档，<span :style="s.highlightBold">Knowledge EviGraph</span> 提取现实种子并构建一个由 <span :style="s.highlightOrange">自主 AI 知识图谱构架</span> 组成的知识库。注入问题，观察涌现行为，并在复杂的知识库群中寻找 <span :style="s.highlightCode">答案</span>。
            </p>
          </div>
        </div>

        <div class="hero-right" :style="s.heroRight">
          <div class="logo-container" :style="s.logoContainer">
            <img src="../assets/logo/EviGraph_log.png" alt="Knowledge EviGraph Logo" :style="s.heroLogo" />
          </div>
          <button :style="s.scrollDownBtn" @click="scrollToBottom">↓</button>
        </div>
      </section>

      <!-- Dashboard: Two-Column Layout -->
      <section class="dashboard-section" :style="s.dashboardSection">
        <!-- Left Column: Status & Steps -->
        <div class="left-panel" :style="s.leftPanel">
          <div class="panel-header" :style="s.panelHeader">
            <span :style="{ color: systemStatus === 'ok' ? '#00FF00' : '#FF4500' }">■</span> 系统状态: {{ systemStatus === 'ok' ? '就绪' : '检查中...' }}
          </div>

          <h2 class="section-title" :style="s.sectionTitle">{{ systemStatus === 'ok' ? '准备就绪' : '正在初始化' }}</h2>
          <p class="section-desc" :style="s.sectionDesc">
            {{ systemStatus === 'ok' ? '本地预测引擎已待命。请上传非结构化数据以初始化仿真。' : '正在连接本地服务和在线模型...' }}
          </p>

          <div class="metrics-row" :style="s.metricsRow">
            <div class="metric-card" :style="s.metricCard">
              <div class="metric-value" :style="s.metricValue">{{ neo4jStatus === 'ok' ? '在线' : '离线' }}</div>
              <div class="metric-label" :style="s.metricLabel">Neo4j 数据库</div>
            </div>
            <div class="metric-card" :style="s.metricCard">
              <div class="metric-value" :style="s.metricValue">{{ embeddingProvider === 'ollama' ? '本地' : '在线' }}</div>
              <div class="metric-label" :style="s.metricLabel">嵌入模型 ({{ embeddingModel }})</div>
            </div>
            <div class="metric-card" :style="s.metricCard">
              <div class="metric-value" :style="s.metricValue">
                {{ llmStatus === 'ok' ? '就绪' : llmStatus === 'error' ? '异常' : '未知' }}
              </div>
              <div class="metric-label" :style="s.metricLabel">LLM ({{ llmModel || '未配置' }})</div>
            </div>
          </div>

          <div class="steps-container" :style="s.stepsContainer">
            <div class="steps-header" :style="s.stepsHeader">
               <span :style="s.diamondIcon">◇</span> 工作流序列
            </div>
            <div :style="s.workflowList">
              <div v-for="(step, i) in steps" :key="i" :style="s.workflowItem">
                <span :style="s.stepNum">{{ step.num }}</span>
                <div :style="s.stepInfo">
                  <div :style="s.stepTitle">{{ step.title }}</div>
                  <div :style="s.stepDesc">{{ step.desc }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Right Column: Interactive Console -->
        <div class="right-panel" :style="s.rightPanel">
          <div class="console-box" :style="s.consoleBox">
            <div :style="s.consoleSection">
              <div class="console-header" :style="s.consoleHeader">
                <span>01 / 现实种子</span>
                <span>支持格式: PDF, WORD</span>
              </div>
              <div
                :style="s.uploadZone"
                @dragover.prevent="handleDragOver"
                @dragleave.prevent="handleDragLeave"
                @drop.prevent="handleDrop"
                @click="triggerFileInput"
              >
                <input ref="fileInput" type="file" accept=".pdf,.doc,.docx" @change="handleFileSelect" style="display: none" :disabled="loading" />
                <div v-if="files.length === 0" :style="s.uploadPlaceholder">
                  <div :style="s.uploadIcon">↑</div>
                  <div :style="s.uploadTitle">将文件拖放到此处</div>
                  <div :style="s.uploadHint">或点击进行浏览</div>
                </div>
                <div v-else :style="s.fileList">
                  <div v-for="(file, index) in files" :key="index" :style="s.fileItem">
                    <span>📄</span>
                    <span :style="s.fileName">{{ file.name }}</span>
                    <button @click.stop="removeFile(index)" :style="s.removeBtn">×</button>
                  </div>
                </div>
              </div>
            </div>

            <div :style="s.consoleSection">
              <div class="console-header" :style="s.consoleHeader">
                <span>>_ 02 / 准备构建</span>
              </div>
              <div :style="s.readyStatus">
                <div :style="s.readyIcon">⚙️</div>
                <div :style="s.readyTitle">准备处理 {{ files.length }} 份文档</div>
                <div :style="s.readyDesc">Knowledge EviGraph 将提取实体、关系，并创建一个支持 PDF 位置映射的可搜索知识图谱。</div>
              </div>

            </div>

            <div :style="s.btnSection">
              <button
                :style="{
                  ...s.startEngineBtn,
                  background: (!canSubmit || loading || systemStatus !== 'ok') ? '#666' : '#FF4500',
                  cursor: (!canSubmit || loading || systemStatus !== 'ok') ? 'not-allowed' : 'pointer'
                }"
                @click="startSimulation"
                :disabled="!canSubmit || loading || systemStatus !== 'ok'"
              >
                <span v-if="loading">正在处理...</span>
                <span v-else-if="systemStatus !== 'ok'">系统未就绪 (请检查左侧状态)</span>
                <span v-else>开始构建知识库</span>
                <span>→</span>
              </button>
            </div>

            <div :style="s.consoleSection">
              <div class="console-header" :style="s.consoleHeader">
                <span>>_ 03 / KB Pipeline</span>
                <span v-if="activePipeline" :style="s.pipelineLiveBadge">● 执行中</span>
              </div>

              <div v-if="activePipeline" :style="s.pipelineLiveCard">
                <div :style="s.pipelineLiveHeader">
                  <span :style="{ ...s.pipelineLiveDot, background: pipelineStatusColor(activePipeline.status) }">●</span>
                  <span :style="s.pipelineLiveStatus">{{ pipelineStatusText(activePipeline.status) }}</span>
                </div>
                <div :style="s.pipelineLiveName">{{ activePipeline.minio_object || '未命名文档' }}</div>
                <div :style="s.pipelineLiveId">{{ activePipeline.pipeline_id }}</div>
                <button
                  :style="s.pipelineLiveBtn"
                  @click="goToPipelineTrack(activePipeline.pipeline_id)"
                >
                  <span>追踪进度</span>
                  <span>➝</span>
                </button>
              </div>

              <button
                v-else
                :style="s.aiAppBtn"
                @click="goToKbPipeline"
              >
                <span>启动知识库流水线</span>
                <span>➝</span>
              </button>
            </div>

            <div :style="s.consoleSection">
              <div class="console-header" :style="s.consoleHeader">
                <span>>_ 04 / 创建AI应用</span>
              </div>

              <button
                :style="s.aiAppBtn"
                @click="goToAiApp"
              >
                <span>创建 AI 应用</span>
                <span>➝</span>
              </button>
            </div>
          </div>
        </div>
      </section>

      <KbPipelineList />
      <ProjectList />
      <AiAppList />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import ProjectList from '../components/ProjectList.vue'
import AiAppList from '../components/AiAppList.vue'
import KbPipelineList from '../components/KbPipelineList.vue'
import { getHealth, getKbPipelineList } from '../api/graph'

const mono = 'JetBrains Mono, monospace'
const sans = 'Space Grotesk, Noto Sans SC, system-ui, sans-serif'

// System status state
const systemStatus = ref('loading')
const neo4jStatus = ref('unknown')
const embeddingProvider = ref('unknown')
const embeddingModel = ref('unknown')
const llmStatus = ref('unknown')
const llmModel = ref('unknown')
const llmProvider = ref('unknown')

const s = reactive({
  navbar: { height: '60px', background: '#000', color: '#fff', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 40px' },
  navBrand: { fontFamily: mono, fontWeight: '800', letterSpacing: '1px', fontSize: '1.2rem' },
  navLinks: { display: 'flex', alignItems: 'center' },
  githubLink: { color: '#fff', textDecoration: 'none', fontFamily: mono, fontSize: '0.9rem', fontWeight: '500', display: 'flex', alignItems: 'center', gap: '8px' },
  mainContent: { maxWidth: '1400px', margin: '0 auto', padding: '60px 40px' },
  heroSection: { display: 'flex', justifyContent: 'space-between', marginBottom: '80px', position: 'relative' },
  heroLeft: { flex: '1', paddingRight: '60px' },
  tagRow: { display: 'flex', alignItems: 'center', gap: '15px', marginBottom: '25px', fontFamily: mono, fontSize: '0.8rem' },
  orangeTag: { background: '#FF4500', color: '#fff', padding: '4px 10px', fontWeight: '700', letterSpacing: '1px', fontSize: '0.75rem' },
  versionText: { color: '#999', fontWeight: '500', letterSpacing: '0.5px' },
  mainTitle: { fontSize: '4.5rem', lineHeight: '1.2', fontWeight: '500', margin: '0 0 40px 0', letterSpacing: '-2px', color: '#000' },
  gradientText: { background: 'linear-gradient(90deg, #000 0%, #444 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', display: 'inline-block' },
  heroDesc: { fontSize: '1.05rem', lineHeight: '1.8', color: '#666', maxWidth: '640px', marginBottom: '50px', fontWeight: '400', textAlign: 'justify' },
  heroDescP: { marginBottom: '1.5rem' },
  highlightBold: { color: '#000', fontWeight: '700' },
  highlightOrange: { color: '#FF4500', fontWeight: '700', fontFamily: mono },
  highlightCode: { background: 'rgba(0,0,0,0.05)', padding: '2px 6px', borderRadius: '2px', fontFamily: mono, fontSize: '0.9em', color: '#000', fontWeight: '600' },
  sloganText: { fontSize: '1.2rem', fontWeight: '520', color: '#000', letterSpacing: '1px', borderLeft: '3px solid #FF4500', paddingLeft: '15px', marginTop: '20px' },
  blinkingCursor: { color: '#FF4500', fontWeight: '700' },
  decorationSquare: { width: '16px', height: '16px', background: '#FF4500' },
  heroRight: { flex: '0.8', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', alignItems: 'flex-end' },
  logoContainer: { width: '100%', display: 'flex', justifyContent: 'flex-end', paddingRight: '40px' },
  heroLogo: { maxWidth: '500px', width: '100%' },
  scrollDownBtn: { width: '40px', height: '40px', border: '1px solid #E5E5E5', background: 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: '#FF4500', fontSize: '1.2rem' },
  dashboardSection: { display: 'flex', gap: '60px', borderTop: '1px solid #E5E5E5', paddingTop: '60px', alignItems: 'flex-start' },
  leftPanel: { flex: '0.8', display: 'flex', flexDirection: 'column' },
  panelHeader: { fontFamily: mono, fontSize: '0.8rem', color: '#999', display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '20px' },
  statusDot: { color: '#FF4500', fontSize: '0.8rem' },
  sectionTitle: { fontSize: '2rem', fontWeight: '520', margin: '0 0 15px 0' },
  sectionDesc: { color: '#666', marginBottom: '25px', lineHeight: '1.6' },
  metricsRow: { display: 'flex', gap: '20px', marginBottom: '15px' },
  metricCard: { border: '1px solid #E5E5E5', padding: '20px 30px', minWidth: '150px' },
  metricValue: { fontFamily: mono, fontSize: '1.8rem', fontWeight: '520', marginBottom: '5px' },
  metricLabel: { fontSize: '0.85rem', color: '#999' },
  stepsContainer: { border: '1px solid #E5E5E5', padding: '30px', position: 'relative' },
  stepsHeader: { fontFamily: mono, fontSize: '0.8rem', color: '#999', marginBottom: '25px', display: 'flex', alignItems: 'center', gap: '8px' },
  diamondIcon: { fontSize: '1.2rem', lineHeight: '1' },
  workflowList: { display: 'flex', flexDirection: 'column', gap: '20px' },
  workflowItem: { display: 'flex', alignItems: 'flex-start', gap: '20px' },
  stepNum: { fontFamily: mono, fontWeight: '700', color: '#000', opacity: '0.3' },
  stepInfo: { flex: '1' },
  stepTitle: { fontWeight: '520', fontSize: '1rem', marginBottom: '4px' },
  stepDesc: { fontSize: '0.85rem', color: '#666' },
  rightPanel: { flex: '1.2', display: 'flex', flexDirection: 'column' },
  consoleBox: { border: '1px solid #CCC', padding: '8px' },
  consoleSection: { padding: '20px' },
  consoleHeader: { display: 'flex', justifyContent: 'space-between', marginBottom: '15px', fontFamily: mono, fontSize: '0.75rem', color: '#666' },
  uploadZone: { border: '1px dashed #CCC', height: '200px', overflowY: 'auto', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', background: '#FAFAFA' },
  uploadPlaceholder: { textAlign: 'center' },
  uploadIcon: { width: '40px', height: '40px', border: '1px solid #DDD', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 15px', color: '#999' },
  uploadTitle: { fontWeight: '500', fontSize: '0.9rem', marginBottom: '5px' },
  uploadHint: { fontFamily: mono, fontSize: '0.75rem', color: '#999' },
  fileList: { width: '100%', padding: '15px', display: 'flex', flexDirection: 'column', gap: '10px' },
  fileItem: { display: 'flex', alignItems: 'center', background: '#fff', padding: '8px 12px', border: '1px solid #EEE', fontFamily: mono, fontSize: '0.85rem' },
  fileName: { flex: '1', margin: '0 10px' },
  removeBtn: { background: 'none', border: 'none', cursor: 'pointer', fontSize: '1.2rem', color: '#999' },
  consoleDivider: { display: 'flex', alignItems: 'center', margin: '10px 0', borderTop: '1px solid #EEE' },
  consoleDividerText: { padding: '0 15px', fontFamily: mono, fontSize: '0.7rem', color: '#BBB', letterSpacing: '1px' },
  inputWrapper: { position: 'relative', border: '1px solid #DDD', background: '#FAFAFA' },
  codeInput: { width: '100%', border: 'none', background: 'transparent', padding: '20px', fontFamily: mono, fontSize: '0.9rem', lineHeight: '1.6', resize: 'vertical', outline: 'none', minHeight: '150px' },
  modelBadge: { position: 'absolute', bottom: '10px', right: '15px', fontFamily: mono, fontSize: '0.7rem', color: '#AAA' },
  readyStatus: { border: '1px solid #DDD', background: '#FAFAFA', padding: '30px', textAlign: 'center' },
  readyIcon: { fontSize: '2rem', marginBottom: '15px' },
  readyTitle: { fontWeight: '700', marginBottom: '8px' },
  readyDesc: { fontSize: '0.85rem', color: '#666', lineHeight: '1.5' },
  btnSection: { padding: '0 20px 20px' },
  startEngineBtn: { width: '100%', background: '#000', color: '#fff', border: 'none', padding: '20px', fontFamily: mono, fontWeight: '700', fontSize: '1.1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', letterSpacing: '1px' },
  aiAppBtn: { width: '100%', background: '#fff', color: '#000', border: '1px solid #000', padding: '16px 20px', fontFamily: mono, fontWeight: '700', fontSize: '0.9rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', letterSpacing: '0.5px', marginTop: '15px' },
  pipelineLiveBadge: { fontFamily: mono, fontSize: '0.7rem', color: '#FF4500', fontWeight: '600', animation: 'pulse 1.5s infinite' },
  pipelineLiveCard: { border: '1px solid #FF4500', background: '#FFF8F5', padding: '16px 20px', marginTop: '15px' },
  pipelineLiveHeader: { display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' },
  pipelineLiveDot: { fontSize: '0.7rem', color: '#fff', width: '8px', height: '8px', borderRadius: '50%', display: 'inline-block' },
  pipelineLiveStatus: { fontFamily: mono, fontSize: '0.8rem', fontWeight: '600', color: '#FF4500' },
  pipelineLiveName: { fontSize: '0.85rem', fontWeight: '600', color: '#000', marginBottom: '4px', wordBreak: 'break-all' },
  pipelineLiveId: { fontFamily: mono, fontSize: '0.65rem', color: '#999', marginBottom: '12px' },
  pipelineLiveBtn: { width: '100%', background: '#FF4500', color: '#fff', border: 'none', padding: '12px 16px', fontFamily: mono, fontWeight: '700', fontSize: '0.85rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', letterSpacing: '0.5px' },
})

const steps = [
  { num: '01', title: '知识库构建', desc: '通过 GraphRAG 流水线处理文档，构建高保真知识图谱。' },
  { num: '02', title: '实体提取', desc: '提取关键角色、概念及关系，并进行 PDF 坐标映射。' },
  { num: '03', title: 'Neo4j 图谱化', desc: '将非结构化数据转化为结构化节点和关系，用于复杂推理。' },
  { num: '04', title: '可追溯检索', desc: '执行深度查询，并将每一条信息追溯到原始 PDF 位置。' },
  { num: '05', title: '交互应用', desc: '使用自然语言查询知识库，获取有证据支撑的回答。' },
]

const router = useRouter()

const activePipeline = ref(null)

const isPipelineRunning = (status) => {
  if (!status) return false
  return status.endsWith('_processing') || status === 'pending'
}

const pipelineStatusText = (status) => {
  if (!status) return '未知'
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

const pipelineStatusColor = (status) => {
  if (!status) return '#999'
  if (status.endsWith('_processing')) return '#FF4500'
  if (status.endsWith('_completed') || status === 'total_completed') return '#00C853'
  if (status.endsWith('_failed')) return '#FF1744'
  return '#999'
}

const checkActivePipeline = async () => {
  try {
    const res = await getKbPipelineList(20)
    if (res.success && res.data) {
      const running = res.data.find(p => isPipelineRunning(p.status))
      if (running) {
        activePipeline.value = running
      }
    }
  } catch (e) {
    console.error('检查活跃 Pipeline 失败:', e)
  }
}

const goToAiApp = () => {
  router.push({ name: 'AiQa', params: { id: 'new' } })
}

const goToKbPipeline = () => {
  router.push({ name: 'KbPipelineLaunch' })
}

const goToPipelineTrack = (pipelineId) => {
  router.push({ name: 'KbPipelineTrack', params: { pipelineId } })
}

const formData = ref({ simulationRequirement: '' })
const files = ref([])
const loading = ref(false)
const error = ref('')
const isDragOver = ref(false)
const fileInput = ref(null)

const canSubmit = computed(() => {
  return files.value.length > 0
})

const triggerFileInput = () => { if (!loading.value) fileInput.value?.click() }
const handleFileSelect = (event) => { addFiles(Array.from(event.target.files)) }
const handleDragOver = (e) => { isDragOver.value = true }
const handleDragLeave = (e) => { isDragOver.value = false }
const handleDrop = (e) => { isDragOver.value = false; addFiles(Array.from(e.dataTransfer.files)) }

const addFiles = (newFiles) => {
  const allowed = ['.pdf', '.doc', '.docx']
  const valid = newFiles.filter(f => allowed.some(ext => f.name.toLowerCase().endsWith(ext)))
  // 只保留第一个有效文件
  if (valid.length > 0) {
    files.value = [valid[0]]
  }
}

const removeFile = (index) => { files.value.splice(index, 1) }

const checkSystemStatus = async () => {
  try {
    const res = await getHealth()
    if (res.status === 'ok') {
      systemStatus.value = 'ok'
      neo4jStatus.value = res.dependencies.neo4j.status
      embeddingProvider.value = res.dependencies.embedding.provider
      embeddingModel.value = res.dependencies.embedding.model
      if (res.dependencies.llm) {
        llmStatus.value = res.dependencies.llm.status
        llmModel.value = res.dependencies.llm.model
        llmProvider.value = res.dependencies.llm.provider
      }
    }
  } catch (err) {
    console.error('Failed to check system status:', err)
    systemStatus.value = 'error'
  }
}

onMounted(() => {
  checkSystemStatus()
  checkActivePipeline()
})

const scrollToBottom = () => { window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }) }

const startSimulation = () => {
  if (!canSubmit.value || loading.value) return
  import('../store/pendingUpload.js').then(({ setPendingUpload }) => {
    setPendingUpload(files.value, formData.value.simulationRequirement)
    router.push({ name: 'ChunkAnalysis', params: { projectId: 'new' } })
  })
}

</script>

<!-- Styles loaded from Home.css via import -->
