# 智能 Chunk 分析页面设计文档

## 一、设计定位

智能 Chunk 分析页面承担三项核心职责：

1. **PDF 文档可视化浏览** — 分页渲染原始 PDF，支持 MinerU 布局标注和 LLM 语义标注两种视图。
2. **多层次知识抽取与编辑** — 将规范文档解构为章节（Section）、条款（Clause）、术语（Term）、条件（Condition）、动作（Action）、实体（Component）六类知识单元，以语义三元组（Semantic Triplet）描述条款逻辑。
3. **Chunk 分析全流程管理** — 提供分析启动、实时日志、进度追踪、章节树浏览、条款实体编辑的完整交互闭环。

---

## 二、布局结构

```
┌─────────────────────────────────────────────────────────────────┐
│                         Header (60px)                           │
│  [Logo]  知识库  /  智能Chunk  /  图谱构建   [状态指示]  [进度条] │
├───────────────────────────────┬─────────────────────────────────┤
│                               │                                 │
│      PDF 阅读器 (左侧 ~48%)    │     分析面板 (右侧 ~52%)        │
│                               │                                 │
│  ┌─────────────────────────┐  │  ┌─────────────────────────┐    │
│  │  工具栏: 文件名 | MinerU │  │  │  MinerU 结果面板        │    │
│  │         切换 | 页码     │  │  │  (仅 MinerU 模式下显示)  │    │
│  └─────────────────────────┘  │  └─────────────────────────┘    │
│                               │                                 │
│  ┌─────────────────────────┐  │  ┌─────────────────────────┐    │
│  │                         │  │  │  统计卡片 (4列)          │    │
│  │    PDF 页面渲染区域      │  │  │  Sections / Clauses /   │    │
│  │    (Canvas + SVG BBox)   │  │  │  Elements / 需求分布图   │    │
│  │                         │  │  └─────────────────────────┘    │
│  │                         │  │                                 │
│  │                         │  │  ┌─────────────────────────┐    │
│  │                         │  │  │  章节树 (可展开/折叠)    │    │
│  │                         │  │  │  ├─ Chapter 3.1 条款数   │    │
│  └─────────────────────────┘  │  │  │  │  ├─ Clause 3.1.1     │    │
│                               │  │  │  │  │  ├─ Terms        │    │
│  ┌─────────────────────────┐  │  │  │  │  ├─ Conditions    │    │
│  │  颜色图例 (随模式变化)   │  │  │  │  │  ├─ Actions      │    │
│  └─────────────────────────┘  │  │  │  │  └─ Components   │    │
│                               │  │  │  └─ Clause 3.1.2     │    │
│                               │  │  └─────────────────────────┘    │
│                               │                                 │
│                               │  ┌─────────────────────────┐    │
│                               │  │  知识实体池 (底部 Tab)   │    │
│                               │  │  All / Terms / Conds /   │    │
│                               │  │       Acts / Comps       │    │
│                               │  └─────────────────────────┘    │
├───────────────────────────────┴─────────────────────────────────┤
│  实时日志抽屉 (可折叠, 底部)                                     │
│  [ SSE 实时推送: 错误(红) / 成功(绿) / 进度(蓝) ]               │
└─────────────────────────────────────────────────────────────────┘

[ 浮动蒙层: 开始分析选择界面 (项目初始化后首次显示) ]
[ 浮动按钮: 进入图谱构建 (分析完成后显示在 Header) ]
```

---

## 三、分阶段交互流程

### 阶段一：项目初始化与 PDF 就绪（页面进入时）

**目标**：完成项目创建（若为新建）、加载 PDF 文档、切换至 MinerU 初览视图，为后续智能分析做准备。

