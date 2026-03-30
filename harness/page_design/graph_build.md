# Graph Build 页面设计文档

## 一、整体流程总览

Graph Build 页面负责将 `intelligent_chunks.json` 中的多层级语义分块数据转化为 Neo4j 知识图谱。

完整流程分为 **三个阶段**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 0: 准备阶段                                                   │
│  用户点击"开始构建图谱"按钮 → 创建后台任务                              │
│  前端 currentPhase: 0 → 1，后端 status: graph_building               │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 1: 图谱构建中（后台线程 _start_build_worker）                  │
│  ┌─ Step 1.1: 加载 intelligent_chunks.json                            │
│  ┌─ Step 1.2: 反序列化为 HierarchicalChunkResult                      │
│  ┌─ Step 1.3: 创建/恢复 Zep Graph                                    │
│  ┌─ Step 1.4: 设置 Ontology                                         │
│  ┌─ Step 1.5: 批量存储 Episode + Entity + 关系                       │
│  ┌─ Step 1.6: 构建交叉引用关系 (CROSS_REFERENCE)                       │
│  ┌─ Step 1.7: 构建层级关系 (PART_OF)                                  │
│  前端 currentPhase: 1，后端 status 经历:                              │
│    graph_building → graph_embedding → graph_indexing                  │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 2: 图谱构建完成                                               │
│  前端 currentPhase: 2，后端 status: graph_completed                  │
│  页面展示图谱可视化，可进入 Hit Test 和 AI 问答                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、前端状态机（MainView.vue）

### 2.1 核心状态变量

| 变量 | 类型 | 含义 |
|---|---|---|
| `currentPhase` | `number` | -1=初始化, 0=就绪, 1=构建中, 2=完成 |
| `hasIntelligentChunks` | `boolean` | 是否存在 intelligent_chunks.json |
| `buildProgress` | `object` | `{ progress: number, message: string }` |
| `graphData` | `object` | Neo4j 图谱数据 `{ nodes, edges }` |
| `projectData` | `object` | 项目元数据 |

### 2.2 Phase 映射逻辑

```javascript
// MainView.vue:updatePhaseByStatus()
switch (status) {
  case 'graph_building':
  case 'graph_chunking':    // 旧状态，兼容处理
  case 'graph_embedding':
  case 'graph_indexing':
    currentPhase = 1; break;  // 构建中
  case 'graph_completed':
    currentPhase = 2; break;  // 完成
  case 'failed':
    error = '...'; break;
  default:
    // 没有 graph_id 时停留在就绪状态
    if (!projectData?.graph_id) currentPhase = 0;
}
```

### 2.3 页面加载初始化流程

```
onMounted()
  └─ initProject()
       ├─ checkHasIntelligentChunks(projectId)
       │    └─ 无 → redirect to ChunkAnalysis
       └─ loadProject()
            ├─ getProject() → 解析 status
            ├─ updatePhaseByStatus()
            │    ├─ 状态为 building/chunking/embedding/indexing → currentPhase=1 → 启动轮询
            │    └─ 状态为 completed → currentPhase=2 → 加载图谱
            └─ startPollingTask() + startGraphPolling()
```

### 2.4 SSE 实时通信

- 任务启动后，通过 EventSource 监听 `GET /api/task/{task_id}/events`
- `init` 消息：加载已有日志，初始化 UI
- `update` 消息：更新 progress 和 message
- `completed` / `failed`：停止轮询，进入对应 Phase

### 2.5 轮询机制

| 轮询目标 | 间隔 | 用途 |
|---|---|---|
| `getProject()` via SSE | 实时推送 | 监听任务状态变更 |
| `getGraphData()` | 10s | 构建过程中逐步获取已生成的图谱数据 |

---

## 三、后端执行流程（_start_build_worker）

### 3.1 入口与任务创建

```
API: POST /api/graph/build
  └─ 创建 TaskManager Task
  └─ 启动后台线程 _start_build_worker()
```

### 3.2 Step 1.1 — 加载 intelligent_chunks.json

```python
intelligent_chunks_data = ProjectManager.get_intelligent_chunks(project_id)
```

**三种数据路径**：

1. **优先路径**：存在 `intelligent_chunks.json` → 直接使用，跳过 LLM 调用
2. **降级路径A**：存在 `chunks.json` → 调用 `TextProcessor.hierarchical_chunk()` 正则分块
3. **降级路径B**：仅有纯文本 → `TextProcessor.hierarchical_chunk_text()`

### 3.3 Step 1.2 — 反序列化为 HierarchicalChunkResult

将 JSON dict 还原为 Python 数据模型：

```
intelligent_chunks.json
    sections[] → SectionSegment[]
    clauses[]  → ClauseSegment[] (含 SemanticTriplet[])
    elements[] → ElementSegment[]
         ↓ HierarchicalChunkResult.to_episode_list()
Episode 字典列表 [{ text, metadata }, ...]
```

### 3.4 Step 1.3 — 创建 / 恢复 Graph

```python
if project.graph_id and not force:
    graph_id = project.graph_id  # 恢复已有图谱
else:
    graph_id = builder.create_graph(name=project.name)
    project.graph_id = graph_id
```

### 3.5 Step 1.4 — 设置 Ontology

```python
builder.set_ontology(graph_id, project.ontology)
```

将项目本体定义写入 Neo4j，包括实体类型和关系类型约束。

### 3.6 Step 1.5 — 批量存储 Episode + Entity + 关系

**核心方法**：`GraphBuilderService.add_hierarchical_chunks()`

