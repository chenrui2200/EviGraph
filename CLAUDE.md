# Knowledge EviGraph 架构文档

## 变更记录 (Changelog)
- **2026-05-25**: 智能分析执行效率全面优化：1）VLM 图片分析并行化（`ThreadPoolExecutor(max_workers=4)` + 复用单个 LLMClient + 60秒超时），替代原串行处理；2）`_process_single_clause` 支持传入内存 `cache` 参数，Fallback 降级路径消除逐条文件 I/O；3）JSONL 增量写入改为章节内批量写入（`ProjectManager.append_clauses_to_jsonl`），单次 `open` 减少文件打开次数；4）Clause batch size 从 5 增大到 8，减少 LLM 调用次数；5）图谱构建阶段 `build_cross_ref_relations` + `build_hierarchical_relations` 并行执行（`ThreadPoolExecutor(max_workers=2)`）；6）`build_cross_ref_relations` 消除 N+1 查询（一次性批量查询所有 metadata_json + UNWIND 批量写入）；7）KB Pipeline `_wait_task` 轮询改为指数退避（0.5s→1s→2s→max 3s）。前端 `KbPipelineTrackView.vue` 流程卡片右下角新增 stage 耗时显示（`duration_ms` → `x分x秒` 格式）。
- **2026-05-18**: KB Pipeline 状态管理重构：`PipelineStageStatus` 从通用 4 状态拆分为每个阶段的 `processing/completed/failed` 细分状态（如 `graph_building_processing`），外层 `pipeline.status` 精确跟踪当前执行阶段。`started_at` 重命名为 `processing_at`，新增 `total_completed` 记录全部阶段完成时间。并发限制时状态改为 `PENDING` 而非 `FAILED`。AI App 新增 `POST /<app_id>/remove-project` 接口，移除项目时同步清理 `selectedProjectIds` 和 `selectedGraphIds`。`create_app` 默认 `intentMatch.rerankMinScore` 从 `0` 改为 `40`，不再支持 `workflow_data` 深度合并和自定义 `nodes`（强制为空数组）。Swagger tags 统一为 `AI App 应用管理`，数组字段补充 `items: type: object` 和 `example`。
- **2026-05-17**: `selectedProjectIds` 成为 App 配置 source of truth：前端 `AiQaView.vue` project-grid 勾选绑定改为 `selectedProjectIds`，`selectedGraphIds` 仅作为运行时动态快照；后端 `public-query`、`query-topic`、`delete_project`、`list_projects` 统一从 `selectedProjectIds` 动态解析最新 `graph_id`。解决 graph_id 重建后 App 引用失效问题。`get_project` 接口新增 `graph_node_stats`（各标签节点数量）、`graph_rel_stats`（各关系类型数量）、`intelligent_chunk_count` / `intelligent_section_count`。`.env` 适配本地 embedding/reranker API。
- **2026-05-17**: 修复全部 38 个后端接口的 Swagger docstring 格式：Flasgger 0.9.7.1 不支持 `---\npost:` 包装格式，改为 summary/description 前置、`tags`/`parameters`/`responses` 顶层键的兼容格式。`requirements.txt` 补充 `PyYAML>=6.0`。
- **2026-04-25**: 清理不存在的关系类型引用：删除 `neo4j_storage.py` 中 `MANDATES/PROHIBITS/RECOMMENDS/HAS_CONDITION/OPERATES_ON/APPLIES_TO/IN_SITUATION` 等孤儿方法（`_create_mandates_relation` 等 10 个）及 `get_graph_data` 查询列表；删除 `graph_tools.py` 死代码 `search_with_dfs_flow` / `search_with_intent_guided_dfs_flow`；删除死模块 `query_intent_parser.py`、`semantic_enricher.py`、`normative_entity.py`；修正 `_expand_object_node_optimized` 注释和 `rel_facts` 映射。更新架构文档明确实际边类型（见"实际图谱结构"）。
- **2026-04-25**: 新增图谱检索测试页面 (`/graph-search/:projectId`)：支持节点类型多选 + 名称模糊搜索，动态加载 1 跳邻域，节点详情面板展示全部属性，关联节点可展开到图谱并高亮；Topic 节点作为独立召回源（策略 A：复用 clause embedding），后端新增 Topic vector index、Topic hybrid 检索、graph_tools 支持 Topic root type；PublicChatView.vue 修复 canvas 渲染时序（ evidence 完全渲染后再显示 report）；后端新增 `/ops/search-nodes`（支持 node_types 数组）和 `/ops/node-neighborhood` API。
- **2026-04-24**: 清理前后端死代码：移除未使用的后端路由（delete_graph、supplement_knowledge、check_graph_references、reset_project、cleanup_tasks、list_tasks）及 entity_routes.py 整个模块；删除 neo4j_storage.py 中的 update_node_labels 孤儿方法；前端移除 searchGraph/aiQa/chatWithAgent/mineruParse/resetIntelligentChunks 等死代码函数，Process.vue Hit-Test 改用 searchObjectFirst 对齐后端 `/tools/search-object-first`。
- **2026-04-13**: 新增 MinerU `parse_method` 参数（auto/ocr）、智能分析进度回调与实时日志推送、并发解析 mineru_parsed.jsonl（多线程 append 无锁）。
- **2026-04-12**: 更新架构文档，反映 QA Pipeline、BGE-reranker、JSONL 解析、智能分块等新功能。
- **2026-03-24**: 初始化项目架构文档，识别后端 (Python/Flask) 与前端 (Vue 3/Vite) 模块。

