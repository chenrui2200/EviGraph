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
ENGINEERING_ONTOLOGY_PROMPT = """You are a professional knowledge graph ontology design expert specializing in **Industrial Engineering Standards, Technical Specifications, and Regulatory Compliance**.

Your task is to analyze engineering documents (like Power Distribution Codes, Safety Manuals, or Design Standards) and design a rigorous schema (Ontology) that captures the technical requirements with extreme precision.

**Important: You must output valid JSON format data, do not output anything else.**

## 🎯 Design Goal: Structural Knowledge for Reasoning and Verification
We are building an **AI Engineering Compliance Auditor**. Every entity and relation must support logical validation and parameter checking.

### 1. Entity Guidelines
- **Structural Nodes**: `Chapter`, `Section`, `Clause` (e.g., Clause 7.6.49), `SubItem`.
- **Physical/Technical Entities**: Specific equipment models, materials (e.g., `ConcretePavement`, `PorousDuct`), specific components.
- **Logic & Constraints**: `Requirement` (The core rule), `Parameter` (e.g., Slope, Depth, Thickness), `Threshold` (Limit values), `Condition` (When the rule applies).
- **Metric-Focused Attributes**: For parameters, always include `unit` (e.g., %, m, mm), `min_value`, `max_value`, and `operator` (>=, <, etc.).

### 2. Relationship Guidelines
- **Structural (CRITICAL)**: `SUB_CLAUSE_OF`, `PART_OF`, `CONTAINED_IN`.
- **Logical Flow**: `GOVERNS` (A clause governs a component), `SPECIFIES` (A clause specifies a parameter), `REFERENCES`, `REQUIRES`, `CONSTRAINS`.
- **Constraint Links**: `HAS_PARAMETER`, `APPLIES_WHEN` (Linking requirements to specific scenarios).

## 📋 Output Format (JSON)
[Same JSON structure as standard mode]

## 📏 Strict Design Constraints
1. **Granularity**: Do not group "Depth" and "Slope" into a single "Parameter". Define specific types if they have different validation logic.
2. **Quantity**: Define **at least 20 and up to 40** specific entity types to ensure maximum technical detail retention.
3. **Hierarchy**: At least 5 relationship types must represent hierarchical containment or logical derivation.
4. **Standard Fallbacks**: Last 2 must be `Person` and `Organization`.
"""

# System prompt for iterative ontology generation (Engineering Standard Mode)
ITERATIVE_ENGINEERING_PROMPT = """You are a professional knowledge graph ontology design expert specializing in **Industrial Engineering Standards and Regulatory Compliance**.

## 🎯 Task: Iterative Structural Ontology Discovery
Your task is to analyze a specific **Text Chunk** from a technical document and update/expand the current knowledge schema (Ontology).

### 1. Document Structure Awareness
Technical documents have rigorous hierarchy. You MUST identify:
- **Structural Links**: How chunks relate via writing order (e.g., `PRECEDES`, `FOLLOWS`, `DEFINES_SCOPE_FOR`).
- **Hierarchy**: `Clause` -> `SubItem` relationship.

### 2. Descriptive Association
Identify how technical nouns relate through description:
- `DESCRIBES`: Technical specs describing equipment.
- `CONSTRAINS`: Safety requirements constraining installation.
- `LOCATES_AT`: Physical location relationship mentioned in text.

### 3. Entity Precision
Capture technical entities with maximum detail (e.g., `PorousDuct`, `CatchBasin`, `ConcreteBed`).

## 📋 Input Context
- **Current Draft Ontology**: The entity and edge types we have discovered so far.
- **Current Text Chunk**: The new content to analyze.

## 📤 Output Requirement
Return ONLY valid JSON with two fields:
1. `new_entity_types`: List of entity type definitions discovered in THIS chunk.
2. `new_edge_types`: List of relationship type definitions discovered in THIS chunk.
3. `structural_context`: A brief description of this chunk's position in the document (e.g., "Part of Chapter 7 regarding conduit laying").

**Constraints**:
- Keep descriptions concise.
- Focus on technical nouns and logical verbs.
"""

