"""
Step 5: 补充其他章节 — 第3-7章条款 → 引用术语实体，建立跨章节关系

本步骤需要:
1. 先用 MinerU 解析第3-7章的 PDF 内容
2. 生成新的 chunks
3. 用 LLM 提取实体和关系（引用已有关术语实体）
4. 合并到现有图谱

本脚本展示完整的跨章节关联设计，并提供增量导入功能。
"""

import json
import os
import re
import time
from pathlib import Path
from collections import defaultdict

# ===== 章节配置 =====
CHAPTER_CONFIG = {
    "3": {
        "name": "电器和导体的选择",
        "sections": ["3.1 电器的选择", "3.2 导体的选择"],
        "entity_types": ["Device", "Conductor", "System", "Parameter"],
        "key_terms": ["TN系统", "TT系统", "IT系统", "TN-C", "TN-S", "约定接触电压限值",
                     "过电流保护", "剩余电流保护", "断路器", "熔断器"]
    },
    "4": {
        "name": "设备布置",
        "sections": ["4.1 一般规定", "4.2 设备的安全措施", "4.3 对建构筑物的要求"],
        "entity_types": ["Device", "PhysicalConcept", "SafetyMeasure"],
        "key_terms": ["外护物", "保护遮栏", "保护阻挡物", "伸臂范围", "IP等级"]
    },
    "5": {
        "name": "配电的接地设计",
        "sections": ["5.1 直接接触防护措施", "5.2 间接接触防护措施",
                    "5.3 SELV系统、PELV系统及FELV系统"],
        "entity_types": ["ProtectionConcept", "ElectricalSystem", "Device", "Parameter"],
        "key_terms": ["直接接触防护", "间接接触防护", "SELV系统", "PELV系统", "FELV系统",
                     "等电位联结", "总等电位联结", "辅助等电位联结", "剩余电流保护器"]
    },
    "6": {
        "name": "电缆的选择与敷设",
        "sections": ["6.1 一般规定", "6.2 电缆的敷设", "6.3 电缆支持装置"],
        "entity_types": ["Conductor", "Device", "PhysicalConcept"],
        "key_terms": ["导管", "电缆托盘", "电缆梯架", "电缆支架", "矿物绝缘电缆"]
    },
    "7": {
        "name": "电气线路设计",
        "sections": ["7.1 一般规定", "7.2 导体的选择", "7.3 线路的敷设"],
        "entity_types": ["Device", "Parameter", "SafetyMeasure"],
        "key_terms": ["过电流保护", "短路保护", "接地故障保护", "电压降", "动作电流"]
    }
}

# ===== 跨章节关系类型 =====
CROSS_CHAPTER_RELATIONSHIPS = {
    "保护措施_适用于_系统": "某保护措施适用于哪些电气系统",
    "设备_执行_保护功能": "某设备执行哪种保护功能",
    "条款_引用_术语": "某条款引用了哪个已定义的术语",
    "条款_引用_表格": "某条款引用了哪个表格或公式",
    "设备_属于_系统": "某设备安装在哪种系统中",
    "参数_约束_设备": "某参数值约束了设备的选择"
}


def design_cross_chapter_relationships() -> dict:
    """
    设计跨章节关系图谱

    Returns:
        跨章节关系设计字典
    """
    print("\n【跨章节关系设计】")

    design = {
        "description": "基于 GB50054-2011 各章节条款，设计条款与术语实体之间的关系",
        "relationship_templates": [
            {
                "from_type": "Clause",
                "to_type": "ProtectionConcept",
                "rel_type": "引用",
                "description": "第3/4/5/6/7章条款引用第2章已定义的防护概念",
                "example": "条款5.2.1 引用 间接接触防护"
            },
            {
                "from_type": "Clause",
                "to_type": "ElectricalSystem",
                "rel_type": "适用于",
                "description": "条款规定某系统下的保护措施选择",
                "example": "条款3.1.14 适用于 TN系统"
            },
            {
                "from_type": "Device",
                "to_type": "ProtectionConcept",
                "rel_type": "执行",
                "description": "设备执行特定的保护功能",
                "example": "剩余电流保护器 执行 间接接触防护"
            },
            {
                "from_type": "Clause",
                "to_type": "Parameter",
                "rel_type": "引用",
                "description": "条款引用参数限值（如50V、30mA）",
                "example": "条款5.1.12 引用 30mA"
            },
            {
                "from_type": "Clause",
                "to_type": "Table",
                "rel_type": "引用",
                "description": "条款引用附录表格",
                "example": "条款3.2.2 引用 表3.2.2"
            }
        ],
        "extraction_prompt_template": """
你是一个电力工程领域知识图谱构建专家。

当前任务是分析第{chapter}章「{chapter_name}」中的条款，提取：
1. 该条款中引用的第2章术语（如"直接接触防护"、"特低电压"、"SELV系统"等）
2. 该条款与其他条款的关系

【已建立的术语实体（第2章）】
- ProtectionConcept: 直接接触防护, 间接接触防护, 附加防护, 电气分隔, 等电位联结, 保护等电位联结, 总等电位联结, 辅助等电位联结, 局部等电位联结
- ElectricalSystem: SELV系统, PELV系统, FELV系统
- Parameter: 特低电压, 约定接触电压限值, 预期接触电压
- Device: 断路器, 隔离开关, 开关电器, 开关, 隔离电器, 手持设备, 移动设备

【跨章节关系类型】
- 引用: 条款引用了某术语/参数/表格
- 适用于: 某规定适用于某电气系统（TN/TT/IT）
- 执行: 设备执行某保护功能
- 条件: 在某条件下（正常/故障/过载）

请分析以下条款，输出JSON：
{{
  "clause_id": "条款编号",
  "clause_text": "条款原文",
  "referenced_terms": [
    {{"term": "术语名", "relation": "引用|适用于|执行|条件", "detail": "关系描述"}}
  ],
  "new_entities": [
    {{"id": "新实体ID", "type": "Device|Parameter|Clause|Table", "name": "名称", "description": "描述"}}
  ],
  "new_relationships": [
    {{"from": "来源", "type": "关系类型", "to": "目标", "detail": "描述"}}
  ]
}}
"""
    }

    return design