## 项目愿景
Knowledge EviGraph 是一个基于 Neo4j 和大语言模型 (LLM) 的知识图谱构建与管理系统。它旨在实现本地优先的群体智能引擎，通过自动化本体生成、多步检索推理等技术，将非结构化文档转化为可查询、可推理的图谱知识库。

## 架构总览

```mermaid
graph TD
    Root[“(根) Knowledge EviGraph”] --> BE[“backend (Flask)”]
    Root --> FE[“frontend (Vue 3)”]

    BE --> API[“API 层 (Blueprint)”]
    BE --> Services[“Service 层 (逻辑核心)”]
    BE --> Storage[“Storage 层 (Neo4j/Embeddings)”]
    BE --> Models[“Models 层 (数据模型)”]
    BE --> Utils[“Utils 层 (Parser/Tools)”]

    FE --> Components[“UI 组件 (Graph/Steps)”]
    FE --> Views[“视图层 (Home/Interaction)”]
    FE --> Router[“路由 (Vue Router)”]
    FE --> Store[“状态管理 (Pinia)”]
    FE --> ApiClient[“API 客户端”]

    Services --> LLM[“LLM 客户端 (Ollama/vLLM)”]
    Services --> QAPipeline[“QA Pipeline 服务”]
    Services --> Chunker[“智能分块服务”]
    Storage --> Neo4j[(“Neo4j 数据库”)]
    Storage --> VectorStore[“向量存储 (Embedding)”]

    click BE “./backend/CLAUDE.md” “查看后端模块文档”
    click FE “./frontend/CLAUDE.md” “查看前端模块文档”
```

## 模块索引

| 模块路径 | 语言 | 职责描述 | 入口文件 |
| :--- | :--- | :--- | :--- |
| [backend](./backend/CLAUDE.md) | Python | 后端 API、图谱构建服务、LLM 链条管理、Neo4j 交互、QA Pipeline | `backend/run.py` |
| [frontend](./frontend/CLAUDE.md) | Vue/JS | 用户界面、图谱可视化 (D3.js)、项目流程管理 | `frontend/src/main.js` |

## 核心功能模块

### 后端 API 路由 (`backend/app/api/`)
| 路由文件 | 路径 | 职责 |
| :--- | :--- | :--- |
| `graph.py` | `/api/graph` | 核心图谱接口、后台 Worker 逻辑 |
| `graph_ops_routes.py` | `/api/graph/ops` | 图谱操作（构建、数据查询） |
| `ai_app.py` | `/api/ai-app` | AI 应用配置与管理 |
| `chunk_routes.py` | `/api/chunk` | 文档智能分块 |
| `ontology_routes.py` | `/api/ontology` | 本体生成与管理 |
| `project_routes.py` | `/api/project` | 项目管理 |
| `report.py` | `/api/report` | 报告生成（检索、重排、LLM 问答工具） |
| `task_routes.py` | `/api/task` | 异步任务状态 |
| `kb_pipeline_routes.py` | `/api/kb-pipeline` | KB Pipeline 管理 |

### 后端核心服务 (`backend/app/services/`)
| 服务文件 | 职责 |
| :--- | :--- |
| `graph_builder.py` | 图谱构建核心逻辑 |
| `graph_tools.py` | 图谱工具集（重排、检索、查询） |
| `qa_pipeline.py` | 统一 QA Pipeline 服务 |
| `llm_driven_chunker.py` | LLM 驱动的智能文档分块 |
| `hierarchical_chunker.py` | 层级分块服务 |
| `ontology_generator.py` | 本体生成 |
| `oasis_profile_generator.py` | OASIS 规范剖面生成 |
| `normative_ontology.py` | 规范本体定义（设计文档，未生成 MANDATES 等边） |

### 后端存储层 (`backend/app/storage/`)
| 存储文件 | 职责 |
| :--- | :--- |
| `neo4j_storage.py` | Neo4j 图数据库封装 |
| `search_service.py` | 检索服务（支持 BGE-reranker） |