# System prompt for iterative ontology generation (Engineering Standard Mode)
ITERATIVE_ENGINEERING_PROMPT = """You are a professional knowledge graph ontology design expert specializing in **Industrial Engineering Standards, Technical Specifications, and Regulatory Compliance**.

## 🎯 Task: High-Density Structural Ontology Discovery
You are analyzing a sequence of **Multiple Text Chunks** from a technical document. Your goal is to design a schema (Ontology) that captures not just entities, but the **Writing Logic** and **Descriptive Associations** inherent in engineering standards.

### 1. Identify Writing & Structural Logic
Technical documents follow a strict flow. Identify relationship types like:
- `PREREQUISITE_FOR`: One requirement must be met before another.
- `ELABORATES_ON`: A later chunk provides details for a term mentioned earlier.
- `GOVERNED_BY`: A component is governed by a specific safety clause.
- `LOGICAL_FLOW`: Sequential steps in a process.

### 2. Identify Descriptive & Functional Associations
Identify how technical nouns (Entities) relate beyond simple physical connection:
- `DEFINES`: A clause defines a technical term.
- `SPECIFIES_LIMIT`: Linking equipment to its technical parameters (slope, depth, etc.).
- `APPLIES_TO`: Linking a rule to a specific material or condition.

### 3. Entity Precision
Capture technical entities with maximum detail (e.g., `PorousDuct`, `CatchBasin`, `ConcreteBed`).

## 📤 Output Requirement
Return ONLY valid JSON with two fields:
1. `new_entity_types`: List of entity type definitions discovered in these chunks.
2. `new_edge_types`: List of relationship type definitions that capture the logical and descriptive links found.
3. `analysis_summary`: A summary of the technical logic and document structure found in this window.

**Constraint**: Focus on the logic between the lines.
"""

# System prompt for iterative ontology generation (High-Fidelity Engineering KG)
ITERATIVE_ENGINEERING_PROMPT = """You are a high-fidelity Knowledge Graph Architect specializing in **Technical Standard Digitization**.

## 🎯 Mission: Exhaustive Structural Discovery
Analyze the provided **Text Chunks** and design an UNRESTRICTED schema (Ontology) that perfectly maps the document's knowledge.

### 1. Technical Noun & Attribute Discovery (Entities)
- Identify EVERY technical noun, material, equipment, and abstract technical concept.
- If a noun has unique attributes (e.g., "Catch Basin" has "Drainage Capacity"), define a specific Entity Type for it.
- **NO LIMITS**: Do not merge distinct concepts. Precision is paramount.

### 2. Chunk as a Semantic Anchor
- Treat `DocumentChunk` as a first-class entity.
- Identify **Cross-References**: If text says "See Section X", "Refer to table Y", or "Consistent with rule Z", extract these as relationships between Chunks or between an Entity and a Chunk.

### 3. Deep Semantic Edge Discovery
Exhaustively identify how entities relate:
- **Structural**: `PART_OF`, `MEMBER_OF`, `COMPOSED_OF`.
- **Descriptive**: `DEFINES` (Clause defines a term), `DESCRIBES_SPECS` (Text describes equipment properties).
- **Logical/Regulatory**: `CONSTRAINS` (A rule restricts a parameter), `PREREQUISITE_FOR`, `GOVERNS`.
- **Positional**: `FOLLOWS` (Writing sequence), `LOCATED_IN`.

## 📤 Output Requirement
Return ONLY valid JSON:
{
  "new_entity_types": [
    {"name": "PreciseType", "description": "Strict technical definition", "attributes": [{"name": "attr", "type": "text"}]}
  ],
  "new_edge_types": [
    {"name": "DEEP_RELATION_NAME", "description": "Specific nature of linkage", "source_targets": [{"source": "TypeA", "target": "TypeB"}]}
  ],
  "logic_analysis": "Briefly explain the document flow and noun associations found here."
}
"""

