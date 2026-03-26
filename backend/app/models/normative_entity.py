"""
NormativeEntity - 规范图谱统一实体模型

设计理念：
- 文本层：Episode（文本片段，作为知识来源证据）
- 语义层：Entity（从文本中提取的结构化实体）
- 关系层：Relation（实体之间的关系）

实体分类（参考 GB 50054 规范图谱模型）：
1. Section - 章节层级
2. Clause - 条文（最小可执行单元）
3. Term - 术语定义
4. Component - 实体对象（设备、系统、材料）
5. Condition - 前提条件（环境、场景）
6. Action - 规范要求的具体动作/措施
7. Requirement - 强制/推荐/禁止标签
8. Parameter - 表格/公式数值
9. Formula - 公式
10. Object - 操作对象（新增）
11. Behavior - 行为动作（新增）
12. Situation - 适用情景（新增）

边类型：
- defines: 术语定义
- has_condition: 条款的前提条件
- mandates / recommends / prohibits: 强制/推荐/禁止动作
- in_situation: 情景直接关联动作
- requires: 动作满足的具体参数
- operates_on: 操作行为作用于对象
- composed_of: 复合对象的组成
- relates_to: 一般关联
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
import hashlib


class EntityType(Enum):
    """实体类型枚举"""

    # 结构层
    SECTION = "Section"           # 章节
    CLAUSE = "Clause"             # 条文

    # 概念层
    TERM = "Term"                 # 术语
    FORMULA = "Formula"           # 公式
    PARAMETER = "Parameter"       # 参数值

    # 实体层
    COMPONENT = "Component"       # 组件/设备
    OBJECT = "Object"             # 操作对象
    MATERIAL = "Material"        # 材料

    # 行为层
    ACTION = "Action"             # 动作措施
    BEHAVIOR = "Behavior"         # 行为
    REQUIREMENT = "Requirement"   # 要求类型

    # 条件层
    CONDITION = "Condition"       # 条件
    SITUATION = "Situation"       # 情景


class RelationType(Enum):
    """关系类型枚举"""

    # 定义关系
    DEFINES = "defines"           # 术语定义

    # 层级关系
    PART_OF = "PART_OF"           # 属于（层级）
    HAS_PART = "HAS_PART"        # 包含部分

    # 条件关系
    HAS_CONDITION = "HAS_CONDITION"  # 有前提条件
    UNDER_CONDITION = "UNDER_CONDITION"  # 在条件下

    # 要求关系
    MANDATES = "MANDATES"        # 强制要求
    RECOMMENDS = "RECOMMENDS"    # 推荐
    PROHIBITS = "PROHIBITS"      # 禁止

    # 动作关系
    IN_SITUATION = "in_situation"  # 情景关联
    REQUIRES = "requires"        # 需要满足

    # 操作关系（新增）
    OPERATES_ON = "operates_on"  # 操作作用于
    COMPOSED_OF = "composed_of"  # 由...组成
    CONSISTS_OF = "consists_of"  # 由...构成

    # 一般关系
    RELATES_TO = "relates_to"    # 关联
    REFERENCES = "REFERENCES"    # 引用
    MENTIONS = "MENTIONS"        # 提及


@dataclass
class ExtractedEntity:
    """
    LLM 提取的实体

    Attributes:
        entity_type: 实体类型
        name: 实体名称
        description: 实体描述
        properties: 额外属性
    """
    entity_type: EntityType
    name: str
    description: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """获取 Neo4j 标签"""
        return f"Entity:{self.entity_type.value}"

    @property
    def name_lower(self) -> str:
        """获取小写名称（用于索引）"""
        return self.name.lower()


@dataclass
class ExtractedRelation:
    """
    LLM 提取的关系

    Attributes:
        relation_type: 关系类型
        source_entity: 源实体名称
        target_entity: 目标实体名称
        fact: 关系事实描述
        properties: 额外属性
    """
    relation_type: RelationType
    source_entity: str
    target_entity: str
    fact: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedClause:
    """
    LLM 解析的单条条文结果

    包含：
    - 条文元信息
    - 提取的实体列表
    - 提取的关系列表
    """
    clause_id: str                    # 条文编号，如 "3.2.1"
    clause_title: str = ""            # 条文标题
    clause_content: str = ""          # 条文原文
    level: int = 2                   # 层级（1=章节，2=条文，3=要素）

    # 语义要素
    requirement_type: str = ""        # mandatory/recommended/prohibited
    conditions: List[str] = field(default_factory=list)  # 前提条件
    actions: List[str] = field(default_factory=list)      # 动作措施

    # 结构化实体
    entities: List[ExtractedEntity] = field(default_factory=list)
    relations: List[ExtractedRelation] = field(default_factory=list)

    # 引用
    referenced_tables: List[str] = field(default_factory=list)  # 引用的表格
    referenced_formulas: List[str] = field(default_factory=list)  # 引用的公式
    referenced_clauses: List[str] = field(default_factory=list)  # 引用的其他条文

    # 来源信息
    source: str = ""                  # 来源文档
    page: int = 0                    # 页码
    bbox: List[float] = field(default_factory=list)  # 位置信息

    @property
    def full_content(self) -> str:
        """获取完整条文内容"""
        parts = [self.clause_content]
        return "\n".join(parts)


@dataclass
class ParsedDocument:
    """
    LLM 解析的完整文档结果

    包含：
    - 章节结构
    - 所有条文解析结果
    - 跨条文关系
    """
    document_title: str = ""
    document_id: str = ""
    sections: List[Dict[str, Any]] = field(default_factory=list)
    clauses: List[ParsedClause] = field(default_factory=list)

    # 全局实体和关系（跨条文）
    global_entities: List[ExtractedEntity] = field(default_factory=list)
    global_relations: List[ExtractedRelation] = field(default_factory=list)

    # 统计信息
    @property
    def total_entities(self) -> int:
        """总实体数"""
        return len(self.global_entities) + sum(len(c.entities) for c in self.clauses)

    @property
    def total_relations(self) -> int:
        """总关系数"""
        return len(self.global_relations) + sum(len(c.relations) for c in self.clauses)


@dataclass
class ParseOptions:
    """
    解析选项

    控制 LLM 解析的深度和范围
    """
    extract_terms: bool = True          # 提取术语定义
    extract_formulas: bool = True       # 提取公式
    extract_tables: bool = True         # 提取表格引用
    extract_components: bool = True     # 提取组件/设备
    extract_conditions: bool = True     # 提取条件
    extract_actions: bool = True        # 提取动作
    extract_objects: bool = True        # 提取操作对象
    extract_behaviors: bool = True      # 提取行为
    resolve_cross_refs: bool = True     # 解析交叉引用
    max_clauses_per_batch: int = 5      # 每批处理的条文数
