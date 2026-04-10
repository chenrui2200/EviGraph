"""
Clause 数据结构定义 - 多层级语义分块系统

定义三级分块模型的数据结构：
- Level-1: 章节级 (Section)
- Level-2: 条文级 (Clause)
- Level-3: 要素级 (Element)
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
import hashlib
import uuid


class ChunkLevel(Enum):
    """分块层级枚举"""
    LEVEL_1 = 1  # 章节级
    LEVEL_2 = 2  # 条文级
    LEVEL_3 = 3  # 要素级


class ElementType(Enum):
    """
    要素类型枚举

    Level-3 要素类型，支持 SOTA 图谱构建：
    - TABLE_ROW: 表格行参数
    - FORMULA: 计算公式
    - TERM: 术语定义
    - PARAMETER: 技术参数
    - COMPONENT: 设备/系统/材料组件
    - OBJECT: 操作对象
    """
    TABLE_ROW = "table_row"
    FORMULA = "formula"
    TERM = "term"
    PARAMETER = "parameter"
    COMPONENT = "component"      # 设备、系统、材料
    OBJECT = "object"            # 操作对象


class RequirementType(Enum):
    """要求类型枚举"""
    MANDATORY = "mandatory"      # 强制性
    RECOMMENDED = "recommended"  # 推荐性
    PROHIBITED = "prohibited"    # 禁止性


@dataclass
class CrossReference:
    """交叉引用"""
    ref_id: str                    # 引用编号，如 "5.2.9"
    ref_type: str = "clause"      # clause, formula, table
    description: str = ""


@dataclass
class ReferencedClause:
    """
    被引用的条款

    用于表示当前条文引用了其他条款的关系。
    例如："应按本规范第5.2.4条的规定" → referenced_clauses = ["5.2.4"]
    """
    clause_id: str = ""           # 被引用的条款编号，如 "5.2.4"、"A.0.7"
    context: str = ""             # 引用上下文，如 "本规范第5.2.4条的规定"
    ref_type: str = "clause"      # clause / appendix / section
    # 物理定位（由 clause_registry 补全）
    page_idx: Optional[int] = None
    chunk_id: Optional[str] = None
    section_title: Optional[str] = None



@dataclass
class SemanticTriplet:
    """
    语义三元组 - ClauseSegment 的核心抽取单元

    每个三元组表示一条完整的语义关系：
    (组件/施事者, 动作, 对象/受事者)

    例如：
    - 断路器 -选用→ 配电线路
    - 导体 -敷设→ 电缆桥架
    - TN系统 -严禁使用→

    这样做的好处：
    1. 消除平行列表的笛卡尔积歧义
    2. 每个三元组独立建关系，语义准确
    3. 支持细粒度的知识问答和推理
    """
    component: str = ""    # 施事组件（设备/系统/材料），如 "断路器"、"导体"
    action: str = ""       # 实操动作（必须可执行），如 "选用"、"敷设"、"接地"
    obj: str = ""          # 受事对象（操作目标），如 "截面积"、"配电线路"、"TN-C系统"
    condition: str = ""     # 适用条件（可选），如 "短路条件下"、"爆炸危险场所"
    requirement: str = "mandatory"  # 要求类型：mandatory / recommended / prohibited

@dataclass
class ClauseItem:
    """
    款/项结构化数据 - ClauseSegment 的子单元

    用于提取条文中的分级列表项（如 "1、"、"2、"）
    每个款/项独立抽取要素，作为更细粒度的检索单元
    """
    item_number: str = ""                       # 款/项编号，如 "1"、"2"、"1）"
    item_content: str = ""                       # 款/项内容
    components: List[str] = field(default_factory=list)   # 款/项涉及的组件
    actions: List[str] = field(default_factory=list)      # 款/项涉及的动作
    conditions: List[str] = field(default_factory=list)   # 款/项涉及的条件
    objects: List[str] = field(default_factory=list)      # 款/项涉及的对象
    # 款/项级语义三元组（每个款/项内的精确三元组）
    triplets: List[SemanticTriplet] = field(default_factory=list)


@dataclass
class SystemApplicability:
    """适用系统"""
    system_type: str               # TN, TT, IT
    sub_type: Optional[str] = None  # TN-C, TN-S, TN-C-S


@dataclass
class HierarchicalChunk:
    """
    多层级分块基类

    所有层级的chunk都继承此基类
    """
    id: str = ""
    level: ChunkLevel = ChunkLevel.LEVEL_2
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None  # 文档来源文件名
    page: Optional[int] = None   # 页码

    def __post_init__(self):
        if not self.id:
            # 生成稳定ID
            content_hash = hashlib.md5(self.content.encode()).hexdigest()[:8]
            self.id = f"{self.level.name}_{content_hash}"

    @property
    def level_name(self) -> str:
        """获取层级名称"""
        return f"Level{self.level.value}"

    def to_episode_dict(self) -> Dict[str, Any]:
        """转换为Episode节点字典"""
        # 构建metadata
        episode_metadata = {
            "level": self.level.value,
            "level_name": self.level_name,
        }
        # 先合并其他metadata
        episode_metadata.update(self.metadata)
        # 然后确保 base class 的 source/page 优先级更高（覆盖可能错误的值）
        if self.source is not None:
            episode_metadata["source"] = self.source
        if self.page is not None:
            episode_metadata["page"] = self.page

        return {
            "text": self.content,
            "metadata": episode_metadata
        }


@dataclass
class SectionSegment(HierarchicalChunk):
    """
    Level-1: 章节级分块

    用于宏观主题检索和知识图谱导航节点
    """
    chapter_number: Optional[int] = None
    section_number: Optional[int] = None
    title: str = ""
    children: List[str] = field(default_factory=list)  # 子章节/条文ID

    def __post_init__(self):
        self.level = ChunkLevel.LEVEL_1
        if not self.id:
            prefix = f"CH{self.chapter_number or 'X'}"
            content_hash = hashlib.md5(self.content.encode()).hexdigest()[:8]
            self.id = f"{prefix}_{content_hash}"

        # 补充metadata
        self.metadata.update({
            "chunk_type": "section",
            "chapter_number": self.chapter_number,
            "section_number": self.section_number,
            "title": self.title,
            "children": self.children
        })

        # 调用父类__post_init__
        super().__post_init__()


@dataclass
class ClauseSegment(HierarchicalChunk):
    """
    Level-2: 条文级分块 (核心检索层)

    单条条文 + 所有款/项，是90%技术问答的主要检索单元

    核心设计：使用语义三元组 (SemanticTriplet) 而非平行列表，
    消除笛卡尔积歧义，确保 (Component, Action, Object) 精确匹配。

    SOTA 图谱构建支持：
    - 语义三元组精确建关系
    - 层级关联 (parent_chapter)
    - OCR 感知处理 (# 前缀、跨行合并、款/项结构化)
    """
    clause_id: str = ""                    # 条文编号，如 "5.2.8"
    clause_title: str = ""
    paragraphs: List[str] = field(default_factory=list)  # 款/项内容
    formula: Optional[str] = None          # 公式内容
    formula_id: Optional[str] = None       # 公式编号，如 "3.2.14"
    table_refs: List[str] = field(default_factory=list)  # 引用的表格
    cross_refs: List[CrossReference] = field(default_factory=list)
    applicable_systems: List[SystemApplicability] = field(default_factory=list)
    requirement_type: RequirementType = RequirementType.RECOMMENDED

    # 核心字段：语义三元组列表（每个三元组 = 一条精确关系）
    triplets: List[SemanticTriplet] = field(default_factory=list)

    # 款/项结构化（每个款/项内也有三元组）
    clause_items: List[ClauseItem] = field(default_factory=list)

    # OCR 增强字段
    is_term_definition: bool = False    # 是否为术语定义章节（如 2.0.x）
    terms: List[Dict] = field(default_factory=list)   # 术语定义列表
    formula_content: Optional[str] = None  # 公式具体表达式
    semantics_enriched: bool = False

    # 层级关联
    parent_chapter: Optional[int] = None  # 所属章节编号

    # 条款引用（新增）
    referenced_clauses: List[ReferencedClause] = field(default_factory=list)  # 引用的其他条款
    referenced_standards: List[str] = field(default_factory=list)              # 引用的外部标准

    # 兼容字段：从 triplets 推导而来（仅用于序列化）
    conditions: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
    objects: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.level = ChunkLevel.LEVEL_2
        if not self.id:
            content_hash = hashlib.md5(self.content.encode()).hexdigest()[:8]
            self.id = f"CL{self.clause_id or content_hash}"

        # 从 triplets 推导兼容字段（去重）
        if self.triplets:
            seen_comp, seen_act, seen_obj, seen_cond = set(), set(), set(), set()
            for t in self.triplets:
                if t.component: seen_comp.add(t.component)
                if t.action: seen_act.add(t.action)
                if t.obj: seen_obj.add(t.obj)
                if t.condition: seen_cond.add(t.condition)
            self.components = list(seen_comp)
            self.actions = list(seen_act)
            self.objects = list(seen_obj)
            self.conditions = list(seen_cond)

        # 补充metadata
        self.metadata.update({
            "chunk_type": "clause",
            "clause_id": self.clause_id,
            "clause_title": self.clause_title,
            "requirement_type": self.requirement_type.value,
            "systems": [s.system_type for s in self.applicable_systems],
            "formula_refs": [self.formula_id] if self.formula_id else [],
            "table_refs": self.table_refs,
            "cross_refs": [r.ref_id for r in self.cross_refs],
            "referenced_clauses": [
                {
                    "clause_id": r.clause_id,
                    "context": r.context,
                    "ref_type": r.ref_type,
                    "page_idx": r.page_idx,
                    "chunk_id": r.chunk_id,
                    "section_title": r.section_title
                }
                for r in self.referenced_clauses
            ],
            "referenced_standards": self.referenced_standards,
            "semantics_enriched": self.semantics_enriched,
            "parent_chapter": self.parent_chapter,
            # 语义三元组（核心）
            "triplets": [
                {
                    "component": t.component,
                    "action": t.action,
                    "obj": t.obj,
                    "condition": t.condition,
                    "requirement": t.requirement
                }
                for t in self.triplets
            ],
            # OCR 增强字段
            "is_term_definition": self.is_term_definition,
            "terms": self.terms,
            "formula_content": self.formula_content,
            "clause_items": [
                {
                    "item_number": ci.item_number,
                    "item_content": ci.item_content,
                    "components": ci.components,
                    "actions": ci.actions,
                    "conditions": ci.conditions,
                    "objects": ci.objects
                }
                for ci in self.clause_items
            ]
        })

        # 调用父类__post_init__
        super().__post_init__()

    @property
    def full_content(self) -> str:
        """获取完整条文内容（含款/项）"""
        parts = [self.content]
        parts.extend(self.paragraphs)
        # 添加 clause_items 内容
        for ci in self.clause_items:
            parts.append(f"{ci.item_number} {ci.item_content}")
        return "\n".join(parts)

    def add_semantic_enrichment(
        self,
        conditions: List[str] = None,
        actions: List[str] = None,
        requirement_type: RequirementType = None,
        triplets: List[Dict] = None
    ):
        """添加语义补充（LLM调用后）- 同时支持旧接口和平行列表"""
        if triplets:
            self.triplets = [
                SemanticTriplet(
                    component=t.get("component", ""),
                    action=t.get("action", ""),
                    obj=t.get("obj", ""),
                    condition=t.get("condition", ""),
                    requirement=t.get("requirement", "mandatory")
                )
                for t in triplets
                if t.get("component") or t.get("action") or t.get("obj")
            ]
        if conditions:
            self.conditions = conditions
        if actions:
            self.actions = actions
        if requirement_type:
            self.requirement_type = requirement_type
        self.semantics_enriched = True
        self.metadata["semantics_enriched"] = True


@dataclass
class ElementSegment(HierarchicalChunk):
    """
    Level-3: 要素级分块

    精确参数匹配、公式计算、术语解释、设备组件识别

    支持的要素类型：
    - TABLE_ROW: 表格行参数
    - FORMULA: 计算公式（含变量定义）
    - TERM: 术语定义
    - PARAMETER: 技术参数
    - COMPONENT: 设备、系统、材料组件
    - OBJECT: 操作对象（截面积、距离等）
    """
    element_type: ElementType = ElementType.TABLE_ROW
    source_id: str = ""              # 表格编号，如 "表3.2.9"，或条文编号
    row_index: Optional[int] = None  # 表格行索引
    key: str = ""                    # 参数名/术语名/组件名
    value: Any = None                # 参数值
    unit: str = ""                   # 单位
    condition: str = ""              # 适用条件

    # LLM 增强字段
    abbreviation: str = ""           # 缩写，如 "RCD"、"PVC"
    definition: str = ""             # 定义/描述
    keywords: List[str] = field(default_factory=list)  # 关联关键词
    variables: List[Dict] = field(default_factory=list)  # 公式变量定义

    # 关联信息
    parent_clause_id: str = ""       # 所属条文 ID
    parent_chapter_id: str = ""     # 所属章节 ID

    def __post_init__(self):
        self.level = ChunkLevel.LEVEL_3

        # 为 ElementSegment 设置有意义的 content（避免 to_episode_dict 空文本被跳过）
        if not self.content:
            parts = []
            if self.key:
                parts.append(self.key)
            if self.value is not None and str(self.value):
                parts.append(str(self.value))
            if self.unit:
                parts.append(self.unit)
            if not parts and self.definition:
                parts.append(self.definition[:100])
            self.content = " ".join(parts) if parts else self.element_type.value

        if not self.id:
            prefix = self.element_type.value[:3].upper()
            content_hash = hashlib.md5(self.content.encode()).hexdigest()[:8]
            self.id = f"EL{prefix}_{content_hash}"

        # 补充metadata
        self.metadata.update({
            "chunk_type": "element",
            "element_type": self.element_type.value,
            "source_id": self.source_id,
            "key": self.key,
            "value": str(self.value) if self.value is not None else "",
            "unit": self.unit,
            "condition": self.condition,
            "abbreviation": self.abbreviation,
            "definition": self.definition,
            "keywords": self.keywords,
            "variables": self.variables,
            "parent_clause_id": self.parent_clause_id,
            "parent_chapter_id": self.parent_chapter_id
        })

        # 调用父类__post_init__
        super().__post_init__()


@dataclass
class HierarchicalChunkResult:
    """
    多层级分块结果容器

    存储一次分块操作的所有结果
    """
    sections: List[SectionSegment] = field(default_factory=list)
    clauses: List[ClauseSegment] = field(default_factory=list)
    elements: List[ElementSegment] = field(default_factory=list)

    @property
    def total_chunks(self) -> int:
        return len(self.sections) + len(self.clauses) + len(self.elements)

    def get_all_chunks(self) -> List[HierarchicalChunk]:
        """获取所有层级的chunk"""
        return (
            [s for s in self.sections] +
            [c for c in self.clauses] +
            [e for e in self.elements]
        )

    def get_level2_clauses(self) -> List[ClauseSegment]:
        """获取所有条文级chunk（用于LLM语义补充）"""
        return [c for c in self.clauses if not c.semantics_enriched]

    def to_episode_list(self) -> List[Dict[str, Any]]:
        """转换为Episode节点列表（按 content 去重）"""
        seen = set()
        result = []
        for chunk in self.get_all_chunks():
            ep = chunk.to_episode_dict()
            key = (ep.get('text', ''), ep.get('metadata', {}).get('clause_id', ''))
            if key not in seen:
                seen.add(key)
                result.append(ep)
        return result

    # ========================================================================
    # 层级关联查询方法 (SOTA 图谱构建支持)
    # ========================================================================

    def get_clauses_in_chapter(self, chapter_number: int) -> List[ClauseSegment]:
        """获取指定章节中的所有条文"""
        return [
            c for c in self.clauses
            if c.metadata.get("parent_chapter") == chapter_number
        ]

    def get_elements_in_clause(self, clause_id: str) -> List[ElementSegment]:
        """获取指定条文中的所有要素"""
        return [
            e for e in self.elements
            if e.metadata.get("source_clause_id") == clause_id
        ]

    def get_elements_in_chapter(self, chapter_number: int) -> List[ElementSegment]:
        """获取指定章节中的所有要素"""
        return [
            e for e in self.elements
            if e.metadata.get("parent_chapter") == chapter_number
        ]

    def get_clause_by_id(self, clause_id: str) -> Optional[ClauseSegment]:
        """根据 ID 获取条文"""
        return next(
            (c for c in self.clauses if c.clause_id == clause_id),
            None
        )

    def get_section_by_number(self, chapter_number: int) -> Optional[SectionSegment]:
        """根据章节号获取章节"""
        return next(
            (s for s in self.sections if s.chapter_number == chapter_number),
            None
        )

    def get_elements_by_type(self, element_type: ElementType) -> List[ElementSegment]:
        """根据类型获取要素"""
        return [e for e in self.elements if e.element_type == element_type]

    def get_elements_by_keyword(self, keyword: str) -> List[ElementSegment]:
        """根据关键词搜索要素"""
        keyword_lower = keyword.lower()
        return [
            e for e in self.elements
            if keyword_lower in e.key.lower()
            or keyword_lower in e.keywords
            or (e.definition and keyword_lower in e.definition.lower())
        ]

    def build_hierarchy_tree(self) -> Dict[str, Any]:
        """
        构建层级树结构

        Returns:
            {
                "chapters": [
                    {
                        "section": SectionSegment,
                        "clauses": [ClauseSegment, ...],
                        "elements": [ElementSegment, ...]
                    }
                ]
            }
        """
        tree = {"chapters": []}

        for section in self.sections:
            chapter_num = section.chapter_number
            tree["chapters"].append({
                "section": section,
                "clauses": self.get_clauses_in_chapter(chapter_num),
                "elements": self.get_elements_in_chapter(chapter_num)
            })

        return tree
