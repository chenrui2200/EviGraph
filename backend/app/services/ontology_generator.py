"""
Ontology generation service
Interface 1: Analyze text content and generate entity and relationship type definitions suitable for social simulation
"""

import json
import logging
import traceback
from typing import Dict, Any, List, Optional, Callable
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger('mirofish.ontology_generator')


# System prompt for ontology generation
ONTOLOGY_SYSTEM_PROMPT = """你是一位专业的知识图谱本体设计专家，专注于**社交媒体仿真与数字孪生**。

你的任务是分析文本内容并设计一个模式（Ontology），该模式将用于提取实体和关系。这个模式是创建模拟社交媒体现实行为的 AI Agent 的基础。

**重要：你必须输出有效的 JSON 格式数据，不要输出任何其他内容。**

## 🌐 语言要求
**必须使用简体中文**：所有定义的实体类型名称（PascalCase 英文除外）、描述、属性名称及描述、关系类型描述，必须统一使用简体中文。

## 🎯 设计目标：创建一个“可仿真”的世界

我们正在构建一个**社交媒体舆情仿真系统**。为了使其有效，你的本体必须关注**行动者（ACTORS）**——即能够发布、响应、影响和被影响的实体。

### 1. 实体准则
- **仅限现实世界主体**：每个实体必须是能够表达观点或作为利益相关者的主体。
- **层次化思维**：设计具体的角色（例如 `研究生`、`技术CEO`、`官方媒体`）而不仅仅是宽泛的类别。
- **禁止抽象概念**：不要将“舆论”、“情绪”或“事件”定义为实体。这些是属性或上下文，而不是行动者。

### 2. 关系准则
- **结构化链接**：雇佣、从属、家庭（例如 `WORKS_FOR`、`MEMBER_OF`）。
- **互动/态度链接**：支持、反对、监管、报道（例如 `SUPPORTS`、`CRITICIZES`、`REGULATES`）。
- **信息流**：谁影响谁，谁向谁提供信息。

### 3. 属性准则（用于画像创建）
- 属性应有助于定义 AI Agent 的“性格”和“社会身份”。
- 推荐：`脆弱性`、`政治立场`、`社会影响力`、`专业领域`。
- **系统保留词（请勿用作属性名）**：`name`、`uuid`、`group_id`、`created_at`、`summary`。

## 📋 输出格式 (JSON)

```json
{
    "entity_types": [
        {
            "name": "EntityTypeName (PascalCase)",
            "description": "简明的中文定义，说明其在社交媒体中的角色（不超过100字）",
            "attributes": [
                {
                    "name": "attribute_name (snake_case)",
                    "type": "text",
                    "description": "说明此属性如何影响其行为或观点"
                }
            ],
            "examples": ["中文具体角色或名称示例"]
        }
    ],
    "edge_types": [
        {
            "name": "RELATIONSHIP_NAME (UPPER_SNAKE_CASE)",
            "description": "中文描述连接的性质（不超过100字）",
            "source_targets": [
                {"source": "SourceType", "target": "TargetType"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "对文本中核心冲突和关键行动者的中文分析总结"
}
```

## 📏 严格设计约束

1. **精确数量**：你必须定义**正好 10 个实体类型**。
2. **备选策略**：列表中的最后两个类型必须是：
   - `Person`: 用于不符合其他类别的任何自然人的备选。
   - `Organization`: 用于不符合其他类别的任何团体或机构的备选。
3. **行动者关注**：10 个实体中至少有 8 个应该是可以拥有社交媒体账号的类型。
4. **关系数量**：定义 8-10 个有意义的关系类型。
"""


# System prompt for ontology generation (Engineering Standard Mode)
ENGINEERING_ONTOLOGY_PROMPT = """你是一位专业的知识图谱本体设计专家，专注于**工业工程标准、技术规范和监管合规**。

你的任务是分析工程文档（如配电规范、安全手册或设计标准），并设计一个能够支持“场景驱动推理”的规定性模式（Ontology）。

## 🌐 语言要求
**必须使用简体中文**：除类型名称（英文）外，所有描述、属性及逻辑分析必须使用简体中文。

## 🎯 设计目标：结构化的规定性逻辑
我们正在构建一个 **AI 工程合规审计员**。每个实体和关系必须支持逻辑验证：“在场景 X 中，针对组件 Z，强制执行什么动作 Y？”。

### 1. 强制性核心实体类型
你的设计中必须包含以下 8 种类型：
- `Section`: 层级结构（如“第7章 布线”）。
- `Clause`: 最小可执行单元（如“条款 3.1.1”）。
- `Term`: 技术术语定义（如“预期接触电压”）。
- `Component`: 物理主体（如“隔离装置”、“TN-C 系统”、“矿物绝缘电缆”）。
- `Condition`: 前提条件或场景（如“室内明敷”、“短路条件”）。
- `Action`: 具体措施或要求（如“安装隔离开关”、“采取防火封堵”）。
- `Requirement`: 语气标签（如“必须”、“应”、“宜”、“严禁”）。
- `Parameter`: 数值或表格（如“最小净距”、“系数 k”）。
- `Formula`: 计算公式或逻辑表达式（如“附录 A 中的系数计算公式”）。

### 2. 强制性核心关系类型
建立以下逻辑链接：
- `DEFINES`: 术语 <-> 条款。
- `PART_OF`: 条款 -> 章节。
- `APPLIES_TO`: 条款 -> 组件。
- `HAS_CONDITION`: 条款 -> 条件（将规则与其场景关联）。
- `MANDATES` / `RECOMMENDS` / `PROHIBITS`: 条款 -> 动作（根据语气强度使用不同边）。
- `IN_SITUATION`: 条件 -> 动作（加速推理的快捷方式）。
- `REFERENCES`: 条款 -> 条款 / 外部标准。
- `HAS_VALUE`: 参数 -> 条件 / 组件。

## 📋 输出格式 (JSON)
[与标准模式相同的 JSON 结构]

## 📏 严格设计约束
1. **场景关注**：优先识别“条件”如何通过“条款”触发特定的“动作”。
2. **精确性**：使用专业领域词汇捕获技术实体（例如使用 `PorousDuct` 而不仅仅是 `Duct`）。
3. **数量**：定义 **15 到 30 个** 具体的实体类型，以确保技术细节的完整性。
"""