| 步骤 | 交互 | 后端动作 | 前端状态变化 |
|------|------|----------|-------------|
| 1.1  | 从 Home 页跳转进入 `/chunk-analysis/:projectId` | — | 显示 loading 蒙层 |
| 1.2  | 检测 URL 中 `projectId` 为 `new` | — | 调用 `generateOntology()` 上传文件，`POST /api/graph/ontology/generate` |
| 1.3  | Ontology 生成完成 | 创建 `project.json`，状态置为 `ontology_generated`；MinerU 解析 PDF，存入 `chunks.json` | — |
| 1.4  | 加载 MinerU 分块结果 | `GET /api/graph/pdf/mineru-parse/:projectId` | 显示 MinerU 结果面板（标题/正文/表格/图片块统计） |
| 1.5  | 渲染 PDF 页面 | — | PDF.js 将每一页渲染到 `<canvas>`，SVG 层叠加 MinerU BBox 标注 |
| 1.6  | 显示开始分析蒙层 | — | 全屏浮动卡片："开始智能分析" 按钮 / "重置并重新分析" 按钮 |
| 1.7  | 用户点击 PDF 页面 | — | 高亮对应的 MinerU 布局块，右侧详情面板显示块内容、viewport bbox、pdf bbox |

**MinerU 结果面板 UI**：
- 顶部摘要徽章：标题块数 / 正文块数 / 表格块数 / 图片块数
- 滚动分块列表：每个分块显示类型徽章（Title/Text/Table/Image）、页码、预览文字
- 点击分块：右侧详情展示完整内容 + 坐标信息
- MinerU 模式下 PDF 标注颜色：标题=深蓝 / 正文=浅蓝 / 表格=橙色 / 图片=紫色

---

### 阶段二：LLM 驱动的智能 Chunk 分析（点击"开始智能分析"后）

**目标**：通过 LLM 驱动的 4 步流水线，对每个章节执行条款提取、语义三元组抽取、技术元素识别，最终生成 `intelligent_chunks.json`。

| 步骤 | 交互 | 后端动作 | 前端状态变化 |
|------|------|----------|-------------|
| 2.1  | 点击"开始智能分析" | `POST /api/graph/chunk/intelligent` `{project_id, reset: false}` | 关闭蒙层，Header 状态变为 `graph_chunking`，进度条出现 |
| 2.2  | 启动后台线程分析 | `POST /api/graph/chunk/intelligent` 立即返回 `{task_id}` | SSE 连接 `/api/graph/task/:taskId/events`，日志抽屉自动展开 |
| 2.3  | 实时日志推送 | SSE 流式推送各章节处理日志（成功/失败/重试/进度） | 日志抽屉实时追加消息，颜色编码：成功=绿 / 失败=红 / 信息=蓝 |
| 2.4  | 轮询进度 | 每 3s 调用 `GET /api/graph/chunk/:projectId/progress` | 进度条更新，显示当前处理章节 / 总章节数 |
| 2.5  | LLM Step 1：抽取目录 | 从 PDF 文本提取目录结构和章节位置 | 日志："[LLM] 正在提取目录结构..." |
| 2.6  | LLM Step 2：精化位置 | 通过文本搜索精化每个章节的起始/终止位置 | 日志："[Refine] 精化章节位置..." |
| 2.7  | LLM Step 3：条款 + 三元组抽取 | **Per-Chapter**：对每个章节调用 LLM，提取 Clause 列表 + `(Component, Action, Object)` 三元组 + `(Condition, Requirement)` 标签 | 日志："[Chapter N] 正在抽取条款..." |
| 2.8  | LLM Step 4：技术元素抽取 | 提取 Term（带定义）、Formula、Parameter、Table Row 等 | 日志："[Chapter N] 正在抽取技术元素..." |
| 2.9  | 章节 Checkpoint 保存 | 每完成一章，追加写入 `chunk_checkpoint_v2.json` | — |
| 2.10 | PDF 坐标标注 | 根据条款文本内容在 PDF 中定位，生成 viewport_bbox 和 pdf_bbox | 日志："[BBox] 正在标注条款位置..." |
| 2.11 | 整本完成 | 保存 `intelligent_chunks.json`，项目状态置为 `graph_chunked` | 进度条 100%，日志抽屉显示完成消息，Header 状态变为完成，显示"进入图谱构建"按钮 |
| 2.12 | 加载分析结果 | `GET /api/graph/chunk/:projectId/analysis` | 分析面板切换：隐藏 MinerU 面板 → 显示统计卡片 + 章节树 + 实体池；PDF 标注切换为 LLM 语义视图 |

