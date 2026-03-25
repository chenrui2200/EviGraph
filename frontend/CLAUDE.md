[根目录](../CLAUDE.md) > **frontend**

## 变更记录 (Changelog)
- **2026-03-24**: 初始扫描，识别 Vue 3 组件与 API 客户端结构。

## 模块职责
提供用户交互界面：
- 项目管理流程 (上传 -> 本体生成 -> 图谱构建 -> 仿真/问答)。
- 基于 D3.js 的图谱可视化 (`components/GraphPanel.vue`)。
- 分步向导式的构建流程 (`components/Step1GraphBuild.vue` 等)。
- 交互式 AI 问答界面。

## 入口与启动
- **入口**: `frontend/src/main.js`。
- **开发**: `npm run dev` (Vite)。
- **构建**: `npm run build`。

## 对外接口 (API Client)
- `frontend/src/api/index.js`: Axios 基础配置与重试机制。
- `frontend/src/api/graph.js`: 图谱相关 API 调用。
- `frontend/src/api/simulation.js`: 仿真相关 API 调用。

## 关键依赖
- **Vue 3**: 前端框架。
- **Vite**: 构建工具。
- **D3.js**: 知识图谱的可视化。
- **Axios**: HTTP 请求。

## 测试与质量
- **覆盖率**: 目前缺少单元测试与 E2E 测试。
- **工具**: 建议使用 `vitest` 和 `Cypress`。

## 相关文件清单
- `frontend/src/views/MainView.vue`: 应用主布局。
- `frontend/src/components/GraphPanel.vue`: 核心图谱展示组件。
- `frontend/src/components/Step1GraphBuild.vue` ~ `Step5Interaction.vue`: 流程化构建步骤。
