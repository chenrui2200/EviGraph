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


# ============================================================================
# 表格图片替换辅助函数
# ============================================================================

def _reconstruct_image_refs_from_placeholders(
    md_content: str,
    img_path_list: list,
    logger
) -> str:
    """
    当 MinerU 输出 [表格内容待解析] 占位符时，重建 ![xxx](images/xxx.jpg) 格式的图片引用。

    策略：由于编码问题导致 table_caption 乱码无法用于匹配，
    采用顺序匹配策略：按顺序将占位符与 img_path_list 中的 img_path 配对。

    流程：
    1. 找到所有 [表格内容待解析] 的位置
    2. 按顺序与 img_path_list 中的 img_path 配对
    3. 将 [表格内容待解析] 替换为 ![表序号](img_path)

    Args:
        md_content: 包含占位符的 md 内容
        img_path_list: [(caption, img_path), ...] 列表（保持顺序）
        logger: 日志记录器

    Returns:
        替换图片引用后的 md_content
    """
    import re

    # 查找所有 [表格内容待解析] 的位置
    placeholder_pattern = re.compile(r'\[表格内容待解析\]')
    placeholder_matches = list(placeholder_pattern.finditer(md_content))

    if not placeholder_matches:
        return md_content

    logger.info(f"[表格替换] 找到 {len(placeholder_matches)} 个 [表格内容待解析] 占位符")

    # 按顺序与 img_path_list 配对
    logger.info(f"[表格替换] 可用的 img_path 数量: {len(img_path_list)}")

    if len(placeholder_matches) > len(img_path_list):
        logger.warning(f"[表格替换] 占位符数量 ({len(placeholder_matches)}) > img_path 数量 ({len(img_path_list)})")

    # 逐个替换（逆序保持位置）
    replacements = []
    for i, ph_match in enumerate(placeholder_matches):
        if i < len(img_path_list):
            caption, img_path = img_path_list[i]
            # 使用序号作为 alt text
            replacement = f"![表{i+1}]({img_path})"
            replacements.append((ph_match.start(), ph_match.end(), replacement))
            logger.info(f"[表格替换] 重建 {i+1}: {replacement}")
        else:
            logger.warning(f"[表格替换] 没有足够的 img_path 用于替换占位符 {i+1}")

    # 逆序替换
    for start, end, replacement in reversed(replacements):
        md_content = md_content[:start] + replacement + md_content[end:]

    logger.info(f"[表格替换] 完成重建，共替换 {len(replacements)} 个占位符")
    return md_content


class LLMChunkerError(Exception):
    """LLM 分块器异常"""
    pass


# ============================================================================
# 公共序列化函数（供外部模块复用）
# ============================================================================

