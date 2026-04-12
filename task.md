# MiroFish-Neo4j 任务清单

> 生成时间: 2026-04-12
> 分支: dev
> 最后更新: 2026-04-12

---

## 一、已完成的安全优化 ✅

### 1.1 Cypher 注入防护
- **状态**: ✅ 已完成
- **文件**: `backend/app/storage/neo4j_storage.py`
- **内容**:
  - 添加 `VALID_NODE_LABELS` / `VALID_EPISODE_LABELS` 白名单
  - 新增 `_safe_label()` 辅助函数
  - 修复 7 处动态标签注入点

### 1.2 裸 except 异常处理
- **状态**: ✅ 已完成
- **文件**: 多个 Python 文件
- **内容**: 将裸 `except: pass` 改为具体异常类型 + 日志记录
- **涉及文件**:
  - `neo4j_storage.py`: JSON 解析异常
  - `graph.py`: current_step 解析异常
  - `graph_builder.py`: clause UUID 查找异常
  - `graph_tools.py`: 多处节点/关系获取异常
  - `oasis_profile_generator.py`: JSON 解析异常
  - `ontology_generator.py`: domain 检测异常

### 1.3 统一 API 响应工具
- **状态**: ✅ 已完成 (25/25 路由)
- **文件**: `backend/app/utils/api_utils.py`
- **内容**:
  - `success_response()` / `error_response()` 统一响应格式
  - `@api_handler` 装饰器统一异常处理
  - 支持按异常类型返回对应 HTTP 状态码
- **已应用**: `list_projects`, `check_has_intelligent_chunks`, `get_task`, `list_tasks`, `update_node_label`, `ai_qa`, `generate_ontology`, `intelligent_chunk`, `get_chunk_progress`, `get_intelligent_chunks`, `get_chunk_analysis`, `update_clause_entity`, `get_project`, `delete_project`, `update_project`, `reset_project`, `get_project_document`, `build_graph`, `get_graph_data`, `delete_graph`, `supplement_knowledge`

### 1.4 路径遍历防护
- **状态**: ✅ 已完成
- **文件**: `backend/app/api/graph.py` (`_resolve_pdf_path`)
- **内容**: 添加 `os.path.realpath()` 验证确保路径在允许目录内

### 1.5 硬编码默认凭证
- **状态**: ✅ 已完成
- **位置**: `backend/app/config.py`
- **修改**:
  - `SECRET_KEY`: 移除默认值，强制环境变量配置
  - `NEO4J_PASSWORD`: 移除默认值，强制环境变量配置
  - 缺失时抛出 `ValueError` 而非使用不安全默认值

---

## 二、未完成的安全优化

### 2.1 速率限制缺失
- **位置**: 敏感 API 无限流
- **问题**: 上传、查询等 API 可被滥用
- **建议**: 集成 Flask-Limiter
- **优先级**: 🟡 低

---

## 三、未完成的代码质量优化

### 3.1 N+1 查询问题
- **位置**: `backend/app/storage/neo4j_storage.py` 多处
- **建议**: 使用 Cypher UNWIND 批量操作
- **优先级**: 🟡 低

### 3.2 内存泄漏风险
- **状态**: ✅ 已完成
- **文件**: `frontend/src/views/Process.vue`, `MainView.vue`
- **修改**: `onUnmounted` → `onBeforeUnmount`

### 3.3 魔法数字
- **状态**: 🟡 低优先级（暂不处理）
- **位置**: 多处无统一规律的进度计算分散在不同文件
- **说明**: `MAX_CONTENT_LENGTH` 是 Flask 标准配置，无需修改

### 3.4 重复代码 - JSON 解析
- **状态**: ✅ 已完成
- **文件**: `backend/app/storage/neo4j_storage.py`
- **修改**: 抽取为 `_parse_json_safe()` 辅助方法，位于 `Neo4jStorage` 类

### 3.5 大型单体文件拆分
- **状态**: ✅ 已完成
- **文件**: `backend/app/api/graph.py` (4293行 → ~1956行，减少54.4%)

#### 拆分结果

```
backend/app/api/
├── __init__.py           # 统一注册所有 blueprint
├── graph.py              # 主入口（保留通用辅助函数）
├── project_routes.py     # 项目管理 (6端点)
├── task_routes.py        # 任务管理 (3端点)
├── entity_routes.py      # 实体管理 (1端点)
├── ai_qa_routes.py       # AI问答 (1端点)
├── ontology_routes.py    # 本体生成 (1端点)
├── chunk_routes.py       # 分块处理 (6端点)
└── graph_ops_routes.py   # 图谱操作 (4端点)
```

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | 创建 `project_routes.py` | ✅ |
| 2 | 创建 `task_routes.py` | ✅ |
| 3 | 创建 `entity_routes.py` | ✅ |
| 4 | 创建 `ai_qa_routes.py` | ✅ |
| 5 | 创建 `ontology_routes.py` | ✅ |
| 6 | 创建 `chunk_routes.py` | ✅ |
| 7 | 创建 `graph_ops_routes.py` | ✅ |
| 8 | 简化 `graph.py` 主文件 | ✅ |
| 9 | 更新 `__init__.py` 注册 | ✅ |

