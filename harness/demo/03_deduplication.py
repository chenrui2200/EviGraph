"""
Step 3: 合并去重
去除重复的实体和关系，生成干净的图谱数据

用法:
    python 03_deduplication.py
"""

import json
import os
from pathlib import Path
from collections import defaultdict


def load_raw_data(raw_dir: str) -> tuple[list, list]:
    """加载原始提取数据"""
    entities_path = os.path.join(raw_dir, "raw_entities.json")
    rels_path = os.path.join(raw_dir, "raw_relationships.json")

    with open(entities_path, 'r', encoding='utf-8') as f:
        entities = json.load(f)

    with open(rels_path, 'r', encoding='utf-8') as f:
        relationships = json.load(f)

    return entities, relationships


def deduplicate_entities(entities: list) -> list[dict]:
    """
    实体去重

    策略：
    1. 按 entity.id 分组
    2. 同 id 取定义最长的
    3. 保留首次出现的 type 和 source_chunk_id
    """
    print("\n【实体去重】")

    # 按 id 分组
    grouped = defaultdict(list)
    for entity in entities:
        entity_id = entity.get("id", "")
        if entity_id:
            grouped[entity_id].append(entity)

    print(f"  原始实体数: {len(entities)}")
    print(f"  不同 entity.id 数: {len(grouped)}")

    deduped = []
    duplicates_removed = 0

    for entity_id, entity_list in grouped.items():
        if len(entity_list) > 1:
            # 选择定义最长的
            best = max(entity_list, key=lambda e: len(e.get("definition_summary", "")))
            duplicates_removed += len(entity_list) - 1
        else:
            best = entity_list[0]

        # 添加来源信息（合并所有来源）
        all_sources = list(set(e.get("source_chunk_id", "") for e in entity_list))
        best["all_source_chunks"] = [s for s in all_sources if s]
        best["mention_count"] = len(entity_list)

        deduped.append(best)

    print(f"  去重后实体数: {len(deduped)}")
    print(f"  移除重复: {duplicates_removed}")

    return deduped


def deduplicate_relationships(relationships: list) -> list[dict]:
    """
    关系去重

    策略：
    1. 按 (from, type, to) 分组
    2. 同组取 detail 最长的
    3. 保留首次出现的 source_chunk_id
    """
    print("\n【关系去重】")

    # 按 (from, type, to) 分组
    grouped = defaultdict(list)
    for rel in relationships:
        key = (
            rel.get("from", ""),
            rel.get("type", ""),
            rel.get("to", "")
        )
        if key[0] and key[2]:  # 确保 from 和 to 都不为空
            grouped[key].append(rel)

    print(f"  原始关系数: {len(relationships)}")
    print(f"  不同 (from, type, to) 组合数: {len(grouped)}")

    deduped = []
    duplicates_removed = 0

    for key, rel_list in grouped.items():
        if len(rel_list) > 1:
            # 选择 detail 最长的
            best = max(rel_list, key=lambda r: len(r.get("detail", "")))
            duplicates_removed += len(rel_list) - 1
        else:
            best = rel_list[0]

        # 添加来源信息
        all_sources = list(set(r.get("source_chunk_id", "") for r in rel_list))
        best["all_source_chunks"] = [s for s in all_sources if s]
        best["mention_count"] = len(rel_list)

        deduped.append(best)

    print(f"  去重后关系数: {len(deduped)}")
    print(f"  移除重复: {duplicates_removed}")

    return deduped


def detect_cycles(relationships: list, entity_ids: set) -> list[list]:
    """
    检测循环关系 (A→B→C→A)

    使用简单的 DFS 检测
    """
    print("\n【循环检测】")

    # 构建邻接表
    graph = defaultdict(set)
    for rel in relationships:
        graph[rel["from"]].add(rel["to"])

    cycles = []

    def dfs(node: str, path: list, visited: set):
        if node in visited:
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            cycles.append(cycle)
            return

        visited.add(node)
        path.append(node)

        for neighbor in graph[node]:
            if neighbor in entity_ids:
                dfs(neighbor, path.copy(), visited)

        visited.remove(node)

    for entity_id in entity_ids:
        dfs(entity_id, [], set())

    print(f"  检测到 {len(cycles)} 个循环关系")
    for cycle in cycles:
        print(f"    循环: {' → '.join(cycle)}")

    return cycles


def detect_self_loops(relationships: list) -> list[dict]:
    """检测自环 (A→A)"""
    print("\n【自环检测】")

    self_loops = [r for r in relationships if r.get("from") == r.get("to")]

    if self_loops:
        print(f"  发现 {len(self_loops)} 个自环:")
        for rel in self_loops:
            print(f"    {rel['from']} → {rel['to']} ({rel['type']})")
    else:
        print(f"  无自环")

    return self_loops


