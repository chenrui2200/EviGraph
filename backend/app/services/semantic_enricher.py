"""
SemanticEnricher - LLM语义补充模块

从Neo4j读取条文后，调用LLM补充语义理解：
- 理解"条件 -> 动作"逻辑
- 识别MANDATES/PROHIBITS关系
- 识别适用条件（环境、场景）
- 生成实体和关系
"""

import json
import logging
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field

from .normative_ontology import NormativeOntology

logger = logging.getLogger('mirofish.semantic_enricher')


@dataclass
class SemanticEnrichment:
    """语义补充结果"""
    clause_id: str
    conditions: List[str]
    actions: List[str]
    prohibited_actions: List[str]
    recommended_actions: List[str]
    requirement_type: str  # mandatory, recommended, prohibited
    related_entities: List[Dict[str, str]]  # 识别的实体


@dataclass
class ElementExtraction:
    """要素提取结果"""
    clause_id: str
    tables: List[Dict[str, Any]] = field(default_factory=list)
    formulas: List[Dict[str, Any]] = field(default_factory=list)
    terms: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TableElement:
    """表格要素"""
    table_id: str  # 如 "表3.2.2"
    description: str  # 表格描述
    key_parameters: List[str] = field(default_factory=list)  # 关键参数列表
    unit: str = ""  # 单位（如有）


@dataclass
class FormulaElement:
    """公式要素"""
    formula_id: str  # 如 "式(3.2.14)" 或 "3.2.14"
    expression: str  # 公式表达式
    description: str = ""  # 公式描述
    variables: List[Dict[str, str]] = field(default_factory=list)  # 变量定义


@dataclass
class TermElement:
    """术语要素"""
    term_id: str  # 如 "2.0.1"
    term_name: str  # 术语名称
    definition: str  # 术语定义


