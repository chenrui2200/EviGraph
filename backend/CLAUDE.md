[根目录](../CLAUDE.md) > **backend**

## 变更记录 (Changelog)
- **2026-04-12**: 新增 QA Pipeline、JSONL 解析支持、智能分块服务、BGE-reranker 重排集成。
- **2026-03-24**: 初始扫描，识别 API、服务与存储结构。

## 模块职责
负责 Knowledge EviGraph 的核心逻辑，包括：
- 文档解析与文本块切分 (`services/text_processor.py`, `services/llm_doc_parser.py`)。
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
| `graph.py` | `/api/graph` | 核心图谱接口、后台 Worker 逻辑 |
| `graph_ops_routes.py` | `/api/graph/ops` | 图谱操作（实体/关系管理） |
| `ai_app.py` | `/api/ai-app` | AI 应用配置与管理 |
| `chunk_routes.py` | `/api/chunk` | 文档智能分块 |
| `ontology_routes.py` | `/api/ontology` | 本体生成与管理 |
| `project_routes.py` | `/api/project` | 项目管理 |
| `entity_routes.py` | `/api/entity` | 实体管理 |
| `report.py` | `/api/report` | 报告生成 |
| `task_routes.py` | `/api/task` | 异步任务状态 |

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

## 相关文件清单
- `backend/app/api/graph.py`: 核心图谱接口，包含复杂的后台 Worker 逻辑。
- `backend/app/services/graph_builder.py`: 处理实体提取、关系建立的 Service。
- `backend/app/storage/neo4j_storage.py`: 封装 Neo4j 的 Cypher 查询。
