"""
Step 2: LLM 实体关系提取
使用 LLM 从每个术语 chunk 中提取 entities 和 relationships

用法:
    python 02_llm_extraction.py

依赖:
    - openai
    - python-dotenv
    - 已启动的 vLLM 服务 (http://192.168.110.126:18001)
"""

import json
import os
import re
import time
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# ===== LLM 配置 =====
LLM_API_KEY = 'vllm'
LLM_BASE_URL = 'http://192.168.110.126:18001/v1/chat/completions'
LLM_MODEL_NAME = 'Qwen/Qwen3-Coder-30B-A3B-Instruct'

# ===== 实体和关系类型定义 =====
ENTITY_TYPES = {
    "ProtectionConcept": "防护概念/措施（如直接接触防护、间接接触防护、附加防护）",
    "ElectricalSystem": "电气系统类型（如SELV、PELV、FELV、TN、TT、IT）",
    "Device": "电气设备（如断路器、隔离开关、熔断器、漏电保护器）",
    "Parameter": "电气参数值（如特低电压50V、约定接触电压限值）",
    "PhysicalConcept": "物理概念/结构（如伸臂范围、导管、电缆托盘）",
}

RELATIONSHIP_TYPES = {
    "定义": "A 是 B 的定义说明",
    "基于": "A 基于 B（如系统基于某电压参数）",
    "限制": "A 不超过 B（如特低电压不超过50V）",
    "组成": "A 由 B 组成或包含 B",
    "对比": "A 与 B 对比（正常vs故障条件）",
    "执行": "A 执行 B（设备执行保护功能）",
    "用于": "A 用于 B",
    "引用": "A 引用 B（条款引用参数或表格）",
    "条件": "A 在 B 条件下发生"
}


def create_llm_client():
    """创建 LLM 客户端"""
    client = OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL
    )
    return client