## 运行与开发

### 环境要求
- Node.js >= 18.0.0
- Python >= 3.10
- Neo4j 数据库
- Ollama / vLLM (用于本地运行 LLM)
- MinerU (PDF 解析，可选)
- BGE-reranker-v2-m3 (重排模型)
- PyYAML >= 6.0 (Flasgger 解析 Swagger docstring 必需)

### 快速启动
1. **安装依赖**: `npm run setup:all` (会自动执行 root, backend 和 frontend 的安装)
2. **启动开发服务**: `npm run dev` (同时启动后端 5001 和前端)
3. **配置文件**: 在根目录或 `backend` 目录下创建 `.env` 文件。

### 启动脚本 (`script/`)
- `mineru_start.sh`: 启动 MinerU PDF 解析服务
- `nomic_embed_start.sh`: 启动 Nomic Embedding 服务
- `vllm_start.sh`: 启动 vLLM LLM 服务

## 测试策略
- **当前状态**: 后端已有 `.pytest_cache`，建议正式引入 `pytest`。
- **建议**:
  - 后端使用 `pytest` 进行 API 与 Service 逻辑测试。
  - 前端引入 `vitest` 进行组件与 API 客户端测试。

## 编码规范
- **后端**: 遵循 PEP 8 规范，使用类型提示 (Type Hints)。
- **前端**: Vue 3 组合式 API (Composition API)，ESLint + Prettier。

## 规范图谱核心模型 (Normative KG Schema)

针对工程规范文档（如 GB 50054），采用**领域专用规范图谱**模型。

### 1. 节点类型 (8 类)
- **Section**: 章节层级。
- **Clause**: 最小可执行单元（带编号条款）。
- **Term**: 术语定义。
- **Component**: 实体对象（设备、系统、材料）。
- **Condition**: 前提条件（环境、场景）。
- **Action**: 规范要求的具体动作/措施。
- **Requirement**: 强制/推荐/禁止标签。
- **Parameter**: 表格/公式数值。

### 2. 实际图谱结构（Neo4j 中真实创建的边）

> ⚠️ **重要区分**：`graph_builder.py` → `batch_add_hierarchical_chunks` 实际写入 Neo4j 的边**只有两种**：

| 边类型 | 方向 | 说明 |
| :--- | :--- | :--- |
| `MENTIONS` | Topic → Entity / Topic → Term / Episode → Clause | Topic 关联实体/术语；Episode 提及 Clause |
| `HAS_TOPIC` | Clause → Topic | 条款关联主题 |

> 清理后仅存的条件触发边（代码保留但当前数据条件不满足，通常不生成）：
> - `DEFINES`：`sync_term_entities` 仅在用户手动编辑条款术语时创建
> - `PART_OF`：`build_hierarchical_relations` 仅在条款编号满足层级规则时创建
> - `CROSS_REFERENCE`：`build_cross_ref_relations` 仅在 metadata 中存在交叉引用时创建
>
> 以下边类型已彻底清理（无创建代码）：`RELATION` / `NEXT_EPISODE` / `HAS_DOCUMENT` / `HAS_PAGE` / `HAS_EPISODE` / `MANDATES` / `PROHIBITS` / `HAS_CONDITION` / `OPERATES_ON` / `APPLIES_TO` / `IN_SITUATION`。

### 3. 检索路径（AI-QA 实际使用）
```
查询 → clause_id 精确匹配
      → Entity/Term/Clause 向量并行召回
      → fallback: Topic hybrid 召回 → Entity 映射
      → 路径: Entity/Term ←MENTIONS-- Topic ←HAS_TOPIC-- Clause
      → BGE-reranker 重排 → LLM 回答
```

### 4. 设计中但未实现的关系类型（仅存在于本体设计 Prompt）
以下关系类型在 `ontology_generator.py` 的 LLM Prompt 中有定义，但**构建代码不会创建**：
- `MANDATES` / `RECOMMENDS` / `PROHIBITS`
- `HAS_CONDITION` / `IN_SITUATION`
- `OPERATES_ON` / `APPLIES_TO`
- `REQUIRES`

> 这些类型保留在设计文档中，作为未来扩展方向。

### 5. 构建流程
- **预处理**: 保留 metadata 溯源。
- **抽取**: 正则识别章节术语；LLM 识别条件、动作与参数。
- **关系**: 扫描”在…时”等句式建立条件关联；表格行拆解为参数节点。

## 检索与重排流程

项目采用 **BGE-reranker-v2-m3** 替代 LLM 重排，提升检索效率：

```
查询 → Embedding 检索 → Top-K 候选 → BGE-reranker 重排 → 最终结果
```

