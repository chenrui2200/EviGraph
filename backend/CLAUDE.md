[根目录](../CLAUDE.md) > **backend**

## 变更记录 (Changelog)
- **2026-03-24**: 初始扫描，识别 API、服务与存储结构。

## 模块职责
负责 MiroFish-Neo4j 的核心逻辑，包括：
- 文档解析与文本块切分 (`utils/file_parser.py`, `services/text_processor.py`)。
- 自动化本体生成 (`services/ontology_generator.py`)。
- 异步图谱构建任务 (`services/graph_builder.py`, `api/graph.py`)。
- Neo4j 数据存储与检索 (`storage/neo4j_storage.py`, `storage/search_service.py`)。
- 多步 (Multi-hop) AI 问答推理 (`api/graph.py` 中的 `ai_qa`)。

## 入口与启动
- **主入口**: `backend/run.py` (Flask 启动)。
- **配置**: `backend/app/config.py` (从 `.env` 加载)。
- **依赖管理**: `backend/pyproject.toml` (使用 `uv` 管理)。

## 对外接口 (API)
- `graph_bp` (`/api/graph`): 图谱管理、任务进度、多步问答。
- `simulation_bp` (`/api/simulation`): 仿真任务管理。
- `report_bp` (`/api/report`): 报告生成。

## 关键依赖与配置
- **Flask**: Web 框架。
- **Neo4j**: 核心图数据库。
- **LangChain/LLM**: 用于本体分析与问答。
- **uv**: 快速 Python 包管理器。

## 数据模型
- `Project`: 项目元数据、状态、关联文件。
- `Task`: 异步任务状态记录 (Created, Processing, Completed, Failed)。
- `AiApp`: AI 应用配置。

## 测试与质量
- **覆盖率**: 目前缺少单元测试。
- **工具**: 建议集成 `pytest` 和 `flake8/black`。

## 相关文件清单
- `backend/app/api/graph.py`: 核心图谱接口，包含复杂的后台 Worker 逻辑。
- `backend/app/services/graph_builder.py`: 处理实体提取、关系建立的 Service。
- `backend/app/storage/neo4j_storage.py`: 封装 Neo4j 的 Cypher 查询。