**LLM 语义标注颜色（Clause 视图）**：
- 条款（Clause）= 蓝色
- 术语（Term）= 绿色
- 公式（Formula）= 橙色
- 参数（Parameter）= 紫色
- 组件（Component）= 粉色
- 条件（Condition）= 青色

**进度条 UI**：
- Header 区域横条，宽度随进度百分比动态变化
- 颜色渐变：蓝色（处理中）→ 绿色（完成）
- 内部文字：`正在分析第 N/M 章...`

**实时日志抽屉 UI**：
- 固定在页面底部，可折叠/展开
- 日志行带时间戳前缀 + 颜色编码
- 自动滚动到底部（可手动上滑暂停）
- 右上角有关闭按钮

---

### 阶段三：分析结果浏览与知识实体编辑（分析完成后）

**目标**：提供章节树浏览、条款详情查看、知识实体编辑与保存的完整交互，最终将编辑结果同步到 Neo4j 图谱。

| 步骤 | 交互 | 后端动作 | 前端状态变化 |
|------|------|----------|-------------|
| 3.1  | 展开章节树 | — | 显示章节列表，每个章节显示编号、标题、条款数量 |
| 3.2  | 展开章节 | — | 显示该章节下所有条款卡片 |
| 3.3  | 展开条款 | — | 显示条款详情：条款 ID、标题、需求类型（M/R/P 徽章）、完整内容段落 |
| 3.4  | 查看语义三元组 | — | 显示 `Component ─M/R/P──> Object @ Condition` 格式的三元组列表 |
| 3.5  | 展开条款子实体 | — | 展开 Terms / Conditions / Actions / Components 四类子实体，每类可折叠 |
| 3.6  | 高亮关联 | 点击条款卡片 | 右侧 PDF 高亮对应条款的 BBox 标注（蓝色边框 + 填充），滚动 PDF 到对应页面 |
| 3.7  | 编辑条款实体 | 修改 Terms/Conditions/Actions/Components 输入框 | 保存按钮变为可用状态 |
| 3.8  | 保存实体编辑 | `PATCH /api/graph/chunk/:projectId/entity` `{clause_id, terms, conditions, actions, components}` | 按钮变为 loading → 恢复；实体更新到 `intelligent_chunks.json` |
| 3.9  | 点击知识实体池中的标签 | — | 过滤显示关联条款，相关条款高亮；再次点击取消过滤 |
| 3.10 | 实体池 Tab 切换 | — | All / Terms / Conds / Acts / Comps 五个 Tab，切换过滤显示 |
| 3.11 | 点击"重新分析" | `POST /api/graph/chunk/intelligent` `{project_id, reset: true}` | 重新执行阶段二，进度条重置 |
| 3.12 | 点击"进入图谱构建" | — | 路由跳转到 `/graph_build/:projectId`（MainView），将编辑后的实体同步写入 Neo4j |

**条款卡片 UI**：
- 顶部：条款编号（大号） + 条款标题
- 需求类型徽章：M（红色 Mandatory）/ R（橙色 Recommended）/ P（灰色 Prohibited）
- 主体：条款内容（可展开更多段落）
- 底部：语义三元组展示 + 子实体折叠区

**统计卡片 UI**（4 列）：
- 第一卡：章节总数（带书页图标）
- 第二卡：条款总数（带列表图标）
- 第三卡：元素总数（带元件图标）
- 第四卡：**需求分布横向条形图**，显示 M / R / P 三类数量比例

**知识实体池 UI**：
- 底部固定 Tab 栏，高度约 80px
- 每个 Tab 显示该类实体数量徽章
- 实体以标签（Tag）形式展示，点击高亮关联条款
- 支持多选高亮（Shift+点击）

