"""
Ontology generation service
Interface 1: Analyze text content and generate entity and relationship type definitions suitable for social simulation
"""

import json
import logging
import traceback
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger('mirofish.ontology_generator')


# System prompt for ontology generation
ONTOLOGY_SYSTEM_PROMPT = """You are a professional knowledge graph ontology design expert specializing in **Social Media Simulation and Digital Twins**.

Your task is to analyze text content and design a schema (Ontology) that will be used to extract entities and relationships. This schema is the foundation for creating AI Agents that simulate real-world behavior on social media.

**Important: You must output valid JSON format data, do not output anything else.**

## 🎯 Design Goal: Creating a "Simulatable" World

We are building a **Social Media Opinion Simulation System**. To make this effective, your ontology must focus on **ACTORS**—entities that can post, respond, influence, and be influenced.

### 1. Entity Guidelines
- **Real-World Subjects Only**: Every entity must be a subject capable of voicing opinions or being a stakeholder.
- **Hierarchical Thinking**: Design specific roles (e.g., `GraduateStudent`, `TechCEO`, `StateMedia`) instead of just broad categories.
- **NO Abstract Concepts**: Do not define "Public Opinion", "Emotion", or "Event" as entities. These are properties or contexts, not actors.

### 2. Relationship Guidelines
- **Structural Links**: Employment, affiliation, family (e.g., `WORKS_FOR`, `MEMBER_OF`).
- **Interaction/Attitude Links**: Support, opposition, regulation, reporting (e.g., `SUPPORTS`, `CRITICIZES`, `REGULATES`).
- **Information Flow**: Who influences whom, who provides information to whom.

### 3. Attribute Guidelines (For Persona Creation)
- Attributes should help define an AI Agent's "Personality" and "Social Identity".
- Recommended: `vulnerability`, `political_stance`, `social_influence`, `expertise_area`.
- **System Reserved Words (DO NOT USE as attribute names)**: `name`, `uuid`, `group_id`, `created_at`, `summary`.

## 📋 Output Format (JSON)

```json
{
    "entity_types": [
        {
            "name": "EntityTypeName (PascalCase)",
            "description": "Definition focusing on their role in social media (English, <100 chars)",
            "attributes": [
                {
                    "name": "attribute_name (snake_case)",
                    "type": "text",
                    "description": "How this affects their behavior or opinions"
                }
            ],
            "examples": ["Real names or specific roles"]
        }
    ],
    "edge_types": [
        {
            "name": "RELATIONSHIP_NAME (UPPER_SNAKE_CASE)",
            "description": "The nature of the connection (English, <100 chars)",
            "source_targets": [
                {"source": "SourceType", "target": "TargetType"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "Analysis of the core conflicts and key actors in the text"
}
```

## 📏 Strict Design Constraints

1. **Exact Quantity**: You must define **exactly 10 entity types**.
2. **Fallback Strategy**: The last 2 types in your list MUST be:
   - `Person`: Fallback for any natural person not fitting other categories.
   - `Organization`: Fallback for any group or institution not fitting other categories.
3. **Actor Focus**: At least 8 of your 10 entities should be types that can "own" a social media account.
4. **Relationship Quantity**: Define 8-10 meaningful relationship types.
"""


# System prompt for ontology generation (Engineering Standard Mode)
ENGINEERING_ONTOLOGY_PROMPT = """You are a professional knowledge graph ontology design expert specializing in **Industrial Engineering Standards and Technical Specifications**.

Your task is to analyze engineering documents (like Power Distribution Codes) and design a schema (Ontology) that captures the rigorous logic of technical requirements.

**Important: You must output valid JSON format data, do not output anything else.**

## 🎯 Design Goal: Structural Knowledge for Reasoning
We are building an **Engineering Design Assistant**. Every entity and relation must support logical deduction.

**Exhaustive Extraction**: Aim for maximum granularity. Do not group distinct technical concepts into broad categories. If the text distinguishes between "Circuit Breaker" and "Fuse", define them as separate types if they have different logic.

### 1. Entity Guidelines
- **Structural Nodes**: `Clause`, `Section`, `Chapter`.
- **Physical/Technical Entities**: Specific equipment types (e.g., `Switchgear`, `Transformer`), materials, specific components.
- **Logic & Constraints**: `Parameter` (e.g., Voltage), `Condition`, `Scenario`, `Threshold`.
- **Hierarchical Thinking**: Define entities at multiple levels.

### 2. Relationship Guidelines
- **Hierarchy (CRITICAL)**: Must include `SUB_CLAUSE_OF`, `PART_OF`, `MEMBER_OF`, `CHILD_OF`.
- **Logical Flow**: `REFERENCES`, `APPLIES_TO`, `CONSTRAINS`, `DETERMINES`.

## 📋 Output Format (JSON)
[Same JSON structure as standard mode]

## 📏 Strict Design Constraints
1. **Quantity**: Define **at least 15 and up to 30** specific entity types to ensure no knowledge loss.
2. **Standard Fallbacks**: Last 2 must be `Person` and `Organization`.
3. **Hierarchy Focus**: At least 3 relationship types must represent hierarchical containment.
"""

