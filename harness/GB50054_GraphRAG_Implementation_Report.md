# GB50054-2011 低压配电设计规范 — GraphRAG 构建完整实施报告

> 本报告详细描述如何将《低压配电设计规范》（GB50054-2011）构建为 GraphRAG 知识库，
> 覆盖 MinerU 解析 → Chunk 清洗 → LLM 实体关系提取 → 去重合并 → Neo4j 存储 → 跨章节关联的完整流程。

---

## 目录

1. [项目背景与目标](#1-项目背景与目标)
2. [完整流程概览](#2-完整流程概览)
3. [数据源说明](#3-数据源说明)
4. [Step 1: MinerU 输出解析与 Chunk 清洗](#4-step-1-mineru-输出解析与-chunk-清洗)
5. [Step 2: LLM 实体关系提取](#5-step-2-llm-实体关系提取)
6. [Step 3: 合并去重](#6-step-3-合并去重)
7. [Step 4: 存入 Neo4j 图数据库](#7-step-4-存入-neo4j-图数据库)
8. [Step 5: 补充其他章节](#8-step-5-补充其他章节)
9. [GraphRAG 查询模式设计](#9-graphrag-查询模式设计)
10. [文件清单](#10-文件清单)

---

## 1. 项目背景与目标

### 1.1 数据源

- **文档**: GB50054-2011《低压配电设计规范》
- **格式**: PDF（44页）
- **来源**: MinerU 解析输出（JSON格式）
- **已解析章节**: 第2章"术语"（2.0.1 ~ 2.0.37）

### 1.2 目标

1. 将 MinerU 解析的术语章节转换为结构化 chunks
2. 使用 LLM 提取实体（Entity）和关系（Relationship）
3. 构建电气工程领域知识图谱
4. 支持多跳检索查询

### 1.3 文档结构

| 章节 | 内容 | 条款数 |
|------|------|--------|
| 第1章 | 总则 | 3条 |
| **第2章** | **术语** | **37条（已完成）** |
| 第3章 | 电器和导体的选择 | ~60条 |
| 第4章 | 设备布置 | ~20条 |
| 第5章 | 配电的接地设计 | ~50条 |
| 第6章 | 电缆的选择与敷设 | ~30条 |
| 第7章 | 电气线路设计 | ~50条 |
| 附录A | k值表 | 表格 |

---

## 2. 完整流程概览

```
┌──────────────────────────────────────────────────────────────┐
│                      完整 Pipeline                            │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  [PDF] ──→ [MinerU] ──→ [mineru_output.json]               │
│                                   │                          │
│                         Step 1: Chunk 清洗                    │
│                                   ↓                          │
│                         [chunks/terms_chunks.json]            │
│                                   │                          │
│                         Step 2: LLM 提取                     │
│                                   ↓                          │
│                         [extraction/raw_entities.json]         │
│                         [extraction/raw_relationships.json]   │
│                                   │                          │
│                         Step 3: 去重合并                      │
│                                   ↓                          │
│                         [dedup/entities.json]                 │
│                         [dedup/relationships.json]           │
│                                   │                          │
│                         Step 4: 存入 Neo4j                   │
│                                   ↓                          │
│                         [Neo4j / Memory Graph]               │
│                                   │                          │
│                         Step 5: 跨章节关联                    │
│                                   ↓                          │
│                         [完整知识图谱] ──→ [GraphRAG 检索]   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. 数据源说明

### 3.1 MinerU 输出格式

MinerU 的 `content` 字段即为可用的解析结果：

```json
{
  "content": [
    {
      "type": "text",
      "text": "2.0.1 预期接触电压 人或动物尚未接触到可导电部分时，可能同时触及的可导电部分之间的电压。",
      "page_idx": 0
    }
  ]
}
```

### 3.2 关键字段

| 字段 | 说明 |
|------|------|
| `content[].type` | 元素类型（text/table/image） |
| `content[].text` | 条款编号 + 术语名 + 定义 |
| `content[].page_idx` | 所在页码（0-indexed） |

---

## 4. Step 1: MinerU 输出解析与 Chunk 清洗

### 4.1 脚本

文件: `scripts/01_mineru_chunking.py`

核心逻辑：
1. 遍历 `content` 列表
2. 跳过标题行（如"2 术语"）
3. 用正则 `(\d+\.\d+)\s+(.+)` 提取条款编号和内容
4. 合并跨页条款（同一 `chunk_id` 多处出现时）
5. 按 `chunk_id` 排序输出

### 4.2 Chunk 输出示例

```json
{
  "chunk_id": "2.0.15",
  "term_name": "SELV 系统",
  "definition": "在正常条件下不接地，且电压不能超过特低电压的电气系统。",
  "page_idx": 1,
  "section": "第2章 术语"
}
```

### 4.3 关键代码片段

```python
def parse_mineru_content(content_list: list) -> list[dict]:
    chunks = []
    for item in content_list:
        if item.get("type") != "text":
            continue
        text = item.get("text", "").strip()
        if not text:
            continue
        # 跳过标题行
        if re.match(r'^\d+\s+[术语章节]', text):
            continue
        # 提取条款编号
        clause_pattern = r'^(\d+\.\d+)\s+(.+?)(?=\s{2,}|(?=\d+\.\d+\s)|$)'
        matches = re.findall(clause_pattern, text)
        for clause_id, rest in matches:
            # 分离术语名和定义
            chunks.append({
                "chunk_id": clause_id,
                "term_name": extract_term_name(rest),
                "definition": extract_definition(rest),
                "page_idx": item.get("page_idx", 0),
                "section": "第2章 术语"
            })
    # 合并跨页条款
    merged = {}
    for chunk in chunks:
        cid = chunk["chunk_id"]
        if cid in merged:
            merged[cid]["definition"] += chunk["definition"]
        else:
            merged[cid] = chunk
    return sorted(list(merged.values()), key=lambda x: x['chunk_id'])
```

---

## 5. Step 2: LLM 实体关系提取

### 5.1 实体类型

| 类型 | 说明 | 示例 |
|------|------|------|
| ProtectionConcept | 防护概念/措施 | 直接接触防护、间接接触防护 |
| ElectricalSystem | 电气系统 | SELV系统、PELV系统 |
| Device | 电气设备 | 断路器、隔离开关 |
| Parameter | 电气参数 | 特低电压50V |
| PhysicalConcept | 物理概念 | 伸臂范围、导管 |

### 5.2 关系类型

| 类型 | 说明 | 示例 |
|------|------|------|
| 定义 | A 是 B 的定义 | 电气分隔 → 定义 → 隔离措施 |
| 基于 | A 基于 B | SELV系统 → 基于 → 特低电压 |
| 限制 | A 不超过 B | 特低电压 → 限制 → 50V |
| 组成 | A 包含 B | 总等电位联结 → 包含 → 金属管道 |
| 对比 | A vs B | 直接接触防护 → 对比 → 间接接触防护 |
| 执行 | A 执行 B | 断路器 → 执行 → 过电流保护 |
| 用于 | A 用于 B | 导管 → 用于 → 导线 |
| 引用 | A 引用 B | 条款5.2.1 → 引用 → 间接接触防护 |

### 5.3 LLM Prompt 示例

```
系统提示:
你是一个电力工程领域知识图谱构建专家。
从 GB50054-2011 术语定义中提取实体和关系...

用户提示:
条款编号: 2.0.15
术语名: SELV 系统
定义: 在正常条件下不接地，且电压不能超过特低电压的电气系统。

输出:
{
  "entities": [
    {"id": "SELV系统", "type": "ElectricalSystem", "name": "SELV系统", ...},
    {"id": "特低电压", "type": "Parameter", "name": "特低电压", ...}
  ],
  "relationships": [
    {"from": "SELV系统", "type": "基于", "to": "特低电压", "detail": "电压不能超过特低电压"}
  ]
}
```

### 5.4 批量提取策略

- 每 5 个 chunks 一批
- 批次间隔 1 秒（避免 API 限流）
- 失败重试最多 3 次
- 保存原始结果到 `raw_entities.json` 和 `raw_relationships.json`

---

## 6. Step 3: 合并去重

### 6.1 去重规则

| 场景 | 策略 |
|------|------|
| 同一 entity.id | 取定义最长的 |
| 同一 (from, type, to) | 取 detail 最长的 |
| 循环关系 | 检测并标记，待审核 |
| 孤立引用 | 过滤掉 |

### 6.2 异常检测

```python
# 检测循环 (A→B→C→A)
def detect_cycles(relationships, entity_ids):
    graph = build_adjacency_list(relationships)
    # DFS 检测环

# 检测自环 (A→A)
self_loops = [r for r in relationships if r["from"] == r["to"]]

# 验证引用完整性
for rel in relationships:
    if rel["to"] not in entity_ids:
        print(f"⚠ 孤立引用: {rel['from']} → {rel['to']}")
```

### 6.3 输出文件

- `dedup/entities.json` — 去重后实体
- `dedup/relationships.json` — 去重后关系
- `dedup/summary.json` — 统计摘要

---

## 7. Step 4: 存入 Neo4j 图数据库

### 7.1 两种存储模式

| 模式 | 说明 | 依赖 |
|------|------|------|
| Neo4j | 真实图数据库 | `pip install neo4j` |
| Memory | 本地 JSON | 无 |

### 7.2 Neo4j 模式

```bash
python scripts/04_neo4j_import.py --mode neo4j \
    --uri bolt://localhost:7687 \
    --user neo4j --password your_password
```

Cypher 建索引：
```cypher
CREATE INDEX entity_id IF NOT EXISTS FOR (e:Entity) ON (e.id);
CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.entity_type);
```

创建节点：
```cypher
MERGE (e:Entity {id: $id})
SET e.name = $name,
    e.entity_type = $type,
    e.definition = $definition_summary;
```

创建关系：
```cypher
MATCH (a:Entity {id: $from_id})
MATCH (b:Entity {id: $to_id})
MERGE (a)-[r:基于]->(b)
SET r.detail = $detail;
```

### 7.3 Memory 模式

```bash
python scripts/04_neo4j_import.py --mode memory
```

输出文件：
- `data/memory/memory_entities.json`
- `data/memory/memory_relationships.json`
- `data/memory/memory_graph.json`（邻接表）

Memory 模式支持基础查询：
```python
result = importer.query("SELV系统", hops=2)
# 返回实体及其2跳邻居
```

---

## 8. Step 5: 补充其他章节

### 8.1 各章节关键术语

| 章节 | 重点实体类型 | 关键术语 |
|------|------------|---------|
| 第3章 | Device, Parameter | TN系统, TT系统, IT系统, 断路器, 熔断器, 过电流保护 |
| 第4章 | PhysicalConcept | 外护物, 保护遮栏, 伸臂范围, IP等级 |
| 第5章 | ProtectionConcept | 直接接触防护, 间接接触防护, SELV/PELV/FELV, 等电位联结 |
| 第6章 | PhysicalConcept | 导管, 电缆托盘, 电缆梯架, 矿物绝缘电缆 |
| 第7章 | Parameter | 过电流保护, 短路保护, 接地故障, 电压降 |

### 8.2 跨章节关系设计

```
第2章术语 ──────────────────────────────────────────────┐
   │                                                      │
   │  (被第3/4/5/6/7章条款引用)                          │
   ▼                                                      │
第3章条款 ──引用──→ "特低电压" ──基于──→ SELV系统      │
   │                    │                                 │
   │                    └───基于──→ PELV系统               │
   │                           └───基于──→ FELV系统        │
   │                                                      │
第5章条款 ──引用──→ "间接接触防护"                       │
   │          ──执行──→ 剩余电流保护器                   │
   │          ──引用──→ "等电位联结" ──包含──→ 总等电位联结
```

### 8.3 增量导入流程

```bash
# 1. MinerU 解析第N章
# 2. 生成 chunks (修改 01_mineru_chunking.py 的输入路径)
# 3. LLM 提取 (运行 02_llm_extraction.py)
# 4. 去重 (运行 03_deduplication.py)
# 5. 合并到主图谱
python -c "
from scripts import merge_graphs
main = load_graph('data/dedup/')
inc = load_graph('data/dedup/chapter_N/')
merged = merge_graphs(main, inc)
save_graph(merged, 'data/dedup/')
"
```

---

## 9. GraphRAG 查询模式设计

### 9.1 查询类型

| 查询类型 | 示例 | 图谱操作 |
|---------|------|---------|
| 术语定义 | "什么是SELV系统？" | 返回 definition 字段 |
| 多跳关系 | "特低电压和SELV系统有什么关系？" | 找两节点间最短路径 |
| 保护措施适用系统 | "哪些系统需要间接接触防护？" | 沿"适用于"边扩展 |
| 设备功能 | "断路器有什么保护功能？" | 找"执行"边 |
| 条款引用 | "5.2.1引用了哪些术语？" | 返回所有"引用"边 |

### 9.2 查询脚本

```bash
# 交互模式
python scripts/06_graphrag_query.py --interactive

# 查询术语定义
python scripts/06_graphrag_query.py --entity "SELV系统"

# 查询2跳邻居
python scripts/06_graphrag_query.py --neighbors "特低电压" --hops 2

# 查询路径
python scripts/06_graphrag_query.py --path "特低电压" "SELV系统"

# 按类型查询
python scripts/06_graphrag_query.py --type "ProtectionConcept"
```

### 9.3 典型查询结果

**查询: SELV系统**

```
实体: SELV系统
类型: ElectricalSystem
定义: 在正常条件下不接地，且电压不能超过特低电压的电气系统。
来源条款: 2.0.15

相关实体 (5个):
→ 特低电压 (Parameter) [基于]
→ 直接接触防护 (ProtectionConcept) [用于]
→ PELV系统 (ElectricalSystem) [对比]
→ FELV系统 (ElectricalSystem) [对比]
→ 预期接触电压 (Parameter) [引用]
```

---

## 10. 文件清单

```
D:\github_mps\MiroFish-Neo4j\
└── script\
    ├── GB50054_GraphRAG_Implementation_Report.md   # 本报告
    │
    ├── scripts\
    │   ├── 01_mineru_chunking.py      # Step1: MinerU → chunks
    │   ├── 02_llm_extraction.py       # Step2: LLM提取
    │   ├── 03_deduplication.py         # Step3: 去重
    │   ├── 04_neo4j_import.py         # Step4: 存入图库
    │   ├── 05_cross_chapter.py         # Step5: 跨章节
    │   └── 06_graphrag_query.py       # GraphRAG查询
    │
    └── data\
        ├── mineru_output.json          # MinerU输出（需用户提供）
        ├── chunks\
        │   └── terms_chunks.json      # 清洗后的chunks
        ├── extraction\
        │   ├── raw_entities.json       # LLM原始实体
        │   └── raw_relationships.json  # LLM原始关系
        ├── dedup\
        │   ├── entities.json          # 去重后实体
        │   ├── relationships.json     # 去重后关系
        │   └── summary.json          # 统计摘要
        └── memory\
            ├── memory_entities.json   # Memory模式-实体
            ├── memory_relationships.json # Memory模式-关系
            └── memory_graph.json      # 邻接表
```

---

## 快速开始

```bash
# 1. 安装依赖
pip install openai python-dotenv neo4j

# 2. 启动 vLLM
bash vllm_start.sh

# 3. 放置 MinerU 输出
cp your_mineru_output.json data/mineru_output.json

# 4. 运行 Pipeline
cd scripts
python 01_mineru_chunking.py    # MinerU → chunks
python 02_llm_extraction.py       # LLM 提取
python 03_deduplication.py       # 去重
python 04_neo4j_import.py --mode memory  # 存入图库

# 5. 查询测试
python 06_graphrag_query.py --interactive
```

---

*报告生成时间: 2026-03-28*
*数据来源: GB50054-2011《低压配电设计规范》*
*已处理章节: 第2章 术语 (2.0.1-2.0.37)*
