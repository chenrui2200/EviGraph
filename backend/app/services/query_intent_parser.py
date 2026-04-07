"""
QueryIntentParser — 将用户自然语言问题解析为结构化意图，
在 DFS 遍历之前引导搜索方向。
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..utils.llm_client import LLMClient

logger = logging.getLogger('mirofish.query_intent')


QUERY_INTENT_SYSTEM_PROMPT = """你是一个工程规范知识图谱的查询意图解析专家。

你的任务是将用户的自然语言问题解析为结构化意图，用于引导图谱遍历方向。

## 图谱节点类型
- Section：章节层级
- Clause：最小可执行单元（带编号条款）
- Term：术语定义
- Component：实体对象（设备、系统、材料）
- Condition：前提条件（环境、场景）
- Action：规范要求的具体动作/措施
- Requirement：强制/推荐/禁止标签
- Parameter：表格/公式数值

## 图谱边类型（遍历方向）
- MANDATES：强制要求
- RECOMMENDS：推荐
- PROHIBITS：禁止
- OPERATES_ON：动作作用于对象
- HAS_CONDITION：动作的前提条件
- APPLIES_TO：条款适用于组件
- IN_SITUATION：情景关联动作
- DEFINES：术语定义
- MENTIONS：提及

## 语义角色定义

| 角色 | 说明 | 示例 |
|------|------|------|
| component | 施事设备/组件 | "剩余电流保护电器"、"配电箱"、"导线" |
| action | 实操动作（必须是可执行动词） | "选用"、"敷设"、"接地"、"检测"、"承受" |
| obj | 受事对象 | "截面积"、"电缆桥架"、"热稳定" |
| condition | 适用条件（可选） | "短路条件下"、"TN系统时"、"爆炸危险场所" |
| requirement | 义务等级 | mandatory（应/必须）、recommended（宜/建议）、prohibited（严禁/禁止）|

**Action 必须是动作词，禁止使用状态词！**
- ✅ 正确动作：选用、敷设、接地、检测、配置、安装、连接、分断、承受、采用
- ❌ 状态词（禁止）：符合、满足、达到、遵守（这些是结果描述，不是动作）

## 支持的意图类型

| 类型 | 触发词 | 说明 |
|------|--------|------|
| requirement | 选用、应如何、如何选用、怎么选 | 查询规范要求 |
| definition | 什么是、定义、含义 | 查询术语定义 |
| condition | 在...时、当...时、什么条件下 | 查询适用条件 |
| procedure | 如何、怎样、步骤、方法 | 查询操作步骤 |
| comparison | 区别、不同、比较 | 对比两个概念 |
| parameter | 多大、多少、参数、取值 | 查询参数数值 |
| prohibition | 严禁、禁止、不得 | 查询禁止事项 |
| general | 其他 | 通用查询 |

## 输出格式

```json
{
  "intents": [
    {
      "type": "requirement|definition|condition|procedure|comparison|parameter|prohibition|general",
      "component": "施事组件（可选）",
      "action": "实操动作（可选）",
      "obj": "受事对象（可选）",
      "condition": "适用条件（可选）",
      "requirement": "mandatory|recommended|prohibited（可选）",
      "confidence": 0.9
    }
  ],
  "confidence": 0.85,
  "reasoning": "解析理由"
}
```

**注意**：
- 至少填充 component、action、obj 之一，不允许全部为空
- confidence 表示该意图的解析置信度（0-1）
- 支持多意图查询（compound query），如"电缆和导线的选用要求"
- 如果用户只问"什么是RCD"，只填 component="RCD"，其余留空
- 如果无法解析，返回 confidence=0 的空意图"""



QUERY_INTENT_USER_PROMPT = """请解析以下用户问题：

{query}

请输出 JSON 格式的意图分析结果。"""


@dataclass
class QueryIntent:
    """单条结构化意图"""
    type: str = ""           # requirement/definition/condition/procedure/comparison/parameter/prohibition/general
    component: str = ""      # 施事组件
    action: str = ""         # 实操动作
    obj: str = ""            # 受事对象
    condition: str = ""      # 适用条件
    requirement: str = ""   # mandatory/recommended/prohibited
    confidence: float = 0.0 # 解析置信度
    raw: Optional[Dict] = None

    def is_empty(self) -> bool:
        return not (self.component or self.action or self.obj)

    def to_search_keywords(self) -> List[str]:
        """提取用于图谱搜索的关键词列表"""
        keywords = [k for k in [self.component, self.obj, self.action, self.condition] if k.strip()]
        return keywords

    def to_edge_types(self) -> List[str]:
        """根据 requirement 推断需要优先遍历的边类型"""
        if self.requirement == "mandatory":
            return ["MANDATES"]
        elif self.requirement == "recommended":
            return ["RECOMMENDS"]
        elif self.requirement == "prohibited":
            return ["PROHIBITS"]
        return []


class QueryIntentParser:
    """使用 LLM 解析用户查询意图"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self._llm = llm_client or LLMClient()
        self._system_prompt = QUERY_INTENT_SYSTEM_PROMPT

    def parse(self, query: str) -> QueryIntent:
        """
        将自然语言问题解析为 QueryIntent。

        解析失败或置信度过低时返回 is_empty=True 的 intent。
        """
        if not query or not query.strip():
            return QueryIntent()

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": QUERY_INTENT_USER_PROMPT.format(query=query)},
        ]

        try:
            result = self._llm.chat_json(
                messages=messages,
                temperature=0.3,
                max_tokens=1024,
            )

            intents_raw = result.get("intents", [])
            if not intents_raw:
                return QueryIntent()

            primary = intents_raw[0]
            return QueryIntent(
                type=primary.get("type", "general"),
                component=primary.get("component", "").strip(),
                action=primary.get("action", "").strip(),
                obj=primary.get("obj", "").strip(),
                condition=primary.get("condition", "").strip(),
                requirement=primary.get("requirement", "").strip(),
                confidence=float(primary.get("confidence", 0.0)),
                raw=primary,
            )

        except Exception as e:
            logger.warning(f"[QueryIntentParser] 解析失败: {e}")
            return QueryIntent()

    def parse_multiple(self, query: str) -> List[QueryIntent]:
        """解析多意图查询"""
        if not query or not query.strip():
            return []

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": QUERY_INTENT_USER_PROMPT.format(query=query)},
        ]

        try:
            result = self._llm.chat_json(
                messages=messages,
                temperature=0.3,
                max_tokens=1024,
            )

            intents_raw = result.get("intents", [])
            return [
                QueryIntent(
                    type=item.get("type", "general"),
                    component=item.get("component", "").strip(),
                    action=item.get("action", "").strip(),
                    obj=item.get("obj", "").strip(),
                    condition=item.get("condition", "").strip(),
                    requirement=item.get("requirement", "").strip(),
                    confidence=float(item.get("confidence", 0.0)),
                    raw=item,
                )
                for item in intents_raw
                if item
            ]
        except Exception as e:
            logger.warning(f"[QueryIntentParser] 多意图解析失败: {e}")
            return []