def validate_references(entities: list, relationships: list) -> dict:
    """
    验证关系引用的实体是否存在
    返回: {"valid": [...], "broken": [...]}
    """
    print("\n【引用验证】")

    entity_ids = {e["id"] for e in entities}
    valid = []
    broken = []

    for rel in relationships:
        from_id = rel.get("from", "")
        to_id = rel.get("to", "")

        from_ok = from_id in entity_ids
        to_ok = to_id in entity_ids

        if from_ok and to_ok:
            valid.append(rel)
        else:
            broken.append({
                "relationship": rel,
                "broken_from": from_id if not from_ok else None,
                "broken_to": to_id if not to_ok else None
            })

    print(f"  有效关系: {len(valid)}")
    print(f"  孤立引用（实体不存在）: {len(broken)}")

    if broken:
        for b in broken[:5]:  # 只显示前5个
            r = b["relationship"]
            if b["broken_from"]:
                print(f"    ⚠ {r['from']} ({r['type']}) → {r['to']} - 来源实体不存在: {b['broken_from']}")
            if b["broken_to"]:
                print(f"    ⚠ {r['from']} ({r['type']}) → {r['to']} - 目标实体不存在: {b['broken_to']}")

    return {"valid": valid, "broken": broken}


def main():
    # ===== 配置 =====
    RAW_DIR = os.path.join(os.path.dirname(__file__), "../../script", "data", "extraction")
    OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "../../script", "data", "dedup")

    # ===== Step 1: 加载原始数据 =====
    print("=" * 60)
    print("Step 3: 合并去重")
    print("=" * 60)

    if not os.path.exists(RAW_DIR):
        print(f"✗ 目录不存在: {RAW_DIR}")
        print("请先运行 Step 2 生成原始提取数据")
        return

    entities, relationships = load_raw_data(RAW_DIR)
    print(f"  加载实体: {len(entities)}")
    print(f"  加载关系: {len(relationships)}")

    # ===== Step 2: 实体去重 =====
    deduped_entities = deduplicate_entities(entities)

    # ===== Step 3: 关系去重 =====
    deduped_relationships = deduplicate_relationships(relationships)

    # ===== Step 4: 异常检测 =====
    entity_ids = {e["id"] for e in deduped_entities}
    cycles = detect_cycles(deduped_relationships, entity_ids)
    self_loops = detect_self_loops(deduped_relationships)
    ref_check = validate_references(deduped_entities, deduped_relationships)

    # 过滤掉孤立引用的关系
    valid_rel_ids = {(r["from"], r["type"], r["to"]) for r in ref_check["valid"]}
    deduped_relationships = [r for r in deduped_relationships
                            if (r["from"], r["type"], r["to"]) in valid_rel_ids]

    # ===== Step 5: 保存 =====
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    entities_path = os.path.join(OUTPUT_DIR, "entities.json")
    rels_path = os.path.join(OUTPUT_DIR, "relationships.json")
    summary_path = os.path.join(OUTPUT_DIR, "summary.json")

    with open(entities_path, 'w', encoding='utf-8') as f:
        json.dump(deduped_entities, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 保存 {len(deduped_entities)} 个去重实体到 {entities_path}")

    with open(rels_path, 'w', encoding='utf-8') as f:
        json.dump(deduped_relationships, f, ensure_ascii=False, indent=2)
    print(f"✓ 保存 {len(deduped_relationships)} 个去重关系到 {rels_path}")

    # ===== Step 6: 生成摘要 =====
    entity_types = defaultdict(int)
    for e in deduped_entities:
        entity_types[e.get("type", "Unknown")] += 1

    rel_types = defaultdict(int)
    for r in deduped_relationships:
        rel_types[r.get("type", "Unknown")] += 1

    summary = {
        "total_entities": len(deduped_entities),
        "total_relationships": len(deduped_relationships),
        "entity_types": dict(entity_types),
        "relationship_types": dict(rel_types),
        "cycles_detected": len(cycles),
        "self_loops_detected": len(self_loops),
        "broken_references": len(ref_check["broken"]),
        "extraction_source": "GB50054-2011 第2章 术语 (2.0.1-2.0.37)"
    }

    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # ===== Step 7: 打印摘要 =====
    print("\n" + "=" * 60)
    print("去重完成 - 摘要")
    print("=" * 60)
    print(f"  实体总数: {summary['total_entities']}")
    print(f"  关系总数: {summary['total_relationships']}")
    print(f"\n  实体类型分布:")
    for t, count in sorted(summary['entity_types'].items()):
        print(f"    {t}: {count}")
    print(f"\n  关系类型分布:")
    for t, count in sorted(summary['relationship_types'].items()):
        print(f"    {t}: {count}")
    print(f"\n  异常检测:")
    print(f"    循环关系: {summary['cycles_detected']}")
    print(f"    自环: {summary['self_loops_detected']}")
    print(f"    孤立引用: {summary['broken_references']}")

    print("\n" + "=" * 60)
    print("✓ Step 3 完成！去重后的数据已保存到 data/dedup/")
    print("=" * 60)


if __name__ == "__main__":
    main()