class SemanticEnricher:
    """
    LLM语义补充器

    工作流程：
    1. 从Neo4j读取未语义化的条文（semantics_enriched = false）
    2. 批量调用LLM进行语义理解
    3. 返回语义补充结果供Neo4j更新
    """

    SYSTEM_PROMPT = """你是一个工程规范文档的语义分析专家。
你的任务是从工程规范的条文中提取结构化的语义信息。

## 输出要求
请分析每条条文，提取以下信息：

1. **条件（conditions）**：条文适用的前提条件、环境或场景
   - 例如："当接地故障电流小于一定值时"、"在潮湿环境中"

2. **动作（actions）**：条文要求执行的动作或措施
   - 例如："应设置剩余电流保护"、"必须采用TN系统"

3. **禁止动作（prohibited_actions）**：条文禁止的动作
   - 例如："严禁使用TN-C系统"、"不得取消保护装置"

4. **推荐动作（recommended_actions）**：条文推荐但不强制要求的动作
   - 例如："建议采用"、"宜设置"

5. **要求类型（requirement_type）**：
   - "mandatory": 必须/应（含严禁、不得等）
   - "recommended": 建议/宜
   - "prohibited": 禁止/严禁

## 实体识别
识别条文中的关键实体：
- Component（组件）：设备、系统、材料
- Condition（条件）：环境、场景、前提
- Action（动作）：具体措施

## 注意事项
- 保持JSON格式输出
- 如果条文没有特定条件，返回空列表
- 如果没有动作，返回空列表
- 使用中文输出
"""

    USER_PROMPT_TEMPLATE = """请分析以下工程规范条文，提取语义信息：

## 条文内容
{clause_content}

## 条文编号
{clause_id}

请以JSON格式输出分析结果：
```json
{{
    "clause_id": "{clause_id}",
    "conditions": ["条件1", "条件2"],
    "actions": ["动作1", "动作2"],
    "prohibited_actions": ["禁止动作1"],
    "recommended_actions": ["推荐动作1"],
    "requirement_type": "mandatory|recommended|prohibited",
    "entities": [
        {{"type": "Component", "name": "设备名称"}},
        {{"type": "Condition", "name": "环境条件"}},
        {{"type": "Action", "name": "具体动作"}}
    ]
}}
```
"""

    # ============================================================================
    # 要素提取 Prompt（新增）
    # ============================================================================
    ELEMENT_SYSTEM_PROMPT = """你是一个工程规范文档的结构化信息提取专家。
你的任务是从工程规范的条文中提取结构化的要素信息。

## 要素类型
你需要提取以下三类要素：

1. **表格要素（tables）**：条文引用或描述的表格
   - 提取表格编号（如"表3.2.2"、"表3.2.3"）
   - 提取表格用途描述
   - 提取表格中的关键参数名称

2. **公式要素（formulas）**：条文引用或描述的公式
   - 提取公式编号（如"式(3.2.14)"、"公式3.2.14"）
   - 提取公式表达式
   - 提取变量定义

3. **术语要素（terms）**：条文定义或解释的术语
   - 提取术语编号（如"2.0.1"、"2.0.2"）
   - 提取术语名称
   - 提取术语定义

## 输出格式
请保持JSON格式输出，如果没有某类要素，返回空列表。
使用中文输出。

## 注意事项
- 表格编号格式：如"表3.2.2"，注意"表"字
- 公式编号格式：如"式(3.2.14)"或"公式3.2.14"
- 术语编号格式：如"2.0.1"、"2.0.2"
- 只提取条文**直接包含或引用**的要素，不要推测不存在的内容
"""

    ELEMENT_USER_PROMPT_TEMPLATE = """请分析以下工程规范条文，提取其中的表格、公式和术语要素。

## 条文内容
{clause_content}

## 条文编号
{clause_id}

请以JSON格式输出分析结果：
```json
{{
    "clause_id": "{clause_id}",
    "tables": [
        {{
            "table_id": "表3.2.2",
            "description": "固定敷设的导体最小截面",
            "key_parameters": ["敷设方式", "导体最小截面", "铜导体", "铝导体"]
        }}
    ],
    "formulas": [
        {{
            "formula_id": "3.2.14",
            "expression": "S >= I*t/k",
            "description": "保护导体截面积计算公式",
            "variables": [
                {{"name": "S", "description": "导体截面积(mm2)"}},
                {{"name": "I", "description": "故障电流(A)"}}
            ]
        }}
    ],
    "terms": [
        {{
            "term_id": "2.0.1",
            "term_name": "预期接触电压",
            "definition": "人或动物尚未接触到可导电部分时，可能同时触及的可导电部分之间的电压"
        }}
    ]
}}
```

如果没有某类要素，请返回空列表：
```json
{{"tables": [], "formulas": [], "terms": []}}
```
"""

    def __init__(self, llm_client=None):
        """
        初始化语义补充器

        Args:
            llm_client: LLM客户端实例，如果为None则使用默认配置
        """
        self.llm_client = llm_client
        self.logger = logging.getLogger('mirofish.semantic_enricher')

    def enrich_clauses(
        self,
        clauses: List[Dict[str, Any]],
        progress_callback: Optional[Callable] = None
    ) -> List[SemanticEnrichment]:
        """
        批量语义补充

        Args:
            clauses: 条文列表，每个包含 id, content, clause_id
            progress_callback: 进度回调函数

        Returns:
            List[SemanticEnrichment]: 语义补充结果列表
        """
        results = []
        total = len(clauses)

        self.logger.info(f"开始语义补充，共 {total} 条条文")

        for idx, clause in enumerate(clauses):
            try:
                enrichment = self._enrich_single_clause(clause)
                if enrichment:
                    results.append(enrichment)
            except Exception as e:
                self.logger.warning(f"语义补充失败 clause_id={clause.get('clause_id', 'unknown')}: {e}")

            if progress_callback and (idx + 1) % 10 == 0:
                progress_callback((idx + 1) / total, f"已处理 {idx + 1}/{total} 条")

        self.logger.info(f"语义补充完成，成功 {len(results)}/{total} 条")
        return results

    def _enrich_single_clause(self, clause: Dict[str, Any]) -> Optional[SemanticEnrichment]:
        """
        对单条条文进行语义补充

        Args:
            clause: 条文数据，包含 id, content, clause_id

        Returns:
            SemanticEnrichment 或 None
        """
        clause_id = clause.get('clause_id', clause.get('id', ''))
        content = clause.get('content', clause.get('data', ''))

        if not content:
            return None

        user_prompt = self.USER_PROMPT_TEMPLATE.format(
            clause_content=content[:1000],  # 限制长度
            clause_id=clause_id
        )

        try:
            response = self._call_llm(user_prompt)
            if response:
                return self._parse_response(response, clause_id)
        except Exception as e:
            self.logger.error(f"LLM调用失败: {e}")

        return None

    def _call_llm(self, user_prompt: str) -> Optional[str]:
        """调用LLM"""
        if self.llm_client:
            response = self.llm_client.chat(
                system=self.SYSTEM_PROMPT,
                user=user_prompt
            )
            return response
        else:
            # 使用默认LLM客户端
            from ..utils.llm_client import LLMClient
            client = LLMClient()
            response = client.chat_json(
                system=self.SYSTEM_PROMPT,
                user=user_prompt
            )
            return json.dumps(response) if response else None

    def _parse_response(self, response: str, clause_id: str) -> Optional[SemanticEnrichment]:
        """解析LLM响应"""
        try:
            # 提取JSON
            json_match = None
            if '```json' in response:
                json_match = response.split('```json')[1].split('```')[0]
            elif '```' in response:
                json_match = response.split('```')[1].split('```')[0]
            else:
                # 尝试直接解析
                json_str = response.strip()
                if json_str.startswith('{'):
                    json_match = json_str

            if not json_match:
                return None

            data = json.loads(json_match.strip())

            return SemanticEnrichment(
                clause_id=clause_id,
                conditions=data.get('conditions', []),
                actions=data.get('actions', []),
                prohibited_actions=data.get('prohibited_actions', []),
                recommended_actions=data.get('recommended_actions', []),
                requirement_type=data.get('requirement_type', 'recommended'),
                related_entities=data.get('entities', [])
            )
        except json.JSONDecodeError as e:
            self.logger.warning(f"JSON解析失败 clause_id={clause_id}: {e}")
            return None

    def generate_neo4j_relations(
        self,
        enrichment: SemanticEnrichment,
        clause_uuid: str
    ) -> List[Dict[str, Any]]:
        """
        将语义补充结果转换为Neo4j关系

        Args:
            enrichment: 语义补充结果
            clause_uuid: 条文节点UUID

        Returns:
            关系列表，格式为 Neo4jStorage.add_relations 所需格式
        """
        relations = []

        # 动作关系
        for action in enrichment.actions:
            relations.append({
                "type": "MANDATES",
                "target": action,
                "fact": f"条款{enrichment.clause_id}规定应{action}",
                "target_type": "Action"
            })

        # 禁止动作
        for action in enrichment.prohibited_actions:
            relations.append({
                "type": "PROHIBITS",
                "target": action,
                "fact": f"条款{enrichment.clause_id}禁止{action}",
                "target_type": "Action"
            })

        # 推荐动作
        for action in enrichment.recommended_actions:
            relations.append({
                "type": "RECOMMENDS",
                "target": action,
                "fact": f"条款{enrichment.clause_id}推荐{action}",
                "target_type": "Action"
            })

        # 条件关系
        for condition in enrichment.conditions:
            relations.append({
                "type": "HAS_CONDITION",
                "target": condition,
                "fact": f"条款{enrichment.clause_id}的适用条件：{condition}",
                "target_type": "Condition"
            })

        # 实体关系
        for entity in enrichment.related_entities:
            entity_type = entity.get('type', 'Component')
            entity_name = entity.get('name', '')
            if entity_name:
                relations.append({
                    "type": "APPLIES_TO",
                    "target": entity_name,
                    "fact": f"条款{enrichment.clause_id}涉及{entity_name}",
                    "target_type": entity_type
                })

        return relations

    # ============================================================================
    # 要素提取方法（新增）
    # ============================================================================

    def extract_elements(
        self,
        clauses: List[Dict[str, Any]],
        progress_callback: Optional[Callable] = None
    ) -> List[ElementExtraction]:
        """
        批量提取要素（表格、公式、术语）

        Args:
            clauses: 条文列表，每个包含 id, content, clause_id
            progress_callback: 进度回调函数

        Returns:
            List[ElementExtraction]: 要素提取结果列表
        """
        results = []
        total = len(clauses)

        self.logger.info(f"开始要素提取，共 {total} 条条文")

        for idx, clause in enumerate(clauses):
            try:
                extraction = self._extract_single_clause_elements(clause)
                if extraction:
                    results.append(extraction)
            except Exception as e:
                self.logger.warning(f"要素提取失败 clause_id={clause.get('clause_id', 'unknown')}: {e}")

            if progress_callback and (idx + 1) % 10 == 0:
                progress_callback((idx + 1) / total, f"已提取 {idx + 1}/{total} 条")

        self.logger.info(f"要素提取完成，成功 {len(results)}/{total} 条")
        return results

    def _extract_single_clause_elements(self, clause: Dict[str, Any]) -> Optional[ElementExtraction]:
        """
        对单条条文提取要素

        Args:
            clause: 条文数据，包含 id, content, clause_id

        Returns:
            ElementExtraction 或 None
        """
        clause_id = clause.get('clause_id', clause.get('id', ''))
        content = clause.get('content', clause.get('data', ''))

        if not content:
            return None

        user_prompt = self.ELEMENT_USER_PROMPT_TEMPLATE.format(
            clause_content=content[:1500],  # 限制长度
            clause_id=clause_id
        )

        try:
            response = self._call_llm_for_elements(user_prompt)
            if response:
                return self._parse_element_response(response, clause_id)
        except Exception as e:
            self.logger.error(f"LLM要素提取调用失败: {e}")

        return None

    def _call_llm_for_elements(self, user_prompt: str) -> Optional[str]:
        """调用LLM提取要素"""
        if self.llm_client:
            response = self.llm_client.chat(
                system=self.ELEMENT_SYSTEM_PROMPT,
                user=user_prompt
            )
            return response
        else:
            from ..utils.llm_client import LLMClient
            client = LLMClient()
            response = client.chat_json(
                system=self.ELEMENT_SYSTEM_PROMPT,
                user=user_prompt
            )
            return json.dumps(response) if response else None

    def _parse_element_response(self, response: str, clause_id: str) -> Optional[ElementExtraction]:
        """解析LLM要素提取响应"""
        try:
            json_match = None
            if '```json' in response:
                json_match = response.split('```json')[1].split('```')[0]
            elif '```' in response:
                json_match = response.split('```')[1].split('```')[0]
            else:
                json_str = response.strip()
                if json_str.startswith('{'):
                    json_match = json_str

            if not json_match:
                return None

            data = json.loads(json_match.strip())

            return ElementExtraction(
                clause_id=clause_id,
                tables=data.get('tables', []),
                formulas=data.get('formulas', []),
                terms=data.get('terms', [])
            )
        except json.JSONDecodeError as e:
            self.logger.warning(f"JSON解析失败 clause_id={clause_id}: {e}")
            return None

    def elements_to_episodes(
        self,
        extraction: ElementExtraction,
        source_metadata: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        将要素提取结果转换为Episode数据

        Args:
            extraction: 要素提取结果
            source_metadata: 来源元数据（包含page, bbox等）

        Returns:
            Episode数据列表，可直接存储到Neo4j
        """
        episodes = []

        # 表格要素
        for table in extraction.tables:
            episodes.append({
                "text": f"{table.get('table_id', '')}: {table.get('description', '')}",
                "metadata": {
                    "chunk_type": "element",
                    "element_type": "table",
                    "source_id": table.get('table_id', ''),
                    "key": table.get('description', ''),
                    "key_parameters": table.get('key_parameters', []),
                    "level": 3,
                    **source_metadata
                }
            })

        # 公式要素
        for formula in extraction.formulas:
            episodes.append({
                "text": f"{formula.get('formula_id', '')}: {formula.get('expression', '')}",
                "metadata": {
                    "chunk_type": "element",
                    "element_type": "formula",
                    "source_id": formula.get('formula_id', ''),
                    "key": formula.get('description', ''),
                    "expression": formula.get('expression', ''),
                    "variables": formula.get('variables', []),
                    "level": 3,
                    **source_metadata
                }
            })

        # 术语要素
        for term in extraction.terms:
            episodes.append({
                "text": f"{term.get('term_id', '')} {term.get('term_name', '')}: {term.get('definition', '')}",
                "metadata": {
                    "chunk_type": "element",
                    "element_type": "term",
                    "source_id": term.get('term_id', ''),
                    "key": term.get('term_name', ''),
                    "value": term.get('definition', ''),
                    "level": 3,
                    **source_metadata
                }
            })

        return episodes


# ============================================================================
# 便捷函数
# ============================================================================

def enrich_clauses(
    clauses: List[Dict[str, Any]],
    progress_callback: Optional[Callable] = None
) -> List[SemanticEnrichment]:
    """
    便捷函数：批量语义补充

    Args:
        clauses: 条文列表
        progress_callback: 进度回调

    Returns:
        List[SemanticEnrichment]
    """
    enricher = SemanticEnricher()
    return enricher.enrich_clauses(clauses, progress_callback)


def extract_elements(
    clauses: List[Dict[str, Any]],
    progress_callback: Optional[Callable] = None
) -> List[ElementExtraction]:
    """
    便捷函数：批量提取要素（表格、公式、术语）

    Args:
        clauses: 条文列表
        progress_callback: 进度回调

    Returns:
        List[ElementExtraction]
    """
    enricher = SemanticEnricher()
    return enricher.extract_elements(clauses, progress_callback)
