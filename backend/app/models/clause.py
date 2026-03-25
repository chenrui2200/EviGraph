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
    """要素类型枚举"""
    TABLE_ROW = "table_row"
    FORMULA = "formula"
    TERM = "term"
    PARAMETER = "parameter"


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

    # LLM补充字段（语义理解）
    conditions: List[str] = field(default_factory=list)   # 前提条件
    actions: List[str] = field(default_factory=list)      # 规定动作
    semantics_enriched: bool = False

    def __post_init__(self):
        self.level = ChunkLevel.LEVEL_2
        if not self.id:
            content_hash = hashlib.md5(self.content.encode()).hexdigest()[:8]
            self.id = f"CL{self.clause_id or content_hash}"

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
            "semantics_enriched": self.semantics_enriched
        })

        # 调用父类__post_init__
        super().__post_init__()

    @property
    def full_content(self) -> str:
        """获取完整条文内容（含款/项）"""
        parts = [self.content]
        parts.extend(self.paragraphs)
        return "\n".join(parts)

    def add_semantic_enrichment(
        self,
        conditions: List[str] = None,
        actions: List[str] = None,
        requirement_type: RequirementType = None
    ):
        """添加语义补充（LLM调用后）"""
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

    精确参数匹配、公式计算、术语解释
    """
    element_type: ElementType = ElementType.TABLE_ROW
    source_id: str = ""              # 表格编号，如 "表3.2.9"
    row_index: Optional[int] = None  # 表格行索引
    key: str = ""                    # 参数名/术语名
    value: Any = None                # 参数值
    unit: str = ""                   # 单位
    condition: str = ""              # 适用条件

    def __post_init__(self):
        self.level = ChunkLevel.LEVEL_3
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
            "condition": self.condition
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
        """转换为Episode节点列表"""
        return [chunk.to_episode_dict() for chunk in self.get_all_chunks()]
