[根目录](../CLAUDE.md) > **frontend**

## 变更记录 (Changelog)
- **2026-04-25**: 新增图谱检索测试页面 `GraphSearchView.vue`（路由 `/graph-search/:projectId`）：节点类型多选 checkbox + 名称模糊搜索，1 跳邻域动态展开，节点详情面板展示全部属性，关联节点点击高亮；`GraphBuild.vue` 新增跳转按钮；`api/graph.js` 新增 `searchNodes`、`getNodeNeighborhood`；各检索视图（AiQaView/HitTestView/PublicChatView）新增 Topic root type 支持；PublicChatView.vue 修复 canvas 渲染时序（evidence 完全渲染后再显示 report）。
- **2026-04-24**: 前端 API 死代码清理：删除 graph.js 中未使用的 searchGraph、aiQa、chatWithAgent、mineruParse、resetIntelligentChunks；Process.vue Hit-Test 检索改为 searchObjectFirst 对齐后端 `/tools/search-object-first`。
- **2026-04-12**: 新增 API 客户端、Composable 状态管理、Router 路由配置、Store 状态管理。
- **2026-03-24**: 初始扫描，识别 Vue 3 组件与 API 客户端结构。

## 模块职责
提供用户交互界面：
- 项目管理流程 (上传 -> 本体生成 -> 图谱构建 -> 仿真/问答)。
- 基于 D3.js 的图谱可视化 (`components/GraphPanel.vue`)。
- 分步向导式的构建流程 (`components/GraphBuild.vue` 等)。
- 交互式 AI 问答界面。
- 实体标签管理与统计展示。

## 入口与启动
- **入口**: `frontend/src/main.js`。
- **开发**: `npm run dev` (Vite)。
- **构建**: `npm run build`。

## 前端目录结构
```
frontend/src/
├── api/           # API 客户端 (Axios)
├── components/   # Vue 组件
├── composables/   # Vue 3 Composable 状态逻辑
├── router/        # Vue Router 路由配置
├── store/        # Pinia 状态管理
├── views/        # 页面视图
├── assets/       # 静态资源
└── App.vue      # 根组件
```

## API 客户端 (`api/`)
| 文件 | 职责 |
| :--- | :--- |
| `index.js` | Axios 基础配置与重试机制 |
| `graph.js` | 图谱相关 API 调用 |
| `ai_app.js` | AI 应用配置 API 调用 |

## 关键依赖
- **Vue 3**: 前端框架。
- **Vite**: 构建工具。
- **D3.js**: 知识图谱的可视化。
- **Axios**: HTTP 请求。
- **Pinia**: 状态管理。
- **Vue Router**: 路由管理。

## 测试与质量
- **覆盖率**: 建议引入单元测试与 E2E 测试。
- **工具**: 建议使用 `vitest` 和 `Cypress`。

## 相关文件清单
- `frontend/src/views/MainView.vue`: 应用主布局。
- `frontend/src/components/GraphPanel.vue`: 核心图谱展示组件。
- `frontend/src/components/GraphBuild.vue`: 知识图谱构建面板。
- `frontend/src/components/AiQaPanel.vue`: AI 问答交互面板。
- `frontend/src/composables/`: 可复用状态逻辑。
