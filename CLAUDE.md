# Knowledge EviGraph 架构文档

## 变更记录 (Changelog)
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
| `graph_ops_routes.py` | `/api/graph/ops` | 图谱操作（实体/关系管理） |
| `ai_qa_routes.py` | `/api/ai-qa` | AI 问答路由 |
| `ai_app.py` | `/api/ai-app` | AI 应用配置与管理 |
| `chunk_routes.py` | `/api/chunk` | 文档智能分块 |
| `ontology_routes.py` | `/api/ontology` | 本体生成与管理 |
| `project_routes.py` | `/api/project` | 项目管理 |
| `entity_routes.py` | `/api/entity` | 实体管理 |
| `report.py` | `/api/report` | 报告生成 |
| `task_routes.py` | `/api/task` | 异步任务状态 |

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
| `normative_ontology.py` | 规范本体定义 |
| `semantic_enricher.py` | 语义 enrichment |
| `llm_doc_parser.py` | LLM 文档解析 |
| `query_intent_parser.py` | 查询意图解析 |

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

### 2. 边类型与情景驱动逻辑
- **defines**: 术语定义。
- **has_condition**: 条款的前提条件。
- **mandates / recommends / prohibits**: 强制/推荐/禁止动作。
- **in_situation**: 情景直接关联动作。
- **requires**: 动作满足的具体参数。
- **核心逻辑**: `Condition` —**under_condition**→ `Clause` —**mandates**→ `Action`

### 3. 构建流程
- **预处理**: 保留 metadata 溯源。
- **抽取**: 正则识别章节术语；LLM 识别条件、动作与参数。
- **关系**: 扫描”在…时”等句式建立条件关联；表格行拆解为参数节点。

## 检索与重排流程

项目采用 **BGE-reranker-v2-m3** 替代 LLM 重排，提升检索效率：

```
查询 → Embedding 检索 → Top-K 候选 → BGE-reranker 重排 → 最终结果
```

## AI 使用指引
- 该项目涉及复杂的图谱构建逻辑 (`backend/app/services/graph_builder.py`) 和多步检索逻辑 (`backend/app/api/graph.py` 中的 `ai_qa`)。
- **QA Pipeline**: 统一服务整合了检索、分块、重排功能 (`qa_pipeline.py`)。
- **智能分块**: 支持 LLM 驱动分块 (`llm_driven_chunker.py`) 和层级分块 (`hierarchical_chunker.py`)。
- **JSONL 支持**: 条款解析支持直接读取 JSONL 格式。
- **核心模型应用**: 必须严格遵循上述 **Normative KG Schema** 进行实体提取和建模，以支持”遇到什么情况应该怎么做”的情景化查询。

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