### 3.6 D3.js 完整导入
- **状态**: ✅ 已完成
- **文件**: `frontend/src/components/GraphPanel.vue`, `frontend/src/views/Process.vue`
- **修改**: 完整导入 `* as d3` → 按模块独立导入（`d3-selection`, `d3-force`, `d3-zoom`, `d3-drag` 等）

### 3.7 文件上传验证不完整
- **位置**: `graph.py:92-97`
- **问题**: 仅检查扩展名，未验证 magic bytes
- **建议**: 添加 MIME 类型检查
- **优先级**: 🟡 低

---

## 四、后端 Simulation Blueprint 🔍

### 4.1 状态: 待确认
- **说明**: `simulation_bp` 未注册，但代码中有引用
- **操作**: 确认是否需要实现，如不需要则清理相关代码

---

## 五、当前工作区改动 (2026-04-12 更新)

### 已修改文件
```
M backend/app/api/__init__.py              # 注册新路由模块
M backend/app/api/graph.py                 # 路由拆分后保留辅助函数
M backend/app/config.py                    # 移除硬编码默认凭证
M backend/app/services/graph_builder.py    # 裸except修复
M backend/app/services/graph_tools.py      # 裸except修复
M backend/app/services/oasis_profile_generator.py  # 裸except修复
M backend/app/services/ontology_generator.py  # 裸except修复
M backend/app/storage/neo4j_storage.py     # Cypher注入防护 + 裸except修复 + JSON辅助方法
M backend/app/utils/__init__.py            # 导出api_utils
M backend/app/api/task_routes.py           # @api_handler
M backend/app/api/entity_routes.py         # @api_handler
M backend/app/api/ai_qa_routes.py         # @api_handler
M backend/app/api/ontology_routes.py       # @api_handler
M backend/app/api/chunk_routes.py         # @api_handler
M backend/app/api/project_routes.py        # @api_handler
M backend/app/api/graph_ops_routes.py     # @api_handler
M frontend/src/components/GraphPanel.vue   # D3.js按需导入
M frontend/src/router/index.js
M frontend/src/views/MainView.vue           # onBeforeUnmount
M frontend/src/views/Process.vue           # D3.js按需导入 + onBeforeUnmount
M frontend/src/views/PublicChatView.vue
```

### 新增文件（已暂存）
```
A  backend/app/api/task_routes.py         # 任务管理路由
A  backend/app/api/entity_routes.py       # 实体管理路由
A  backend/app/api/ai_qa_routes.py       # AI问答路由
A  backend/app/api/ontology_routes.py    # 本体生成路由
A  backend/app/api/chunk_routes.py       # 分块处理路由
A  backend/app/api/project_routes.py     # 项目管理路由
A  backend/app/api/graph_ops_routes.py   # 图谱操作路由
A  backend/app/utils/api_utils.py         # 统一API响应工具
A  task.md                               # 任务清单
```

---

## 六、优化优先级排序

| 优先级 | 任务 | 状态 | 预计工作量 |
|--------|------|------|-----------|
| 🔴 高 | 路径遍历防护 | ✅ 已完成 | 小 |
| 🔴 高 | 移除硬编码默认凭证 | ✅ 已完成 | 小 |
| 🟠 中 | 统一 API 响应（全面应用 @api_handler） | ✅ 已完成 | 中 |
| 🟠 中 | 拆分 graph.py | ✅ 已完成 | 大 |
| 🟠 中 | D3.js 按需导入 | ✅ 已完成 | 小 |
| 🟠 中 | 抽取 JSON 解析辅助方法 | ✅ 已完成 | 小 |
| 🟡 低 | 速率限制 (Flask-Limiter) | 🔴 未完成 | 中 |
| 🟡 低 | N+1 查询优化 (UNWIND) | 🔴 未完成 | 中 |
| 🟡 低 | 文件上传 Magic Bytes 验证 | 🔴 未完成 | 小 |
| 🟡 低 | 内存泄漏修复 | ✅ 已完成 | 小 |
| 🟡 低 | 魔法数字常量提取 | ⏸️ 暂缓 | 小 |
| 🔍 低 | simulation_bp 清理确认 | 🔴 未完成 | 小 |
