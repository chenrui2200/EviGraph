"""
Normative Ontology - 固定本体定义

工程规范文档（如GB 50054）的标准本体定义：
- 8类实体类型
- 10种关系类型
- 替代动态本体生成
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class EntityType:
    """实体类型定义"""
    name: str
    description: str
    properties: List[str]


@dataclass
class EdgeType:
    """关系类型定义"""
    name: str
    source: str
    target: str
    description: str


# ============================================================================
# 工程规范文档的实体类型定义（8类）
# ============================================================================
ENTITY_TYPES: List[Dict[str, str]] = [
    {
        "name": "Section",
        "description": "章节层级 - 用于宏观主题检索和知识图谱导航节点",
        "properties": ["chapter_number", "section_number", "title", "level"]
    },
    {
        "name": "Clause",
        "description": "条款 - 带编号的最小执行单元，是90%技术问答的主要检索对象",
        "properties": ["clause_id", "clause_title", "requirement_type", "content"]
    },
    {
        "name": "Term",
        "description": "术语定义 - 规范中的专业术语解释",
        "properties": ["term_name", "definition", "source"]
    },
    {
        "name": "Component",
        "description": "组件/实体对象 - 设备、系统、材料等实体",
        "properties": ["component_name", "category", "specification"]
    },
    {
        "name": "Condition",
        "description": "前提条件 - 环境、场景、适用前提",
        "properties": ["condition_type", "description", "threshold"]
    },
    {
        "name": "Action",
        "description": "规定动作 - 规范要求的具体动作/措施",
        "properties": ["action_type", "description", "mandatory"]
    },
    {
        "name": "Requirement",
        "description": "要求标签 - 强制/推荐/禁止标记",
        "properties": ["requirement_type", "description", "enforcement"]
    },
    {
        "name": "Parameter",
        "description": "参数值 - 表格/公式中的具体数值",
        "properties": ["parameter_name", "value", "unit", "condition", "source"]
    },
    {
        "name": "Formula",
        "description": "计算公式 - 工程计算公式",
        "properties": ["formula_id", "expression", "variables", "applicability"]
    }
]

# ============================================================================
# 关系类型定义（10种）
# ============================================================================
EDGE_TYPES: List[Dict[str, str]] = [
    {
        "name": "PART_OF",
        "source": "Clause",
        "target": "Section",
        "description": "条款属于章节的层级归属关系"
    },
    {
        "name": "SUB_CLAUSE_OF",
        "source": "Clause",
        "target": "Clause",
        "description": "条款的子款关系（如5.2.8.1从属于5.2.8）"
    },
    {
        "name": "HAS_CONDITION",
        "source": "Clause",
        "target": "Condition",
        "description": "条款的前提条件"
    },
    {
        "name": "MANDATES",
        "source": "Clause",
        "target": "Action",
        "description": "条款强制要求的动作"
    },
    {
        "name": "PROHIBITS",
        "source": "Clause",
        "target": "Action",
        "description": "条款禁止的动作"
    },
    {
        "name": "RECOMMENDS",
        "source": "Clause",
        "target": "Action",
        "description": "条款推荐的动作"
    },
    {
        "name": "CROSS_REFERENCE",
        "source": "Clause",
        "target": "Clause",
        "description": "条文间的交叉引用"
    },
    {
        "name": "APPLIES_TO",
        "source": "Clause",
        "target": "Component",
        "description": "条款适用的系统/场景/设备"
    },
    {
        "name": "DEFINES",
        "source": "Term",
        "target": "Clause",
        "description": "术语定义来源"
    },
    {
        "name": "HAS_VALUE",
        "source": "Parameter",
        "target": "Formula",
        "description": "参数与公式的取值关系"
    },
    {
        "name": "REFERENCES",
        "source": "Clause",
        "target": "Formula",
        "description": "条款引用公式"
    }
]

# ============================================================================
# 完整本体定义（用于存储和LLM抽取）
# ============================================================================
NORMATIVE_ONTOLOGY: Dict[str, Any] = {
    "name": "NormativeEngineeringOntology",
    "version": "1.0",
    "description": "工程规范文档的标准本体定义（替代动态生成）",
    "entity_types": ENTITY_TYPES,
    "edge_types": EDGE_TYPES
}


class NormativeOntology:
    """本体查询和验证工具类"""

    @staticmethod
    def get_ontology() -> Dict[str, Any]:
        """获取完整本体定义"""
        return NORMATIVE_ONTOLOGY

    @staticmethod
    def get_entity_types() -> List[Dict[str, str]]:
        """获取实体类型列表"""
        return ENTITY_TYPES

    @staticmethod
    def get_edge_types() -> List[Dict[str, str]]:
        """获取关系类型列表"""
        return EDGE_TYPES

    @staticmethod
    def get_entity_type_names() -> List[str]:
        """获取所有实体类型名称"""
        return [e["name"] for e in ENTITY_TYPES]

    @staticmethod
    def get_edge_type_names() -> List[str]:
        """获取所有关系类型名称"""
        return [e["name"] for e in EDGE_TYPES]

    @staticmethod
    def validate_entity_type(entity_type: str) -> bool:
        """验证实体类型是否合法"""
        return entity_type in NormativeOntology.get_entity_type_names()

    @staticmethod
    def validate_edge_type(edge_type: str) -> bool:
        """验证关系类型是否合法"""
        return edge_type in NormativeOntology.get_edge_type_names()

    @staticmethod
    def get_edge_sources_targets(edge_type: str) -> Optional[tuple]:
        """获取关系类型的源和目标类型"""
        for edge in EDGE_TYPES:
            if edge["name"] == edge_type:
                return (edge["source"], edge["target"])
        return None

    @staticmethod
    def get_prompt_context() -> str:
        """
        生成用于LLM抽取的本体上下文提示词

        返回格式化的本体信息，用于NER/RE抽取提示
        """
        entity_lines = []
        for et in ENTITY_TYPES:
            entity_lines.append(f"- {et['name']}: {et['description']}")

        edge_lines = []
        for ed in EDGE_TYPES:
            edge_lines.append(f"- {ed['name']}: {ed['description']} ({ed['source']} -> {ed['target']})")

        return f"""
## 工程规范本体定义

### 实体类型（{len(ENTITY_TYPES)}类）：
{chr(10).join(entity_lines)}

### 关系类型（{len(EDGE_TYPES)}种）：
{chr(10).join(edge_lines)}
"""

    @staticmethod
    def get_ontology_json() -> str:
        """获取本体JSON字符串（用于存储）"""
        import json
        return json.dumps(NORMATIVE_ONTOLOGY, ensure_ascii=False, indent=2)
