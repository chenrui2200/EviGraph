"""
LLMDrivenChunker - 基于 LLM 的智能三级分块引擎

设计理念：
- 彻底摆脱正则对文档格式的依赖
- 使用 LLM 智能识别任意格式的章节、条文和要素
- 可靠的 LLM 重试机制，不使用正则备用
- 自动建立 Element ↔ Clause ↔ Chapter 的语义关联

SOTA 知识图谱构建模式：
1. 实体识别：使用 LLM 识别 Component、Term、Parameter、Formula 等
2. 关系抽取：MANDATES/HAS_CONDITION/OPERATES_ON 等关系
3. 层级关联：通过位置和语义双重关联

增强特性（V2）：
- 章节位置边界精确划分
- 完整的状态跟踪
- 断点恢复增强
"""

import json
import logging
import re
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Callable
from dataclasses import asdict
from datetime import datetime

from ..models.clause import (
    HierarchicalChunkResult,
    SectionSegment,
    ClauseSegment,
    ClauseItem,
    SemanticTriplet,
    ElementSegment,
    ChunkLevel,
    ElementType,
    RequirementType,
    CrossReference,
    ReferencedClause,
    SystemApplicability
)
from ..models.normative_entity import (
    EntityType,
    RelationType
)
from ..models.project import (
    ChunkCheckpoint,
    ChapterPlan,
    ChapterStatus,
    ProjectManager
)
from ..utils.file_parser import TextChunk
from ..utils.llm_client import LLMClient

logger = logging.getLogger('mirofish.llm_chunker')


class LLMChunkerError(Exception):
    """LLM 分块器异常"""
    pass


# ============================================================================
# 公共序列化函数（供外部模块复用）
# ============================================================================

def clause_to_dict(clause: "ClauseSegment") -> Dict[str, Any]:
    """将 ClauseSegment 转换为完整字典（含 triplets、terms、clause_items 等核心语义字段）"""
    return {
        "clause_id": clause.clause_id,
        "clause_title": clause.clause_title,
        "content": clause.content,
        "source": clause.source or "",
        "page": clause.page,
        "requirement_type": clause.requirement_type.value if hasattr(clause.requirement_type, 'value') else str(clause.requirement_type),
        "conditions": clause.conditions or [],
        "actions": clause.actions or [],
        "components": clause.components or [],
        "objects": clause.objects or [],
        "parent_chapter": clause.metadata.get("parent_chapter") if clause.metadata else None,
        "is_term_definition": clause.is_term_definition,
        "terms": clause.terms or [],
        "formula_content": clause.formula_content,
        "referenced_tables": clause.metadata.get("referenced_tables", []) if clause.metadata else [],
        "referenced_formulas": clause.metadata.get("referenced_formulas", []) if clause.metadata else [],
        # 条款引用（新增）
        "referenced_clauses": [
            {
                "clause_id": r.clause_id,
                "context": r.context,
                "ref_type": r.ref_type,
                "page_idx": r.page_idx,
                "chunk_id": r.chunk_id,
                "section_title": r.section_title,
            }
            for r in clause.referenced_clauses
        ] if clause.referenced_clauses else [],
        "referenced_standards": clause.referenced_standards or [],
        # 语义三元组（核心！）
        "triplets": [
            {
                "component": t.component,
                "action": t.action,
                "obj": t.obj,
                "condition": t.condition,
                "requirement": t.requirement
            }
            for t in clause.triplets
        ] if clause.triplets else [],
        # 款/项结构化（含款/项级三元组）
        "clause_items": [
            {
                "item_number": ci.item_number,
                "item_content": ci.item_content,
                "components": ci.components,
                "actions": ci.actions,
                "conditions": ci.conditions,
                "objects": ci.objects,
                "triplets": [
                    {
                        "component": t.component,
                        "action": t.action,
                        "obj": t.obj,
                        "condition": t.condition,
                        "requirement": t.requirement
                    }
                    for t in ci.triplets
                ] if ci.triplets else []
            }
            for ci in clause.clause_items
        ] if clause.clause_items else [],
        "metadata": clause.metadata or {}
    }


def element_to_dict(element: "ElementSegment") -> Dict[str, Any]:
    """将 ElementSegment 转换为完整字典"""
    return {
        "element_type": element.element_type.value if hasattr(element.element_type, 'value') else str(element.element_type),
        "source_id": element.source_id,
        "key": element.key,
        "value": str(element.value) if element.value else "",
        "unit": element.unit,
        "condition": element.condition,
        "abbreviation": element.abbreviation,
        "definition": element.definition,
        "keywords": element.keywords,
        "content": element.content,
        "metadata": element.metadata or {}
    }