逐个处理 chunk，调用 `Neo4jStorage.add_hierarchical_chunk_with_entities()`:

```
Episode 节点（文本片段）
    ├─ Episode --MENTIONS--> Clause/Component/Action/Object
    ├─ Episode --MENTIONS--> Formula/Parameter
    └─ Episode --PART_OF--> Section

Clause Entity
    ├─ Clause --APPLIES_TO--> Component
    ├─ Clause --MANDATES/RECOMMENDS/PROHIBITS--> Action
    ├─ Action --OPERATES_ON--> Object
    ├─ Clause --HAS_CONDITION--> Condition
    └─ Condition --IN_SITUATION--> Action
```

进度回调映射：`0-100%` → `15-90%`（其中前 15% 和后 10% 预留给其他步骤）

### 3.7 Step 1.6 — 构建交叉引用关系

```python
cross_ref_count = storage.build_cross_ref_relations(graph_id)
```

扫描每条 Episode 的 `metadata_json`，提取 `cross_refs`（如"见 3.2.1"），建立 `CROSS_REFERENCE` 边。

### 3.8 Step 1.7 — 构建层级关系

```python
hier_count = storage.build_hierarchical_relations(graph_id)
```

基于 `level` 和 `parent_chapter` 字段，构建 `PART_OF` 边：
- Section 包含 Clause
- Clause 包含 Element

### 3.9 状态变更时序

```
graph_building (0%)
  → graph_embedding (约 75%)
  → graph_indexing (约 90%)
  → graph_completed (100%)
```

---

## 四、关键数据结构转换

### 4.1 intelligent_chunks.json → Episode 字典

```python
# clause.py: HierarchicalChunk.to_episode_dict()
{
    "text": content,
    "metadata": {
        "level": 2,
        "chunk_type": "clause",
        "clause_id": "3.1.1",
        "triplets": [...],
        "source": "xxx.pdf",   # ← 来自 IntelligentChunks.json 顶层 source
        "page": 5,             # ← 来自 IntelligentChunks.json 顶层 page
        ...metadata
    }
}
```

**关键**：`to_episode_dict()` 中 `source` 和 `page` 优先级：
1. `HierarchicalChunk.source` / `HierarchicalChunk.page`（直接属性）
2. `metadata` 中的同名字段（覆盖）

### 4.2 Episode 字典 → Neo4j Episode 节点

```cypher
MERGE (ep:Episode {uuid: $uuid})
ON CREATE SET
    ep.graph_id = $graph_id,
    ep.data = $data,
    ep.metadata_json = $metadata_json,
    ep.processed = true,
    ep.embedding = $embedding,
    ep.source = $source,
    ep.page = $page
```

Episode 的 `source` 和 `page` 属性直接来自 `intelligent_chunks.json` 的 clause 顶层字段。

### 4.3 Entity 节点类型

| Entity 标签 | 来源 | 属性 |
|---|---|---|
| `Clause` | clause.chunk_type | clause_id, requirement_type, summary |
| `Section` | section.chunk_type | chapter_number, title |
| `Action` | triplet.action | name, summary |
| `Component` | triplet.component | name, summary |
| `Object` | triplet.obj | name, summary |
| `Condition` | triplet.condition | name, summary |
| `Formula` | metadata.formula_refs | name |
| `Parameter` | metadata.table_refs | name |
| `Term` | clause.terms + is_term_definition | name, definition |

---

## 五、UI 组件结构

```
MainView.vue (GraphBuild 路由页面)
  ├─ GraphPanel.vue         (左侧：图谱可视化)
  ├─ GraphBuild.vue          (右侧：构建步骤面板)
  │    ├─ Step 01: 知识图谱构建
  │    │    ├─ 进度条（当前 Phase = 0 时显示）
  │    │    ├─ 重置按钮
  │    │    ├─ 图谱统计（nodes / edges / schema types）
  │    │    └─ 开始构建按钮
  │    ├─ Step 02: 知识召回命中测试
  │    │    ├─ 进入 Hit Test 按钮
  │    │    └─ 直接创建 AI 应用按钮
  │    └─ System Logs（底部日志区）
  └─ StepNavigator.vue      (Header：项目进度指示器)
```

---

## 六、重置与恢复

### 6.1 重置（force = true）

```python
# API: POST /api/graph/build { force: true }
- 删除项目已有的 graph_id（内存置空）
- 不删除 intelligent_chunks.json
- 重新创建新的 graph_id
- 覆盖写入 Neo4j 数据
```

### 6.2 恢复（force = false + 已有 graph_id）

```python
- 复用已有的 graph_id
- 追加写入新的 episode/entity
- Neo4j MERGE 语义保证幂等性
```

---

## 七、文件对应关系

| 环节 | 前端文件 | 后端文件 | 关键方法 |
|---|---|---|---|
| 页面入口 | `MainView.vue` | — | — |
| 构建面板 | `components/GraphBuild.vue` | — | — |
| API 触发 | `api/graph.js:buildGraph()` | `graph.py:build_graph()` | 创建任务 + 启动线程 |
| 后台执行 | — | `graph.py:_start_build_worker()` | 核心构建逻辑 |
| 图谱服务 | — | `graph_builder.py:add_hierarchical_chunks()` | Episode + Entity 批量写入 |
| Neo4j 存储 | — | `neo4j_storage.py:add_hierarchical_chunk_with_entities()` | Cypher MERGE |
| SSE 推送 | — | `task.py` | 实时日志 + 进度 |