def clause_to_dict(clause: "ClauseSegment") -> Dict[str, Any]:
    """
    将 ClauseSegment 转换为完整字典

    当前实际提取的字段：
    - topic：条款语义摘要（_process_single_clause 提取）
    - entities：知识实体列表（_process_single_clause 提取）
    - terms：术语定义列表（_process_single_clause 提取）
    """
    return {
        "clause_id": clause.clause_id,
        "clause_title": clause.clause_title,
        "content": clause.content,
        "source": clause.source or "",
        "page": clause.page,
        "page_idx": clause.metadata.get("page_idx") if clause.metadata else None,
        # 所有来源 chunk 的 bbox（按 page 分组，同页合并）
        "bboxs": clause.metadata.get("bboxs", []) if clause.metadata else [],
        # 来源 chunks.json 的 chunk_id（可能有多个，聚合跟踪）
        "chunks": clause.metadata.get("chunks", []) if clause.metadata else [],
        "requirement_type": clause.requirement_type.value if hasattr(clause.requirement_type, 'value') else str(clause.requirement_type),
        # 以下字段当前未被 LLM 提取，预留接口
        "conditions": clause.conditions if clause.conditions else [],
        "actions": clause.actions if clause.actions else [],
        "components": clause.components if clause.components else [],
        "objects": clause.objects if clause.objects else [],
        "parent_chapter": clause.metadata.get("parent_chapter") if clause.metadata else None,
        # 知识域字段（SATO 级别查询支持）
        "scope_prefix": clause.metadata.get("scope_prefix") if clause.metadata else None,
        "chapter": clause.metadata.get("chapter") if clause.metadata else None,
        "is_term_definition": clause.is_term_definition,
        "terms": clause.metadata.get("terms", []) if clause.metadata else clause.terms or [],
        "formula_content": clause.formula_content,
        "referenced_tables": clause.metadata.get("referenced_tables", []) if clause.metadata else [],
        "referenced_formulas": clause.metadata.get("referenced_formulas", []) if clause.metadata else [],
        # 条款引用
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
        # 当前 LLM 实际提取的知识实体
        "entities": clause.metadata.get("entities", []) if clause.metadata else [],
        # 条款语义摘要
        "topic": clause.metadata.get("topic", "") if clause.metadata else "",
        # 以下字段当前未启用，预留接口
        "triplets": [],
        "clause_items": [],
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
    TOC_SYSTEM_PROMPT = """你是工程规范文档的目录分析专家。

## 任务
从文档开头提取章节结构（最多10个章节），返回JSON格式。

## 章节类型
- 含"术语"、"名词解释"→"term_definition"
- 含"附录"、"附表"→"appendix"
- 其他→"normative"

## 输出格式（必须严格遵守）
```json
{"chapters":[{"chapter_number":1,"title":"总则","chapter_type":"normative","start_position":0,"end_position":5000}]}
```

## 规则
1. 只分析前5000字符中的目录部分
2. 最多返回10个章节，超出忽略
3. OCR中#前缀不是内容，忽略
4. 输出必须是合法JSON，不要 markdown 包裹"""



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

    # 简化版：只做实体抽取
    CLAUSE_SYSTEM_PROMPT = """你是一个工程规范文档的实体抽取专家。

你的任务是从工程规范文本中提取知识实体。

## 实体类型（只抽取这6类）

1. **term（术语）**：条文定义的专门术语
   - 例："直接接触防护"、"预期接触电压"
   - 属性：term_name, definition

2. **component（组件/设备）**：电气设备、系统、材料
   - 例："剩余电流保护电器"、"配电变压器"、"电缆"
   - 属性：name, abbreviation, type

3. **parameter（参数/数值）**：技术参数
   - 例："最小截面积：4mm²"、"额定电流：16A"
   - 属性：name, value, unit

4. **formula（公式）**：计算公式
   - 例："S ≥ I·t / k"
   - 属性：formula_id, expression

5. **condition（条件）**：适用条件、环境、场景
   - 例："短路条件下"、"潮湿环境"
   - 属性：name, type

6. **system（系统）**：配电系统类型
   - 例："TN-S系统"、"TT系统"、"IT系统"
   - 属性：name, type

## 条文识别规则

1. **条文编号**：如 "3.2.1"、"5.1.3"、"第4.2.5条" 等
2. **术语章节**：条文编号 X.0.N 格式为术语定义

## OCR 文本处理

1. **# 前缀**不是条文内容，忽略
2. **LaTeX 公式**：如 `$公式内容$`，提取为 formula
3. **表格引用**：提取表格编号（如 `表3.2.2`）到 `referenced_tables`
4. **条款引用**：提取条款编号（如 `第5.2.4条`）到 `referenced_clauses`
5. **外部标准**：提取外部标准编号（如 `GB/T16895.15`）到 `referenced_standards`

## 输出格式

请输出 JSON：
```json
{{
    "entities": [
        {{
            "entity_type": "term|component|parameter|formula|condition|system",
            "name": "实体名称",
            "value": "参数值或公式表达式（可选）",
            "unit": "单位（可选）",
            "abbreviation": "缩写（可选）",
            "definition": "定义（仅用于term）"
        }}
    ],
    "referenced_tables": ["表3.2.2"],
    "referenced_formulas": ["公式(3.2.14)"],
    "referenced_clauses": ["5.2.4"],
    "referenced_standards": ["GB/T16895.15"]
}}
```

请保持 JSON 格式输出。只抽取文本中**明确提到**的实体，不要臆造。"""

    CLAUSE_USER_PROMPT = """
请分析以下文本，提取知识实体：

{document_text}

来源：{source}

## 抽取规则
1. **实体类型**：只抽取 term、component、parameter、formula、condition、system
2. **条款引用**：放入 referenced_clauses，外部标准放入 referenced_standards
3. **表格/公式引用**：放入 referenced_tables / referenced_formulas

## 示例

原文：`2.0.5 直接接触防护 无故障条件下的电击防护。`
→ entities: [{{"entity_type": "term", "name": "直接接触防护", "definition": "无故障条件下的电击防护"}}]

原文：`配电箱内应设置剩余电流保护电器（RCD）。`
→ entities: [{{"entity_type": "component", "name": "剩余电流保护电器", "abbreviation": "RCD"}}]

原文：`本规范第5.2.4条的要求应符合GB/T16895.15的规定。`
→ referenced_clauses: ["5.2.4"], referenced_standards: ["GB/T16895.15"]

请输出 JSON：
```json
{{
    "entities": [],
    "referenced_tables": [],
    "referenced_formulas": [],
    "referenced_clauses": [],
    "referenced_standards": []
}}
```"""

    # ============================================================
    # Topic 提取 Prompt（阶段 A）
    # ============================================================
    TOPIC_SYSTEM_PROMPT = """你是一个工程规范文档的条文分析专家。

你的任务是为每条条文生成一个简洁的 topic（主题摘要）。

## 要求
- topic 描述这条条文介绍了什么方面的知识信息
- topic 应该是一个简短的短语或句子（不超过 30 字）
- 只输出 topic 内容，不要其他解释
- 例如："剩余电流保护电器的设置要求"、"直接接触防护的措施"、"短路电流的计算方法"

## 输出格式
请输出 JSON 格式：
{{"topic": "topic 内容"}}

不要输出其他内容，只输出 JSON。"""

    TOPIC_USER_PROMPT = """请为以下条文生成 topic：

{clause_text}

要求：topic 应该说明这条条文介绍了什么方面的知识信息，简短且精确。

请输出 JSON："""

    # ============================================================
    # 实体提取 Prompt（阶段 B）- 简化版
    # ============================================================
    ENTITY_SYSTEM_PROMPT = """你是一个工程规范文档的实体抽取专家。

你的任务是从条文中提取知识实体（名词）。

## 要求
- 只提取条文中明确提到的实体名词
- 不要提取动词、形容词、副词等
- 不要提取条文编号、日期等非实体内容
- 实体应该是技术相关的：设备、系统、材料、参数、场所等
- 严格控制数量，提取 5-15 个与 topic 高度相关的知识实体

## 输出格式
请输出 JSON 格式：
{{"entities": ["实体1", "实体2", "实体3"]}}

如果没有有意义的实体，输出：{{"entities": []}}"""

    ENTITY_USER_PROMPT = """请提取以下条文中的知识实体：

条文内容：{clause_text}

topic：{topic}

要求：
1. topic 描述了这条条文的核心主题，实体必须与 topic 高度相关
2. 严格控制数量，提取 5-15 个与 topic 高度相关的知识实体
3. 无关的实体不要提取

请输出 JSON："""

    # ============================================================
    # 术语提取 Prompt（阶段 C）- 新增
    # ============================================================
    TERM_SYSTEM_PROMPT = """你是一个工程规范文档的术语定义专家。

你的任务是从条文中识别并提取专业术语定义。

## 术语定义格式
- 术语名称（term_name）：条文中明确定义的技术术语
- 缩写（abbreviation）：术语的缩写（如 RCD、TN-S）
- 定义（definition）：条文中对该术语的完整解释

## 要求
- 只提取条文中明确给出定义的术语，不要提取未定义的简称
- 术语应该是技术相关的核心概念：保护措施、设备类型、系统形式等
- 定义必须引用条文中对应的原文解释
- 数量：每条条文最多提取 1-3 个核心术语

## 输出格式
请输出 JSON 格式：
{{"terms": [{{"term_name": "术语名称", "abbreviation": "缩写", "definition": "定义原文"}}]}}

如果没有明确的术语定义，输出：{{"terms": []}}"""

    TERM_USER_PROMPT = """请从以下条文中提取术语定义：

条文内容：{clause_text}

要求：
1. 只提取条文中明确给出定义的术语（如"本规范所指XXX，是指..."、"XXX定义为..."）
2. 缩写和全称同时存在时一并提取
3. 每条条文最多提取 1-3 个核心术语
4. 定义必须引用条文中对应的原文

请输出 JSON格式：{{"terms": [{{"term_name": "...", "abbreviation": "...", "definition": "..."}}]}}"""

    # ============================================================
    # 知识实体数量启发式配置
    # ============================================================
    # 条款数量 → 目标实体数量映射
    ENTITY_COUNT_HEURISTIC = [
        (3,   (3, 5)),
        (8,   (5, 10)),
        (15,  (8, 15)),
        (999, (10, 20)),
    ]

    @staticmethod
    def calc_target_entity_count(clause_count: int) -> tuple:
        """
        根据章节内条款数量启发式计算目标实体提取数量。

        Args:
            clause_count: 该章节的条款数量

        Returns:
            (min, max) 元组
        """
        for threshold, (min_cnt, max_cnt) in LLMDrivenChunker.ENTITY_COUNT_HEURISTIC:
            if clause_count <= threshold:
                return (min_cnt, max_cnt)
        return (10, 20)  # fallback

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
    MAX_CHARS_FOR_TOC = 5000           # 目录识别最大字符数
    MAX_TOKENS_FOR_TOC = 4096 * 2      # 目录提取专用 token 限制

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

    def _merge_clause_chunks(
        self,
        clause_id: str,
        position_index: Dict[str, Any],
        clause_registry: Dict[str, Dict]
    ) -> tuple[str, list, str]:
        """
        合并条款的 title chunk + 后续 text chunks，还原完整条款内容。

        策略：
        1. 找到 clause_id 对应的所有 chunks（by_clause_id 索引）
        2. 按 page_idx 和 chunk 内顺序排列
        3. 合并相邻的 title + text chunks
        4. 返回 (merged_content, merged_bbox, scope_prefix)

        Returns:
            (完整条款文本, 合并bbox列表, scope_prefix)
        """
        import re

        def get_scope_prefix(cid: str, registry: Dict[str, Dict]) -> str:
            """从 clause_id 推导 scope_prefix: '2.0.1'→'2.0', '3.1'→'3.1'（若有子条款）/'3'（若无）, 'A.0.7'→'A.0'"""
            parts = cid.split('.')
            if len(parts) == 3:
                return '.'.join(parts[:2])
            elif len(parts) == 2:
                # 判断是否字母开头（如 A.0）
                if parts[0].isalpha():
                    return parts[0]
                # 检查是否有子条款（如 3.1 下存在 3.1.1）：若有则用完整 X.Y 作为 scope
                prefix = cid + '.'
                has_child = any(k.startswith(prefix) for k in registry.keys())
                if has_child:
                    return cid
                return parts[0]
            return parts[0]

        def get_chapter_num(cid: str) -> str:
            """从 clause_id 推导章节号: '2.0.1'→'2', 'A.0.7'→'A'"""
            return cid.split('.')[0]

        all_clause_chunks = []

        # 从 by_clause_id 获取该条款的所有 chunks（title + text）
        if clause_id in position_index.get('by_clause_id', {}):
            all_clause_chunks = list(position_index['by_clause_id'][clause_id])

        # 也通过 text 内容匹配来补充（条款正文可能不以 clause_id 开头）
        # 按 page_idx 和 chunk_id 排序（chunk_id 的整数顺序即阅读顺序）
        def sort_key(c):
            cid_str = c.get('chunk_id', 'chunk_0')
            parts = cid_str.replace('chunk_', '').split('_')
            return (int(parts[0]) if parts[0].isdigit() else 0,
                    int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)
        all_clause_chunks.sort(key=sort_key)

        if not all_clause_chunks:
            return '', [], get_scope_prefix(clause_id, clause_registry)

        # 收集所有 clause_chunks 的 page_idx（去重）
        pages_involved = sorted(set(c.get('page_idx', 0) for c in all_clause_chunks))

        # 合并文本内容
        merged_texts = []
        for c in all_clause_chunks:
            text = c.get('content', '') or ''
            if text.strip():
                merged_texts.append(text.strip())

        # 如果只有 title chunk（无正文），补充条款注册表中的 title
        if not merged_texts:
            entry = clause_registry.get(clause_id, {})
            title = entry.get('title', '')
            if title:
                merged_texts.append(title)

        merged_content = '\n'.join(merged_texts)

        # 合并 bbox：取所有 chunks bbox 的并集（跨页时分组处理）
        all_bboxes = []
        for c in all_clause_chunks:
            bbox = c.get('bbox_pdf') or c.get('bbox_viewport') or []
            if bbox and len(bbox) >= 4:
                all_bboxes.append((bbox, c.get('page_idx', 0)))

        # 按 page 分组合并 bbox
        merged_bboxes = []
        for page_idx in pages_involved:
            page_bboxes = [bbox for bbox, pid in all_bboxes if pid == page_idx]
            if not page_bboxes:
                continue
            x0 = min(b[0] for b in page_bboxes)
            y0 = min(b[1] for b in page_bboxes)
            x1 = max(b[2] for b in page_bboxes)
            y1 = max(b[3] for b in page_bboxes)
            merged_bboxes.append([x0, y0, x1, y1])

        return merged_content, merged_bboxes, get_scope_prefix(clause_id, clause_registry)

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
        position_index: Dict[str, Any],
        clause_registry: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        将 clause 文本匹配到 chunks.json 中的对应块，返回位置信息 + 合并后的条款内容。

        匹配策略（优先级递减）：
        1. clause_id 精确匹配 → 直接命中 title/text chunk → 合并 title+text
        2. 子串包含匹配 → clause_text 在 chunk.content 中
        3. n-gram 交集匹配 → 统计共同 n-gram 数量

        新增功能：
        - 返回 merged_content（title + text 合并后的完整条款文本）
        - 返回 scope_prefix（知识域前缀，如 "2.0"）
        - 返回 chapter（章节号，如 "2"）

        Args:
            clause_id: 条款编号（如 "3.2.5"）
            clause_text: 条款文本内容
            position_index: _build_chunk_position_index 构建的索引
            clause_registry: 可选，条款注册表（用于补充 title）

        Returns:
            位置信息字典（含 page_idx, bbox_pdf, chunk_id, merged_content, scope_prefix, chapter 等）
        """
        result: Dict[str, Any] = {}

        def get_scope_prefix(cid: str, registry: Optional[Dict[str, Any]] = None) -> str:
            """从 clause_id 推导 scope_prefix: '2.0.1'→'2.0', '3.1'→'3.1'（若有子条款）/'3'（若无）, 'A.0.7'→'A.0'"""
            parts = cid.split('.')
            if len(parts) == 3:
                return '.'.join(parts[:2])
            elif len(parts) == 2:
                # 判断是否字母开头（如 A.0）
                if parts[0].isalpha():
                    return parts[0]
                # 检查是否有子条款（如 3.1 下存在 3.1.1）：若有则用完整 X.Y 作为 scope
                if registry:
                    prefix = cid + '.'
                    has_child = any(k.startswith(prefix) for k in registry.keys())
                    if has_child:
                        return cid
                return parts[0]
            return parts[0]

        def get_chapter_num(cid: str) -> str:
            return cid.split('.')[0]

        # 策略1: clause_id 精确匹配 → 优先使用 chunks 合并方案
        if clause_id and clause_id in position_index.get('by_clause_id', {}):
            # 使用 _merge_clause_chunks 合并 title + text chunks
            merged_content, merged_bboxes, scope_prefix = self._merge_clause_chunks(
                clause_id, position_index, clause_registry or {}
            )
            all_matched = position_index['by_clause_id'][clause_id]
            # 取第一个 chunk 的基本信息
            first_chunk = next((c for c in all_matched if c.get('type') == 'title'), all_matched[0])
            pos_info = self._extract_position_from_chunk(first_chunk)

            # 补充新增字段
            pos_info['merged_content'] = merged_content
            pos_info['scope_prefix'] = scope_prefix
            pos_info['chapter'] = get_chapter_num(clause_id)
            if merged_bboxes:
                pos_info['merged_bboxes'] = merged_bboxes
            self.logger.debug(f"[位置解析] clause_id={clause_id} 策略1命中: content_chars={len(merged_content)}")
            return pos_info

        # 策略2: 子串包含匹配
        if clause_text:
            clause_stripped = clause_text.strip()
            best_match = None
            best_ratio = 0.0

            for chunk in position_index['by_id'].values():
                chunk_text = (chunk.get('content') or '').strip()
                if not chunk_text or len(chunk_text) < 10:
                    continue
                if clause_stripped in chunk_text:
                    ratio = len(clause_stripped) / max(len(chunk_text), 1)
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_match = chunk

            if best_match and best_ratio >= 0.3:
                result = self._extract_position_from_chunk(best_match)
                result['merged_content'] = best_match.get('content', '') or clause_text
                result['scope_prefix'] = get_scope_prefix(clause_id, clause_registry)
                result['chapter'] = get_chapter_num(clause_id)
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
                    result['merged_content'] = chunk.get('content', '') or clause_text
                    result['scope_prefix'] = get_scope_prefix(clause_id, clause_registry)
                    result['chapter'] = get_chapter_num(clause_id)
                    self.logger.debug(f"[位置解析] clause_id={clause_id} 策略3命中: chunk={best_chunk_id} ngram_count={best_count}")
                    return result

        # 回退：用 clause 注册表的 title
        if clause_registry and clause_id in clause_registry:
            entry = clause_registry[clause_id]
            result['merged_content'] = entry.get('title', clause_text)
            result['scope_prefix'] = get_scope_prefix(clause_id, clause_registry)
            result['chapter'] = get_chapter_num(clause_id)

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

    def _clause_to_dict(self, clause: ClauseSegment) -> Dict[str, Any]:
        """将 ClauseSegment 转换为字典（委托给模块级函数）"""
        return clause_to_dict(clause)

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
            self.logger.info(f"[LLM 调用] attempt {attempt + 1}/{self.MAX_RETRIES + 1}")
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
                    self.logger.info(f"  → 等待 {actual_delay:.1f} 秒后重试 (attempt {attempt + 2}/{self.MAX_RETRIES + 1})...")
                    time.sleep(actual_delay)
                    retry_delay *= self.RETRY_MULTIPLIER
                else:
                    self.logger.error(f"LLM 重试次数耗尽: {e}")

        raise LLMChunkerError(f"LLM 调用失败，重试 {self.MAX_RETRIES} 次后仍失败: {last_error}")

    # =========================================================================
    # 并行处理工具（用于章节内 clause 并行 LLM 调用）
    # =========================================================================

    def _process_single_clause(
        self,
        clause,
        entity_count_range: tuple = (5, 15)
    ) -> None:
        """处理单个 clause 的 topic + entities + terms 提取（三阶段 LLM）"""
        if not clause.content:
            clause.metadata['topic'] = ""
            clause.metadata['entities'] = []
            clause.metadata['terms'] = []
            clause.metadata['semantics_enriched'] = True
            return

        # 阶段 A: 提取 topic
        topic = self._extract_topic_from_text(clause.content)
        clause.metadata['topic'] = topic

        # 阶段 B: 提取 entities
        entities = self._extract_entities_by_topic(clause.content, topic, entity_count_range)
        clause.metadata['entities'] = entities

        # 阶段 C: 提取 terms（术语定义）
        terms = self._extract_terms_from_text(clause.content)
        clause.metadata['terms'] = terms

        clause.metadata['semantics_enriched'] = True

        # 通过进度回调推送详细日志到前端
        if self.progress_callback and entities:
            try:
                term_info = f" | 术语: {', '.join([t['term_name'] for t in terms[:2]])}" if terms else ""
                self.progress_callback(
                    -1,  # 不更新进度百分比
                    f"📝 {clause.clause_id}: {topic[:20] if topic else '(无)'} | 实体: {', '.join(entities[:10])}{'...' if len(entities) > 10 else ''}{term_info}"
                )
            except Exception:
                pass  # 忽略回调错误

    # =========================================================================
    # JSONL 增量写入工具
    # =========================================================================

    def _append_clause_to_jsonl(self, project_id: Optional[str], clause) -> None:
        """将单个 clause 追加写入 JSONL 文件"""
        if not project_id:
            return
        from ..models.project import ProjectManager
        clause_dict = self._clause_to_dict(clause)
        # clause_to_dict 已包含 bboxs 和 chunks，直接写入
        ProjectManager.append_clause_to_jsonl(project_id, clause_dict)

    def _init_jsonl_file(self, project_id: Optional[str]) -> None:
        """初始化/清空 JSONL 文件（每轮任务从头写）"""
        if not project_id:
            return
        from ..models.project import ProjectManager
        path = ProjectManager._get_intelligent_chunks_jsonl_path(project_id)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('')  # 清空文件

    def chunk(
        self,
        text_chunks: List[TextChunk],
        progress_callback: Optional[Callable] = None,
        resume_from_chapter: int = 0,
        checkpoint: Optional[ChunkCheckpoint] = None,
        project_id: Optional[str] = None,
        md_content: Optional[str] = None,
        chunks_data: Optional[List[Dict]] = None,
        pdf_path: Optional[str] = None
    ) -> HierarchicalChunkResult:
        """
        主入口：基于 chunks.json 的智能分块

        策略：
        1. 直接从 chunks_data 构建章节和条款结构（基于 type 和位置）
        2. 逐章 LLM 提取实体（Term/Component/Parameter/Formula 等）
        3. 保留检查点机制支持断点恢复

        Args:
            text_chunks: 原始文本块列表
            progress_callback: 进度回调，签名: (progress, message, checkpoint_info)
            resume_from_chapter: 从第几个章节恢复（0表示从头开始）
            checkpoint: 增强版检查点（用于断点恢复）
            project_id: 项目ID（用于保存检查点）
            md_content: MinerU 解析的 Markdown 内容（可选，用于兼容）
            chunks_data: chunks.json 数据（核心数据源）
            pdf_path: PDF 文件路径（可选）

        Returns:
            HierarchicalChunkResult: 包含所有层级分块的结果
        """
        import time
        start_time = time.time()

        # 更新回调
        if progress_callback:
            self.progress_callback = progress_callback

        result = HierarchicalChunkResult()

        # =====================================================================
        # Step 0: 数据准备 - 直接从 chunks_data 构建章节和条款
        # =====================================================================
        self._report_progress(0.0, "🚀 开始智能标注分析...")
        self.logger.info("[LLM分块] Step 0/4: 从 chunks.json 构建章节和条款结构")

        # 优先使用 chunks_data
        if not chunks_data:
            # fallback：从 text_chunks 转换
            chunks_data = []
            for idx, c in enumerate(text_chunks):
                chunks_data.append({
                    'chunk_id': getattr(c, 'id', '') or c.metadata.get('chunk_id', f'tc_{idx}'),
                    'type': c.metadata.get('type', 'text'),
                    'content': c.text,
                    'page_idx': c.metadata.get('page', 0),
                    'bbox_viewport': c.metadata.get('bbox', []),
                    'source': c.metadata.get('source', ''),
                    'table_caption': c.metadata.get('table_caption', ''),
                })

        source_info = {"source": chunks_data[0].get('source', '') if chunks_data else ''}

        # 直接从 chunks_data 构建章节和条款
        sections_data, clauses_data, chapter_plan = self._build_sections_and_clauses_from_chunks(chunks_data)
        chapter_count = len(sections_data)

        self.logger.info(f"[LLM分块] ✅ 章节构建完成: {chapter_count} 章节, {len(clauses_data)} 条款")

        # =====================================================================
        # Step 1: 初始化/恢复检查点
        # =====================================================================
        self.logger.info("[LLM分块] Step 1/4: 初始化检查点")
        if checkpoint and checkpoint.chapter_plan:
            # 从检查点恢复
            self.logger.info(f"[LLM分块] 从检查点恢复: 已处理 {len(checkpoint.completed_clauses)} 条文")
            current_checkpoint = checkpoint
            start_index = checkpoint.current_chapter_index + 1

            # 从检查点恢复已完成的数据
            result = self._build_result_from_checkpoint(checkpoint)
            all_clauses = list(result.clauses)
        else:
            # 新任务，初始化检查点
            start_index = resume_from_chapter
            current_checkpoint = ChunkCheckpoint(
                chapter_plan=chapter_plan,
                current_chapter_index=-1,
                total_chapters=chapter_count,
                total_clauses=len(clauses_data),  # 总条款数用于细粒度进度
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat()
            )
            all_clauses = []

        if start_index > 0:
            self.logger.info(f"[LLM分块] 从章节 {start_index} 恢复，跳过前 {start_index} 个章节")

        all_edges: List[Dict] = []
        chapter_results = []  # [(section, chapter_clauses), ...]

        # =====================================================================
        # Step 2: 逐章 LLM 提取实体
        # =====================================================================
        self.logger.info(f"[LLM分块] Step 2/4: 逐章 LLM 提取实体（共 {chapter_count} 章）")

        # 初始化 JSONL 文件（每轮任务从头写）
        if project_id:
            self._init_jsonl_file(project_id)

        # 线程池：用于章节内 clause 并行 LLM 调用
        # max_workers 控制最大并发 LLM 请求数，避免压垮 LLM 服务
        max_workers = min(8, len(sections_data))

        for i, section_data in enumerate(sections_data):
            chapter_num = section_data['chapter_number']
            chapter_title = section_data['title']

            # 跳过已完成的章节（all_clauses 已通过 checkpoint 恢复包含了所有已完成 clauses）
            if i < start_index:
                self.logger.info(f"[LLM分块] 跳过章节 {chapter_num} (已处理)")
                section = SectionSegment(
                    chapter_number=chapter_num,
                    title=chapter_title,
                    content=""
                )
                # 从 checkpoint.completed_clauses 恢复该章节的 clauses（作为 dict）
                skipped_clauses = [
                    c for c in (checkpoint.completed_clauses if checkpoint else [])
                    if c.get('parent_chapter') == chapter_num
                ]
                chapter_results.append((section, skipped_clauses))
                continue

            # 更新检查点
            current_checkpoint.current_chapter_index = i
            if i < len(current_checkpoint.chapter_plan):
                current_checkpoint.chapter_plan[i].status = ChapterStatus.PROCESSING
                current_checkpoint.chapter_plan[i].started_at = datetime.now().isoformat()

            if project_id:
                self._save_checkpoint(project_id, current_checkpoint)

            # 计算进度
            chapter_progress_base = 0.1 + (i / chapter_count) * 0.65

            self.logger.info(f"[LLM分块] ▶ 处理章节 {chapter_num}/{chapter_count}: {chapter_title}")

            self._report_progress(
                chapter_progress_base,
                f"📑 处理章节 {chapter_num}/{chapter_count}: {chapter_title}...",
                checkpoint_info={
                    "current_chapter": chapter_num,
                    "total_chapters": chapter_count,
                    "completed_chapters": i,
                    "completed_clauses_count": len(all_clauses),
                    "completed_entities_count": sum(len(c.metadata.get("entities", [])) for c in all_clauses),
                    "is_resuming": i > start_index
                }
            )

            # 获取该章节的条款
            chapter_clauses = [c for c in clauses_data if c.parent_chapter == chapter_num]
            chapter_clause_count = len(chapter_clauses)

            # 启发式计算该章节的目标实体数量范围
            entity_count_range = self.calc_target_entity_count(chapter_clause_count)
            self.logger.info(f"[LLM分块] 章节 {chapter_num} 条款数={chapter_clause_count}, 目标实体数量={entity_count_range[0]}-{entity_count_range[1]}")

            # 并行执行：章节内所有 clause 的 LLM 调用同时进行
            para_start = time.time()
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._process_single_clause, clause, entity_count_range): clause
                    for clause in chapter_clauses
                }
                for future in as_completed(futures):
                    # 等待所有 clause 完成（结果在 clause.metadata 中直接修改）
                    pass

            # JSONL 增量写入：每 clause 处理完立即落盘
            if project_id:
                for clause in chapter_clauses:
                    self._append_clause_to_jsonl(project_id, clause)

            chapter_entities_count = sum(len(c.metadata.get("entities", [])) for c in chapter_clauses)
            para_time = time.time() - para_start

            self.logger.info(
                f"[LLM分块]   ← 章节 {chapter_num} 条款分析完成: {len(chapter_clauses)} 条文, "
                f"{chapter_entities_count} 实体 (并行耗时 {para_time:.1f}s)"
            )

            # 构建该章节的文本（用于 section.content）
            chapter_texts = []
            for clause in chapter_clauses:
                if clause.content:
                    chapter_texts.append(f"[{clause.clause_id}] {clause.content}")
            chapter_text = "\n\n".join(chapter_texts)

            # 创建章节对象
            section = SectionSegment(
                chapter_number=chapter_num,
                title=chapter_title,
                content=chapter_text[:500] if chapter_text else ""
            )

            # 更新检查点
            current_checkpoint.current_chapter_index = i
            if i < len(current_checkpoint.chapter_plan):
                current_checkpoint.chapter_plan[i].status = ChapterStatus.COMPLETED
                current_checkpoint.chapter_plan[i].completed_at = datetime.now().isoformat()
                current_checkpoint.chapter_plan[i].clauses_count = len(chapter_clauses)
                current_checkpoint.chapter_plan[i].elements_count = chapter_entities_count

            if project_id:
                self._save_checkpoint(project_id, current_checkpoint)

            chapter_time = time.time() - para_start
            self._report_progress(
                0.1 + (i + 1) / chapter_count * 0.65,
                f"✅ 章节 {chapter_num} 完成: {len(chapter_clauses)} 条文, {chapter_entities_count} 实体 (耗时 {chapter_time:.1f}s)",
                checkpoint_info={
                    "current_chapter": chapter_num,
                    "total_chapters": chapter_count,
                    "completed_clauses_count": len(all_clauses) + len(chapter_clauses),
                    "chapter_completed": True
                }
            )
            self.logger.info(f"[LLM分块] ✅ 章节 {chapter_num} 处理完成，JSONL 已增量写入")

            chapter_results.append((section, list(chapter_clauses)))
            all_clauses.extend(chapter_clauses)

        # =====================================================================
        # Step 3: 保存结果（JSONL 已增量写入，sections + edges 最后写入）
        # =====================================================================
        self.logger.info(f"[LLM分块] Step 3/4: 保存 sections + edges 元数据")
        self._report_progress(0.80, "💾 保存 sections + edges 元数据...")

        # 从 chapter_results 构建 sections
        result.sections = [cr[0] for cr in chapter_results]
        result.clauses = all_clauses
        result.edges = all_edges

        # 将 sections + edges 写入 sections JSON（供 get_intelligent_chunks 读取）
        if project_id:
            sections_data_out = [
                {"chapter_number": s.chapter_number, "title": s.title, "content": s.content}
                for s in result.sections
            ]
            # 将 ClauseSegment 对象转换为字典
            clauses_data_out = [self._clause_to_dict(c) for c in all_clauses]
            from ..models.project import ProjectManager
            ProjectManager.build_intelligent_chunks_tree(
                project_id,
                sections=sections_data_out,
                edges=all_edges or [],
                clauses=clauses_data_out
            )
            self.logger.info(f"[LLM分块] ✅ intelligent_chunks_tree.json 已生成")

        # =====================================================================
        # Step 4: 汇总报告
        # =====================================================================
        self.logger.info("[LLM分块] Step 4/4: 汇总报告")
        total_entities = sum(len(c.metadata.get("entities", [])) for c in all_clauses)
        total_time = time.time() - start_time
        self._report_progress(
            0.95,
            f"📊 标注分析汇总: {len(result.sections)} 章节, {len(all_clauses)} 条文, {total_entities} 实体"
        )

        self._report_progress(1.0, f"✅ 智能标注分析完成! (总耗时 {total_time:.1f}s)")

        self.logger.info(
            f"[LLM分块] ✅ 分析完成 - 章节: {len(result.sections)}, 条文: {len(all_clauses)}, 实体: {total_entities}, "
            f"总耗时: {total_time:.1f}s"
        )

        return result

    def _extract_topic_from_text(
        self,
        clause_text: str
    ) -> str:
        """
        从单条条款文本中提取 topic（阶段 A）

        Args:
            clause_text: 条款文本

        Returns:
            topic 字符串，如果提取失败返回空字符串
        """
        if not clause_text or len(clause_text.strip()) < 10:
            return ""

        try:
            self.logger.info(f"[LLM Topic 提取] 开始调用, clause_text长度={len(clause_text)}")
            response = self._call_llm_with_retry(
                messages=[
                    {"role": "system", "content": self.TOPIC_SYSTEM_PROMPT},
                    {"role": "user", "content": self.TOPIC_USER_PROMPT
                        .replace("{clause_text}", clause_text[:2000])}
                ],
                temperature=0.3
            )
            # 兼容处理：response 可能是 dict 也可能是 list
            if isinstance(response, dict):
                topic = response.get("topic", "") or response.get("content", "") or ""
            elif isinstance(response, list):
                # LLM 返回了数组，尝试取第一个元素作为 topic
                self.logger.warning(f"[LLM Topic 提取] 响应为 list，取第一个元素: {response[0] if response else '空'}")
                topic = str(response[0]) if response else ""
            else:
                topic = str(response) if response else ""
            self.logger.info(f"[LLM Topic 提取] 完成, topic={topic[:30] if topic else '(空)'}")
            return topic.strip()
        except Exception as e:
            self.logger.warning(f"[LLM Topic 提取] 失败: {e}")
            return ""

    def _extract_entities_by_topic(
        self,
        clause_text: str,
        topic: str,
        entity_count_range: tuple = (5, 15)
    ) -> List[str]:
        """
        基于 topic 从条款中提取实体（阶段 B）

        Args:
            clause_text: 条款文本
            topic: 条款的 topic
            entity_count_range: 目标实体数量范围 (min, max)，用于动态调整 prompt

        Returns:
            实体名称列表（扁平字符串列表）
        """
        if not clause_text or len(clause_text.strip()) < 10:
            return []

        min_cnt, max_cnt = entity_count_range

        try:
            self.logger.info(f"[LLM 实体提取] 开始, clause_text长度={len(clause_text)}, topic={topic if topic else '(空)'}, 目标数量={min_cnt}-{max_cnt}")
            # 动态构建用户 prompt，注入目标数量范围
            user_prompt = f"""请提取以下条文中的知识实体：

条文内容：{clause_text[:2000]}

topic：{topic}

要求：
1. topic 描述了这条条文的核心主题，实体必须与 topic 高度相关
2. 严格控制数量，提取 {min_cnt}-{max_cnt} 个与 topic 高度相关的知识实体
3. 无关的实体不要提取

请输出 JSON格式：{{"entities": ["实体1", "实体2", "实体3"]}}"""

            response = self._call_llm_with_retry(
                messages=[
                    {"role": "system", "content": self.ENTITY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            # 兼容处理：response 可能是 dict{"entities": [...]} 也可能是直接 list[...]
            if isinstance(response, dict):
                entities = response.get("entities", [])
            elif isinstance(response, list):
                # LLM 返回了直接数组
                self.logger.warning(f"[LLM 实体提取] 响应为 list，直接作为结果")
                entities = response
            else:
                self.logger.warning(f"[LLM 实体提取] 响应类型异常: {type(response)}")
                entities = []
            # 确保返回的是字符串列表
            if isinstance(entities, list):
                result = [e for e in entities if isinstance(e, str)]
                self.logger.info(f"[LLM 实体提取] 完成, entities_count={len(result)}, entities={result}")
                return result
            self.logger.info(f"[LLM 实体提取] 完成, entities非列表类型")
            return []
        except Exception as e:
            self.logger.warning(f"[LLM 实体提取] 失败: {e}")
            return []

    def _extract_terms_from_text(self, clause_text: str) -> List[Dict]:
        """
        从条款文本中提取术语定义（阶段 C）

        Args:
            clause_text: 条款文本

        Returns:
            术语列表 [{"term_name": ..., "abbreviation": ..., "definition": ...}]
        """
        if not clause_text or len(clause_text.strip()) < 10:
            return []

        try:
            self.logger.info(f"[LLM 术语提取] 开始, clause_text长度={len(clause_text)}")
            user_prompt = self.TERM_USER_PROMPT.replace("{clause_text}", clause_text[:2000])
            response = self._call_llm_with_retry(
                messages=[
                    {"role": "system", "content": self.TERM_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            # 兼容处理
            if isinstance(response, dict):
                terms = response.get("terms", [])
            elif isinstance(response, list):
                self.logger.warning(f"[LLM 术语提取] 响应为 list，直接作为结果")
                terms = response
            else:
                self.logger.warning(f"[LLM 术语提取] 响应类型异常: {type(response)}")
                terms = []
            # 确保返回的是列表
            if isinstance(terms, list):
                result = []
                for t in terms:
                    if isinstance(t, dict):
                        result.append({
                            "term_name": str(t.get("term_name", "")),
                            "abbreviation": str(t.get("abbreviation", "")),
                            "definition": str(t.get("definition", ""))
                        })
                    elif isinstance(t, str):
                        result.append({"term_name": t, "abbreviation": "", "definition": ""})
                self.logger.info(f"[LLM 术语提取] 完成, terms_count={len(result)}")
                return result
            return []
        except Exception as e:
            self.logger.warning(f"[LLM 术语提取] 失败: {e}")
            return []

    def _build_sections_and_clauses_from_chunks(
        self,
        chunks_data: List[Dict]
    ) -> tuple:
        """
        直接从 chunks.json 构建章节和条款结构。

        逻辑：
        1. type=="title" 的 chunk 作为章节标题
        2. 相邻 title chunk 之间的内容属于前一个章节
        3. type=="text" 且以 X.Y.Z 格式开头的作为条款
        4. 小章节信号（如 1.0.1）标记为 sub_chapter

        Args:
            chunks_data: chunks.json 数据列表

        Returns:
            (sections, clauses, chapter_plan)
            - sections: SectionSegment 列表
            - clauses: ClauseSegment 列表（含 parent_chapter 关联）
            - chapter_plan: ChapterPlan 列表（用于检查点）
        """
        import re

        sections = []
        clauses = []
        chapter_plan = []

        # 用于匹配大章节标题（如 "2 术语"）
        CHAPTER_PATTERN = re.compile(r'^(\d+(?:\.\d+)?)\s+(.+)')

        # 用于匹配附录标题（如 "附录A"、"附录 A"、"附录A 系数k值"）
        APPENDIX_PATTERN = re.compile(r'^附录[A-Z](?:\s+(.+))?$')

        # 编码修复：尝试将乱码内容转换为正确的中文
        def _fix_encoding(content: str) -> str:
            """
            修复 MinerU 解析产生的编码问题。
            MinerU 返回的 GBK 编码内容被当作 UTF-8 写入文件，导致中文显示为乱码。
            此函数尝试用 GBK 重新解码 UTF-8 字节来修复。
            """
            if not content or not isinstance(content, str):
                return content
            try:
                # 将 content 编码为 UTF-8 字节，再用 GBK 解码
                fixed = content.encode('utf-8').decode('gbk')
                return fixed
            except (UnicodeDecodeError, UnicodeEncodeError):
                return content

        # 用于匹配条款编号（如 "2.1"、"3.5.2"、"1.0.1"）
        CLAUSE_PATTERN = re.compile(r'^(\d+\.\d+(?:\.\d+)?)\s*(.*)')

        # 用于匹配附录条款编号（如 "A.0.7"、"B.1.3"）
        APPENDIX_CLAUSE_PATTERN = re.compile(r'^([A-Z]\.\d+(?:\.\d+)?)\s*(.*)')

        # 用于匹配子章节信号（如 "3.1"、"3.1.1"）- 只要有 . 就是子章节
        SUB_CHAPTER_PATTERN = re.compile(r'^\d+\.\d+')

        current_chapter = None
        current_chapter_idx = -1

        for i, chunk in enumerate(chunks_data):
            # 兼容多种 chunk 格式
            # 1. 顶层字段：chunk.get('type'), chunk.get('content')
            # 2. metadata 嵌套：chunk.get('metadata', {}).get('type')
            # 3. 旧格式 text 字段：chunk.get('text')
            metadata = chunk.get('metadata', {})
            chunk_type = chunk.get('type') or metadata.get('type', '')
            content = chunk.get('content') or chunk.get('text') or metadata.get('content', '')

            # 修复编码问题：GBK 编码内容被当作 UTF-8 写入
            content = _fix_encoding(content)

            # 调试日志：打印前 5 条 chunk 的 type 和 content
            if i < 5:
                self.logger.info(f"[章节构建] chunk[{i}] type={chunk_type!r}, content={str(content)[:60]!r}")
            if isinstance(content, str):
                content = content.strip()
            else:
                content = ''
            page_idx = chunk.get('page_idx') or metadata.get('page_idx') or 0
            bbox_viewport = chunk.get('bbox_viewport') or metadata.get('bbox_viewport', [])
            chunk_id = chunk.get('chunk_id') or metadata.get('chunk_id', f'chunk_{i}')
            source = chunk.get('source') or metadata.get('source', '')

            if not content:
                continue

            # 判断是否为章节标题
            # 策略：type=='title' 且符合章节编号格式（如 "3 电气和导体的选择"）
            if chunk_type == 'title' and CHAPTER_PATTERN.match(content):
                m = CHAPTER_PATTERN.match(content)
                chapter_num_str = m.group(1)
                title = m.group(2).strip()
                self.logger.info(f"[章节构建] ✅ 识别到一级标题: page={page_idx}, idx={i}, chapter={chapter_num_str}, title={title!r}")

                # 转换章节编号
                parts = chapter_num_str.split('.')
                if len(parts) == 1:
                    chapter_num = int(parts[0])
                else:
                    chapter_num = float(chapter_num_str)

                # 检查是否是小章节信号（如 1.0.1）作为条款而非章节
                if SUB_CHAPTER_PATTERN.match(chapter_num_str):
                    # 这是一个小章节信号，实际上应该是条款
                    # 不创建新章节，保留 current_chapter
                    pass
                else:
                    # 创建新章节
                    current_chapter_idx += 1
                    current_chapter = {
                        'chapter_number': chapter_num,
                        'title': title,
                        'page_idx': page_idx,
                        'start_idx': i,
                        'end_idx': i,
                        'level': 1 if len(parts) == 1 else 2,
                        'sub_chapters': []
                    }
                    sections.append(current_chapter)
                    chapter_plan.append(ChapterPlan(
                        chapter_number=chapter_num,
                        title=title,
                        start_position=i,
                        end_position=len(chunks_data),
                        status=ChapterStatus.PENDING,
                        chapter_type='normative'
                    ))

                    self.logger.info(f"[章节构建] {chapter_num}. {title} (page={page_idx})")
                continue

            # 判断是否为附录标题（如 "附录A"、"附录A 系数k值"）
            if chunk_type == 'title' and APPENDIX_PATTERN.match(content):
                m = APPENDIX_PATTERN.match(content)
                # m.group(0) = "附录A" 或 "附录A 系数k值"，索引 2 即字母 "A"
                appendix_letter = m.group(0)[2]
                # m.group(1) = "系数k值"（可选部分，无则标题同字母）
                title = m.group(1).strip() if m.group(1) else appendix_letter
                self.logger.info(f"[章节构建] ✅ 识别到附录标题: page={page_idx}, idx={i}, appendix={appendix_letter}, title={title!r}")

                # 创建附录章节（使用字母编号，chapter_number 存储为负数或特殊值以区分）
                # 使用 100 + ord(letter) - ord('A') 作为章节编号，附录A=165, 附录B=166...
                chapter_num = 200 + ord(appendix_letter) - ord('A')
                current_chapter_idx += 1
                current_chapter = {
                    'chapter_number': chapter_num,
                    'chapter_letter': appendix_letter,  # 保存原始字母
                    'title': f"附录{appendix_letter}" + (f" {title}" if title else ""),
                    'page_idx': page_idx,
                    'start_idx': i,
                    'end_idx': i,
                    'level': 1,
                    'sub_chapters': [],
                    'chapter_type': 'appendix'
                }
                sections.append(current_chapter)
                chapter_plan.append(ChapterPlan(
                    chapter_number=chapter_num,
                    title=current_chapter['title'],
                    start_position=i,
                    end_position=len(chunks_data),
                    status=ChapterStatus.PENDING,
                    chapter_type='appendix'
                ))

                self.logger.info(f"[章节构建] 附录 {appendix_letter}: {title} (page={page_idx})")
                continue

            # 如果 type=='title' 但不符合编号格式（如 "前 言"、"目 录"），跳过
            # 不影响 current_chapter，条款仍归属到前一个有效章节
            elif chunk_type == 'title' and not CHAPTER_PATTERN.match(content):
                self.logger.info(f"[章节构建] ⏭️ type=title 但无章节编号，跳过: page={page_idx}, idx={i}, content={content[:50]!r}")
            elif chunk_type == 'title':
                # X.0.Y 格式的术语标题（如 "2.0.1 预期接触电压"），应作为条款处理
                term_title_m = re.match(r'^(\d+\.\d+\.\d+)\s+(.+)', content)
                if term_title_m and current_chapter is not None:
                    clause_id = term_title_m.group(1)
                    clause_title = term_title_m.group(2).strip()[:80]
                    parts = clause_id.split('.')
                    is_sub_chapter = len(parts) == 3 and parts[2] == '0'
                    # 收集术语定义：检查下一个 chunk 是否为同页 text（定义内容）
                    term_definition = content
                    next_chunk = chunks_data[i + 1] if i + 1 < len(chunks_data) else None
                    if (next_chunk and next_chunk.get('page_idx') == page_idx
                            and next_chunk.get('type') == 'text'):
                        term_definition = content + '\n' + (next_chunk.get('content') or '').strip()
                    clause = ClauseSegment(
                        clause_id=clause_id,
                        clause_title=clause_title,
                        content=term_definition,
                        paragraphs=[],
                        requirement_type=RequirementType.RECOMMENDED,
                        applicable_systems=[],
                        cross_refs=[],
                        source=source,
                        page=page_idx,
                        triplets=[],
                        clause_items=[],
                        is_term_definition=True,
                        terms=[{"term_name": clause_title, "definition": term_definition.split('\n', 1)[-1].strip()}],
                        formula_content=None,
                        semantics_enriched=False,
                        parent_chapter=current_chapter['chapter_number'],
                        referenced_clauses=[],
                        referenced_standards=[],
                        metadata={
                            "chunk_type": chunk_type,
                            "chunk_id": chunk_id,
                            "parent_chapter": current_chapter['chapter_number'],
                            "parent_chapter_title": current_chapter['title'],
                            "page_idx": page_idx,
                            "bbox_viewport": bbox_viewport,
                            "is_sub_chapter": is_sub_chapter,
                            "entities": []
                        }
                    )
                    clauses.append(clause)
                    if is_sub_chapter:
                        current_chapter['sub_chapters'].append(clause_id)
                    self.logger.debug(f"[条款构建]   {clause_id} {clause_title[:30]}... (page={page_idx}) [术语条款]")
                    current_chapter['end_idx'] = i
                else:
                    self.logger.debug(f"[章节构建] 跳过无编号标题: {content[:30]}")
                continue

            # 如果有当前章节，处理条款
            if current_chapter is not None:
                # 更新章节的结束位置
                current_chapter['end_idx'] = i

                # 检查是否为条款（先检查普通条款，再检查附录条款）
                clause_match = CLAUSE_PATTERN.match(content)
                appendix_match = APPENDIX_CLAUSE_PATTERN.match(content) if not clause_match else None

                m = clause_match or appendix_match
                if m:
                    clause_id = m.group(1)
                    clause_title = m.group(2).strip()[:80] if m.group(2) else ''

                    # 判断条款层级（附录条款不算 sub_chapter）
                    parts = clause_id.split('.')
                    is_appendix_clause = clause_id[0].isalpha()
                    is_sub_chapter = not is_appendix_clause and len(parts) == 3 and parts[2] == '0'

                    # 判断 requirement_type（基于关键词）
                    req_type = RequirementType.RECOMMENDED
                    if any(kw in content for kw in ['应', '必须', '严禁', '不得', '应不', '不应', '不宜']):
                        req_type = RequirementType.MANDATORY
                    elif any(kw in content for kw in ['宜', '可', '建议', '推荐']):
                        req_type = RequirementType.RECOMMENDED
                    elif any(kw in content for kw in ['禁止', '不应', '不得']):
                        req_type = RequirementType.PROHIBITED

                    clause = ClauseSegment(
                        clause_id=clause_id,
                        clause_title=clause_title,
                        content=content,
                        paragraphs=[],
                        requirement_type=req_type,
                        applicable_systems=[],
                        cross_refs=[],
                        source=source,
                        page=page_idx,
                        triplets=[],
                        clause_items=[],
                        is_term_definition=False,
                        terms=[],
                        formula_content=None,
                        semantics_enriched=False,
                        parent_chapter=current_chapter['chapter_number'],
                        referenced_clauses=[],
                        referenced_standards=[],
                        metadata={
                            "chunk_type": chunk_type,
                            "chunk_id": chunk_id,
                            "parent_chapter": current_chapter['chapter_number'],
                            "parent_chapter_title": current_chapter['title'],
                            "page_idx": page_idx,
                            "bbox_viewport": bbox_viewport,
                            "is_sub_chapter": is_sub_chapter,
                            "entities": []
                        }
                    )
                    clauses.append(clause)

                    if is_sub_chapter:
                        current_chapter['sub_chapters'].append(clause_id)

                    clause_type = "附录条款" if is_appendix_clause else "条款"
                    self.logger.debug(f"[条款构建]   {clause_id} {clause_title[:30]}... (page={page_idx}) [{clause_type}]")

        # 更新 chapter_plan 的 end_position
        for plan in chapter_plan:
            plan.end_position = len(chunks_data)  # 简化处理

        # =====================================================================
        # 聚合所有 chunk 的 bbox + chunk_id 到对应 clause
        # （一个 clause 可能跨多个 chunk，需要扫描所有 chunk 归属到对应条款）
        # =====================================================================
        clause_bbox_map: Dict[str, List[tuple]] = {}
        clause_chunk_map: Dict[str, List[str]] = {}
        current_clause_id: Optional[str] = None
        for chunk in chunks_data:
            chunk_id = chunk.get('chunk_id', '')
            page_idx = chunk.get('page_idx', 0)
            bbox = chunk.get('bbox_viewport') or chunk.get('bbox_pdf') or []
            # 查找该 chunk 的 clause_id（通过 content 中的条款编号）
            chunk_content = _fix_encoding(chunk.get('content', ''))
            m = CLAUSE_PATTERN.match(chunk_content.strip())
            if not m:
                # 尝试附录条款（如 A.0.1）
                m = APPENDIX_CLAUSE_PATTERN.match(chunk_content.strip())
            if m:
                current_clause_id = m.group(1)
            # 当前 chunk 归属到 current_clause_id（直到遇到新的条款编号）
            if current_clause_id is not None:
                # bbox
                if bbox and len(bbox) >= 4:
                    if current_clause_id not in clause_bbox_map:
                        clause_bbox_map[current_clause_id] = []
                    clause_bbox_map[current_clause_id].append((page_idx, bbox))
                # chunk_id
                if current_clause_id not in clause_chunk_map:
                    clause_chunk_map[current_clause_id] = []
                if chunk_id:
                    clause_chunk_map[current_clause_id].append(chunk_id)

        # 将聚合的 bboxs 和多 chunk 文本写入各 clause
        chunk_content_map: Dict[str, str] = {}
        chunk_content_map_built = False
        for clause in clauses:
            cid = clause.clause_id
            if cid in clause_bbox_map:
                # 按 page 分组：同页的多个 bbox 合并为最大矩形
                page_groups: Dict[int, List[list]] = {}
                for p_idx, bb in clause_bbox_map[cid]:
                    if p_idx not in page_groups:
                        page_groups[p_idx] = []
                    page_groups[p_idx].append(bb)

                merged_bboxs = []
                for p_idx, bboxes in page_groups.items():
                    # 取所有 bbox 的并集（最大覆盖范围）
                    x0 = min(bb[0] for bb in bboxes)
                    y0 = min(bb[1] for bb in bboxes)
                    x1 = max(bb[2] for bb in bboxes)
                    y1 = max(bb[3] for bb in bboxes)
                    # 简化格式: [page, x0, y0, x1, y1]
                    merged_bboxs.append([p_idx + 1, x0, y0, x1, y1])

                clause.metadata['bboxs'] = merged_bboxs
                # 同时记录所有来源 chunk_id（去重）
                clause.metadata['chunks'] = list(dict.fromkeys(clause_chunk_map.get(cid, [])))
            elif cid in clause_chunk_map:
                # 无 bbox 但有 chunk 映射：仍需记录 chunks 元数据
                clause.metadata['chunks'] = list(dict.fromkeys(clause_chunk_map.get(cid, [])))

            # 合并同一 clause 下所有 chunk 的文本到 content
            if cid in clause_chunk_map:
                chunk_ids = clause_chunk_map[cid]
                # 构建 chunk_id → chunk content 的快速查找表（复用于所有 clause）
                if not chunk_content_map_built:
                    for chunk in chunks_data:
                        c_id = chunk.get('chunk_id') or chunk.get('metadata', {}).get('chunk_id', '')
                        if c_id:
                            chunk_content_map[c_id] = (chunk.get('content') or chunk.get('text') or '').strip()
                    chunk_content_map_built = True

                if len(chunk_ids) > 1:
                    # 按原始顺序拼接所有 chunk 文本（跳过空内容，去重首行标题重复）
                    merged_texts = []
                    seen_first_line = None
                    missing_ids = []
                    for c_id in chunk_ids:
                        txt = chunk_content_map.get(c_id, '')
                        if not txt:
                            missing_ids.append(c_id)
                            continue
                        first_line = txt.split('\n', 1)[0]
                        if seen_first_line is None:
                            seen_first_line = first_line
                            merged_texts.append(txt)
                        else:
                            # 跳过与首 chunk 首行相同的行（标题重复）
                            if first_line == seen_first_line:
                                remaining = txt.split('\n', 1)
                                if len(remaining) > 1 and remaining[1].strip():
                                    merged_texts.append(remaining[1].strip())
                            else:
                                merged_texts.append(txt)
                    if missing_ids:
                        self.logger.warning(f"[条款合并] clause_id={cid}: {len(missing_ids)}/{len(chunk_ids)} chunks 内容缺失: {missing_ids}")
                    if merged_texts:
                        clause.content = '\n'.join(merged_texts)
                        self.logger.info(f"[条款合并] clause_id={cid}: merged {len(chunk_ids)} chunks ({len(merged_texts)} texts), content_len={len(clause.content)}")
                else:
                    # 单 chunk 情况：确保 content 包含该 chunk 的原始文本
                    if len(chunk_ids) == 1:
                        txt = chunk_content_map.get(chunk_ids[0], '')
                        if txt and len(txt) > len(clause.content):
                            clause.content = txt
                            self.logger.debug(f"[条款合并] clause_id={cid}: 单 chunk，content 已更新为原始文本 (len={len(txt)})")

        self.logger.info(f"[章节构建] 完成: {len(sections)} 章节, {len(clauses)} 条款，bboxs 聚合完成")
        return sections, clauses, chapter_plan

    # =========================================================================
    # 核心提取方法 - LLM 目录提取（保留用于参考，后续可删除）
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
                temperature=0.3,
                max_tokens=self.MAX_TOKENS_FOR_TOC
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
        chapter_num: int,
        chapter_start_pos: int,
        chapter_end_pos: int
    ) -> List[ClauseSegment]:
        """
        从章节文本中提取条文及实体（使用 LLM）

        简化版：LLM 只提取实体，条文结构从 md_content 的条款注册表获取。

        Args:
            text: 章节文本
            source_info: 来源信息
            chapter_num: 章节编号

        Returns:
            条文列表（含提取的实体）
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

            # 简化版：解析实体列表（不再是 clauses 数组）
            entities_data = response.get("entities", [])
            referenced_tables = response.get("referenced_tables", [])
            referenced_formulas = response.get("referenced_formulas", [])
            referenced_clauses = response.get("referenced_clauses", [])
            referenced_standards = response.get("referenced_standards", [])

            # 从条款注册表获取该章节的条款列表
            # 用 char_offset（字符偏移）判断条款是否在本章节范围内
            chapter_clauses = []
            for cid, entry in self._clause_registry.items():
                char_offset = entry.get("char_offset", 0)
                if chapter_start_pos <= char_offset < chapter_end_pos:
                    chapter_clauses.append({
                        "clause_id": cid,
                        "clause_title": entry.get("title", ""),
                        "content": entry.get("title", ""),  # 实际内容会在 chunk() 中补充
                        "line_range": entry.get("line_range"),
                        "char_offset": char_offset
                    })

            # 按 line_range 排序
            chapter_clauses.sort(key=lambda x: x.get("line_range", 0))

            clauses = []
            for cd in chapter_clauses:
                clause_id = cd.get("clause_id", "")

                # 从 LLM 返回的实体中筛选属于本条款的
                # （简化：全部实体都关联到第一个条款，实际应在款/项级别提取）
                clause_entities = entities_data if entities_data else []

                clause = ClauseSegment(
                    clause_id=clause_id,
                    clause_title=cd.get("clause_title", ""),
                    content=cd.get("content", ""),
                    paragraphs=[],
                    requirement_type=RequirementType.RECOMMENDED,
                    applicable_systems=[],
                    cross_refs=[],
                    source=source_info.get("source", ""),
                    page=source_info.get("page"),
                    triplets=[],  # 简化版不使用三元组
                    clause_items=[],
                    is_term_definition=False,
                    terms=[],  # 术语从实体中提取
                    formula_content=None,
                    semantics_enriched=False,
                    parent_chapter=chapter_num,
                    referenced_clauses=[],
                    referenced_standards=referenced_standards,
                    metadata={
                        "chunk_type": "clause",
                        "parent_chapter": chapter_num,
                        "semantics_enriched": False,
                        "entities": clause_entities,  # 新增：提取的实体
                        "referenced_tables": referenced_tables,
                        "referenced_formulas": referenced_formulas,
                        "referenced_clauses": referenced_clauses,
                        "referenced_standards": referenced_standards
                    }
                )

                # 构建交叉引用
                clause_cross_refs, clause_referenced = self._build_cross_refs(
                    {
                        "referenced_tables": referenced_tables,
                        "referenced_formulas": referenced_formulas,
                        "referenced_clauses": referenced_clauses,
                        "referenced_standards": referenced_standards
                    },
                    self._clause_registry
                )
                clause.cross_refs = clause_cross_refs
                clause.referenced_clauses = clause_referenced

                clauses.append(clause)

            self.logger.info(f"章节 {chapter_num}: LLM 提取 {len(clauses)} 条条文, {len(entities_data)} 个实体")
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

