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

1. **核心**：使用 `triplets` 数组提取语义三元组，不要用平行列表！
2. **术语章节（如"2.0.5 直接接触防护"）需特殊处理**，在 terms 字段中返回术语定义，triplets 设为 []。
3. **款/项（如"1、"、"2、"）应作为 clause_items 独立提取**，每个款/项单独抽取三元组。
4. **OCR 文本中的 # 前缀不是条文内容**，忽略即可。
5. **表格引用**（如 `<table>...</table>` 或 "表3.2.2"）放入 referenced_tables。
6. **公式引用**（如 "公式（3.2.14）" 或 "$...$"）放入 referenced_formulas 和 formula_content。
7. **三元组不要编造**，只在条文中明确出现的 component/action/obj 才提取。


请输出 JSON 格式：
```json
{{
    "clauses": [
        {{
            "clause_id": "3.2.1",
            "clause_title": "导体应满足线路保护的要求",
            "clause_content": "导体应满足线路保护的要求...",
            "requirement_type": "mandatory",
            "is_term_definition": false,
            "triplets": [
                {{"component": "导体", "action": "承受", "obj": "线路保护", "condition": "过负荷时", "requirement": "mandatory"}},
                {{"component": "导体", "action": "选用", "obj": "截面积", "condition": "", "requirement": "mandatory"}}
            ],
            "terms": [],
            "referenced_tables": ["表3.2.2"],
            "referenced_formulas": ["公式（3.2.14）"],
            "formula_content": "S >= I*t/k",
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

示例条文解析：

**普通条文**：
原文："低压配电设计所选用的电器，应符合国家现行的有关产品标准"
错误提取（不要这样）：
- triplets: [{"component": "电器", "action": "选用", "obj": ""}, {"component": "", "action": "符合", "obj": "产品标准"}]  ← 编造了空字段

正确提取：
- clause_id: "3.1.1"
- triplets: [{"component": "电器", "action": "选用", "obj": "国家标准"}]  ← 单一准确三元组

**更多正确三元组示例**：
- "应设置剩余电流保护电器" → {"component": "配电系统", "action": "设置", "obj": "剩余电流保护电器"}
- "采用阻燃电缆" → {"component": "线路", "action": "采用", "obj": "阻燃电缆"}
- "严禁使用TN-C系统" → {"component": "配电系统", "action": "使用", "obj": "TN-C系统", "requirement": "prohibited"}
- "电缆应敷设在电缆桥架内" → {"component": "电缆", "action": "敷设", "obj": "电缆桥架"}

**普通条文**：
原文："低压配电设计所选用的电器，应符合国家现行的有关产品标准"
错误提取（不要这样）：
- actions: ["选用", "符合"]  ← "符合"不是动作，是状态描述

正确提取：
- clause_id: "3.1.1"
- components: ["电器"]
- actions: ["选用"]  ← "选用"是实操动作，保留
- objects: ["产品标准"]  ← "符合"应归为objects
- conditions: []

更多正确示例：
- "应设置剩余电流保护电器" → actions: ["设置"]，components: ["剩余电流保护电器"]
- "采用阻燃电缆" → actions: ["采用"]，components: ["阻燃电缆"]
- "严禁使用TN-C系统" → actions: ["使用"]，conditions: ["TN-C系统"]，requirement_type: "prohibited"

**术语章节**（第2章，格式如 2.0.x）：
原文：
```
2.0.5 直接接触防护
无故障条件下的电击防护。
```
正确提取：
- clause_id: "2.0.5"
- clause_title: "直接接触防护"
- clause_content: "无故障条件下的电击防护"
- is_term_definition: true
- terms: [{{"term_name": "直接接触防护", "definition": "无故障条件下的电击防护"}}]
- actions: []，conditions: []，components: [] ← 术语章节不提取这些！

原文：
```
2.0.12 预期接触电压
人或动物尚未接触到可导电部分时，可能同时触及的可导电部分之间的电压。
```
正确提取：
- clause_id: "2.0.12"
- clause_title: "预期接触电压"
- clause_content: "人或动物尚未接触到可导电部分时，可能同时触及的可导电部分之间的电压"
- is_term_definition: true
- terms: [{{"term_name": "预期接触电压", "definition": "人或动物尚未接触到可导电部分时，可能同时触及的可导电部分之间的电压"}}]

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
    MAX_CHARS_PER_CHAPTER = 3000        # 每章节最大字符数
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

    def _clause_to_dict(self, clause: ClauseSegment) -> Dict[str, Any]:
        """将 ClauseSegment 转换为字典"""
        return {
            "clause_id": clause.clause_id,
            "clause_title": clause.clause_title,
            "content": clause.content,
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
            # 语义三元组（核心）
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
            # 款/项结构化
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

    def _element_to_dict(self, element: ElementSegment) -> Dict[str, Any]:
        """将 ElementSegment 转换为字典"""
        return {
            "element_type": element.element_type.value if hasattr(element.element_type, 'value') else str(element.element_type),
            "source_id": element.source_id,
            "key": element.key,
            "value": str(element.value) if element.value else "",
            "unit": element.unit,
            "condition": element.condition,
            "abbreviation": element.abbreviation,
            "definition": element.definition,
            "content": element.content,
            "metadata": element.metadata or {}
        }

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
                metadata=cd.get('metadata', {})
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
        project_id: Optional[str] = None
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

        # 合并文本
        full_text = self._merge_text_chunks(text_chunks)
        source_info = self._get_source_info(text_chunks)

        self.logger.info(f"[LLM分块] 开始分析，文本长度: {len(full_text)}")
        self.logger.info(f"[LLM分块] 来源信息: {source_info}")
        self._report_progress(0.0, "🚀 开始智能标注分析...")

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

            # 提取章节内的条文
            clause_start = time.time()
            self.logger.info(f"[LLM分块]   → LLM 提取条文中...")
            chapter_clauses = self._extract_clauses_from_chapter(
                chapter_text, source_info, chapter_num
            )
            clause_time = time.time() - clause_start
            self.logger.info(f"[LLM分块]   ← 条文提取完成: {len(chapter_clauses)} 条 (耗时 {clause_time:.1f}s)")

            # 提取章节内的要素
            element_start = time.time()
            self.logger.info(f"[LLM分块]   → LLM 提取要素中...")
            chapter_elements = self._extract_elements_from_chapter(
                chapter_text, chapter_clauses, chapter_num
            )
            element_time = time.time() - element_start
            self.logger.info(f"[LLM分块]   ← 要素提取完成: {len(chapter_elements)} 个 (耗时 {element_time:.1f}s)")

            # 为条文和要素标注 PDF 位置（基于字符偏移估算）
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
            chapter_time = time.time() - clause_start
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
        progress_callback: Optional[Callable] = None
    ) -> HierarchicalChunkResult:
        """单文本分块入口"""
        if progress_callback:
            self.progress_callback = progress_callback

        fake_chunk = TextChunk(text=text, metadata={})
        return self.chunk([fake_chunk])

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
                    {"role": "user", "content": self.CLAUSE_USER_PROMPT.format(
                        document_text=text[:self.MAX_CHARS_PER_CHAPTER],
                        source=source_info.get("source", "")
                    )}
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
                    cross_refs=self._build_cross_refs(cd),
                    source=source_info.get("source", ""),
                    triplets=triplets,
                    clause_items=clause_items,
                    is_term_definition=cd.get("is_term_definition", False),
                    terms=terms_list,
                    formula_content=cd.get("formula_content"),
                    semantics_enriched=bool(triplets),
                    parent_chapter=chapter_num,
                    metadata={
                        "chunk_type": "clause",
                        "parent_chapter": chapter_num,
                        "semantics_enriched": bool(triplets),
                        "is_term_definition": cd.get("is_term_definition", False),
                        "terms": terms_list,
                        "formula_content": cd.get("formula_content"),
                        "referenced_tables": cd.get("referenced_tables", []),
                        "referenced_formulas": cd.get("referenced_formulas", [])
                    }
                )
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

    def _build_cross_refs(self, clause_data: Dict) -> List[CrossReference]:
        """构建交叉引用"""
        refs = []

        for table in clause_data.get("referenced_tables", []):
            refs.append(CrossReference(
                ref_id=table,
                ref_type="table",
                description=f"引用表格 {table}"
            ))

        for formula in clause_data.get("referenced_formulas", []):
            refs.append(CrossReference(
                ref_id=formula,
                ref_type="formula",
                description=f"引用公式 {formula}"
            ))

        return refs

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
    return chunker.chunk(text_chunks, progress_callback)