class LLMDrivenChunker:
    """
    基于 LLM 的智能三级分块引擎

    工作流程（渐进式）：
    1. Level-1: 读取目录（章节结构）
    2. Level-2: 提取条文 + 语义要素
    3. Level-3: 提取技术要素
    4. 关联分析: 建立三层级的双向关联

    核心特点：
    - 不使用正则备用方案，完全依赖 LLM
    - 可靠的指数退避重试机制
    - 进度实时回调，支持前端显示
    """

    # =========================================================================
    # LLM Prompt 模板
    # =========================================================================

    # 目录提取 Prompt
    TOC_SYSTEM_PROMPT = """你是一个工程规范文档的目录分析专家。

你的任务是从文档中提取目录结构，记住每个章节的位置信息。

## 重要说明

1. **只分析目录部分**：通常在文档开头，包含"目录"、"Contents"、"第X章"等
2. **记录位置**：估算每个章节在文档中的大概位置（字符偏移量）
3. **章节编号**：提取章节编号和标题
4. **章节类型识别**（重要）：
   - 如果章节标题包含"术语"、"名词解释"，则 `chapter_type` 为 `"term_definition"`
   - 如果章节标题包含"附录"、"附表"，则 `chapter_type` 为 `"appendix"`
   - 其他章节为 `"normative"`（规范正文）
5. **OCR文本注意**：如果文档中章节标题前有 `#` 符号（如 "# 3 电器和导体的选择"），这不代表实际内容，忽略即可

## 输出要求

请输出 JSON 格式，包含章节编号、标题、估算位置和章节类型：
```json
{{
    "chapters": [
        {{
            "chapter_number": 1,
            "title": "总则",
            "chapter_type": "normative",
            "start_position": 0,
            "end_position": 5000
        }},
        {{
            "chapter_number": 2,
            "title": "术语",
            "chapter_type": "term_definition",
            "start_position": 5000,
            "end_position": 12000
        }}
    ]
}}
```

如果没有目录，请根据文档内容识别章节边界。"""

    TOC_USER_PROMPT = """请分析以下文档，提取目录结构（章节列表）：

{document_text}

请输出 JSON 格式。"""

    # 条文提取 Prompt
    CLAUSE_SYSTEM_PROMPT = """你是一个工程规范文档的条文分析专家。

你的任务是从规范文本中提取条文（条款）及其语义要素。

## 条文识别规则

1. **条文编号**：如 "3.2.1"、"5.1.3"、"第4.2.5条" 等
2. **条文标题**：条文编号后紧跟的描述性标题
3. **条文内容**：条文的具体要求描述
4. **款/项**：条文中的分级列表项，如 "1、"、"2、" 或 "1）"、"2）"

## ⚠️ OCR 文本处理（重要！）

OCR 工具提取的文本可能带有以下格式噪声，**必须正确处理**：

1. **# 前缀**：章节标题前可能有 `#` 符号（如 "# 3 电器和导体的选择"）
   - 这不是条文内容，**必须忽略**
   - 条文本身不以 `#` 开头，正常提取即可
2. **款/项编号**：OCR 可能将 "1、" 拆成多行，应合并
3. **跨行条文**：OCR 可能将一条条文拆成多行，应合并
4. **LaTeX 公式**：如 `$公式内容$`，提取为 `formula_content`
5. **表格**：如 `<table>...</table>`，提取表格标题编号（如 `表3.2.2`）到 `referenced_tables`

## ⚠️ 术语章节识别（重要！）

**术语章节特征**：
- 章节编号通常为 "第2章"、"第2章 术语" 或 "2 术语"
- 条文编号格式为 "2.0.1"、"2.0.2" 等（如 GB 50054 标准）
- 内容格式为 "术语名称：定义" 或 "术语名称 定义内容"

**术语章节OCR格式处理（关键！必须执行）**：
- 如果条文编号匹配 `X.0.N` 格式（第三段为 0，如 "2.0.5"、"3.0.12"），**自动识别为术语章节**
- 即使标题和定义内容之间**无空行分隔**（OCR 将标题和定义连排），也必须提取
- 例如 OCR 输出：
  ```
  2.0.5 直接接触防护
  无故障条件下的电击防护。
  ```
  或（无空行）:
  ```
  2.0.5 直接接触防护无故障条件下的电击防护。
  ```
  两种格式都应提取为：
  - `is_term_definition: true`
  - `terms: [{"term_name": "直接接触防护", "definition": "无故障条件下的电击防护"}]`
  - `clause_content` 应包含**完整定义内容**，不能仅截取条文标题后第一句

**术语章节处理方式**：
- clause_id: "2.0.5"
- clause_title: "直接接触防护"（术语名称）
- clause_content: "无故障条件下的电击防护"（定义内容）
- **必须设置**: `"is_term_definition": true`
- **提取术语要素**: 在 `terms` 字段中返回:
  ```json
  "terms": [
    {
      "term_name": "直接接触防护",
      "definition": "无故障条件下的电击防护"
    }
  ]
  ```

**术语与其他条文的主要区别**：
- 术语章节不提取 actions/conditions，只提取 terms
- 术语定义是"是什么"而非"要求做什么"

## ⚠️ 语义三元组提取（核心！必须执行）

每个条文必须提取**语义三元组 (Semantic Triplet)**，而不是平行列表。

**格式**：
{{
    "triplets": [
        {{"component": "施事组件", "action": "实操动作", "obj": "受事对象", "condition": "适用条件", "requirement": "mandatory|recommended|prohibited"}}
    ]
}}

**三元组含义**： -→ 

**✅ 正确示例**：
- "电器应选用符合产品标准的断路器" → {{"component": "电器", "action": "选用", "obj": "断路器"}}
- "导体在短路条件下应能承受热稳定" → {{"component": "导体", "action": "承受", "obj": "热稳定", "condition": "短路条件下"}}
- "配电箱严禁使用TN-C系统" → {{"component": "配电箱", "action": "使用", "obj": "TN-C系统", "requirement": "prohibited"}}
- "电缆应敷设在电缆桥架内" → {{"component": "电缆", "action": "敷设", "obj": "电缆桥架"}}

**❌ 错误示例（笛卡尔积歧义，禁止！）**：
{{
    "components": ["电器", "断路器"],
    "actions": ["应符合", "选用", "严禁"]
}}
→ 这样建图谱时，代码不知道  对应  还是 ，完全错误！

**Action 提取原则**（必须可执行的动作）：
- ✅ 安装类：安装、敷设、连接、接头、配线、铺设
- ✅ 选用类：选用、配置、设置、布置、选择
- ✅ 采用类：采用、使用、应用
- ✅ 防护类：接地、接零、屏蔽、隔离
- ✅ 检测类：检测、试验、校验、测量、检查
- ✅ 动作类：动作、分断、闭合、承受
- ❌ "符合"、"满足"、"达到"、"遵守" → 这些是状态词，不是动作。如果条文说"应符合XXX"，省略 action，只提取 component + obj，requirement=mandatory

**重要**：
- 同一个条文可以提取多个三元组
- 三元组数量宁缺毋滥，每条都必须是条文中明确表达的
- condition/requirement 可选，condition 为空时不写，requirement 默认为 mandatory

## ⚠️ 款/项结构化提取（重要！）

每个条文的款/项（如 "1、"、"2、"、"1）"、"2）"）应作为独立的子单元提取：

- 每个款/项应有 `item_number` 和 `item_content`
- 每个款/项应抽取对应的 **语义三元组 triplets**
- 款/项作为 `clause_items` 数组返回

## 表格与公式引用

- **表格引用**：在 `referenced_tables` 中提取所有表格编号（如 `表3.2.2`、`表4.2.5`）
- **公式引用**：在 `referenced_formulas` 中提取所有公式编号（如 `公式（3.2.14）`）
- **公式内容**：如果有公式的具体表达式（如 `$S \\geq I \\cdot t / k$`），提取到 `formula_content` 字段

## ⚠️ 条款引用识别（通用扩展版！）

当条文内容中引用了其他条款时，必须在 `referenced_clauses` 中列出被引用的条款编号。

### 引用格式识别规则
| 原文示例 | 提取结果 | 说明 |
|---------|---------|------|
| "本规范第5.2.4条" | `"5.2.4"` | 标准格式 |
| "按第3.2.14条" | `"3.2.14"` | 无"本规范" |
| "见3.2.5条" | `"3.2.5"` | 无"第"字 |
| "上述条款3.2.4" | `"3.2.4"` | 含"上述"指代词 |
| "第3.2.5至3.2.10条" | `["3.2.5", "3.2.10"]` | 范围引用 |
| "第1、2、3条" | `["1", "2", "3"]` | 顿号分隔多条款 |
| "第3.2.5条或第3.2.6条" | `["3.2.5", "3.2.6"]` | "或"连接 |
| "按附录A.0.7条" | `"A.0.7"` | 附录条款，保留字母 |
| "Article 3.2.5规定" | `"3.2.5"` | 英文格式 |
| "§ 3.2.5" | `"3.2.5"` | 段落符号格式 |
| "本规范第5.2.4条第1款" | `"5.2.4"` | 款号仅作上下文 |
| "本节第X条" | 根据上下文补充完整编号 | 含"本节"等指代 |

### ⚠️ 严格区分
- `referenced_clauses`：仅填条款编号（数字/字母混合格式，如 `"5.2.4"`、`"A.0.7"`）
- `referenced_standards`：填外部标准编号（如 `"GB/T16895.15"`、`"IEC 60364"`、`"JB/T"）`
  → **不得**将 GB/T、IEC 等外部标准放入 `referenced_clauses`！

### 常见引用词识别
- 引导词：`本规范`、`本条`、`本节`、`上述条款`、`见`、`按`、`依据`、`符合`、`遵照`
- 连接符：`至`、`到`、`和`、`与`、`或`、`，`

## 要求类型
- mandatory: 必须、应、须
- recommended: 建议、宜、推荐
- prohibited: 严禁、不得、禁止

**重要**：请从条文内容中**实际识别并提取**上述要素，不要返回空数组！

请保持 JSON 格式输出。"""

    CLAUSE_USER_PROMPT = """
请分析以下文本，提取条文及其语义要素：

{document_text}

来源：{source}

## 关键规则
1. **语义三元组**：使用 `triplets` 数组，`component → action → obj` 结构，**不要用 components/actions 平行列表**
2. **术语章节**（编号 X.0.N）：在 terms 字段返回术语定义，triplets 设为 []
3. **款/项**（"1、"、"2、"）：作为 clause_items 独立提取
4. **OCR # 前缀**不是条文内容，忽略
5. **表格/公式引用**放入 referenced_tables / referenced_formulas
6. **条款引用**放入 referenced_clauses，**外部标准**放入 referenced_standards
   - 支持格式：`本规范第X条`、`见X.Y.Z条`、`Article X.Y.Z`、`§ X.Y.Z`、`第1、2、3条`（顿号分隔）
7. 三元组只提取条文中明确出现的 component/action/obj，宁缺毋滥

## 要求类型
- mandatory: 必须、应、须
- recommended: 建议、宜
- prohibited: 严禁、不得、禁止

## 正确三元组示例
- "应设置剩余电流保护电器" → {"component": "配电系统", "action": "设置", "obj": "剩余电流保护电器"}
- "严禁使用TN-C系统" → {"component": "配电系统", "action": "使用", "obj": "TN-C系统", "requirement": "prohibited"}
- "电缆应敷设在电缆桥架内" → {"component": "电缆", "action": "敷设", "obj": "电缆桥架"}
- "导体应承受热稳定" → {"component": "导体", "action": "承受", "obj": "热稳定"}

## 术语章节示例（X.0.N）
原文：`2.0.5 直接接触防护 无故障条件下的电击防护。`
→ is_term_definition: true, terms: [{"term_name": "直接接触防护", "definition": "无故障条件下的电击防护"}]

## 条款引用示例
原文：`应按本规范第5.2.4条第1款的规定，且应符合本规范第5.2.8条的要求。`
→ referenced_clauses: ["5.2.4", "5.2.8"]

原文：`线路敷设应符合GB/T16895.15的规定，并按本规范第6.3.1条执行。`
→ referenced_clauses: ["6.3.1"], referenced_standards: ["GB/T16895.15"]

原文：`见3.2.5条和4.1.2条的规定，或参照第5.1.3条执行。`
→ referenced_clauses: ["3.2.5", "4.1.2", "5.1.3"]

原文：`第1、2、3条的要求应同时满足。`
→ referenced_clauses: ["1", "2", "3"]

原文：`Article 3.2.5 和 Clause 4.1.2 规定了具体要求。`
→ referenced_clauses: ["3.2.5", "4.1.2"]

请输出 JSON：
```json
{{
    "clauses": [
        {{
            "clause_id": "5.2.13",
            "clause_title": "TN系统配电线路的保护",
            "clause_content": "TN系统配电线路的保护...",
            "requirement_type": "mandatory",
            "is_term_definition": false,
            "triplets": [
                {{"component": "配电线路", "action": "选用", "obj": "短路保护电器", "condition": "", "requirement": "mandatory"}}
            ],
            "terms": [],
            "referenced_tables": [],
            "referenced_formulas": [],
            "referenced_clauses": ["5.2.4", "5.2.8"],
            "referenced_standards": ["GB/T16895.15"],
            "formula_content": null,
            "clause_items": [
                {{
                    "item_number": "1",
                    "item_content": "按敷设方式及环境条件确定的导体载流量，不应小于计算电流",
                    "triplets": [
                        {{"component": "导体", "action": "承受", "obj": "计算电流"}}
                    ]
                }}
            ]
        }}
    ]
}}
```

如果没有发现条文，返回：
```json
{{"clauses": []}}
```"""

    # 要素提取 Prompt
    ELEMENT_SYSTEM_PROMPT = """你是一个工程规范文档的要素提取专家。

你的任务是从规范文本中提取所有技术要素。

## 要素类型

1. **Term（术语）**：条文定义的专门术语
   - 例："预期接触电压"、"直接接触防护"
   - 属性：name、definition

2. **Component（组件）**：设备、系统、材料
   - 例："剩余电流保护电器(RCD)"、"配电变压器"
   - 属性：name、abbreviation、type

3. **Parameter（参数）**：技术参数和数值
   - 例："最小截面积：4mm²"、"额定电流：16A"
   - 属性：name、value、unit、condition

4. **Formula（公式）**：计算公式
   - 例："S ≥ I·t / k"
   - 属性：expression、variables

5. **Table Reference（表格引用）**：
   - 例："见表3.2.1"

请保持 JSON 格式输出。"""

    ELEMENT_USER_PROMPT = """请从以下文本中提取所有技术要素：

{document_text}

请输出 JSON 格式：
```json
{{
    "elements": [
        {{
            "element_type": "Term",
            "name": "预期接触电压",
            "definition": "电气装置发生故障时，可能出现在两个可同时触及的外露导电部分间的电压",
            "source_clause_id": "2.0.12"
        }},
        {{
            "element_type": "Component",
            "name": "剩余电流保护电器",
            "abbreviation": "RCD",
            "description": "检测漏电电流并动作的防护电器",
            "source_clause_id": "3.2.8"
        }},
        {{
            "element_type": "Parameter",
            "name": "最小截面积",
            "value": "4mm²",
            "condition": "铜芯导线固定敷设",
            "source_clause_id": "3.2.2"
        }},
        {{
            "element_type": "Formula",
            "name": "热稳定校验公式",
            "expression": "S ≥ I·t / k",
            "variables": [
                {{"name": "S", "description": "导体截面积(mm²)"}},
                {{"name": "I", "description": "故障电流(A)"}}
            ],
            "source_clause_id": "3.2.14"
        }}
    ]
}}
```

如果没有发现要素，返回：
```json
{{"elements": []}}
```"""

    # 关联分析 Prompt
    RELATION_SYSTEM_PROMPT = """你是一个知识图谱关系分析专家。

你的任务是为提取的条文和要素建立语义关联关系。

## 关联模式

1. **ELEMENT_IN_CLAUSE**: 要素属于条文
2. **CLAUSE_IN_CHAPTER**: 条文属于章节
3. **MANDATES/RECOMMENDS/PROHIBITS**: 条文对动作的要求
4. **HAS_CONDITION**: 条文的前提条件
5. **OPERATES_ON**: 动作作用于对象

## 输出要求

请输出 JSON 格式：
```json
{{
    "relations": [
        {{
            "relation_type": "ELEMENT_IN_CLAUSE",
            "source": "剩余电流保护电器",
            "source_type": "Component",
            "target": "3.2.8",
            "target_type": "Clause"
        }},
        {{
            "relation_type": "MANDATES",
            "source": "3.2.8",
            "source_type": "Clause",
            "target": "设置RCD保护",
            "target_type": "Action"
        }}
    ]
}}
```"""

    RELATION_USER_PROMPT = """请为以下条文和要素建立关联关系：

条文列表：
{clauses_json}

要素列表：
{elements_json}

请输出关联关系 JSON。"""

    # =========================================================================
    # 配置参数
    # =========================================================================

    # LLM 调用配置
    MAX_RETRIES = 3                    # 最大重试次数
    INITIAL_RETRY_DELAY = 2             # 初始重试延迟（秒）
    MAX_RETRY_DELAY = 30               # 最大重试延迟（秒）
    RETRY_MULTIPLIER = 2               # 延迟倍增因子

    # Token 限制
    MAX_CHARS_PER_CHAPTER = 12000       # 每章节最大字符数（优化：从3000→12000，减少调用次数）
    MAX_CHARS_FOR_TOC = 8000           # 目录识别最大字符数

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        progress_callback: Optional[Callable] = None
    ):
        """
        初始化 LLM 驱动的分块器

        Args:
            llm_client: LLM 客户端，默认创建新实例
            progress_callback: 进度回调函数，格式: callback(progress, message)
        """
        self.llm_client = llm_client
        self.progress_callback = progress_callback
        self.logger = logging.getLogger('mirofish.llm_chunker')

    @property
    def client(self) -> LLMClient:
        """获取或创建 LLM 客户端"""
        if self.llm_client is None:
            self.llm_client = LLMClient()
        return self.llm_client

    def _refine_chapter_positions(
        self,
        text: str,
        chapter_toc: List[Dict]
    ) -> List[Dict]:
        """
        精化章节位置边界 - 通过文本匹配获取精确位置

        策略：
        1. 在文档中查找章节标题的实际位置
        2. 计算相邻章节之间的精确边界

        Args:
            text: 完整文档文本
            chapter_toc: LLM 提取的目录数据

        Returns:
            精化后的章节位置列表
        """
        self.logger.info(f"[章节位置精化] 开始精化 {len(chapter_toc)} 个章节的位置...")

        refined_chapters = []

        for i, chapter in enumerate(chapter_toc):
            title = chapter.get("title", "")
            chapter_num = chapter.get("chapter_number", i + 1)

            # 尝试在文本中查找章节标题
            # 多种匹配模式
            patterns = [
                title,  # 精确标题
                f"第{chapter_num}章",  # 章节标记
                f"{chapter_num}\\s*[.、]\\s*{re.escape(title)}",  # 编号+标题
                f"{re.escape(title)}",  # 仅标题
            ]

            found_pos = None
            for pattern in patterns:
                try:
                    match = re.search(pattern, text[:50000], re.IGNORECASE)  # 限制搜索范围
                    if match:
                        found_pos = match.start()
                        self.logger.info(f"[章节位置精化] 章节 {chapter_num} '{title}': 位置 {found_pos}")
                        break
                except Exception as e:
                    self.logger.warning(f"[章节位置精化] 模式 '{pattern}' 匹配失败: {e}")
                    continue

            if found_pos is not None:
                chapter["start_position"] = found_pos
            else:
                # 使用 LLM 提供的估算位置
                chapter["start_position"] = chapter.get("start_position", 0)
                self.logger.warning(f"[章节位置精化] 章节 {chapter_num} '{title}': 使用估算位置 {chapter['start_position']}")

            refined_chapters.append(chapter)

        # 计算相邻章节之间的边界
        total_len = len(text)
        for i, chapter in enumerate(refined_chapters):
            if i < len(refined_chapters) - 1:
                next_chapter = refined_chapters[i + 1]
                # 下一个章节的开始位置 - 1 作为当前章节的结束位置
                next_start = next_chapter.get("start_position", total_len)
                chapter["end_position"] = max(chapter["start_position"], next_start - 1)
            else:
                # 最后一个章节，延伸到文档末尾
                chapter["end_position"] = total_len

            # 确保边界有效
            if chapter["end_position"] <= chapter["start_position"]:
                chapter["end_position"] = min(chapter["start_position"] + 10000, total_len)

        self.logger.info(f"[章节位置精化] ✅ 精化完成")
        return refined_chapters

    def _build_clause_registry(self, md_content: str) -> Dict[str, Dict]:
        """
        扫描 md_content，提取所有条款编号及其位置信息，构建注册表。

        通用匹配模式（支持多种文档格式）：
        - X.Y.Z 编号（如 "3.2.5 配电线路..."、"3.2.5配电线路..."）
        - 第X.Y.Z条  显式条款标记
        - 第A.B条    附录条款（字母编号，如 A.0.7）
        - Article/Clause X.Y.Z  英文格式
        - § X.Y.Z  段落符号格式
        - 【X.Y.Z】  方括号格式
        - Markdown 标题包裹格式（## 3.2.5 直接接触防护）

        Args:
            md_content: MinerU 解析的 Markdown 内容

        Returns:
            注册表: {条款编号: {line_range, title, is_appendix, page_idx, chunk_id, section_title}}
        """
        registry: Dict[str, Dict] = {}

        # 通用条款编号匹配模式（支持多种格式）
        CLAUSE_PATTERNS = [
            # 模式1：行首为 X.Y.Z 编号，可选空格（如 "3.2.5配电线路..." 或 "3.2.5 配电线路..."）
            (re.compile(r'^(\d+(?:\.\d+)+)\s*'), False),
            # 模式2：带"第"前缀的数字条款（如 "第3.2.5条配电线路..."）
            (re.compile(r'^第(\d+(?:\.\d+)+)条\s*'), False),
            # 模式3：附录条款（如 "第A.0.7条..."）
            (re.compile(r'^第([A-Z](?:\.\d+)+)条\s*'), True),
            # 模式4：Article/条款 英文格式（如 "Article 3.2.5" 或 "Clause 3.2.5"）
            (re.compile(r'^(?:Article|Clause|Art\.)\s+(\d+(?:\.\d+)+)', re.I), False),
            # 模式5：段落符号格式（如 "§ 3.2.5" 或 "§3.2.5"）
            (re.compile(r'^§\s*(\d+(?:\.\d+)+)'), False),
            # 模式6：方括号格式（如 "【3.2.5】配电线路..."）
            (re.compile(r'^【(\d+(?:\.\d+)+)】\s*'), False),
            # 模式7：X.Y.Z. 格式（带尾部句点）
            (re.compile(r'^(\d+(?:\.\d+)+)\.\s*'), False),
        ]

        # Markdown 标题前缀（匹配后去掉再处理）
        MARKDOWN_PREFIXES = re.compile(r'^#{1,6}\s+')

        for i, line in enumerate(md_content.split('\n')):
            stripped = line.strip()
            if not stripped:
                continue

            # 去掉 Markdown 标题前缀后再匹配
            md_match = MARKDOWN_PREFIXES.match(stripped)
            if md_match:
                # Markdown 标题行，检查去掉前缀后的内容
                inner = stripped[md_match.end():]
                # 用各种模式匹配内部内容
                for pattern, is_appendix in CLAUSE_PATTERNS:
                    m = pattern.match(inner)
                    if m:
                        cid = m.group(1)
                        if cid not in registry:
                            registry[cid] = {
                                "line_range": i,
                                "title": stripped[:120],
                                "is_appendix": is_appendix,
                                "page_idx": None,
                                "chunk_id": None,
                                "section_title": None,
                            }
                        break
                continue

            # 普通行：直接用各种模式匹配
            for pattern, is_appendix in CLAUSE_PATTERNS:
                m = pattern.match(stripped)
                if m:
                    cid = m.group(1)
                    if cid not in registry:
                        registry[cid] = {
                            "line_range": i,
                            "title": stripped[:120],
                            "is_appendix": is_appendix,
                            "page_idx": None,
                            "chunk_id": None,
                            "section_title": None,
                        }
                    break

        self.logger.info(f"[条款注册表] 构建完成: {len(registry)} 个条款")
        return registry

    def _build_chunk_position_index(self, chunks: List[Dict]) -> Dict[str, Any]:
        """
        为 chunks.json 构建位置索引，供 clause 文本匹配使用。

        索引结构：
        - by_id: chunk_id → chunk
        - by_clause_id: 条款编号 → chunk（从 title chunk 的 content 提取）
        - by_page: page_idx → [chunks]
        - text_ngrams: ngram(20字符) → [(chunk_id, char_pos)]
        - by_table_caption: 表格编号 → table chunk（从 table_caption 提取）

        Args:
            chunks: chunks.json 中的 chunk 列表

        Returns:
            位置索引字典
        """
        index: Dict[str, Any] = {
            'by_id': {},
            'by_clause_id': {},
            'by_page': {},
            'text_ngrams': {},
            'by_table_caption': {},
        }

        for chunk in chunks:
            chunk_id = chunk.get('chunk_id', '')
            chunk_type = chunk.get('type', '')
            page_idx = chunk.get('page_idx', 0)
            text = chunk.get('content', '') or ''
            caption = chunk.get('table_caption', '') or ''

            index['by_id'][chunk_id] = chunk

            # 按 page_idx 分组
            if page_idx not in index['by_page']:
                index['by_page'][page_idx] = []
            index['by_page'][page_idx].append(chunk)

            # 从 title chunk 提取 clause_id（如 "3.2.5 配电线路..."）
            if chunk_type == 'title' and text:
                # 匹配 X.Y.Z 格式开头
                m = re.match(r'^(\d+(?:\.\d+)+)\s+', text.strip())
                if m:
                    cid = m.group(1)
                    if cid not in index['by_clause_id']:
                        index['by_clause_id'][cid] = []
                    index['by_clause_id'][cid].append(chunk)

            # 从 text chunk 也提取（条款正文块可能以编号开头）
            if chunk_type == 'text' and text:
                m = re.match(r'^(\d+(?:\.\d+)+)\s+', text.strip())
                if m:
                    cid = m.group(1)
                    if cid not in index['by_clause_id']:
                        index['by_clause_id'][cid] = []
                    index['by_clause_id'][cid].append(chunk)

            # 从 table chunk 的 table_caption 提取表格编号
            if chunk_type == 'table' and caption:
                # "表3.2.2" 或 "表 3.2.2" 格式
                m = re.search(r'表[ ]?([A-Z]?[\d\.]+)', caption)
                if m:
                    table_num = m.group(1)
                    if table_num not in index['by_table_caption']:
                        index['by_table_caption'][table_num] = []
                    index['by_table_caption'][table_num].append(chunk)

            # 构建 n-gram 倒排索引（20字符步长10）
            for start in range(0, max(len(text) - 20, 0), 10):
                ngram = text[start:start + 20]
                if ngram not in index['text_ngrams']:
                    index['text_ngrams'][ngram] = []
                index['text_ngrams'][ngram].append((chunk_id, start))

        self.logger.info(
            f"[位置索引] 构建完成: {len(index['by_id'])} chunks, "
            f"{len(index['by_clause_id'])} clause_id, "
            f"{len(index['by_table_caption'])} table_caption, "
            f"{len(index['text_ngrams'])} ngrams"
        )
        return index

    def _resolve_clause_position(
        self,
        clause_id: str,
        clause_text: str,
        position_index: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        将 clause 文本匹配到 chunks.json 中的对应块，返回位置信息。

        匹配策略（优先级递减）：
        1. clause_id 精确匹配 → 直接命中 title/text chunk
        2. 子串包含匹配 → clause_text 在 chunk.content 中
        3. n-gram 交集匹配 → 统计共同 n-gram 数量

        Args:
            clause_id: 条款编号（如 "3.2.5"）
            clause_text: 条款文本内容
            position_index: _build_chunk_position_index 构建的索引

        Returns:
            位置信息字典（含 page_idx, bbox_pdf, chunk_id 等）
        """
        result: Dict[str, Any] = {}

        # 策略1: clause_id 精确匹配 title chunk
        if clause_id and clause_id in position_index['by_clause_id']:
            chunks = position_index['by_clause_id'][clause_id]
            # 优先选 title chunk，其次选 text chunk
            best = next((c for c in chunks if c.get('type') == 'title'), chunks[0])
            result = self._extract_position_from_chunk(best)
            self.logger.debug(f"[位置解析] clause_id={clause_id} 策略1命中: chunk={best.get('chunk_id')}")
            return result

        # 策略2: 子串包含匹配
        if clause_text:
            clause_stripped = clause_text.strip()
            best_match = None
            best_ratio = 0.0

            for chunk in position_index['by_id'].values():
                chunk_text = (chunk.get('content') or '').strip()
                if not chunk_text or len(chunk_text) < 10:
                    continue
                # 检查 clause_text 是否在 chunk_text 中
                if clause_stripped in chunk_text:
                    ratio = len(clause_stripped) / max(len(chunk_text), 1)
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_match = chunk

            if best_match and best_ratio >= 0.3:
                result = self._extract_position_from_chunk(best_match)
                self.logger.debug(f"[位置解析] clause_id={clause_id} 策略2命中: ratio={best_ratio:.2f}")
                return result

            # 策略3: n-gram 交集匹配
            ngram_matches: Dict[str, int] = {}
            for start in range(0, max(len(clause_stripped) - 20, 0), 5):
                ngram = clause_stripped[start:start + 20]
                if ngram in position_index['text_ngrams']:
                    for (chunk_id, _) in position_index['text_ngrams'][ngram]:
                        ngram_matches[chunk_id] = ngram_matches.get(chunk_id, 0) + 1

            if ngram_matches:
                best_chunk_id = max(ngram_matches, key=ngram_matches.get)
                best_count = ngram_matches[best_chunk_id]
                if best_count >= 3:
                    chunk = position_index['by_id'].get(best_chunk_id, {})
                    result = self._extract_position_from_chunk(chunk)
                    self.logger.debug(f"[位置解析] clause_id={clause_id} 策略3命中: chunk={best_chunk_id} ngram_count={best_count}")
                    return result

        self.logger.debug(f"[位置解析] clause_id={clause_id} 未匹配到 chunks")
        return result

    def _resolve_table_reference(
        self,
        table_ref: str,
        position_index: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        将 "表X.Y.Z" 引用匹配到 chunks.json 中的 table chunk。

        Args:
            table_ref: 表格引用文本（如 "表3.2.2"、"表 3.2.2"）
            position_index: 位置索引

        Returns:
            table chunk 的位置信息
        """
        # 提取编号：表3.2.2 → 3.2.2
        m = re.search(r'表[ ]?([A-Z]?[\d\.]+)', table_ref)
        if not m:
            return {}
        table_num = m.group(1)

        if table_num in position_index['by_table_caption']:
            chunks = position_index['by_table_caption'][table_num]
            best = next((c for c in chunks if c.get('type') == 'table'), chunks[0])
            result = self._extract_position_from_chunk(best)
            self.logger.debug(f"[表格解析] ref={table_ref} 命中: chunk={best.get('chunk_id')}")
            return result

        return {}

    def _extract_position_from_chunk(self, chunk: Dict) -> Dict[str, Any]:
        """
        从 chunk 提取所有位置相关字段。
        """
        if not chunk:
            return {}
        return {
            'page_idx': chunk.get('page_idx'),
            'bbox_pdf': chunk.get('bbox_pdf'),
            'bbox_viewport': chunk.get('bbox_viewport'),
            'chunk_id': chunk.get('chunk_id'),
            'page_width': chunk.get('page_width'),
            'page_height': chunk.get('page_height'),
            'source': chunk.get('source'),
            # table 特有字段
            'type': chunk.get('type'),
            'table_caption': chunk.get('table_caption'),
            'table_content': chunk.get('table_content'),
        }

    def _parse_md_content_sections(
        self,
        md_content: str,
        title_chunks: List[Dict],
        clause_registry: Dict[str, Dict]
    ) -> List[Dict]:
        """
        将 md_content 按章节标题切分为段落，并关联物理定位。

        流程：
        1. 按 52 个 # 标题切分 md_content，得到 md_sections
        2. 用标题相似度将 md_section 与 title_chunk 匹配
        3. 用 title_chunk 的 page_idx 为 md_section 补充 page 信息
        4. 用 page_idx 将 clause_registry 中的条款补充 page_idx 和 chunk_id

        Args:
            md_content: MinerU 解析的 Markdown 内容
            title_chunks: chunks.json 中 type=title 的 chunks
            clause_registry: _build_clause_registry 构建的条款注册表

        Returns:
            md_sections: [{header, body, line_range, page_idx, chunk_id, section_title}, ...]
        """
        lines = md_content.split('\n')

        # Step 1: 提取所有 # 标题行的位置
        header_lines = []  # [(line_idx, header_text), ...]
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('# '):
                header_lines.append((i, stripped[2:].strip()))

        if not header_lines:
            self.logger.warning("[md_content] 未找到 # 标题，返回全文作为单个 section")
            return [{
                "header": "",
                "body": md_content,
                "line_range": (0, len(lines) - 1),
                "page_idx": None,
                "chunk_id": None,
                "section_title": None,
            }]

        # Step 2: 切分 md_content 为 sections
        md_sections: List[Dict] = []
        for idx, (h_line, h_text) in enumerate(header_lines):
            next_h_line = header_lines[idx + 1][0] if idx + 1 < len(header_lines) else len(lines)
            body_lines = lines[h_line + 1:next_h_line]
            # 去掉末尾空行
            while body_lines and not body_lines[-1].strip():
                body_lines.pop()
            body = '\n'.join(body_lines)

            md_sections.append({
                "header": h_text,
                "header_line": h_line,
                "body": body,
                "line_range": (h_line, next_h_line - 1),
                "page_idx": None,
                "chunk_id": None,
                "section_title": h_text,
            })

        self.logger.info(f"[md_content] 解析完成: {len(md_sections)} 个 sections")

        # Step 3: 标题匹配：将 md_sections 与 title_chunks 关联
        from difflib import SequenceMatcher

        def title_similarity(a: str, b: str) -> float:
            """计算两个标题文本的相似度"""
            return SequenceMatcher(None, a, b).ratio()

        # 按 page_idx 排序 title_chunks
        sorted_chunks = sorted(title_chunks, key=lambda c: c.get('page_idx', 0))

        for sec in md_sections:
            h = sec["header"]
            best_score = 0.0
            best_chunk = None
            for chunk in sorted_chunks:
                chunk_text = chunk.get('content', '')
                score = title_similarity(h, chunk_text)
                if score > best_score:
                    best_score = score
                    best_chunk = chunk
            if best_chunk and best_score > 0.3:
                sec["page_idx"] = best_chunk.get('page_idx')
                sec["chunk_id"] = best_chunk.get('chunk_id')
                sec["section_title"] = best_chunk.get('content', sec['header'])

        # Step 4: 用 section 的 page_idx 为 clause_registry 补充 page_idx 和 chunk_id
        for cid, entry in clause_registry.items():
            sec_line = entry.get("line_range", 0)
            # 找到 line_range 最接近的 md_section
            for sec in md_sections:
                l_start, l_end = sec.get("line_range", (0, 0))
                if l_start <= sec_line <= l_end:
                    if sec.get("page_idx") is not None:
                        entry["page_idx"] = sec["page_idx"]
                    if sec.get("chunk_id") is not None:
                        entry["chunk_id"] = sec["chunk_id"]
                    if sec.get("section_title"):
                        entry["section_title"] = sec["section_title"]
                    break

        matched = sum(1 for s in md_sections if s.get("page_idx") is not None)
        self.logger.info(f"[md_content] title_chunks 匹配完成: {matched}/{len(md_sections)} 个 section 有 page_idx")
        reg_matched = sum(1 for e in clause_registry.values() if e.get("page_idx") is not None)
        self.logger.info(f"[md_content] clause_registry 补充完成: {reg_matched}/{len(clause_registry)} 个条款有 page_idx")

        return md_sections

    def _clause_to_dict(self, clause: ClauseSegment) -> Dict[str, Any]:
        """将 ClauseSegment 转换为字典（委托给模块级函数）"""
        return clause_to_dict(clause)

    def _element_to_dict(self, element: ElementSegment) -> Dict[str, Any]:
        """将 ElementSegment 转换为字典（委托给模块级函数）"""
        return element_to_dict(element)

    def _save_checkpoint(
        self,
        project_id: str,
        checkpoint: ChunkCheckpoint
    ) -> None:
        """保存检查点到磁盘"""
        try:
            ProjectManager.save_chunk_checkpoint_v2(project_id, checkpoint)
            self.logger.debug(f"[检查点] 已保存检查点: 项目={project_id}, 章节={checkpoint.current_chapter_index + 1}/{checkpoint.total_chapters}")
        except Exception as e:
            self.logger.error(f"[检查点] 保存失败: {e}")

    def _build_result_from_checkpoint(
        self,
        checkpoint: ChunkCheckpoint
    ) -> HierarchicalChunkResult:
        """从检查点构建 HierarchicalChunkResult"""
        from ..models.clause import RequirementType, SemanticTriplet, ClauseItem

        result = HierarchicalChunkResult()

        # 恢复章节
        for cp in checkpoint.chapter_plan:
            section = SectionSegment(
                chapter_number=cp.chapter_number,
                title=cp.title,
                content=""  # 内容会在重新处理时填充
            )
            result.sections.append(section)

        # 恢复条文
        for cd in checkpoint.completed_clauses:
            req_type_str = cd.get('requirement_type', 'recommended')
            try:
                req_type = RequirementType(req_type_str)
            except ValueError:
                req_type = RequirementType.RECOMMENDED

            # 恢复 clause_items（含款/项级三元组）
            clause_items = []
            for ci_data in cd.get('clause_items', []):
                ci_triplets = []
                for t in ci_data.get('triplets', []):
                    ci_triplets.append(SemanticTriplet(
                        component=t.get('component', ''),
                        action=t.get('action', ''),
                        obj=t.get('obj', ''),
                        condition=t.get('condition', ''),
                        requirement=t.get('requirement', 'mandatory')
                    ))
                clause_items.append(ClauseItem(
                    item_number=ci_data.get('item_number', ''),
                    item_content=ci_data.get('item_content', ''),
                    components=ci_data.get('components', []),
                    actions=ci_data.get('actions', []),
                    conditions=ci_data.get('conditions', []),
                    objects=ci_data.get('objects', []),
                    triplets=ci_triplets
                ))

            # 恢复条文级语义三元组
            clause_triplets = []
            for t in cd.get('triplets', []):
                clause_triplets.append(SemanticTriplet(
                    component=t.get('component', ''),
                    action=t.get('action', ''),
                    obj=t.get('obj', ''),
                    condition=t.get('condition', ''),
                    requirement=t.get('requirement', 'mandatory')
                ))

            clause = ClauseSegment(
                clause_id=cd.get('clause_id', ''),
                clause_title=cd.get('clause_title', ''),
                content=cd.get('content', ''),
                requirement_type=req_type,
                triplets=clause_triplets,
                parent_chapter=cd.get('parent_chapter'),
                is_term_definition=cd.get('is_term_definition', False),
                terms=cd.get('terms', []),
                formula_content=cd.get('formula_content'),
                clause_items=clause_items,
                metadata=cd.get('metadata', {}),
                source=cd.get('source', ''),
                page=cd.get('page')
            )
            result.clauses.append(clause)

        # 恢复要素
        from ..models.clause import ElementType
        for ed in checkpoint.completed_elements:
            elem_type_str = ed.get('element_type', 'parameter')
            try:
                elem_type = ElementType(elem_type_str)
            except ValueError:
                elem_type = ElementType.PARAMETER

            element = ElementSegment(
                element_type=elem_type,
                source_id=ed.get('source_id', ''),
                key=ed.get('key', ''),
                value=ed.get('value', ''),
                unit=ed.get('unit', ''),
                condition=ed.get('condition', ''),
                abbreviation=ed.get('abbreviation', ''),
                definition=ed.get('definition', ''),
                content=ed.get('content', ''),
                metadata=ed.get('metadata', {})
            )
            result.elements.append(element)

        return result

    def _report_progress(self, progress: float, message: str, checkpoint_info: Optional[Dict] = None) -> None:
        """报告进度"""
        self.logger.info(message)
        if self.progress_callback:
            try:
                # 支持带检查点信息的回调
                if checkpoint_info is not None:
                    self.progress_callback(progress, message, checkpoint_info)
                else:
                    self.progress_callback(progress, message)
            except Exception as e:
                self.logger.warning(f"进度回调失败: {e}")

    def _call_llm_with_retry(
        self,
        messages: List[Dict],
        temperature: float = 0.3,
        max_tokens: int = 4096 * 4
    ) -> Dict[str, Any]:
        """
        使用指数退避重试机制调用 LLM

        Args:
            messages: 对话消息
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            解析后的 JSON 响应

        Raises:
            LLMChunkerError: 重试次数耗尽时抛出
        """
        last_error = None
        retry_delay = self.INITIAL_RETRY_DELAY

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self.client.chat_json(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                return response

            except Exception as e:
                last_error = e
                self.logger.warning(f"LLM 调用失败 (尝试 {attempt + 1}/{self.MAX_RETRIES + 1}): {e}")

                if attempt < self.MAX_RETRIES:
                    # 指数退避 + 抖动
                    jitter = random.uniform(0, 1)
                    actual_delay = min(retry_delay + jitter, self.MAX_RETRY_DELAY)
                    self.logger.info(f"等待 {actual_delay:.1f} 秒后重试...")
                    time.sleep(actual_delay)
                    retry_delay *= self.RETRY_MULTIPLIER
                else:
                    self.logger.error(f"LLM 重试次数耗尽: {e}")

        raise LLMChunkerError(f"LLM 调用失败，重试 {self.MAX_RETRIES} 次后仍失败: {last_error}")

    def chunk(
        self,
        text_chunks: List[TextChunk],
        progress_callback: Optional[Callable] = None,
        resume_from_chapter: int = 0,
        checkpoint: Optional[ChunkCheckpoint] = None,
        project_id: Optional[str] = None,
        md_content: Optional[str] = None,
        chunks_data: Optional[List[Dict]] = None
    ) -> HierarchicalChunkResult:
        """
        主入口：LLM 驱动的三级分块（渐进式，支持增强版断点恢复）

        策略：渐进式披露
        1. 先读取目录（章节结构），记住位置
        2. 基于章节分段处理
        3. 每段独立提取条文和要素
        4. 根据要素和条文在 PDF 中标注位置

        Args:
            text_chunks: 原始文本块列表
            progress_callback: 进度回调，签名: (progress, message, checkpoint_info)
            resume_from_chapter: 从第几个章节恢复（0表示从头开始）
            checkpoint: 增强版检查点（用于断点恢复）
            project_id: 项目ID（用于保存检查点）

        Returns:
            HierarchicalChunkResult: 包含所有层级分块的结果
        """
        import time
        start_time = time.time()

        # 更新回调
        if progress_callback:
            self.progress_callback = progress_callback

        result = HierarchicalChunkResult()

        # 先合并同条款的相邻 chunks，再拼接为全文
        text_chunks = self.merge_adjacent_chunks(text_chunks)
        full_text = self._merge_text_chunks(text_chunks)
        source_info = self._get_source_info(text_chunks)

        self.logger.info(f"[LLM分块] 开始分析，文本长度: {len(full_text)}")
        self.logger.info(f"[LLM分块] 来源信息: {source_info}")
        self._report_progress(0.0, "🚀 开始智能标注分析...")

        # =====================================================================
        # Step 0: 构建位置索引 + 处理 md_content
        # =====================================================================
        # 构建 chunks 位置索引（无论是否有 md_content 都构建）
        self._position_index: Dict[str, Any] = {}
        if chunks_data:
            # 使用传入的 chunks_data（来自 chunks.json，已带物理位置）
            self._position_index = self._build_chunk_position_index(chunks_data)
            self.logger.info(f"[LLM分块] 位置索引构建完成: {len(chunks_data)} chunks")
        elif text_chunks:
            # fallback：从 text_chunks 构建
            raw_chunks = []
            for c in text_chunks:
                raw_chunks.append({
                    'chunk_id': getattr(c, 'id', '') or c.metadata.get('chunk_id', ''),
                    'type': c.metadata.get('type', 'text'),
                    'content': c.text,
                    'page_idx': c.metadata.get('page'),
                    'bbox_pdf': c.metadata.get('bbox'),
                    'bbox_viewport': c.metadata.get('bbox'),
                    'page_width': c.metadata.get('page_width'),
                    'page_height': c.metadata.get('page_height'),
                    'source': c.metadata.get('source'),
                    'table_caption': c.metadata.get('table_caption', ''),
                    'table_content': c.metadata.get('table_content', ''),
                })
            self._position_index = self._build_chunk_position_index(raw_chunks)

        self._clause_registry: Dict[str, Dict] = {}
        md_sections: List[Dict] = []
        title_chunks = [c for c in text_chunks if c.metadata.get('type') == 'title']
        if md_content:
            self.logger.info("[LLM分块] 检测到 md_content，开始解析条款注册表...")
            self._clause_registry = self._build_clause_registry(md_content)
            # 将 TextChunk 对象转换为 dict（_parse_md_content_sections 内部用 .get()）
            title_chunks_dicts = [
                {"content": c.text, "page_idx": c.metadata.get("page_idx"), "chunk_id": c.metadata.get("chunk_id")}
                for c in title_chunks
            ]
            md_sections = self._parse_md_content_sections(
                md_content, title_chunks_dicts, self._clause_registry
            )
            self.logger.info(
                f"[LLM分块] md_content 解析完成: {len(md_sections)} sections, "
                f"{len(self._clause_registry)} 个条款注册"
            )

        # =====================================================================
        # Step 1: 读取目录 - 识别章节结构
        # =====================================================================
        self._report_progress(0.02, "📖 LLM 提取目录结构...")
        self.logger.info("[LLM分块] Step 1/4: 开始提取目录结构")

        chapter_toc = self._extract_table_of_contents(full_text)

        # 精化章节位置边界
        refined_chapters = self._refine_chapter_positions(full_text, chapter_toc)
        chapter_count = len(refined_chapters)

        self.logger.info(f"[LLM分块] ✅ 目录提取完成: {chapter_count} 个章节")
        self.logger.info(f"[LLM分块] 章节列表: {[c.get('title', 'N/A') for c in refined_chapters]}")
        self._report_progress(
            0.1,
            f"✅ 读取目录完成: {chapter_count} 个章节"
        )

        # =====================================================================
        # Step 2: 初始化或恢复检查点
        # =====================================================================
        if checkpoint and checkpoint.chapter_plan:
            # 从检查点恢复
            self.logger.info(f"[LLM分块] 从检查点恢复: 已处理 {len(checkpoint.completed_clauses)} 条文, {len(checkpoint.completed_elements)} 要素")
            current_checkpoint = checkpoint
            start_index = checkpoint.current_chapter_index + 1  # 从下一个章节继续

            # 从检查点恢复已完成的数据
            result = self._build_result_from_checkpoint(checkpoint)
        else:
            # 新任务，初始化检查点
            start_index = resume_from_chapter
            current_checkpoint = ChunkCheckpoint(
                chapter_plan=[
                    ChapterPlan(
                        chapter_number=ch.get("chapter_number", i + 1),
                        title=ch.get("title", f"章节{i + 1}"),
                        start_position=ch.get("start_position", 0),
                        end_position=ch.get("end_position", len(full_text)),
                        status=ChapterStatus.PENDING,
                        chapter_type=ch.get("chapter_type", "normative")
                    )
                    for i, ch in enumerate(refined_chapters)
                ],
                current_chapter_index=-1,
                total_chapters=chapter_count,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat()
            )

        if start_index > 0:
            self.logger.info(f"[LLM分块] 从章节 {start_index} 恢复，跳过前 {start_index} 个章节")

        # =====================================================================
        # Step 3: 基于章节分段 - 渐进式处理每个章节
        # =====================================================================
        self.logger.info(f"[LLM分块] Step 2/4: 开始处理 {chapter_count} 个章节")
        all_clauses = list(result.clauses)  # 已有数据
        all_elements = list(result.elements)  # 已有数据

        for i, chapter in enumerate(refined_chapters):
            chapter_num = chapter.get("chapter_number", i + 1)
            chapter_title = chapter.get("title", f"章节{chapter_num}")
            start_pos = chapter.get("start_position", 0)
            end_pos = chapter.get("end_position", len(full_text))

            # 跳过已完成的章节
            if i < start_index:
                self.logger.info(f"[LLM分块] 跳过章节 {chapter_num} (已处理)")
                continue

            # 更新检查点：设置当前章节为处理中
            current_checkpoint.current_chapter_index = i
            if i < len(current_checkpoint.chapter_plan):
                current_checkpoint.chapter_plan[i].status = ChapterStatus.PROCESSING
                current_checkpoint.chapter_plan[i].started_at = datetime.now().isoformat()

            # 保存检查点（处理中状态）
            if project_id:
                self._save_checkpoint(project_id, current_checkpoint)

            # 计算进度
            base_progress = 0.1
            chapter_progress_base = 0.1 + (i / chapter_count) * 0.6

            self.logger.info(f"[LLM分块] ▶ 处理章节 {chapter_num}/{chapter_count}: {chapter_title}")
            self.logger.info(f"[LLM分块]   - 位置范围: [{start_pos}, {end_pos}], 字符数: {end_pos - start_pos}")

            self._report_progress(
                chapter_progress_base,
                f"📑 处理章节 {chapter_num}/{chapter_count}: {chapter_title}...",
                checkpoint_info={
                    "current_chapter": chapter_num,
                    "total_chapters": chapter_count,
                    "completed_chapters": i,
                    "completed_clauses_count": len(all_clauses),
                    "completed_elements_count": len(all_elements),
                    "is_resuming": i > start_index
                }
            )

            # 提取该章节的文本
            chapter_text = full_text[start_pos:end_pos]

            # 提取章节内的条文 + 要素（优化：并行执行，两个 LLM 调用同时进行）
            para_start = time.time()
            self.logger.info(f"[LLM分块]   → LLM 并行提取条文 + 要素...")

            with ThreadPoolExecutor(max_workers=2) as executor:
                f_clauses = executor.submit(
                    self._extract_clauses_from_chapter,
                    chapter_text, source_info, chapter_num
                )
                f_elements = executor.submit(
                    self._extract_elements_from_chapter,
                    chapter_text, [], chapter_num  # 要素提取不依赖条文结果，传空列表
                )
                chapter_clauses = f_clauses.result()
                chapter_elements = f_elements.result()

            para_time = time.time() - para_start
            self.logger.info(f"[LLM分块]   ← 并行提取完成: {len(chapter_clauses)} 条文, {len(chapter_elements)} 要素 (耗时 {para_time:.1f}s)")

            # 为条文解析精确物理位置（从 chunks 位置索引匹配）
            for clause in chapter_clauses:
                pos_info = self._resolve_clause_position(
                    clause.clause_id,
                    clause.content,
                    self._position_index
                )
                if pos_info:
                    clause.metadata.update(pos_info)

                # 增强 table 引用：把 referenced_tables 解析为具体位置
                raw_tables = clause.metadata.get('referenced_tables', [])
                for table_ref in raw_tables:
                    table_info = self._resolve_table_reference(table_ref, self._position_index)
                    if table_info and table_info.get('type') == 'table':
                        ref = next(
                            (r for r in clause.referenced_clauses if r.clause_id == table_ref),
                            None
                        )
                        if ref:
                            ref.page_idx = table_info.get('page_idx')
                            ref.chunk_id = table_info.get('chunk_id')
                            ref.section_title = table_info.get('table_caption')
                        clause.metadata['referenced_table_positions'] = clause.metadata.get('referenced_table_positions', [])
                        clause.metadata['referenced_table_positions'].append({
                            'ref': table_ref,
                            'page_idx': table_info.get('page_idx'),
                            'bbox_pdf': table_info.get('bbox_pdf'),
                            'chunk_id': table_info.get('chunk_id'),
                            'table_caption': table_info.get('table_caption'),
                        })
            self.logger.info(f"[LLM分块]   → clause 位置解析完成: {sum(1 for c in chapter_clauses if c.metadata.get('page_idx'))}/{len(chapter_clauses)} 个 clause 有精确位置")

            # 为要素标注 PDF 位置（基于字符偏移估算）
            annotated_elements = self._annotate_positions(
                chapter_elements, chapter_text, source_info
            )
            self.logger.info(f"[LLM分块]   → PDF 位置标注完成: {len(annotated_elements)} 个要素")

            # 记录结果
            all_clauses.extend(chapter_clauses)
            all_elements.extend(annotated_elements)

            # 创建章节对象
            section = SectionSegment(
                chapter_number=chapter_num,
                title=chapter_title,
                content=chapter_text[:500]
            )
            result.sections.append(section)

            # 更新检查点：标记章节为完成
            current_checkpoint.current_chapter_index = i
            if i < len(current_checkpoint.chapter_plan):
                current_checkpoint.chapter_plan[i].status = ChapterStatus.COMPLETED
                current_checkpoint.chapter_plan[i].completed_at = datetime.now().isoformat()
                current_checkpoint.chapter_plan[i].clauses_count = len(chapter_clauses)
                current_checkpoint.chapter_plan[i].elements_count = len(chapter_elements)

            # 将新处理的条文和要素添加到检查点
            for clause in chapter_clauses:
                current_checkpoint.completed_clauses.append(self._clause_to_dict(clause))
            for element in annotated_elements:
                current_checkpoint.completed_elements.append(self._element_to_dict(element))

            # 保存检查点
            if project_id:
                self._save_checkpoint(project_id, current_checkpoint)

            # 章节处理完成
            chapter_time = time.time() - para_start
            self._report_progress(
                (i + 1) / chapter_count * 0.6 + 0.1,
                f"✅ 章节 {chapter_num} 完成: {len(chapter_clauses)} 条文, {len(chapter_elements)} 要素 (耗时 {chapter_time:.1f}s)",
                checkpoint_info={
                    "current_chapter": chapter_num,
                    "total_chapters": chapter_count,
                    "completed_chapters": i + 1,
                    "completed_clauses_count": len(all_clauses),
                    "completed_elements_count": len(all_elements),
                    "chapter_completed": True
                }
            )
            self.logger.info(f"[LLM分块] ✅ 章节 {chapter_num} 处理完成，检查点已保存")

        # =====================================================================
        # Step 4: 保存结果
        # =====================================================================
        self.logger.info(f"[LLM分块] Step 3/4: 保存分析结果")
        result.sections = list(result.sections) + [s for s in result.sections if s not in result.sections]
        result.clauses = all_clauses
        result.elements = all_elements

        # =====================================================================
        # Step 5: 汇总报告
        # =====================================================================
        total_time = time.time() - start_time
        self._report_progress(
            0.95,
            f"📊 标注分析汇总: {len(result.sections)} 章节, {len(all_clauses)} 条文, {len(all_elements)} 要素"
        )

        self._report_progress(1.0, f"✅ 智能标注分析完成! (总耗时 {total_time:.1f}s)")

        self.logger.info(
            f"[LLM分块] ✅ 分析完成 - 章节: {len(result.sections)}, 条文: {len(all_clauses)}, 要素: {len(all_elements)}, "
            f"总耗时: {total_time:.1f}s"
        )

        return result

    def chunk_single_text(
        self,
        text: str,
        progress_callback: Optional[Callable] = None,
        md_content: Optional[str] = None,
        chunks_data: Optional[List[Dict]] = None
    ) -> HierarchicalChunkResult:
        """单文本分块入口"""
        if progress_callback:
            self.progress_callback = progress_callback

        fake_chunk = TextChunk(text=text, metadata={})
        return self.chunk([fake_chunk], md_content=md_content, chunks_data=chunks_data)

    # =========================================================================
    # 核心提取方法
    # =========================================================================

    def _extract_table_of_contents(self, text: str) -> List[Dict]:
        """
        提取目录（章节结构）

        使用 LLM 识别文档章节结构，返回章节位置信息
        """
        self.logger.info("使用 LLM 提取目录结构...")

        try:
            response = self._call_llm_with_retry(
                messages=[
                    {"role": "system", "content": self.TOC_SYSTEM_PROMPT},
                    {"role": "user", "content": self.TOC_USER_PROMPT.format(
                        document_text=text[:self.MAX_CHARS_FOR_TOC]
                    )}
                ],
                temperature=0.3
            )

            toc = response.get("chapters", [])

            if not toc:
                self.logger.warning("LLM 返回空目录，使用默认章节划分")
                # 如果 LLM 返回空，将全文划分为单个章节
                toc = [{
                    "chapter_number": 1,
                    "title": "全文",
                    "start_position": 0,
                    "end_position": len(text)
                }]

            self.logger.info(f"LLM 提取目录: {len(toc)} 个章节")
            return toc

        except LLMChunkerError as e:
            self.logger.error(f"目录提取失败: {e}")
            raise

    def _extract_clauses_from_chapter(
        self,
        text: str,
        source_info: Dict[str, Any],
        chapter_num: int
    ) -> List[ClauseSegment]:
        """
        从章节文本中提取条文（使用 LLM）

        Args:
            text: 章节文本
            source_info: 来源信息
            chapter_num: 章节编号

        Returns:
            条文列表
        """
        try:
            response = self._call_llm_with_retry(
                messages=[
                    {"role": "system", "content": self.CLAUSE_SYSTEM_PROMPT},
                    {"role": "user", "content": self.CLAUSE_USER_PROMPT
                        .replace("{document_text}", text[:self.MAX_CHARS_PER_CHAPTER])
                        .replace("{source}", source_info.get("source", ""))}
                ],
                temperature=0.3
            )

            clauses_data = response.get("clauses", [])
            clauses = []

            for cd in clauses_data:
                clause_id = cd.get("clause_id", "")
                requirement_type = self._parse_requirement_type(
                    cd.get("requirement_type", "recommended")
                )

                systems = self._extract_systems_from_text(cd.get("clause_content", ""))

                # 解析语义三元组（核心改动）
                triplets = []
                for t in cd.get("triplets", []):
                    if t.get("component") or t.get("action") or t.get("obj"):
                        triplets.append(SemanticTriplet(
                            component=t.get("component", "").strip(),
                            action=t.get("action", "").strip(),
                            obj=t.get("obj", "").strip(),
                            condition=t.get("condition", "").strip(),
                            requirement=t.get("requirement", "mandatory")
                        ))

                # 解析款/项结构化数据（款/项内也有三元组）
                clause_items = []
                for ci_data in cd.get("clause_items", []):
                    ci_triplets = []
                    for t in ci_data.get("triplets", []):
                        if t.get("component") or t.get("action") or t.get("obj"):
                            ci_triplets.append(SemanticTriplet(
                                component=t.get("component", "").strip(),
                                action=t.get("action", "").strip(),
                                obj=t.get("obj", "").strip(),
                                condition=t.get("condition", "").strip(),
                                requirement=t.get("requirement", "mandatory")
                            ))
                    clause_items.append(ClauseItem(
                        item_number=ci_data.get("item_number", ""),
                        item_content=ci_data.get("item_content", ""),
                        # 兼容旧格式：从 flat lists 提取（fallback）
                        components=[c.get("name", "") for c in ci_data.get("components", [])],
                        actions=[a.get("name", "") for a in ci_data.get("actions", [])],
                        conditions=[c.get("name", "") for c in ci_data.get("conditions", [])],
                        objects=[o.get("name", "") for o in ci_data.get("objects", [])]
                    ))

                # 解析术语数据
                terms_list = []
                for term_data in cd.get("terms", []):
                    terms_list.append({
                        "term_name": term_data.get("term_name", ""),
                        "definition": term_data.get("definition", "")
                    })

                clause = ClauseSegment(
                    clause_id=clause_id,
                    clause_title=cd.get("clause_title", ""),
                    content=cd.get("clause_content", ""),
                    paragraphs=cd.get("paragraphs", []),
                    requirement_type=requirement_type,
                    applicable_systems=systems,
                    cross_refs=[],
                    source=source_info.get("source", ""),
                    page=source_info.get("page"),
                    triplets=triplets,
                    clause_items=clause_items,
                    is_term_definition=cd.get("is_term_definition", False),
                    terms=terms_list,
                    formula_content=cd.get("formula_content"),
                    semantics_enriched=bool(triplets),
                    parent_chapter=chapter_num,
                    referenced_clauses=[],  # 暂空，先创建对象再赋值
                    referenced_standards=cd.get("referenced_standards", []),
                    metadata={
                        "chunk_type": "clause",
                        "parent_chapter": chapter_num,
                        "semantics_enriched": bool(triplets),
                        "is_term_definition": cd.get("is_term_definition", False),
                        "terms": terms_list,
                        "formula_content": cd.get("formula_content"),
                        "referenced_tables": cd.get("referenced_tables", []),
                        "referenced_formulas": cd.get("referenced_formulas", []),
                        "referenced_standards": cd.get("referenced_standards", [])
                    }
                )

                # 构建交叉引用（一次性调用）
                clause_cross_refs, clause_referenced = self._build_cross_refs(
                    cd, self._clause_registry
                )
                clause.cross_refs = clause_cross_refs
                clause.referenced_clauses = clause_referenced

                clauses.append(clause)

            self.logger.info(f"章节 {chapter_num}: LLM 提取 {len(clauses)} 条条文")
            return clauses

        except LLMChunkerError as e:
            self.logger.error(f"章节 {chapter_num} 条文提取失败: {e}")
            raise

    def _extract_elements_from_chapter(
        self,
        text: str,
        clauses: List[ClauseSegment],
        chapter_num: int = 0
    ) -> List[ElementSegment]:
        """
        从章节文本中提取要素（使用 LLM）

        Args:
            text: 章节文本
            clauses: 该章节的条文列表
            chapter_num: 章节编号（用于日志）

        Returns:
            要素列表
        """
        try:
            response = self._call_llm_with_retry(
                messages=[
                    {"role": "system", "content": self.ELEMENT_SYSTEM_PROMPT},
                    {"role": "user", "content": self.ELEMENT_USER_PROMPT.format(
                        document_text=text[:self.MAX_CHARS_PER_CHAPTER]
                    )}
                ],
                temperature=0.3
            )

            elements_data = response.get("elements", [])
            elements = []

            for ed in elements_data:
                element_type_str = ed.get("element_type", "Component")
                element_type = self._map_element_type(element_type_str)

                value = ed.get("value", "")

                element = ElementSegment(
                    element_type=element_type,
                    source_id=ed.get("source_clause_id", ""),
                    content=ed.get("description", ""),
                    key=ed.get("name", ""),
                    value=value,
                    unit=self._extract_unit(value),
                    condition=ed.get("condition", ""),
                    metadata={
                        "chunk_type": "element",
                        "element_type": element_type.value,
                        "source_clause_id": ed.get("source_clause_id", ""),
                        "abbreviation": ed.get("abbreviation", ""),
                        "keywords": ed.get("keywords", []),
                        "formula_expression": ed.get("expression", ""),
                        "formula_variables": ed.get("variables", [])
                    }
                )
                elements.append(element)

            self.logger.info(f"[章节{chapter_num}] 要素: LLM 提取 {len(elements)} 个要素")
            return elements

        except LLMChunkerError as e:
            self.logger.error(f"[章节{chapter_num}] 要素提取失败: {e}")
            raise

    # =========================================================================
    # 辅助方法
    # =========================================================================

    def merge_adjacent_chunks(self, text_chunks: List[TextChunk]) -> List[TextChunk]:
        """
        将属于同一条款的相邻 chunks 合并成一个 TextChunk。

        MinerU 按 PDF 视觉块边界切分，同一个条款（如 7.2.1）的条文编号和款/项
        内容可能被切到不同 chunk 中。此方法在拼接前将同页、连续、不含新条款编号
        的 chunks 合并，使 LLM 拿到完整的条款文本。

        合并规则：
        1. 同一 page_idx（同一页）
        2. 后续 chunk 内容不以独立条款编号（X.Y.Z）开头
        3. 最多连续合并 5 个 chunk（防止整页合并）
        """
        import re
        # 匹配独立条款编号开头，如 "7.2.1"、"5.1.3"、"第4.2.5条"
        CLAUSE_ID_PATTERN = re.compile(
            r'^\d+(\.\d+)+\s*[章节条]?\s*[^\d\s]'
        )

        if not text_chunks:
            return text_chunks

        merged: List[TextChunk] = []
        i = 0
        while i < len(text_chunks):
            current = text_chunks[i]
            merged_chunks: List[TextChunk] = [current]
            page_idx = current.metadata.get('page_idx', 0)

            # 向前看最多 5 个相邻 chunk
            j = i + 1
            consecutive_merges = 0
            while (
                j < len(text_chunks)
                and consecutive_merges < 5
                and text_chunks[j].metadata.get('page_idx', 0) == page_idx
            ):
                next_text = text_chunks[j].text.strip()
                # 如果下一个 chunk 以独立条款编号开头，说明遇到新条款，停止合并
                if CLAUSE_ID_PATTERN.match(next_text):
                    break
                merged_chunks.append(text_chunks[j])
                j += 1
                consecutive_merges += 1

            # 合并文本，保留第一个 chunk 的 metadata
            combined_text = "\n".join(tc.text for tc in merged_chunks)
            merged.append(TextChunk(text=combined_text, metadata=current.metadata))
            i = j

        self.logger.info(
            f"[合并相邻chunks] 原始 {len(text_chunks)} 个 → 合并后 {len(merged)} 个"
        )
        return merged

    def _merge_text_chunks(self, text_chunks: List[TextChunk]) -> str:
        """合并文本块"""
        texts = []
        for tc in text_chunks:
            if tc.text.strip():
                texts.append(tc.text)
        return "\n\n".join(texts)

    def _get_source_info(self, text_chunks: List[TextChunk]) -> Dict[str, Any]:
        """获取来源信息"""
        if text_chunks:
            first = text_chunks[0]
            meta = first.metadata or {}
            return {
                "source": meta.get("source", ""),
                "page": meta.get("page"),
                "bbox": meta.get("bbox")
            }
        return {"source": "", "page": None, "bbox": None}

    def _parse_requirement_type(self, req_type_str: str) -> RequirementType:
        """解析要求类型"""
        req_type_str = req_type_str.lower()
        if req_type_str in ['mandatory', '必须', '应', '须']:
            return RequirementType.MANDATORY
        elif req_type_str in ['prohibited', '禁止', '严禁', '不得']:
            return RequirementType.PROHIBITED
        return RequirementType.RECOMMENDED

    def _extract_systems_from_text(self, text: str) -> List[SystemApplicability]:
        """从文本中提取适用系统"""
        import re
        systems = []
        system_pattern = re.compile(r'(TN|TT|IT)(?:-C|-S|-C-S)?', re.IGNORECASE)
        matches = system_pattern.findall(text)
        for match in matches:
            system_type = match[0].upper() if isinstance(match, tuple) else match
            sub_type = match[1] if isinstance(match, tuple) and len(match) > 1 else None
            systems.append(SystemApplicability(system_type=system_type, sub_type=sub_type))
        return systems

    def _extract_unit(self, value: str) -> str:
        """从值中提取单位"""
        import re
        unit_pattern = re.compile(r'[\d.]+\s*([a-zA-Z°%Ωμ²³]+)')
        match = unit_pattern.search(value)
        return match.group(1) if match else ""

    def _map_element_type(self, type_str: str) -> ElementType:
        """映射要素类型"""
        type_str = type_str.lower()
        if type_str == 'term':
            return ElementType.TERM
        elif type_str == 'formula':
            return ElementType.FORMULA
        elif type_str == 'parameter':
            return ElementType.PARAMETER
        elif type_str == 'component':
            return ElementType.COMPONENT
        elif type_str == 'object':
            return ElementType.OBJECT
        return ElementType.TABLE_ROW

    def _build_cross_refs(
        self,
        clause_data: Dict,
        clause_registry: Optional[Dict[str, Dict]] = None
    ) -> tuple[List[CrossReference], List["ReferencedClause"]]:
        """
        构建交叉引用（表格、公式、条款）

        Args:
            clause_data: LLM 返回的条款数据
            clause_registry: 条款注册表，用于补全被引用条款的 page_idx/chunk_id

        Returns:
            (cross_refs, referenced_clauses) 元组
        """
        refs: List[CrossReference] = []
        clause_refs: List[ReferencedClause] = []

        # 表格引用
        for table in clause_data.get("referenced_tables", []):
            refs.append(CrossReference(
                ref_id=table,
                ref_type="table",
                description=f"引用表格 {table}"
            ))

        # 公式引用
        for formula in clause_data.get("referenced_formulas", []):
            refs.append(CrossReference(
                ref_id=formula,
                ref_type="formula",
                description=f"引用公式 {formula}"
            ))

        # 条款引用
        for clause_ref_str in clause_data.get("referenced_clauses", []):
            # clause_ref_str 可能是字符串或字典
            if isinstance(clause_ref_str, dict):
                cid = clause_ref_str.get("clause_id", "")
            else:
                cid = str(clause_ref_str)

            if not cid:
                continue

            # 从注册表补全物理信息
            reg_entry = {}
            if clause_registry:
                reg_entry = clause_registry.get(cid, {})

            ref_type = "appendix" if cid[0].isalpha() else "clause"
            clause_refs.append(ReferencedClause(
                clause_id=cid,
                context=f"本规范第{cid}条",
                ref_type=ref_type,
                page_idx=reg_entry.get("page_idx"),
                chunk_id=reg_entry.get("chunk_id"),
                section_title=reg_entry.get("section_title"),
            ))

            # 同时加到 cross_refs（保持向后兼容）
            refs.append(CrossReference(
                ref_id=cid,
                ref_type="clause",
                description=f"引用条款 {cid}"
            ))

        return refs, clause_refs

    def _annotate_positions(
        self,
        elements: List[ElementSegment],
        chapter_text: str,
        source_info: Dict[str, Any]
    ) -> List[ElementSegment]:
        """
        为要素标注 PDF 中的位置信息

        根据要素内容在章节文本中查找位置，估算页码和坐标

        Args:
            elements: 要素列表
            chapter_text: 章节文本
            source_info: 来源信息（包含 page, bbox 等）

        Returns:
            带位置信息的要素列表
        """
        import re

        base_page = source_info.get("page", 1) or 1
        total_text_len = len(chapter_text)

        # 估算每页平均字符数（假设约 2000 字符/页）
        CHARS_PER_PAGE_ESTIMATE = 2000

        annotated_elements = []

        for element in elements:
            # 在章节文本中查找要素位置
            position_info = self._find_position_in_text(
                element.key or element.content,
                chapter_text
            )

            if position_info:
                char_offset = position_info["char_offset"]
                # 估算页码：基页 + (字符偏移 / 每页字符数)
                estimated_page = base_page + int(char_offset / CHARS_PER_PAGE_ESTIMATE)

                # 估算 bbox（基于字符偏移位置，生成一个矩形区域）
                # 假设每行约 100 字符，估算行列位置
                line_num = chapter_text[:char_offset].count('\n')
                # 估算 x, y 坐标（相对于页内）
                x0 = 50  # 左边距
                y0 = 50 + (line_num % 50) * 20  # 每行约 20px
                x1 = x0 + 500  # 假设宽度 500px
                y1 = y0 + 20  # 行高 20px

                # 更新要素的 metadata
                annotated_element = ElementSegment(
                    element_type=element.element_type,
                    source_id=element.source_id,
                    content=element.content,
                    key=element.key,
                    value=element.value,
                    unit=element.unit,
                    condition=element.condition,
                    abbreviation=element.abbreviation,
                    definition=element.definition,
                    keywords=element.keywords,
                    metadata={
                        **element.metadata,
                        "annotated": True,
                        "char_offset": char_offset,
                        "estimated_page": estimated_page,
                        "bbox": [x0, y0, x1, y1],
                        "position_source": "llm_annotation"
                    }
                )
                annotated_elements.append(annotated_element)
            else:
                # 未找到位置，仍保留要素但标记为未标注
                element.metadata["annotated"] = False
                element.metadata["annotation_note"] = "在原文中未找到精确位置"
                annotated_elements.append(element)

        return annotated_elements

    def _find_position_in_text(self, search_text: str, full_text: str) -> Optional[Dict]:
        """
        在文本中查找指定内容的位置

        Args:
            search_text: 要查找的内容
            full_text: 全文

        Returns:
            包含 char_offset 的字典，未找到返回 None
        """
        if not search_text or not full_text:
            return None

        # 尝试精确匹配
        idx = full_text.find(search_text)
        if idx >= 0:
            return {"char_offset": idx, "match_type": "exact"}

        # 尝试模糊匹配（忽略空白）
        normalized_search = re.sub(r'\s+', '', search_text)
        normalized_full = re.sub(r'\s+', '', full_text)
        idx = normalized_full.find(normalized_search)
        if idx >= 0:
            return {"char_offset": idx, "match_type": "fuzzy"}

        # 尝试匹配关键词（取前 10 个字符）
        short_key = search_text[:min(10, len(search_text))]
        if len(short_key) >= 3:
            idx = full_text.find(short_key)
            if idx >= 0:
                return {"char_offset": idx, "match_type": "prefix"}

        return None


# ============================================================================
# 便捷函数
# ============================================================================

def chunk_text_llm(
    text: str,
    progress_callback: Optional[Callable] = None
) -> HierarchicalChunkResult:
    """
    便捷函数：使用 LLM 对单个文本进行多层级分块

    Args:
        text: 输入文本
        progress_callback: 进度回调

    Returns:
        HierarchicalChunkResult
    """
    chunker = LLMDrivenChunker(progress_callback=progress_callback)
    return chunker.chunk_single_text(text, progress_callback)


def chunk_texts_llm(
    text_chunks: List[TextChunk],
    progress_callback: Optional[Callable] = None
) -> HierarchicalChunkResult:
    """
    便捷函数：使用 LLM 对多个文本块进行多层级分块

    Args:
        text_chunks: TextChunk列表
        progress_callback: 进度回调

    Returns:
        HierarchicalChunkResult
    """
    chunker = LLMDrivenChunker(progress_callback=progress_callback)
    text_chunks = chunker.merge_adjacent_chunks(text_chunks)
    return chunker.chunk(text_chunks, progress_callback)
