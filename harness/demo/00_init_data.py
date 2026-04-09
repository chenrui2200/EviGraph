"""
初始化演示数据
将 demo 数据复制到 dedup 目录，用于演示 pipeline

用法:
    python 00_init_data.py
"""

import json
import os
import shutil

def main():
    script_dir = os.path.dirname(__file__)
    demo_dir = os.path.join(script_dir, "../../script", "data", "extraction")
    dedup_dir = os.path.join(script_dir, "../../script", "data", "dedup")
    memory_dir = os.path.join(script_dir, "../../script", "data", "memory")

    print("=" * 60)
    print("初始化演示数据")
    print("=" * 60)

    # 复制 demo entities
    demo_entities_path = os.path.join(demo_dir, "demo_entities.json")
    demo_rels_path = os.path.join(demo_dir, "demo_relationships.json")

    if os.path.exists(demo_entities_path):
        # 创建 dedup 目录
        os.makedirs(dedup_dir, exist_ok=True)

        # 复制到 dedup
        with open(demo_entities_path, 'r', encoding='utf-8') as f:
            entities = json.load(f)
        with open(os.path.join(dedup_dir, "entities.json"), 'w', encoding='utf-8') as f:
            json.dump(entities, f, ensure_ascii=False, indent=2)
        print(f"✓ 复制实体到 {dedup_dir}/entities.json ({len(entities)} 个)")

    if os.path.exists(demo_rels_path):
        with open(demo_rels_path, 'r', encoding='utf-8') as f:
            relationships = json.load(f)
        with open(os.path.join(dedup_dir, "relationships.json"), 'w', encoding='utf-8') as f:
            json.dump(relationships, f, ensure_ascii=False, indent=2)
        print(f"✓ 复制关系到 {dedup_dir}/relationships.json ({len(relationships)} 个)")

    # 生成 summary
    entity_types = {}
    for e in entities:
        t = e.get("type", "Unknown")
        entity_types[t] = entity_types.get(t, 0) + 1

    rel_types = {}
    for r in relationships:
        t = r.get("type", "Unknown")
        rel_types[t] = rel_types.get(t, 0) + 1

    summary = {
        "total_entities": len(entities),
        "total_relationships": len(relationships),
        "entity_types": entity_types,
        "relationship_types": rel_types,
        "cycles_detected": 0,
        "self_loops_detected": 0,
        "broken_references": 0,
        "extraction_source": "GB50054-2011 第2章 术语 (演示数据)"
    }

    with open(os.path.join(dedup_dir, "summary.json"), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"✓ 生成 summary.json")

    # 创建 memory 目录数据
    os.makedirs(memory_dir, exist_ok=True)

    # 邻接表
    graph = {}
    for rel in relationships:
        from_id = rel.get("from", "")
        to_id = rel.get("to", "")
        if from_id and to_id:
            if from_id not in graph:
                graph[from_id] = []
            graph[from_id].append({
                "to": to_id,
                "type": rel.get("type", ""),
                "detail": rel.get("detail", "")
            })

    with open(os.path.join(memory_dir, "memory_entities.json"), 'w', encoding='utf-8') as f:
        json.dump(entities, f, ensure_ascii=False, indent=2)
    with open(os.path.join(memory_dir, "memory_relationships.json"), 'w', encoding='utf-8') as f:
        json.dump(relationships, f, ensure_ascii=False, indent=2)
    with open(os.path.join(memory_dir, "memory_graph.json"), 'w', encoding='utf-8') as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    print(f"✓ 生成 memory 图谱数据 ({len(entities)} 实体, {len(relationships)} 关系)")

    print("\n" + "=" * 60)
    print("✓ 演示数据初始化完成！")
    print("\n可以运行以下命令测试 GraphRAG 查询:")
    print("  python 06_graphrag_query.py --interactive")
    print("=" * 60)


if __name__ == "__main__":
    main()