def create_chunking_guidelines():
    """生成各章节的 chunking 指南"""
    print("\n【各章节 Chunk 拆分指南】")

    guidelines = {}

    for chapter, config in CHAPTER_CONFIG.items():
        guidelines[chapter] = {
            "chapter_name": config["name"],
            "chunk_strategy": "按条款编号拆分（建议 chunk_size=600-1000 tokens）",
            "key_entity_types": config["entity_types"],
            "important_terms": config["key_terms"],
            "special_handling": []
        }

        # 第3章特殊处理
        if chapter == "3":
            guidelines[chapter]["special_handling"].append("表格：表3.2.2（导体最小截面）、表3.2.3（敷设条件温度）、表3.2.5（线路敷设条件）、表3.2.9（谐波电流校正系数）")
            guidelines[chapter]["special_handling"].append("公式：公式3.2.14（导体截面计算）")

        # 第5章特殊处理
        elif chapter == "5":
            guidelines[chapter]["special_handling"].append("SELV/PELV/FELV系统定义引用第2章")
            guidelines[chapter]["special_handling"].append("等电位联结系列（总/辅助/局部）引用第2章")
            guidelines[chapter]["special_handling"].append("剩余电流保护器动作电流30mA")

    return guidelines


def generate_incremental_import_script():
    """生成增量导入脚本模板"""
    script = '''
"""
增量导入：第{chapter}章条款 → 追加到现有图谱

使用方法：
1. 先用 MinerU 解析第{chapter}章
2. 运行 mineru_chunking 生成 chunks
3. 运行本脚本进行增量 LLM 提取
4. 运行 merge_graph 合并到主图谱
"""

import json
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from 02_llm_extraction import create_llm_client, build_extraction_prompt, extract_from_chunk, batch_extract
from 03_deduplication import deduplicate_entities, deduplicate_relationships

# ===== 第{chapter}章配置 =====
CHAPTER = "{chapter}"
CHAPTER_NAME = "{chapter_name}"
CHAPTER_CHUNKS_PATH = f"../data/chunks/chapter_{chapter}_chunks.json"
CHAPTER_EXTRACTION_DIR = f"../data/extraction/chapter_{chapter}"
CHAPTER_DEDUP_DIR = f"../data/dedup/chapter_{chapter}"

# 已有关键术语（来自第2章，用于跨章节关联）
EXISTING_TERMS = {existing_terms}

def filter_references(result: dict, existing_terms: set) -> dict:
    """过滤出跨章节引用"""
    new_rels = []
    for rel in result.get("relationships", []):
        if rel.get("to") in existing_terms or rel.get("from") in existing_terms:
            new_rels.append(rel)
    result["relationships"] = new_rels
    return result

def main():
    print("=" * 60)
    print(f"增量导入: 第{chapter}章 - {CHAPTER_NAME}")
    print("=" * 60)

    # ... (类似 02_llm_extraction 的逻辑)
    pass

if __name__ == "__main__":
    main()
'''

    return script


def merge_graphs(main_graph: dict, incremental_graph: dict) -> dict:
    """
    合并增量图谱到主图谱

    Args:
        main_graph: {"entities": [...], "relationships": [...]}
        incremental_graph: {"entities": [...], "relationships": [...]}

    Returns:
        合并后的图谱
    """
    print("\n【合并图谱】")

    # 实体去重合并
    existing_entity_ids = {e["id"] for e in main_graph["entities"]}
    merged_entities = list(main_graph["entities"])

    for entity in incremental_graph["entities"]:
        if entity["id"] not in existing_entity_ids:
            merged_entities.append(entity)
            existing_entity_ids.add(entity["id"])

    print(f"  实体: {len(main_graph['entities'])} + {len(incremental_graph['entities'])} = {len(merged_entities)}")

    # 关系去重合并
    existing_rels = {(r["from"], r["type"], r["to"]) for r in main_graph["relationships"]}
    merged_rels = list(main_graph["relationships"])

    for rel in incremental_graph["relationships"]:
        key = (rel.get("from"), rel.get("type"), rel.get("to"))
        if key not in existing_rels:
            merged_rels.append(rel)
            existing_rels.add(key)

    print(f"  关系: {len(main_graph['relationships'])} + {len(incremental_graph['relationships'])} = {len(merged_rels)}")

    return {
        "entities": merged_entities,
        "relationships": merged_rels
    }


