<template>
  <div class="step-navigator">
    <!-- 面包屑导航 -->
    <div class="breadcrumb">
      <span class="breadcrumb-brand" @click="router.push('/')">
        知识库
      </span>
      <span class="breadcrumb-sep">/</span>

      <!-- Step 1: 智能 Chunk -->
      <div
        class="breadcrumb-step"
        :class="{
          'step-active': currentStep === 1,
          'step-done': isStep1Done,
          'step-disabled': !canNavigateToStep1
        }"
        @click="navigateTo(1)"
        :title="step1Tip"
      >
        <span class="step-indicator">
          <span v-if="isStep1Done" class="step-check">✓</span>
          <span v-else class="step-num">1</span>
        </span>
        <span class="step-label">智能Chunk</span>
        <span v-if="!isStep1Done" class="step-lock">🔒</span>
      </div>

      <span class="breadcrumb-sep">/</span>

      <!-- Step 2: 图谱构建 -->
      <div
        class="breadcrumb-step"
        :class="{
          'step-active': currentStep === 2,
          'step-done': isStep2Done,
          'step-disabled': !canNavigateToStep2
        }"
        @click="navigateTo(2)"
        :title="step2Tip"
      >
        <span class="step-indicator">
          <span v-if="isStep2Done" class="step-check">✓</span>
          <span v-else class="step-num">2</span>
        </span>
        <span class="step-label">图谱构建</span>
        <span v-if="!isStep2Done" class="step-lock">🔒</span>
      </div>
    </div>

    <!-- 状态提示 -->
    <div v-if="tipMessage" class="step-tip">
      {{ tipMessage }}
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  projectId: { type: String, required: true },
  projectStatus: { type: String, default: '' },
  currentStep: { type: Number, default: 0 } // 0=列表, 1=chunk, 2=graph
})

const emit = defineEmits(['navigate'])

const router = useRouter()

// Step 1 (智能Chunk) 是否完成
// 完成条件: graph_chunked 及之后的所有状态
const isStep1Done = computed(() => {
  const doneStatuses = [
    'graph_chunked',
    'ontology_generated',
    'graph_building',
    'graph_chunking',
    'graph_embedding',
    'graph_indexing',
    'graph_completed',
    'failed'
  ]
  return doneStatuses.includes(props.projectStatus)
})

// Step 2 (图谱构建) 是否完成
const isStep2Done = computed(() => {
  return props.projectStatus === 'graph_completed'
})

// 是否可以跳转到 Step 1
const canNavigateToStep1 = computed(() => {
  // 项目存在就可以进入 Step 1
  return !!props.projectId && props.projectId !== 'new'
})

// 是否可以跳转到 Step 2
const canNavigateToStep2 = computed(() => {
  // Step 1 完成后才能进入 Step 2
  return isStep1Done.value
})

// Step 1 提示
const step1Tip = computed(() => {
  if (!canNavigateToStep1.value) return '项目加载中...'
  if (isStep1Done.value) return '智能Chunk标注已完成，点击查看'
  return '点击进入智能Chunk标注'
})

// Step 2 提示
const step2Tip = computed(() => {
  if (!canNavigateToStep2.value) return '请先完成智能Chunk标注'
  if (isStep2Done.value) return '图谱构建已完成，点击查看'
  return '点击进入图谱构建'
})

// 当前提示消息
const tipMessage = computed(() => {
  if (props.currentStep === 0) {
    // 在列表页，根据状态显示提示
    if (props.projectStatus === 'graph_completed') return '✓ 项目已完成，可随时查看'
    if (isStep1Done.value) return '→ 请继续 Step 2 图谱构建'
    if (props.projectStatus) return '→ 请先完成 Step 1 智能Chunk'
  }
  return ''
})

const navigateTo = (step) => {
  if (step === 1) {
    if (!canNavigateToStep1.value) return
    emit('navigate', { step: 1, route: { name: 'ChunkAnalysis', params: { projectId: props.projectId } } })
    router.push({ name: 'ChunkAnalysis', params: { projectId: props.projectId } })
  } else if (step === 2) {
    if (!canNavigateToStep2.value) {
      alert('请先完成 Step 1 智能Chunk 标注，再进入图谱构建。')
      return
    }
    emit('navigate', { step: 2, route: { name: 'Process', params: { projectId: props.projectId } } })
    router.push({ name: 'Process', params: { projectId: props.projectId } })
  }
}
</script>

<style scoped>
.step-navigator {
  margin-bottom: 20px;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: 'JetBrains Mono', 'SF Mono', monospace;
  font-size: 0.8rem;
}

.breadcrumb-brand {
  color: #9CA3AF;
  cursor: pointer;
  transition: color 0.2s;
}

.breadcrumb-brand:hover {
  color: #1a1a2e;
}

.breadcrumb-sep {
  color: #D1D5DB;
}

.breadcrumb-step {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 6px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.2s;
  color: #6B7280;
  background: transparent;
}

.breadcrumb-step:hover:not(.step-disabled) {
  background: #F3F4F6;
  color: #1a1a2e;
}

.breadcrumb-step.step-active {
  background: #EFF6FF;
  border-color: #BFDBFE;
  color: #2563EB;
  font-weight: 600;
}

.breadcrumb-step.step-done {
  color: #16A34A;
}

.breadcrumb-step.step-done:hover {
  background: #F0FDF4;
  color: #16A34A;
}

.breadcrumb-step.step-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.step-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  font-size: 0.7rem;
  font-weight: 700;
  flex-shrink: 0;
}

.step-num {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1.5px solid currentColor;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.65rem;
}

.step-check {
  color: #16A34A;
  font-weight: 700;
}

.step-label {
  font-size: 0.8rem;
}

.step-lock {
  font-size: 0.65rem;
  opacity: 0.6;
}

.step-tip {
  margin-top: 6px;
  font-size: 0.75rem;
  color: #9CA3AF;
  font-family: 'JetBrains Mono', monospace;
  padding-left: 2px;
}
</style>