def build_extraction_prompt(chunk: dict) -> list[dict]:
    """构建提取 prompt"""

    system_prompt = f"""你是一个电力工程领域知识图谱构建专家。
你的任务是从低压配电设计规范（GB50054-2011）的术语定义中提取实体和关系。

【实体类型定义】
{json.dumps(ENTITY_TYPES, ensure_ascii=False, indent=2)}

【关系类型定义】
{json.dumps(RELATIONSHIP_TYPES, ensure_ascii=False, indent=2)}

【重要规则】
1. 只提取文本中**明确提到**的关系，不要做推测
2. 如果一个术语引用了另一个术语（如"SELV系统"提到"特低电压"），建立"基于"或"引用"关系
3. 防护概念之间的对比关系（如直接接触防护 vs 间接接触防护）建立"对比"关系
4. 实体ID使用术语/概念的标准名称
5. 输出必须是合法的 JSON 格式，不要有额外的解释文字
6. 如果某个实体已经在之前的条款中定义，在该实体的source_clause中注明（如"已在2.0.14定义"）
"""

    chunk_id = chunk['chunk_id']
    term_name = chunk['term_name']
    definition = chunk['definition']
    page_idx = chunk['page_idx']

    user_prompt = f"""【待提取的术语定义】

条款编号: {chunk_id}
术语名: {term_name}
定义: {definition}
页码: 第{page_idx + 1}页

请提取该定义中的所有实体和关系。

输出格式（必须是合法JSON）：
{{
  "entities": [
    {{
      "id": "实体ID（使用术语名称）",
      "type": "ProtectionConcept|ElectricalSystem|Device|Parameter|PhysicalConcept",
      "name": "实体显示名",
      "source_clause": "{chunk_id}",
      "definition_summary": "从原定义中提取的简短描述"
    }}
  ],
  "relationships": [
    {{
      "from": "实体A名称",
      "type": "关系类型",
      "to": "实体B名称",
      "detail": "关系描述"
    }}
  ]
}}

注意：如果某实体（如"特低电压"）已在之前条款中定义，在该实体字段中标注"已在2.0.XX定义"。

如果没有某种类型的实体或关系，输出空数组 []。
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]


def extract_from_chunk(client, chunk: dict, max_retries: int = 3) -> dict:
    """
    从单个 chunk 提取实体和关系

    Args:
        client: OpenAI 客户端
        chunk: 术语 chunk dict
        max_retries: 最大重试次数

    Returns:
        {"entities": [...], "relationships": [...]}
    """
    messages = build_extraction_prompt(chunk)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL_NAME,
                messages=messages,
                temperature=0.1,
                max_tokens=2048,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content.strip()

            # 清理可能的 markdown 代码块
            content = re.sub(r'^```json\s*', '', content)
            content = re.sub(r'^```\s*', '', content)
            content = content.strip()

            result = json.loads(content)

            # 添加 chunk 来源信息
            if "entities" in result:
                for entity in result["entities"]:
                    entity["source_chunk_id"] = chunk["chunk_id"]
            if "relationships" in result:
                for rel in result["relationships"]:
                    rel["source_chunk_id"] = chunk["chunk_id"]

            return result

        except json.JSONDecodeError as e:
            print(f"    ⚠ JSON解析失败 (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
        except Exception as e:
            print(f"    ⚠ LLM调用失败 (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(5)

    # 重试全部失败时返回空结果
    return {
        "entities": [],
        "relationships": [],
        "source_chunk_id": chunk["chunk_id"],
        "error": "max_retries_exceeded"
    }


def batch_extract(client, chunks: list, output_dir: str, batch_size: int = 5, delay: float = 1.0):
    """
    批量提取所有 chunks 的实体和关系

    Args:
        client: LLM 客户端
        chunks: chunks 列表
        output_dir: 输出目录
        batch_size: 每批处理数量
        delay: 批次间隔延迟（秒）
    """
    all_entities = []
    all_relationships = []

    total = len(chunks)

    print(f"\n开始批量提取 {total} 个 chunks...")

    for i, chunk in enumerate(chunks):
        chunk_id = chunk['chunk_id']
        term_name = chunk['term_name']

        print(f"\n  [{i+1}/{total}] 处理 {chunk_id}: {term_name}")

        result = extract_from_chunk(client, chunk)

        if result.get("entities"):
            print(f"    ✓ 提取 {len(result['entities'])} 个实体")
            all_entities.extend(result["entities"])
        else:
            print(f"    - 无实体")

        if result.get("relationships"):
            print(f"    ✓ 提取 {len(result['relationships'])} 个关系")
            all_relationships.extend(result["relationships"])
        else:
            print(f"    - 无关系")

        if result.get("error"):
            print(f"    ✗ 错误: {result['error']}")

        # 批次延迟
        if (i + 1) % batch_size == 0 and i < total - 1:
            print(f"\n  --- 批次完成，延迟 {delay}s ---")
            time.sleep(delay)

    # 保存原始提取结果
    os.makedirs(output_dir, exist_ok=True)

    entities_path = os.path.join(output_dir, "raw_entities.json")
    rels_path = os.path.join(output_dir, "raw_relationships.json")

    with open(entities_path, 'w', encoding='utf-8') as f:
        json.dump(all_entities, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 保存 {len(all_entities)} 个原始实体到 {entities_path}")

    with open(rels_path, 'w', encoding='utf-8') as f:
        json.dump(all_relationships, f, ensure_ascii=False, indent=2)
    print(f"✓ 保存 {len(all_relationships)} 个原始关系到 {rels_path}")

    return all_entities, all_relationships


def main():
    # ===== 配置 =====
    CHUNKS_PATH = os.path.join(os.path.dirname(__file__), "../../script", "data", "chunks", "terms_chunks.json")
    OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "../../script", "data", "extraction")

    # ===== Step 1: 加载 chunks =====
    print("=" * 60)
    print("Step 2: LLM 实体关系提取")
    print("=" * 60)

    if not os.path.exists(CHUNKS_PATH):
        print(f"✗ Chunks 文件不存在: {CHUNKS_PATH}")
        print("请先运行 Step 1 生成 chunks")
        return

    with open(CHUNKS_PATH, 'r', encoding='utf-8') as f:
        chunks = json.load(f)

    print(f"  加载 {len(chunks)} 个 chunks")

    # ===== Step 2: 创建 LLM 客户端 =====
    client = create_llm_client()
    print(f"  LLM: {LLM_MODEL_NAME}")
    print(f"  API: {LLM_BASE_URL}")

    # ===== Step 3: 批量提取 =====
    entities, relationships = batch_extract(
        client=client,
        chunks=chunks,
        output_dir=OUTPUT_DIR,
        batch_size=5,
        delay=1.0
    )

    # ===== Step 4: 统计 =====
    print("\n" + "=" * 60)
    print("提取完成")
    print("=" * 60)
    print(f"  实体总数: {len(entities)}")
    print(f"  关系总数: {len(relationships)}")

    # 按类型统计
    entity_types = {}
    for e in entities:
        t = e.get("type", "Unknown")
        entity_types[t] = entity_types.get(t, 0) + 1

    rel_types = {}
    for r in relationships:
        t = r.get("type", "Unknown")
        rel_types[t] = rel_types.get(t, 0) + 1

    print("\n  实体类型分布:")
    for t, count in sorted(entity_types.items()):
        print(f"    {t}: {count}")

    print("\n  关系类型分布:")
    for t, count in sorted(rel_types.items()):
        print(f"    {t}: {count}")


if __name__ == "__main__":
    main()