**条款详情展开项**：
- **Terms**：术语名 + 定义，定义可展开显示完整说明
- **Conditions**：前提条件文本
- **Actions**：规范动作描述
- **Components**：涉及的技术组件/设备名

---

## 四、关键交互细节

### 4.1 MinerU / LLM 视图切换

```javascript
// mineruMode 状态控制
const mineruMode = ref(true) // true=MinerU布局视图, false=LLM语义视图

// MinerU 视图：显示 chunks.json 中的布局块（标题/正文/表格/图片）
// LLM 视图：显示 intelligent_chunks.json 中的语义标注（条款/术语/公式/参数）
```

- 工具栏中的 MinerU 切换按钮控制 `mineruMode` 状态
- 切换时 PDF SVG 标注层重新渲染（不同颜色体系）
- 分析未完成时（无 `intelligent_chunks.json`），LLM 按钮不可用

### 4.2 实时日志 SSE 连接

```javascript
// 建立 SSE 连接
const eventSource = new EventSource(`/api/graph/task/${taskId}/events`)

eventSource.addEventListener('log', (e) => {
  realtimeLogs.push(JSON.parse(e.data))
  // 自动滚动到底部
})

eventSource.addEventListener('progress', (e) => {
  progressPercent.value = JSON.parse(e.data).percent
})

eventSource.addEventListener('complete', () => {
  eventSource.close()
  // 触发分析结果加载
  await loadAnalysisData()
})
```

- SSE 连接在页面卸载或任务完成时主动关闭
- 重连策略：指数退避，最多 3 次重连
- 日志消息数据结构：`{timestamp, level, message, chapter?, clause?}`

### 4.3 Checkpoint 断点续传

- 后端在 `chunk_checkpoint_v2.json` 中记录每个章节的处理状态
- 前端在任务中断重连后，调用 `GET /chunk/:projectId/progress` 获取已完成的章节列表
- 进度条从已完成的百分比继续显示
- 用户点击"重置并重新分析"时，后端删除 checkpoint 和结果文件，从头开始

### 4.4 条款与 PDF BBox 联动

- 条款展开时，查询该条款的 `pdf_location.page` 和 `pdf_location.bbox`
- PDF 阅读器滚动到对应页面，在 canvas 上叠加 SVG 高亮矩形
- 用户点击 PDF 上的 BBox 区域，触发对应条款在章节树中自动展开并滚动到可见位置

### 4.5 实体编辑保存

```javascript
// 编辑模式状态
const editingClauseId = ref(null)
const editingField = ref(null) // 'terms' | 'conditions' | 'actions' | 'components'
const editingValue = ref([])

// 保存逻辑
async function saveEntityEdit(clauseId, field, value) {
  await updateClauseEntity(projectId, {
    clause_id: clauseId,
    [field]: value
  })
  // 乐观更新 UI
}
```

- 编辑字段仅支持文本输入（逗号分隔多个值）
- 保存成功后自动关闭编辑状态
- 保存失败时回滚输入值，显示错误提示

---

## 五、状态变量清单

