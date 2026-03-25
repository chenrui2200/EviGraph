"""
NER/RE Extractor — entity and relation extraction via local LLM

Replaces Zep Cloud's built-in NER/RE pipeline.
Uses LLMClient.chat_json() with a structured prompt to extract
entities and relations from text chunks, guided by the graph's ontology.
"""

import logging
import traceback
from typing import Dict, Any, List, Optional

from ..utils.llm_client import LLMClient

logger = logging.getLogger('mirofish.ner_extractor')

# Core system prompt components
_BASE_INSTRUCTION = """你是一个高精度的知识抽取引擎。你的任务是根据提供的本体（Ontology），将非结构化文本转换为结构化图谱。

## 🌐 语言要求
**必须使用简体中文**：所有提取的实体名称、描述、关系事实（fact）以及属性值，必须统一使用简体中文。即使原文包含英文术语，也请提供中文翻译或在括号内保留英文。

## 📚 上下文：本体定义
你必须严格遵守以下本体。如果某个实体或关系不符合定义的类型，请使用备选类型或将其丢弃。
{ontology_description}

## 🛠 提取规则
1. **规范命名**：使用官方全称。避免使用“该设备”，应使用“断路器（型号 X）”。
2. **严格的关系提取**：关系 `type` 必须是上述关系类型列表中的名称之一。
3. **高质量的“事实”描述**：`fact` 字段必须是一个清晰、自洽的中文句子，解释连接的具体性质。
4. **指代消解**：不要使用代词（他、她、它们、它）。请将它们还原为完整的实体名称。
"""

_SOCIAL_MODE_PROMPT = """
## 🎭 领域模式：社交画像重构
- **核心关注**：个人、组织及其互动。
- **参与者准则**：仅提取可以作为“行动者”的实体（能发表意见、拥有账户）。
- **负向约束**：不要提取像“安全”或“风险”这样的抽象概念。
"""

_ENGINEERING_MODE_PROMPT = """
## ⚙️ 领域模式：工程与规范逻辑提取
你的目标是提取技术知识，重点关注“规定性逻辑”（前提条件 -> 规定动作）。

### 1. 强制性核心实体
- **Clause (条款)**：用于带编号的项目（如“条款 3.1.1”）。名称格式必须为“条款 [编号]: [标题]”。
- **Condition (前提条件)**：前提、环境因素或系统类型（如“在 TN-C 系统中”、“室内敷设”）。
- **Action (规定动作)**：强制性措施或物理要求（如“安装隔离开关”、“距离 >= 2.5m”）。
- **Component (组件/实体)**：设备、系统或材料（如“隔离装置”、“多孔导管”）。
- **Parameter (参数)**：来自表格或文本的数值（如“系数 k=1.2”）。

### 2. 强制性核心关系
- **HAS_CONDITION**：将“条款”或“动作”链接到其前提“条件”。（这对“在什么情况下”的查询至关重要）。
- **MANDATES / PROHIBITS**：将“条款”链接到其规定的强制或禁止“动作”。
- **IN_SITUATION**：条件与动作之间的直接链接，用于快速推理。
- **APPLIES_TO**：将“条款/动作/条件”链接到特定的“组件”。

### 3. 提取精度
- **规范命名**：对于条款，捕获精确的编号。
- **逻辑重于文本**：如果句子说“当发生 X 时，必须执行 Y”，请提取：
  - 实体(type=Condition, name="发生 X")
  - 实体(type=Action, name="执行 Y")
  - 关系(source="发生 X", target="执行 Y", type="IN_SITUATION")
"""


_OUTPUT_FORMAT = """
## 📥 输出格式
仅返回有效的 JSON 格式：
{{
  "entities": [
    {{"name": "完整名称", "type": "本体类型", "description": "基于文本的简明中文摘要", "attributes": {{"键": "值"}}}}
  ],
  "relations": [
    {{"source": "完整名称", "target": "完整名称", "type": "关系类型", "fact": "详细的中文上下文描述。"}}
  ]
}}"""

_USER_PROMPT = """Extract entities and relations from the following text:

{text}"""


