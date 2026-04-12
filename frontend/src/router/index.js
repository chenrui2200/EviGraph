import { createRouter, createWebHistory } from 'vue-router'
import Home from '../views/Home.vue'
import GraphBuild from '../views/MainView.vue'
import AiQaView from '../views/AiQaView.vue'
import HitTestView from '../views/HitTestView.vue'
import PublicChatView from '../views/PublicChatView.vue'
import ChunkAnalysisView from '../views/ChunkAnalysisView.vue'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: Home
  },
  {
    path: '/chat/:id',
    name: 'PublicChat',
    component: PublicChatView,
    props: true
  },
  {
    path: '/graph_build/:projectId',
    name: 'GraphBuild',
    component: GraphBuild,
    props: true
  },
  {
    path: '/hit-test/:projectId',
    name: 'HitTest',
    component: HitTestView,
    props: true
  },
  {
    path: '/chunk-analysis/:projectId',
    name: 'ChunkAnalysis',
    component: ChunkAnalysisView,
    props: true
  },
  {
    path: '/ai-qa/:id',
    name: 'AiQa',
    component: AiQaView,
    props: true
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
