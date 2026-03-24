import { createRouter, createWebHistory } from 'vue-router'
import Home from '../views/Home.vue'
import Process from '../views/MainView.vue'
import SimulationView from '../views/SimulationView.vue'
import SimulationRunView from '../views/SimulationRunView.vue'
import ReportView from '../views/ReportView.vue'
import InteractionView from '../views/InteractionView.vue'
import AiQaView from '../views/AiQaView.vue'
import HitTestView from '../views/HitTestView.vue'
import PublicChatView from '../views/PublicChatView.vue'

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
    path: '/process/:projectId',
    name: 'Process',
    component: Process,
    props: true
  },
  {
    path: '/hit-test/:projectId',
    name: 'HitTest',
    component: HitTestView,
    props: true
  },
  {
    path: '/ai-qa/:id',
    name: 'AiQa',
    component: AiQaView,
    props: true
  },
  {
    path: '/simulation/:simulationId',
    name: 'Simulation',
    component: SimulationView,
    props: true
  },
  {
    path: '/simulation/:simulationId/start',
    name: 'SimulationRun',
    component: SimulationRunView,
    props: true
  },
  // Report功能暂时隐藏
  // {
  //   path: '/report/:reportId',
  //   name: 'Report',
  //   component: ReportView,
  //   props: true
  // },
  // {
  //   path: '/interaction/:reportId',
  //   name: 'Interaction',
  //   component: InteractionView,
  //   props: true
  // }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