class NERExtractor:
    """Extract entities and relations from text using local LLM."""

    def __init__(self, llm_client: Optional[LLMClient] = None, max_retries: int = 3):
        self.llm = llm_client or LLMClient()
        self.max_retries = max_retries

    def extract(self, text: str, ontology: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract entities and relations from text, guided by ontology.

        Args:
            text: Input text chunk
            ontology: Dict with 'entity_types' and 'relation_types' from graph

        Returns:
            Dict with 'entities' and 'relations' lists:
            {
                "entities": [{"name": str, "type": str, "attributes": dict}],
                "relations": [{"source": str, "target": str, "type": str, "fact": str}]
            }
        """
        if not text or not text.strip():
            return {"entities": [], "relations": []}

        # Detect mode based on ontology types
        entity_names = [str(et.get('name') if isinstance(et, dict) else et).lower()
                       for et in ontology.get('entity_types', [])]
        is_engineering = any(name in entity_names for name in ['clause', 'parameter', 'equipment', 'requirement', 'component'])

        ontology_desc = self._format_ontology(ontology)

        # Assemble system prompt
        domain_prompt = _ENGINEERING_MODE_PROMPT if is_engineering else _SOCIAL_MODE_PROMPT
        system_msg = _BASE_INSTRUCTION.format(ontology_description=ontology_desc) + domain_prompt + _OUTPUT_FORMAT

        user_msg = _USER_PROMPT.format(text=text.strip())

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                logger.info(f"Calling LLM for {'Engineering' if is_engineering else 'Social'} NER extraction (attempt {attempt + 1})...")
                result = self.llm.chat_json(
                    messages=messages,
                    temperature=0.1,  # Low temp for extraction precision
                    max_tokens=4096 * 3,
                )
                logger.debug(f"LLM raw response for extraction: {result}")
                cleaned_result = self._validate_and_clean(result, ontology)
                logger.info(f"Extracted {len(cleaned_result.get('entities', []))} entities and {len(cleaned_result.get('relations', []))} relations.")
                return cleaned_result

            except ValueError as e:
                last_error = e
                logger.warning(
                    f"NER extraction failed (attempt {attempt + 1}): invalid JSON — {e}"
                )
            except Exception as e:
                last_error = e
                logger.error(f"NER extraction error: {str(e)}\n{traceback.format_exc()}")
                if attempt >= self.max_retries:
                    break

        logger.error(
            f"NER extraction failed after {self.max_retries + 1} attempts: {last_error}"
        )
        return {"entities": [], "relations": []}

    def _format_ontology(self, ontology: Optional[Dict[str, Any]]) -> str:
        """Format ontology dict into readable text for the LLM prompt."""
        if not ontology:
            return "No specific ontology defined. Extract all entities and relations you find."

        parts = []

        entity_types = ontology.get("entity_types", [])
        if entity_types:
            parts.append("Entity Types:")
            for et in entity_types:
                if isinstance(et, dict):
                    name = et.get("name", str(et))
                    desc = et.get("description", "")
                    attrs = et.get("attributes", [])
                    line = f"  - {name}"
                    if desc:
                        line += f": {desc}"
                    if attrs:
                        attr_names = [a.get("name", str(a)) if isinstance(a, dict) else str(a) for a in attrs]
                        line += f" (attributes: {', '.join(attr_names)})"
                    parts.append(line)
                else:
                    parts.append(f"  - {et}")

        relation_types = ontology.get("relation_types", ontology.get("edge_types", []))
        if relation_types:
            parts.append("\nRelation Types:")
            for rt in relation_types:
                if isinstance(rt, dict):
                    name = rt.get("name", str(rt))
                    desc = rt.get("description", "")
                    source_targets = rt.get("source_targets", [])
                    line = f"  - {name}"
                    if desc:
                        line += f": {desc}"
                    if source_targets:
                        st_strs = [f"{st.get('source', '?')} → {st.get('target', '?')}" for st in source_targets]
                        line += f" ({', '.join(st_strs)})"
                    parts.append(line)
                else:
                    parts.append(f"  - {rt}")

        if not parts:
            parts.append("No specific ontology defined. Extract all entities and relations you find.")

        return "\n".join(parts)

    def _validate_and_clean(
        self, result: Optional[Dict[str, Any]], ontology: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Validate and normalize LLM output."""
        if not result:
            return {"entities": [], "relations": []}

        ontology = ontology or {}
        entities = result.get("entities", [])
        relations = result.get("relations", [])

        # Get valid type names from ontology
        valid_entity_types = set()
        for et in ontology.get("entity_types", []):
            if isinstance(et, dict):
                valid_entity_types.add(et.get("name", "").strip())
            else:
                valid_entity_types.add(str(et).strip())

        valid_relation_types = set()
        for rt in ontology.get("relation_types", ontology.get("edge_types", [])):
            if isinstance(rt, dict):
                valid_relation_types.add(rt.get("name", "").strip())
            else:
                valid_relation_types.add(str(rt).strip())

        # Clean entities
        cleaned_entities = []
        seen_names = set()
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            name = str(entity.get("name", "")).strip()
            etype = str(entity.get("type", "Entity")).strip()
            if not name:
                continue

            # Deduplicate by normalized name
            name_lower = name.lower()
            if name_lower in seen_names:
                continue
            seen_names.add(name_lower)

            # If ontology has types, warn but keep entities with unknown types
            if valid_entity_types and etype not in valid_entity_types:
                logger.debug(f"Entity '{name}' has type '{etype}' not in ontology, keeping anyway")

            cleaned_entities.append({
                "name": name,
                "type": etype,
                "description": str(entity.get("description", "")).strip(),
                "attributes": entity.get("attributes", {}),
            })

        # Clean relations
        cleaned_relations = []
        entity_names_lower = {e["name"].lower() for e in cleaned_entities}
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            source = str(relation.get("source", "")).strip()
            target = str(relation.get("target", "")).strip()
            rtype = str(relation.get("type", "RELATED_TO")).strip()
            fact = str(relation.get("fact", "")).strip()

            if not source or not target:
                continue

            # Ensure source and target entities exist
            # (they might not if LLM hallucinated a relation without the entity)
            if source.lower() not in entity_names_lower:
                cleaned_entities.append({
                    "name": source,
                    "type": "Entity",
                    "attributes": {},
                })
                entity_names_lower.add(source.lower())

            if target.lower() not in entity_names_lower:
                cleaned_entities.append({
                    "name": target,
                    "type": "Entity",
                    "attributes": {},
                })
                entity_names_lower.add(target.lower())

            cleaned_relations.append({
                "source": source,
                "target": target,
                "type": rtype,
                "fact": fact or f"{source} {rtype} {target}",
            })

        return {
            "entities": cleaned_entities,
            "relations": cleaned_relations,
        }