def generate_neo4j_merge_cypher() -> str:
    """生成 Neo4j 增量合并的 Cypher 脚本"""
    cypher = '''
// ============================================
// 增量合并 Cypher 脚本
// 用于将新章节的实体和关系追加到已有图谱
// ============================================

// 1. 导入新实体（不覆盖已有实体）
LOAD CSV WITH HEADERS FROM "file:///chapter_N_entities.csv" AS row
MERGE (e:Entity {id: row.id})
SET e.name = row.name,
    e.entity_type = row.type,
    e.source_clause = row.source_clause,
    e.definition = row.description,
    e.chapter = row.chapter
ON CREATE SET e.created_at = timestamp();

// 2. 导入新关系（不覆盖已有关系）
LOAD CSV WITH HEADERS FROM "file:///chapter_N_relationships.csv" AS row
MATCH (a:Entity {id: row.from_id})
MATCH (b:Entity {id: row.to_id})
CALL apoc.merge.relationship(a, row.rel_type, {}, row.detail, b, {})
YIELD rel
RETURN count(rel) AS merged_count;

// 或者不使用 APOC 的简单版本：
LOAD CSV WITH HEADERS FROM "file:///chapter_N_relationships.csv" AS row
MATCH (a:Entity {id: row.from_id})
MATCH (b:Entity {id: row.to_id})
MERGE (a)-[r:RELATES]->(b)
SET r.detail = row.detail,
    r.source_chunk = row.source_chunk,
    r.chapter = row.chapter;
'''

    return cypher


def main():
    print("=" * 60)
    print("Step 5: 补充其他章节 — 跨章节关联设计")
    print("=" * 60)

    # 1. 设计跨章节关系
    design = design_cross_chapter_relationships()

    print("\n【跨章节关系模板】")
    for template in design["relationship_templates"]:
        print(f"\n  {template['from_type']} --[{template['rel_type']}]--> {template['to_type']}")
        print(f"    说明: {template['description']}")
        print(f"    示例: {template['example']}")

    # 2. 各章节 Chunking 指南
    guidelines = create_chunking_guidelines()

    print("\n【各章节 Chunking 指南】")
    for chapter, guide in guidelines.items():
        print(f"\n第{chapter}章: {guide['chapter_name']}")
        print(f"  策略: {guide['chunk_strategy']}")
        print(f"  重点实体类型: {', '.join(guide['key_entity_types'])}")
        print(f"  重要术语: {', '.join(guide['important_terms'][:5])}")

    # 3. 保存设计文档
    design_path = os.path.join(os.path.dirname(__file__), "../../script", "data", "cross_chapter_design.json")
    os.makedirs(os.path.dirname(design_path), exist_ok=True)

    design_output = {
        "chapter_config": CHAPTER_CONFIG,
        "cross_chapter_relationships": CROSS_CHAPTER_RELATIONSHIPS,
        "guidelines": guidelines,
        "design": design
    }

    with open(design_path, 'w', encoding='utf-8') as f:
        json.dump(design_output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 设计文档已保存到 {design_path}")

    # 4. 生成增量导入脚本模板
    for chapter, config in CHAPTER_CONFIG.items():
        chapter_terms = config.get("key_terms", [])[:10]
        script = generate_incremental_import_script().format(
            chapter=chapter,
            chapter_name=config["name"],
            existing_terms=str(chapter_terms)
        )

        script_path = os.path.join(os.path.dirname(__file__),
                                   f"../../script", "scripts", f"05_incremental_chapter_{chapter}.py")
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(script)

        print(f"✓ 生成增量导入脚本: 05_incremental_chapter_{chapter}.py")

    # 5. 生成 Neo4j 合并 Cypher
    cypher = generate_neo4j_merge_cypher()
    cypher_path = os.path.join(os.path.dirname(__file__), "../../script", "data", "merge_cypher.cql")
    with open(cypher_path, 'w', encoding='utf-8') as f:
        f.write(cypher)

    print(f"✓ 生成 Neo4j 合并 Cypher: merge_cypher.cql")

    print("\n" + "=" * 60)
    print("✓ Step 5 完成！")
    print("\n下一步：")
    print("  1. 用 MinerU 解析第3-7章 PDF")
    print("  2. 运行 scripts/05_incremental_chapter_N.py 逐章导入")
    print("  3. 运行 Neo4j merge_cypher.cql 合并到主图谱")
    print("=" * 60)


if __name__ == "__main__":
    main()