| 变量名 | 类型 | 描述 |
|--------|------|------|
| `projectId` | `ref<string>` | 当前项目 ID |
| `analysisStatus` | `ref<ProjectStatus>` | 项目分析状态枚举 |
| `taskId` | `ref<string>` | 当前后台任务 ID |
| `pdfUrl` | `ref<string>` | PDF 文件 URL |
| `pdfFileName` | `ref<string>` | PDF 文件名 |
| `totalPages` | `ref<number>` | PDF 总页数 |
| `pdfDoc` | `ref<PDFDocumentProxy>` | PDF.js 文档对象 |
| `mineruMode` | `ref<boolean>` | MinerU 布局视图 / LLM 语义视图 |
| `mineruChunks` | `ref<Chunk[]>` | MinerU 分块结果列表 |
| `mineruSelectedChunk` | `ref<Chunk>` | 当前选中的 MinerU 分块 |
| `analysisData` | `ref<AnalysisData>` | LLM 分析结果（章节树 + 条款 + 元素） |
| `expandedChapters` | `ref<Set<string>>` | 已展开的章节 ID 集合 |
| `expandedClauseId` | `ref<string>` | 当前展开的条款 ID |
| `highlightedClauseId` | `ref<string>` | 当前高亮的条款 ID（关联 PDF） |
| `allExpanded` | `ref<boolean>` | 全部展开/折叠状态 |
| `logDrawerOpen` | `ref<boolean>` | 实时日志抽屉展开状态 |
| `realtimeLogs` | `ref<Log[]>` | 实时日志消息列表 |
| `progressPercent` | `ref<number>` | 分析进度百分比 |
| `showStartOverlay` | `ref<boolean>` | 开始分析蒙层显示状态 |
| `starting` | `ref<boolean>` | 正在启动分析中 |
| `poolTabs` | `ref<string[]>` | 实体池 Tab 列表 |
| `activePoolTab` | `ref<string>` | 当前选中的实体池 Tab |
| `editingClauseId` | `ref<string>` | 当前编辑实体的条款 ID |
| `editingField` | `ref<string>` | 当前编辑的字段名 |
| `editingValue` | `ref<any>` | 当前编辑的值 |

---

## 六、后端 API 清单

| 接口 | 方法 | 描述 |
|------|------|------|
| `/api/graph/chunk/intelligent` | POST | 启动 LLM 智能分析任务 |
| `/api/graph/chunk/:projectId/progress` | GET | 获取章节级进度详情 |
| `/api/graph/chunk/:projectId` | GET | 获取原始 LLM 分析结果 |
| `/api/graph/chunk/:projectId/analysis` | GET | 获取格式化后的分析数据 |
| `/api/graph/chunk/:projectId/has_intelligent_chunks` | GET | 检查是否存在分析结果 |
| `/api/graph/chunk/:projectId/entity` | PATCH | 更新单个条款的知识实体 |
| `/api/graph/pdf/mineru-parse` | POST | 调用 MinerU API 解析 PDF |
| `/api/graph/pdf/mineru-parse/:projectId` | GET | 获取 MinerU 解析结果 |
| `/api/graph/task/:taskId` | GET | 获取任务状态 |
| `/api/graph/task/:taskId/events` | GET | SSE 实时日志流 |

---

## 七、样式规范

- **主色调**：蓝色 `#409EFF`（Element Plus 默认蓝）
- **成功色**：绿色 `#67C23A`
- **警告色**：橙色 `#E6A23C`
- **危险色**：红色 `#F56C6C`
- **背景色**：浅灰 `#F5F7FA`
- **卡片圆角**：8px
- **卡片阴影**：`0 2px 12px rgba(0, 0, 0, 0.1)`
- **PDF 标注透明度**：0.3（填充）+ 1.0（描边）
- **布局比例**：左侧 PDF 阅读器 48%，右侧分析面板 52%
- **Header 高度**：60px 固定
- **日志抽屉高度**：200px（展开时）
- **实体池高度**：~80px（底部 Tab 栏）

---

## 八、与其他页面的关系

```
HomeView
  └── [上传文档] → ChunkAnalysisView (/chunk-analysis/new)
                        │
                        ├── MinerU 初览 ──[开始智能分析]──→ LLM 语义分析
                        │
                        └── [进入图谱构建] → MainView (/graph_build/:projectId)
                                                   │
                                                   └── 节点/关系查看与交互
```

- ChunkAnalysisView 是文档上传后的第一个处理页面
- HomeView 提供文档上传入口，ChunkAnalysisView 负责完成整个 Chunk 分析流程
- 分析完成后，用户通过"进入图谱构建"按钮进入 MainView 完成图谱构建与交互
- 所有项目数据持久化在 `backend/projects/<projectId>/` 目录下（`project.json`、`chunks.json`、`intelligent_chunks.json`、`chunk_checkpoint_v2.json`）
