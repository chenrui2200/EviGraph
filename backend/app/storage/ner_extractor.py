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
_BASE_INSTRUCTION = """You are a high-precision Knowledge Extraction Engine. Your task is to transform unstructured text into a structured graph based on the provided ontology.

## 📚 Context: The Ontology
You MUST strictly adhere to the following ontology. If an entity or relation doesn't fit, use fallback types or discard it.
{ontology_description}

## 🛠 Extraction Rules
1. **Canonical Naming**: Use official full names. Instead of "the equipment", use "Circuit Breaker (Model X)".
2. **Strict Relation Extraction**: Relationship `type` MUST be one of the names defined in the Relation Types list above.
3. **High-Quality "Fact" Sentences**: The `fact` field must be a clear, self-contained sentence explaining the EXACT nature of the connection.
4. **Resolution**: DO NOT use pronouns (he, she, they, it). Resolve them to full entity names.
"""

_SOCIAL_MODE_PROMPT = """
## 🎭 Domain Mode: Social Persona Reconstruction
- **Focus**: Individuals, organizations, and their interactions.
- **Actor Guideline**: Only extract entities that can be "Actors" (voice opinions, own accounts).
- **Negative Constraints**: DO NOT extract abstract concepts like "Safety" or "Risk".
"""

_ENGINEERING_MODE_PROMPT = """
## ⚙️ Domain Mode: Engineering & Logic Specification
- **Exhaustive Extraction**: Capture ALL technical entities mentioned. Do not group distinct components (e.g., "Transformer", "Switch") into a single "Equipment" type if the ontology allows for specificity.
- **Structural Integrity**: Ensure clause numbers (e.g., "3.1.1") are captured as part of names or attributes.
- **Hierarchy Detection**: If the text indicates a containment or parent-child relationship (e.g., "Section 3 includes Clause 3.1", "The panel contains a breaker"), EXPLICITLY extract this as a relationship (e.g., `SUB_CLAUSE_OF`, `PART_OF`).
- **Logical Chaining**: Extract relationships like "REFERENCES", "CONSTRAINS", or "REQUIRES" even if they involve abstract concepts.
- **Parameter Precision**: Capture units (A, V, m) and limit types (min/max) accurately.
"""

_OUTPUT_FORMAT = """
## 📥 Output Format
Return ONLY valid JSON:
{{
  "entities": [
    {{"name": "Full Name", "type": "OntologyType", "attributes": {{"key": "value"}}}}
  ],
  "relations": [
    {{"source": "Full Name", "target": "Full Name", "type": "ONTOLOGY_TYPE", "fact": "Detailed context."}}
  ]
}}"""

_USER_PROMPT = """Extract entities and relations from the following text:

{text}"""


class NERExtractor:
    """Extract entities and relations from text using local LLM."""

    def __init__(self, llm_client: Optional[LLMClient] = None, max_retries: int = 2):
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
                    max_tokens=4096,
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