class OntologyGenerator:
    """
    High-Fidelity Ontology Generator.
    Supports unrestricted technical entity discovery and structural anchor modeling.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None,
        chunks: Optional[List[Any]] = None
    ) -> Dict[str, Any]:
        """
        Generates a deep, structural ontology without artificial constraints.
        """
        # Step 1: Detect domain (Keep this to branch strategies if needed)
        sample = "\n".join(document_texts[:2])[:2000]
        domain = self._detect_domain([sample], simulation_requirement)

        if domain == "engineering" and chunks:
            logger.info("Initializing High-Fidelity Engineering Ontology Discovery...")
            return self._generate_high_fidelity_iterative(chunks, simulation_requirement)

        # Fallback for non-engineering
        return self._generate_standard(document_texts, simulation_requirement, additional_context)

    def _generate_high_fidelity_iterative(self, chunks: List[Any], requirement: str) -> Dict[str, Any]:
        """
        Unrestricted iterative discovery across the document.
        """
        import concurrent.futures
        final_ontology = {
            "entity_types": [
                {
                    "name": "DocumentChunk",
                    "description": "A physical segment of the technical document acting as a semantic anchor.",
                    "attributes": [{"name": "chunk_index", "type": "number"}, {"name": "source", "type": "text"}]
                }
            ],
            "edge_types": [
                {
                    "name": "CROSS_REFERENCES",
                    "description": "Explicit mention of another section, clause, or chunk.",
                    "source_targets": [{"source": "DocumentChunk", "target": "DocumentChunk"}]
                }
            ],
            "analysis_summary": "High-fidelity structural analysis."
        }

        # High-density window for relationship context
        window_size = 12
        # Scan up to 200 chunks for deep discovery in large documents
        max_discovery_chunks = 200
        discovery_subset = chunks[:max_discovery_chunks]

        windows = []
        for i in range(0, len(discovery_subset), window_size):
            window = discovery_subset[i : i + window_size]
            window_text = "\n\n---\n\n".join([f"[Chunk {i+j}] {c.text if hasattr(c, 'text') else str(c)}" for j, c in enumerate(window)])
            windows.append(window_text)

        logger.info(f"Launching {len(windows)} parallel discovery workers for high-fidelity modeling...")

        def _worker(w_text):
            messages = [
                {"role": "system", "content": ITERATIVE_ENGINEERING_PROMPT},
                {"role": "user", "content": f"## Context Requirement\n{requirement}\n\n## Text to Mine\n{w_text}"}
            ]
            try:
                return self.llm_client.chat_json(messages=messages, temperature=0.1)
            except Exception as e:
                logger.error(f"Discovery worker failed: {e}")
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(_worker, windows))

        for res in results:
            if res:
                self._merge_increment(final_ontology, res)

        # Use unconstrained validation for engineering
        return self._validate_and_process_unconstrained(final_ontology)

    def _merge_increment(self, base: Dict[str, Any], increment: Dict[str, Any]):
        """Smartly merge discoveries without keys mismatch."""
        if not isinstance(increment, dict): return

        # Handle Entities
        seen_entities = {e.get("name", "").lower() for e in base.get("entity_types", []) if isinstance(e, dict) and "name" in e}
        for et in increment.get("new_entity_types", []):
            if not isinstance(et, dict) or "name" not in et: continue
            name_lower = et["name"].lower()
            if name_lower and name_lower not in seen_entities:
                base["entity_types"].append(et)
                seen_entities.add(name_lower)

        # Handle Edges
        seen_edges = {e.get("name", "").upper() for e in base.get("edge_types", []) if isinstance(e, dict) and "name" in e}
        for edge in increment.get("new_edge_types", []):
            if not isinstance(edge, dict) or "name" not in edge: continue
            name_upper = edge["name"].upper()
            if name_upper and name_upper not in seen_edges:
                base["edge_types"].append(edge)
                seen_edges.add(name_upper)

    def _validate_and_process_unconstrained(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Post-processing without artificial caps for high-fidelity graphs."""
        if "relation_types" in result and "edge_types" not in result:
            result["edge_types"] = result.pop("relation_types")

        # Ensure description length etc.
        for e in result.get("entity_types", []):
            if len(e.get("description", "")) > 200: e["description"] = e["description"][:197] + "..."
        for e in result.get("edge_types", []):
            if len(e.get("description", "")) > 200: e["description"] = e["description"][:197] + "..."

        return result

    def _merge_increment(self, base: Dict[str, Any], increment: Dict[str, Any]):
        """Smartly merge new discoveries into the base ontology with error handling."""
        if not isinstance(increment, dict):
            return

        # Handle Entities
        seen_entities = {e.get("name", "").lower() for e in base.get("entity_types", []) if isinstance(e, dict) and "name" in e}
        for et in increment.get("new_entity_types", []):
            if not isinstance(et, dict) or "name" not in et:
                continue

            name_lower = et["name"].lower()
            if name_lower and name_lower not in seen_entities:
                base["entity_types"].append(et)
                seen_entities.add(name_lower)

        # Handle Edges
        seen_edges = {e.get("name", "").upper() for e in base.get("edge_types", []) if isinstance(e, dict) and "name" in e}
        for edge in increment.get("new_edge_types", []):
            if not isinstance(edge, dict) or "name" not in edge:
                continue

            name_upper = edge["name"].upper()
            if name_upper and name_upper not in seen_edges:
                base["edge_types"].append(edge)
                seen_edges.add(name_upper)

    def _generate_standard(self, document_texts: List[str], simulation_requirement: str, additional_context: Optional[str]) -> Dict[str, Any]:
        """Original one-shot generation logic."""
        system_prompt = ENGINEERING_ONTOLOGY_PROMPT # We'll keep the previous enhanced prompt here
        user_message = self._build_user_message(document_texts, simulation_requirement, additional_context)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        result = self.llm_client.chat_json(messages=messages, temperature=0.3)
        return self._validate_and_process(result)


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
2. Include relationship types for hierarchy (e.g., SUB_CLAUSE_OF, PART_OF).
3. All types must be real subjects or logical concepts, not metadata.
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

        # NEW LIMIT: Up to 40 custom entity types, maximum 25 custom edge types
        MAX_ENTITY_TYPES = 40
        MAX_EDGE_TYPES = 25

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

