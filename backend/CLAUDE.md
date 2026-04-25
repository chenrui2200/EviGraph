[根目录](../CLAUDE.md) > **backend**

## 变更记录 (Changelog)
- **2026-04-25**: 清理不存在的关系类型代码：删除 `neo4j_storage.py` 孤儿方法 10 个（`_create_mandates_relation` 等）及查询列表中的 `MANDATES/PROHIBITS/RECOMMENDS/HAS_CONDITION/OPERATES_ON/APPLIES_TO/IN_SITUATION`；删除 `graph_tools.py` 死代码 `search_with_dfs_flow` / `search_with_intent_guided_dfs_flow`；删除死模块 `query_intent_parser.py`、`semantic_enricher.py`、`normative_entity.py`。
- **2026-04-25**: neo4j_schema.py 新增 Topic vector index；neo4j_storage.py 新增 `search_topic_nodes`（Topic hybrid 向量+关键字检索）、`search_nodes_by_name`（支持 node_types 多类型过滤）、`get_node_neighborhood`（1 跳邻域查询）；search_service.py 新增 Topic 检索 Cypher 模板与 `search_topic_nodes`；graph_tools.py `search_term_entity_to_clause_batch` 扩展支持 Topic root type（复用 clause embedding）；graph_ops_routes.py 新增 `POST /ops/search-nodes`（node_types 数组参数）和 `POST /ops/node-neighborhood`。
- **2026-04-24**: 清理未使用路由：删除 entity_routes.py 模块、ai_qa_routes.py（已废弃）、reset_project、delete_graph、supplement_knowledge、check_graph_references、cleanup_tasks、list_tasks、get_intelligent_chunks；移除 neo4j_storage.py 中无引用的 update_node_labels 方法；删除无引用的 llm_doc_parser.py；检索工具统一收敛到 report.py。
- **2026-04-12**: 新增 QA Pipeline、JSONL 解析支持、智能分块服务、BGE-reranker 重排集成。
- **2026-03-24**: 初始扫描，识别 API、服务与存储结构。

## 模块职责
负责 Knowledge EviGraph 的核心逻辑，包括：
- 文档解析与文本块切分 (`services/text_processor.py`)。
- 智能文档分块 (`services/llm_driven_chunker.py`, `services/hierarchical_chunker.py`)。
- 自动化本体生成 (`services/ontology_generator.py`, `services/normative_ontology.py`)。
- 异步图谱构建任务 (`services/graph_builder.py`, `api/graph.py`)。
- Neo4j 数据存储与检索 (`storage/neo4j_storage.py`, `storage/search_service.py`)。
- 多步 (Multi-hop) AI 问答推理 (`api/report.py` 工具接口)。
- 报告生成 (`api/report.py`)。

## 入口与启动
- **主入口**: `backend/run.py` (Flask 启动)。
- **配置**: `backend/app/config.py` (从 `.env` 加载)。
- **依赖管理**: `backend/pyproject.toml` (使用 `uv` 管理)。

## 对外接口 (API)
| 路由文件 | 路径 | 职责 |
| :--- | :--- | :--- |
| `graph.py` | `/api/graph` | 核心图谱接口、后台 Worker 逻辑（PDF 解析） |
| `graph_ops_routes.py` | `/api/graph/ops` | 图谱操作（构建、数据查询） |
| `ai_app.py` | `/api/ai-app` | AI 应用配置与管理 |
| `chunk_routes.py` | `/api/chunk` | 文档智能分块 |
| `ontology_routes.py` | `/api/ontology` | 本体生成与管理 |
| `project_routes.py` | `/api/project` | 项目管理 |
| `report.py` | `/api/report` | 检索、重排、LLM 问答工具 |
| `task_routes.py` | `/api/task` | 异步任务状态 |
| `kb_pipeline_routes.py` | `/api/kb-pipeline` | KB Pipeline 管理 |

## 关键依赖与配置
- **Flask**: Web 框架。
- **Neo4j**: 核心图数据库。
- **LangChain/LLM**: 用于本体分析与问答。
- **BGE-reranker-v2-m3**: 检索重排模型。
- **uv**: 快速 Python 包管理器。

## 数据模型
- `Project`: 项目元数据、状态、关联文件。
- `Task`: 异步任务状态记录 (Created, Processing, Completed, Failed)。
- `AiApp`: AI 应用配置。
- `Clause`: 条款（规范图谱核心单元）。

## 测试与质量
- **覆盖率**: 建议集成 `pytest`。
- **工具**: 建议集成 `flake8/black`。

## 核心服务说明

### 智能分块 (`services/llm_driven_chunker.py`)
基于 LLM 的文档语义分块，保留上下文关联。

### 图谱工具集 (`services/graph_tools.py`)
提供重排、检索、查询等工具方法。

### Neo4j 实际边类型
`graph_builder.py` → `batch_add_hierarchical_chunks` 实际写入 Neo4j 的边**只有两种**：
- `MENTIONS`: Topic→Entity/Term, Episode→Clause
- `HAS_TOPIC`: Clause→Topic

> 清理后仅存的条件触发边（代码保留但当前数据条件不满足，通常不生成）：
> - `DEFINES`：`sync_term_entities` 仅在用户手动编辑条款术语时创建
> - `PART_OF`：`build_hierarchical_relations` 仅在条款编号满足层级规则时创建
> - `CROSS_REFERENCE`：`build_cross_ref_relations` 仅在 metadata 中存在交叉引用时创建
>
> 以下边类型已彻底清理（无创建代码）：`RELATION` / `NEXT_EPISODE` / `HAS_DOCUMENT` / `HAS_PAGE` / `HAS_EPISODE` / `MANDATES` / `PROHIBITS` / `HAS_CONDITION` / `OPERATES_ON` / `APPLIES_TO` / `IN_SITUATION`。

## 相关文件清单
- `backend/app/api/graph.py`: 核心图谱接口，包含复杂的后台 Worker 逻辑。
- `backend/app/services/graph_builder.py`: 处理实体提取、关系建立的 Service。
- `backend/app/storage/neo4j_storage.py`: 封装 Neo4j 的 Cypher 查询。
