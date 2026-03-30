"""
LLM Document Parser - 基于 LLM 的统一文档解析器

设计理念：
- 抛弃正则，使用 LLM 智能解析
- 统一处理章节、条文、术语、表格、公式、组件、条件、动作等
- 支持操作对象（Object）和行为（Behavior）的实体提取

输入：原始 TextChunk 列表
输出：ParsedDocument 结构
"""

import json
import logging
from typing import List, Dict, Any, Optional, Callable
from dataclasses import asdict

from ..models.normative_entity import (
    ParsedDocument,
    ParsedClause,
    ExtractedEntity,
    ExtractedRelation,
    EntityType,
    RelationType,
    ParseOptions
)
from ..utils.file_parser import TextChunk

logger = logging.getLogger('mirofish.doc_parser')


class LLMDocParser:
    """
    基于 LLM 的文档解析器

    工作流程：
    1. 将原始文本按段落分割为 Chunk
    2. 调用 LLM 批量解析 Chunk，提取实体和关系
    3. 组装 ParsedDocument 结构
    """

    # LLM 系统提示词
    SYSTEM_PROMPT = """你是一个工程规范文档的结构化知识提取专家。

你的任务是将工程规范（如 GB 50054 低压配电设计规范）的文本内容转换为结构化的知识图谱数据。

## 实体类型（必须提取）

1. **Term（术语）**: 条文定义的术语
   - 例：预期接触电压、约定接触电压限值、直接接触
   - 属性：term_id（如2.0.1）、term_name、definition

2. **Component（组件）**: 设备、系统、材料等实体
   - 例：剩余电流保护电器、配电屏、矿物绝缘电缆
   - 属性：name、type（如设备/系统/材料）

3. **Object（操作对象）**: 动作作用的对象
   - 例：保护导体截面积、导线至地面的距离
   - 属性：name、measurement（如截面积/距离/温度）

4. **Condition（条件）**: 适用前提、环境、场景
   - 例：潮湿环境、接地故障电流小于一定值、热稳定校验
   - 属性：name、type（如环境/电气参数/场景）

5. **Action（动作）**: 规范要求的动作措施
   - 例：应设置剩余电流保护、必须采用TN系统、严禁使用TN-C系统
   - 属性：name、requirement（mandatory/recommended/prohibited）

6. **Formula（公式）**: 计算公式
   - 例：式(3.2.14) S ≥ I·t / k
   - 属性：formula_id、expression、variables（变量定义）

7. **Parameter（参数）**: 表格参数
   - 例：表3.2.2 固定敷设的导体最小截面
   - 属性：table_id、description、key_parameters

8. **Situation（情景）**: 适用情景
   - 例：低压配电系统、电气装置的电击防护
   - 属性：name、scope（如领域/系统/场所）

## 关系类型（必须提取）

1. **MANDATES / RECOMMENDS / PROHIBITS**: 条款对动作的要求
   - 源：Clause -> 目标：Action

2. **HAS_CONDITION**: 条款的前提条件
   - 源：Clause -> 目标：Condition

3. **OPERATES_ON**: 动作作用于对象
   - 源：Action -> 目标：Object
   - 例："设置剩余电流保护" operates_on "电气装置"

4. **COMPOSED_OF**: 对象的组成
   - 源：Component/Object -> 目标：Component/Object

5. **REFERENCES**: 引用关系
   - 源：Clause -> 目标：Formula/Parameter/Term

6. **DEFINES**: 术语定义
   - 源：Term -> 目标：definition（作为属性）

7. **in_situation**: 适用于某情景
   - 源：Clause/Action -> 目标：Situation

8. **PART_OF**: 层级归属
   - 源：Clause -> 目标：Section

## 输出要求

1. 提取所有实体，类型必须从上述实体类型中选择
2. 提取所有关系，必须指定 relation_type
3. 每个实体和关系都必须有清晰的描述（description/fact）
4. 条款编号要精确（如"3.2.1"而非"3.2"）
5. 保持 JSON 格式输出
6. 使用中文输出

## 重要原则

- 不要臆造信息，只提取文本中明确包含的内容
- 如果某类实体不存在，返回空列表
- 实体名称要简洁明确，便于检索
- 关系描述要能回答"谁对谁做了什么"的问题
"""

    USER_PROMPT_TEMPLATE = """请分析以下工程规范文本，提取结构化实体和关系。

## 待分析文本
{chunk_content}

## 当前上下文
- 文档标题：{doc_title}
- 起始条款编号：{start_clause_id}
- 来源：{source}

请以 JSON 格式输出：

```json
{{
    "document_title": "{doc_title}",
    "clauses": [
        {{
            "clause_id": "3.2.1",
            "clause_title": "导体应满足线路保护的要求",
            "clause_content": "...",
            "requirement_type": "mandatory",
            "entities": [
                {{
                    "entity_type": "Component",
                    "name": "导体",
                    "description": "配电线路中的导电材料"
                }},
                {{
                    "entity_type": "Action",
                    "name": "满足线路保护",
                    "description": "导体应满足线路保护的要求"
                }},
                {{
                    "entity_type": "Condition",
                    "name": "正常持续运行",
                    "description": "负荷电流在正常持续运行中产生的温度条件"
                }}
            ],
            "relations": [
                {{
                    "relation_type": "MANDATES",
                    "source_entity": "条款3.2.1",
                    "target_entity": "满足线路保护",
                    "fact": "条款3.2.1 规定导体应满足线路保护的要求"
                }},
                {{
                    "relation_type": "OPERATES_ON",
                    "source_entity": "满足线路保护",
                    "target_entity": "导体",
                    "fact": "线路保护的要求作用于导体"
                }}
            ],
            "referenced_tables": ["表3.2.2"],
            "referenced_formulas": ["式(3.2.14)"],
            "referenced_clauses": []
        }}
    ],
    "global_entities": [],
    "global_relations": []
}}
```

如果没有发现任何条款，可以只返回空结构：
```json
{{"clauses": [], "global_entities": [], "global_relations": []}}
```
"""

    BATCH_PROMPT_TEMPLATE = """请分析以下工程规范文本（多条款），批量提取结构化实体和关系。

## 待分析文本
{chunk_content}

## 当前上下文
- 文档标题：{doc_title}
- 来源：{source}

请以 JSON 格式输出：

```json
{{
    "clauses": [
        {{
            "clause_id": "条款编号",
            "clause_title": "条款标题",
            "clause_content": "条款完整内容",
            "requirement_type": "mandatory|recommended|prohibited",
            "entities": [
                {{
                    "entity_type": "Component|Object|Condition|Action|Term|Formula|Parameter|Situation",
                    "name": "实体名称",
                    "description": "实体描述"
                }}
            ],
            "relations": [
                {{
                    "relation_type": "MANDATES|HAS_CONDITION|OPERATES_ON|REFERENCES|in_situation",
                    "source_entity": "源实体",
                    "target_entity": "目标实体",
                    "fact": "关系描述"
                }}
            ],
            "referenced_tables": ["表3.2.2"],
            "referenced_formulas": ["式(3.2.14)"],
            "referenced_clauses": ["3.2.1"]
        }}
    ],
    "global_entities": [],
    "global_relations": []
}}
```

请保持 JSON 格式输出。
"""

    def __init__(self, llm_client=None):
        """
        初始化解析器

        Args:
            llm_client: LLM 客户端，默认使用系统配置
        """
        self.llm_client = llm_client
        self.logger = logging.getLogger('mirofish.doc_parser')
        self.options = ParseOptions()

    def parse_chunks(
        self,
        text_chunks: List[TextChunk],
        doc_title: str = "未命名文档",
        progress_callback: Optional[Callable] = None
    ) -> ParsedDocument:
        """
        解析文本块列表

        Args:
            text_chunks: 原始 TextChunk 列表
            doc_title: 文档标题
            progress_callback: 进度回调

        Returns:
            ParsedDocument: 解析结果
        """
        if not text_chunks:
            return ParsedDocument(document_title=doc_title)

        # 合并文本（保留边界信息）
        full_text = "\n".join([tc.text for tc in text_chunks])
        source_info = {
            "source": text_chunks[0].metadata.get("source", "") if text_chunks else "",
            "page": text_chunks[0].metadata.get("page", 1) if text_chunks else 1,
            "bbox": text_chunks[0].metadata.get("bbox") if text_chunks else None
        }

        self.logger.info(f"开始解析文档 '{doc_title}'，文本长度: {len(full_text)}")

        # 按段落分割，每段约 500-1000 字
        paragraphs = self._split_into_paragraphs(full_text, max_chars=800)

        if progress_callback:
            progress_callback(0.1, f"分割为 {len(paragraphs)} 个段落")

        # 批量调用 LLM 解析
        parsed_clauses = []
        global_entities = []
        global_relations = []

        for i, para in enumerate(paragraphs):
            try:
                result = self._parse_single_paragraph(
                    para,
                    doc_title=doc_title,
                    start_idx=i * self.options.max_clauses_per_batch,
                    source_info=source_info
                )

                if result:
                    parsed_clauses.extend(result.get("clauses", []))
                    global_entities.extend(result.get("global_entities", []))
                    global_relations.extend(result.get("global_relations", []))

                if progress_callback and (i + 1) % 5 == 0:
                    progress_callback((i + 1) / len(paragraphs) * 0.8,
                                    f"已解析 {i + 1}/{len(paragraphs)} 段落")

            except Exception as e:
                self.logger.warning(f"解析段落 {i} 失败: {e}")

        # 组装 ParsedDocument
        parsed_doc = ParsedDocument(
            document_title=doc_title,
            clauses=[self._dict_to_parsed_clause(c) for c in parsed_clauses],
            global_entities=[self._dict_to_entity(e) for e in global_entities],
            global_relations=[self._dict_to_relation(r) for r in global_relations]
        )

        if progress_callback:
            progress_callback(1.0, f"解析完成: {len(parsed_clauses)} 条条款")

        self.logger.info(
            f"文档解析完成: {len(parsed_clauses)} 条条款, "
            f"{len(parsed_doc.global_entities)} 全局实体, "
            f"{len(parsed_doc.global_relations)} 全局关系"
        )

        return parsed_doc

    def _split_into_paragraphs(self, text: str, max_chars: int = 800) -> List[str]:
        """将文本分割为段落"""
        paragraphs = []

        # 按双换行分割（段落）
        raw_paras = text.split("\n\n")

        current = []
        current_len = 0

        for para in raw_paras:
            para_len = len(para)
            if current_len + para_len <= max_chars:
                current.append(para)
                current_len += para_len
            else:
                if current:
                    paragraphs.append("\n\n".join(current))
                # 如果单个段落超过 max_chars，进一步分割
                if para_len > max_chars:
                    sub_parts = self._split_long_paragraph(para, max_chars)
                    paragraphs.extend(sub_parts[:-1])
                    current = [sub_parts[-1]]
                    current_len = len(sub_parts[-1])
                else:
                    current = [para]
                    current_len = para_len

        if current:
            paragraphs.append("\n\n".join(current))

        return paragraphs

    def _split_long_paragraph(self, text: str, max_chars: int) -> List[str]:
        """分割长段落"""
        lines = text.split("\n")
        parts = []
        current = []
        current_len = 0

        for line in lines:
            if current_len + len(line) <= max_chars:
                current.append(line)
                current_len += len(line)
            else:
                if current:
                    parts.append("\n".join(current))
                current = [line]
                current_len = len(line)

        if current:
            parts.append("\n".join(current))

        return parts

    def _parse_single_paragraph(
        self,
        paragraph: str,
        doc_title: str,
        start_idx: int,
        source_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """解析单个段落"""
        user_prompt = self.BATCH_PROMPT_TEMPLATE.format(
            chunk_content=paragraph[:3000],  # 限制长度
            doc_title=doc_title,
            source=source_info.get("source", "")
        )

        try:
            response = self._call_llm(user_prompt)
            if response:
                result = self._parse_llm_response(response)
                # 补充来源信息
                for clause in result.get("clauses", []):
                    if "source" not in clause or not clause["source"]:
                        clause["source"] = source_info.get("source", "")
                    if "page" not in clause or clause["page"] == 0:
                        clause["page"] = source_info.get("page", 1)
                    if "bbox" not in clause:
                        clause["bbox"] = source_info.get("bbox", [])
                return result
        except Exception as e:
            self.logger.error(f"LLM 调用失败: {e}")

        return None

    def _call_llm(self, user_prompt: str) -> Optional[str]:
        """调用 LLM"""
        if self.llm_client:
            return self.llm_client.chat(
                system=self.SYSTEM_PROMPT,
                user=user_prompt
            )
        else:
            from ..utils.llm_client import LLMClient
            client = LLMClient()
            response = client.chat_json(
                system=self.SYSTEM_PROMPT,
                user=user_prompt
            )
            return json.dumps(response) if response else None

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """解析 LLM 响应"""
        # 提取 JSON
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
            return {"clauses": [], "global_entities": [], "global_relations": []}

        try:
            return json.loads(json_match.strip())
        except json.JSONDecodeError as e:
            self.logger.warning(f"JSON 解析失败: {e}")
            return {"clauses": [], "global_entities": [], "global_relations": []}

    def _dict_to_parsed_clause(self, d: Dict[str, Any]) -> ParsedClause:
        """字典转换为 ParsedClause"""
        return ParsedClause(
            clause_id=d.get("clause_id", ""),
            clause_title=d.get("clause_title", ""),
            clause_content=d.get("clause_content", ""),
            requirement_type=d.get("requirement_type", ""),
            conditions=d.get("conditions", []),
            actions=d.get("actions", []),
            entities=[self._dict_to_entity(e) for e in d.get("entities", [])],
            relations=[self._dict_to_relation(r) for r in d.get("relations", [])],
            referenced_tables=d.get("referenced_tables", []),
            referenced_formulas=d.get("referenced_formulas", []),
            referenced_clauses=d.get("referenced_clauses", []),
            source=d.get("source", ""),
            page=d.get("page", 1),
            bbox=d.get("bbox", [])
        )

    def _dict_to_entity(self, d: Dict[str, Any]) -> ExtractedEntity:
        """字典转换为 ExtractedEntity"""
        try:
            entity_type = EntityType(d.get("entity_type", "Component"))
        except ValueError:
            entity_type = EntityType.COMPONENT

        return ExtractedEntity(
            entity_type=entity_type,
            name=d.get("name", ""),
            description=d.get("description", ""),
            properties=d.get("properties", {})
        )

    def _dict_to_relation(self, d: Dict[str, Any]) -> ExtractedRelation:
        """字典转换为 ExtractedRelation"""
        try:
            relation_type = RelationType(d.get("relation_type", "RELATES_TO"))
        except ValueError:
            relation_type = RelationType.RELATES_TO

        return ExtractedRelation(
            relation_type=relation_type,
            source_entity=d.get("source_entity", ""),
            target_entity=d.get("target_entity", ""),
            fact=d.get("fact", ""),
            properties=d.get("properties", {})
        )

    def parsed_document_to_episodes(
        self,
        doc: ParsedDocument
    ) -> List[Dict[str, Any]]:
        """
        将 ParsedDocument 转换为 Episode 数据列表

        用于存储到 Neo4j
        """
        episodes = []

        for clause in doc.clauses:
            # 条文文本作为 Episode
            episodes.append({
                "text": clause.clause_content,
                "metadata": {
                    "chunk_type": "clause",
                    "level": 2,
                    "level_name": "Level2",
                    "clause_id": clause.clause_id,
                    "clause_title": clause.clause_title,
                    "requirement_type": clause.requirement_type,
                    "source": clause.source,
                    "page": clause.page,
                    "bbox": clause.bbox,
                    "entities": [
                        {"type": e.entity_type.value, "name": e.name}
                        for e in clause.entities
                    ],
                    "relations": [
                        {"type": r.relation_type.value, "source": r.source_entity, "target": r.target_entity}
                        for r in clause.relations
                    ],
                    "referenced_tables": clause.referenced_tables,
                    "referenced_formulas": clause.referenced_formulas
                }
            })

            # 实体也作为 Episode（方便检索）
            for entity in clause.entities:
                if entity.entity_type in [EntityType.TERM, EntityType.FORMULA, EntityType.PARAMETER]:
                    episodes.append({
                        "text": f"{entity.name}: {entity.description}",
                        "metadata": {
                            "chunk_type": "element",
                            "level": 3,
                            "level_name": "Level3",
                            "element_type": entity.entity_type.value.lower(),
                            "source_id": entity.properties.get("id", ""),
                            "key": entity.name,
                            "value": entity.description,
                            "clause_id": clause.clause_id,
                            "source": clause.source,
                            "page": clause.page,
                            "bbox": clause.bbox,
                            "page_width": clause.page_width,
                            "page_height": clause.page_height
                        }
                    })

        # 全局实体
        for entity in doc.global_entities:
            episodes.append({
                "text": f"{entity.name}: {entity.description}",
                "metadata": {
                    "chunk_type": "global_entity",
                    "level": 0,
                    "level_name": "Global",
                    "element_type": entity.entity_type.value.lower(),
                    "key": entity.name,
                    "value": entity.description,
                    "source": doc.document_title
                }
            })

        return episodes


# ============================================================================
# 便捷函数
# ============================================================================

def parse_document(
    text_chunks: List[TextChunk],
    doc_title: str = "未命名文档",
    progress_callback: Optional[Callable] = None
) -> ParsedDocument:
    """
    便捷函数：解析文档

    Args:
        text_chunks: 原始文本块
        doc_title: 文档标题
        progress_callback: 进度回调

    Returns:
        ParsedDocument
    """
    parser = LLMDocParser()
    return parser.parse_chunks(text_chunks, doc_title, progress_callback)