## AI 使用指引
- 该项目涉及复杂的图谱构建逻辑 (`backend/app/services/graph_builder.py`) 和多步检索逻辑 (`backend/app/api/report.py` 工具接口)。
- **QA Pipeline**: 统一服务整合了检索、分块、重排功能 (`qa_pipeline.py`)。
- **智能分块**: 支持 LLM 驱动分块 (`llm_driven_chunker.py`) 和层级分块 (`hierarchical_chunker.py`)。
- **JSONL 支持**: 条款解析支持直接读取 JSONL 格式。
- **核心模型应用**: 实际构建的图谱**只有** `MENTIONS` 和 `HAS_TOPIC` 两种边（见”实际图谱结构”）。检索路径固定为 `Entity/Term ←MENTIONS-- Topic ←HAS_TOPIC-- Clause`。

## App 配置与 Project-Graph 映射

### `selectedProjectIds` 作为 Source of Truth

App 配置中持久化的是 `project_id`（`selectedProjectIds`），而非固定的 `graph_id`。因为图谱重建后 `graph_id` 会变化，App 必须在运行时动态查询 Project 获取最新的 `graph_id`。

| 方向 | 逻辑 | 说明 |
|:---|:---|:---|
| **前端保存** | `selectedGraphIds` → 反向查 `project_id` → 存入 `selectedProjectIds` | `saveWorkflowApp` |
| **前端加载** | `selectedProjectIds` → 查 `project.graph_id` → 恢复勾选 | `loadAppConfig` |
| **前端勾选** | 基于 `selectedProjectIds`（绑定 project） | project-grid |
| **后端检索** | `selectedProjectIds` → `ProjectManager.get_project(pid).graph_id` | `public-query`、`query-topic` |
| **后端删除检查** | `selectedProjectIds` → 解析 graph_id → 匹配 | `delete_project`、`list_projects` |

### source_link PDF 预览数据链路

```
MinerU API → chunks.json (含 page_idx、bbox_viewport)
           → LLM 智能分块回填 pdf_bboxes
           → intelligent_chunks.json (clause.metadata.bboxs)
           → 图谱构建 (Clause 节点存入 source_link、pdf_bboxes)
           → 检索透传 (source_link 含 page、bbox)
           → 前端 PdfPreviewView.vue
             → PDF.js 渲染 canvas
             → canvas 绘制红色虚线高亮框
```

### `create_app` 默认工作流配置

```json
{
  “selectedGraphIds”: [],
  “selectedProjectIds”: [],
  “temperature”: 0.7,
  “similarityThreshold”: 50,
  “topK”: 10,
  “rerankMinScore”: 50,
  “maxDepth”: 3,
  “rootTypes”: [“Entity”, “Term”],
  “intentMatch”: {
    “topicLimit”: 50,
    “entityLimit”: 50,
    “rerankMinScore”: 40
  }
}
```

## MinerU PDF 解析流程

### 解析模式
- `parse_method='auto'`: 自动选择解析策略
- `parse_method='ocr'`: 强制启用 OCR（默认）

### 并发解析（`/pdf/re-annotate`）
- `ThreadPoolExecutor(max_workers=8)` 并发调用 MinerU API 解析每一页
- 每页结果以 append 模式写入 `mineru_parsed.jsonl`（多线程并发写入，**无锁**）
- 解析完成后按页码顺序拼接 md_content，保存 `chunks.json`

### 关键数据结构
| 文件 | 来源 | 内容 |
| :--- | :--- | :--- |
| `mineru_parsed.jsonl` | MinerU API 并发解析 | 每行一页的原始 JSON（含 md_content、preproc_blocks） |
| `chunks.json` | `_parse_mineru_to_chunks()` | 标准化后结构：`chunk_id`、`type`（title/text/table）、`content`、`page_idx`、`bbox_viewport`、`category_id` |
| `intelligent_chunks.json` | `LLMDrivenChunker.chunk()` | LLM 语义分析结果，含章节、条款、实体、三元组 |

### 智能分析章节构建逻辑（`_build_sections_and_clauses_from_chunks`）
1. 遍历 `chunks.json`，识别 `type='title'` + 章节编号格式（如 `”3 电气和导体的选择”`）→ 创建一级章节
2. 识别 `type='text'` + 条款编号格式（如 `”3.1.1 导体应采用...”`）→ 挂到当前活跃 `current_chapter` 下
3. `type='title'` 但无编号格式（如”前 言”）→ 跳过，不影响条款归属
4. 逐章 LLM 提取 Topic + 实体（通过 `progress_callback` 实时推送日志，progress=-1 时仅写日志不更新进度）

### 项目数据文件
- `Config` 类：配置管理，导入时自动验证，**无需手动调用 `validate()`**
- `EntityReader` 类：**已移除**（相关功能整合至 `LLMDrivenChunker`）