class OntologyGenerator:
    """
    Ontology generator
    Analyze text content and generate entity and relationship type definitions
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate ontology definition by first detecting the domain.
        """
        # Step 1: Smart Domain Detection via LLM
        domain = self._detect_domain(document_texts[:3], simulation_requirement)

        # Step 2: Select Prompt Strategy
        prompt_map = {
            "engineering": ENGINEERING_ONTOLOGY_PROMPT,
            "social": ONTOLOGY_SYSTEM_PROMPT
        }

        system_prompt = prompt_map.get(domain, ONTOLOGY_SYSTEM_PROMPT)
        logger.info(f"Detected domain: {domain}. Selecting strategy with high granularity...")

        # Step 3: Build user message
        user_message = self._build_user_message(
            document_texts,
            simulation_requirement,
            additional_context
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        # Call LLM
        try:
            logger.info(f"Calling LLM ({self.llm_client.model}) for exhaustive {domain} ontology generation...")
            result = self.llm_client.chat_json(
                messages=messages,
                temperature=0.3,
                max_tokens=4096
            )
        except Exception as e:
            logger.error(f"Ontology LLM call failed: {str(e)}\n{traceback.format_exc()}")
            raise RuntimeError(f"LLM analysis failed: {str(e)}. Please check your API key and model configuration.")

        # Validate and post-process
        result = self._validate_and_process(result)

        return result

    def _detect_domain(self, samples: List[str], requirement: str) -> str:
        """
        Classify the document domain using a fast LLM call.
        """
        classification_prompt = """You are a document classifier. Identify the primary domain of this project based on the requirement and text samples.

Choices:
- 'engineering': Technical standards, codes, equipment, parameters, physical logic.
- 'social': People, organizations, public opinion, interactions.

Respond with ONLY the choice name.
"""

        sample_text = "\n".join(samples)[:2000]
        user_msg = f"Requirement: {requirement}\n\nSample Text: {sample_text}"

        try:
            domain = self.llm_client.chat(
                messages=[
                    {"role": "system", "content": classification_prompt},
                    {"role": "user", "content": user_msg}
                ],
                temperature=0,
                max_tokens=10
            ).strip().lower()

            if "engineering" in domain: return "engineering"
            return "social"
        except:
            return "social"

    # Maximum text length for LLM (50,000 characters)
    MAX_TEXT_LENGTH_FOR_LLM = 50000

    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """Build user message with smart sampling for long texts"""

        # Combine texts
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)

        # Smart sampling if text exceeds limit
        if original_length > self.MAX_TEXT_LENGTH_FOR_LLM:
            logger.info(f"Text length {original_length} exceeds limit {self.MAX_TEXT_LENGTH_FOR_LLM}. Applying smart sampling...")

            # Divide budget: 30% head, 30% tail, 40% middle samples
            head_size = int(self.MAX_TEXT_LENGTH_FOR_LLM * 0.3)
            tail_size = int(self.MAX_TEXT_LENGTH_FOR_LLM * 0.3)
            middle_budget = self.MAX_TEXT_LENGTH_FOR_LLM - head_size - tail_size

            head_text = combined_text[:head_size]
            tail_text = combined_text[-tail_size:]

            # Sample from the middle
            middle_part = combined_text[head_size:-tail_size]
            num_samples = 10
            sample_size = middle_budget // num_samples
            step = len(middle_part) // num_samples

            middle_samples = []
            for i in range(num_samples):
                start = i * step
                middle_samples.append(middle_part[start : start + sample_size])

            sampled_text = head_text + "\n\n...[Middle Content Sampled]...\n\n" + \
                          "\n\n...".join(middle_samples) + \
                          "\n\n...[End Content]...\n\n" + tail_text

            combined_text = sampled_text
            logger.info(f"Smart sampling completed. Reduced {original_length} to ~{len(combined_text)} chars.")

        message = f"""## Simulation Requirements

{simulation_requirement}

## Document Content

{combined_text}
"""

        if additional_context:
            message += f"""
## Additional Explanation

{additional_context}
"""

        message += """
Based on the above content, design entity types and relationship types.
**Rules to follow**:
1. Define at least 15 specific entity types to capture details.
2. Last 2 must be Person and Organization.
3. Include relationship types for hierarchy (e.g., SUB_CLAUSE_OF, PART_OF).
4. All types must be real subjects or logical concepts, not metadata.
"""

        return message

    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and post-process result"""

        # Normalize relationship field names
        if "relation_types" in result and "edge_types" not in result:
            result["edge_types"] = result.pop("relation_types")

        # Ensure necessary fields exist
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""

        # System reserved words for attributes
        RESERVED_ATTRS = {"name", "uuid", "group_id", "created_at", "summary", "embedding", "attributes_json", "graph_id"}

        # Validate entity types
        for entity in result["entity_types"]:
            if "attributes" not in entity:
                entity["attributes"] = []

            # Filter out reserved words from attributes
            entity["attributes"] = [
                a for a in entity["attributes"]
                if (isinstance(a, dict) and a.get("name") not in RESERVED_ATTRS) or
                   (isinstance(a, str) and a not in RESERVED_ATTRS)
            ]

            if "examples" not in entity:
                entity["examples"] = []
            # Ensure description doesn't exceed 100 characters
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."

        # Validate relationship types
        for edge in result["edge_types"]:
            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []

            # Filter reserved words from edge attributes too
            edge["attributes"] = [
                a for a in edge["attributes"]
                if (isinstance(a, dict) and a.get("name") not in RESERVED_ATTRS) or
                   (isinstance(a, str) and a not in RESERVED_ATTRS)
            ]

            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."

        # NEW LIMIT: Up to 30 custom entity types, maximum 20 custom edge types
        MAX_ENTITY_TYPES = 30
        MAX_EDGE_TYPES = 20

        # Fallback type definitions
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }

        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }

        # Check if fallback types already exist
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names

        # Fallback types to add
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)

        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)

            # If adding would exceed max, need to remove some existing types
            if current_count + needed_slots > MAX_ENTITY_TYPES:
                # Calculate how many to remove
                to_remove = current_count + needed_slots - MAX_ENTITY_TYPES
                # Remove from end (keep more important specific types in front)
                result["entity_types"] = result["entity_types"][:-to_remove]

            # Add fallback types
            result["entity_types"].extend(fallbacks_to_add)

        # Final check to ensure limits not exceeded
        if len(result["entity_types"]) > MAX_ENTITY_TYPES:
            result["entity_types"] = result["entity_types"][:MAX_ENTITY_TYPES]

        if len(result["edge_types"]) > MAX_EDGE_TYPES:
            result["edge_types"] = result["edge_types"][:MAX_EDGE_TYPES]

        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        [DEPRECATED] Convert ontology definition to Zep-format Pydantic code.
        Not used in MiroFish-Offline (ontology stored as JSON in Neo4j).
        Kept for reference only.
        """
        code_lines = [
            '"""',
            'Custom entity type definitions',
            'Auto-generated by MiroFish for social opinion simulation',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== Entity Type Definitions ==============',
            '',
        ]

        # Generate entity types
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")

            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')

            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')

            code_lines.append('')
            code_lines.append('')

        code_lines.append('# ============== Relationship Type Definitions ==============')
        code_lines.append('')

        # Generate relationship types
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # Convert to PascalCase class name
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")

            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')

            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')

            code_lines.append('')
            code_lines.append('')

        # Generate type dictionaries
        code_lines.append('# ============== Type Configuration ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')

        # Generate source_targets mapping for edges
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')

        return '\n'.join(code_lines)