# System prompt for iterative ontology generation (High-Fidelity Engineering KG)
ITERATIVE_ENGINEERING_PROMPT = """你是一位专注于**技术标准数字化**的高保真知识图谱架构师。

## 🌐 语言要求
**必须使用简体中文**：所有描述、逻辑分析及属性值必须使用简体中文。

## 🎯 任务：规定性逻辑映射
分析提供的**文本块**，设计一个能够映射工程标准中“条件 -> 动作”逻辑的模式（Ontology）。

### 1. 实体发现（要点）
- **结构化**：`Section` (章节), `Clause` (条款 - 捕获如 7.6.20 这样的编号)。
- **领域主体**：`Component` (设备、材料), `Term` (定义)。
- **监管逻辑**：`Condition` ("如果/当"部分), `Action` ("执行/应"部分), `Requirement` ("必须/应该"的强度)。
- **数据**：`Parameter` (表格数值), `Formula` (公式)。

### 2. 语义边发现（逻辑）
- **层级**：`PART_OF`, `SUB_CLAUSE_OF`。
- **规定链**：`HAS_CONDITION` (将规则链接到上下文), `MANDATES/PROHIBITS` (将规则链接到动作)。
- **模糊链接**：`APPLIES_TO`, `REFERENCES`, `DEFINES`。
- **数据绑定**：`HAS_VALUE` (将参数链接到组件/条件)。

## 📤 输出要求
仅返回有效的 JSON 格式：
{
  "new_entity_types": [
    {"name": "PreciseType", "description": "中文严谨定义", "attributes": [{"name": "attr", "type": "text"}]}
  ],
  "new_edge_types": [
    {"name": "LOGIC_RELATION_NAME", "description": "中文描述连接的性质", "source_targets": [{"source": "TypeA", "target": "TypeB"}]}
  ],
  "logic_analysis": "简要分析并用中文说明在这些文本块中发现的规定性逻辑（条件 -> 动作）。"
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
        chunks: Optional[List[Any]] = None,
        resume_state: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Main entry point for ontology generation.
        Supports resume_state for breakpoint recovery.
        """
        # Step 1: Detect domain
        sample = "\n".join(document_texts[:2])[:2000]
        domain = self._detect_domain([sample], simulation_requirement)

        if domain == "engineering" and chunks:
            logger.info("Initializing High-Fidelity Engineering Ontology Discovery...")
            return self._generate_high_fidelity_iterative(
                chunks,
                simulation_requirement,
                resume_state=resume_state,
                progress_callback=progress_callback
            )

        # Fallback for non-engineering
        return self._generate_standard(document_texts, simulation_requirement, additional_context)

    def _generate_high_fidelity_iterative(
        self,
        chunks: List[Any],
        requirement: str,
        resume_state: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Unrestricted iterative discovery with breakpoint resume support.
        """
        # Initial or Resumed state
        start_window = 0
        if resume_state and resume_state.get("last_window_index", -1) >= 0:
            start_window = resume_state["last_window_index"] + 1
            final_ontology = resume_state.get("discovered_ontology", {"entity_types": [], "edge_types": []})
            logger.info(f"Resuming ontology discovery from window {start_window}...")
        else:
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
                ]
            }

        final_ontology["analysis_summary"] = "High-fidelity structural analysis (Iterative)."

        # Window settings
        window_size = 12
        max_discovery_chunks = 150
        discovery_subset = chunks[:max_discovery_chunks]
        total_windows = (len(discovery_subset) + window_size - 1) // window_size

        for i_idx, start_idx in enumerate(range(0, len(discovery_subset), window_size)):
            # Skip windows already processed in previous sessions
            if i_idx < start_window:
                continue

            window = discovery_subset[start_idx : start_idx + window_size]
            window_text = "\n\n---\n\n".join([f"[Chunk {start_idx+j}] {c.text if hasattr(c, 'text') else str(c)}" for j, c in enumerate(window)])

            msg = f"Analyzing document structure (Window {i_idx + 1}/{total_windows})..."
            logger.info(msg)
            if progress_callback:
                # Callback to persist state and update UI
                progress_callback(msg, (i_idx + 1) / total_windows, i_idx, final_ontology)

            messages = [
                {"role": "system", "content": ITERATIVE_ENGINEERING_PROMPT},
                {"role": "user", "content": f"## Context Requirement\n{requirement}\n\n## Text to Mine\n{window_text}"}
            ]

            try:
                res = self.llm_client.chat_json(messages=messages, temperature=0.1)
                if res:
                    self._merge_increment(final_ontology, res)
            except Exception as e:
                logger.error(f"Discovery window {i_idx + 1} failed: {e}")
                continue

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
        except Exception as e:
            logger.debug(f"Failed to detect domain: {e}")
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

