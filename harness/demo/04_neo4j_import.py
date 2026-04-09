"""
Step 4: 存入 Neo4j 图数据库
将去重后的 entities 和 relationships 导入 Neo4j

支持两种模式:
1. Neo4j 模式: 导入到真实的 Neo4j 数据库
2. Memory 模式: 保存为本地 JSON 文件（无需 Neo4j 环境）

用法:
    # Neo4j 模式（需要安装 neo4j 库）
    python 04_neo4j_import.py --mode neo4j

    # Memory 模式
    python 04_neo4j_import.py --mode memory
"""

import json
import os
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# 可选的 Neo4j 驱动（未安装时 graceful fallback）
try:
    from neo4j import GraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False
    print("⚠ Neo4j 驱动未安装，将使用 Memory 模式")
    print("  安装命令: pip install neo4j")
    print("")


def load_dedup_data(dedup_dir: str) -> tuple[list, list]:
    """加载去重后的数据"""
    entities_path = os.path.join(dedup_dir, "entities.json")
    rels_path = os.path.join(dedup_dir, "relationships.json")

    with open(entities_path, 'r', encoding='utf-8') as f:
        entities = json.load(f)

    with open(rels_path, 'r', encoding='utf-8') as f:
        relationships = json.load(f)

    return entities, relationships


class Neo4jImporter:
    """Neo4j 导入器"""

    def __init__(self, uri: str, user: str, password: str):
        if not NEO4J_AVAILABLE:
            raise RuntimeError("Neo4j 驱动不可用，请安装: pip install neo4j")

        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def create_entities(self, entities: list, clear_existing: bool = True):
        """创建实体节点"""
        print(f"\n【Neo4j】创建 {len(entities)} 个实体节点")

        with self.driver.session() as session:
            # 清除旧数据（可选）
            if clear_existing:
                print("  清除已有数据...")
                session.run("MATCH (n) DETACH DELETE n")

            # 创建实体
            for i, entity in enumerate(entities):
                props = {
                    "id": entity.get("id", ""),
                    "name": entity.get("name", entity.get("id", "")),
                    "type": entity.get("type", ""),
                    "source_clause": entity.get("source_clause", ""),
                    "definition_summary": entity.get("definition_summary", ""),
                    "mention_count": entity.get("mention_count", 1),
                    "all_source_chunks": entity.get("all_source_chunks", []),
                }

                # 过滤空值
                props = {k: v for k, v in props.items() if v}

                cypher = f"""
                    MERGE (e:Entity {{id: $id}})
                    SET e.name = $name,
                        e.entity_type = $type,
                        e.source_clause = $source_clause,
                        e.definition = $definition_summary,
                        e.mention_count = $mention_count
                """

                session.run(cypher, **props)

                if (i + 1) % 10 == 0:
                    print(f"  进度: {i + 1}/{len(entities)}")

        print(f"  ✓ 完成")

    def create_relationships(self, relationships: list):
        """创建关系边"""
        print(f"\n【Neo4j】创建 {len(relationships)} 个关系边")

        with self.driver.session() as session:
            for i, rel in enumerate(relationships):
                from_node = rel.get("from", "")
                to_node = rel.get("to", "")
                rel_type = rel.get("type", "")
                detail = rel.get("detail", "")

                if not all([from_node, to_node, rel_type]):
                    continue

                # 关系类型需要转成 Cypher 兼容格式（无空格，无特殊字符）
                cypher_type = rel_type.upper().replace(" ", "_").replace("-", "_")

                cypher = f"""
                    MATCH (a:Entity {{id: $from_id}})
                    MATCH (b:Entity {{id: $to_id}})
                    MERGE (a)-[r:{cypher_type}]->(b)
                    SET r.detail = $detail,
                        r.source_chunk = $source_chunk
                """

                session.run(cypher,
                           from_id=from_node,
                           to_id=to_node,
                           detail=detail,
                           source_chunk=rel.get("source_chunk_id", ""))

                if (i + 1) % 10 == 0:
                    print(f"  进度: {i + 1}/{len(relationships)}")

        print(f"  ✓ 完成")

    def create_indexes(self):
        """创建索引"""
        print("\n【Neo4j】创建索引")

        with self.driver.session() as session:
            # 实体主键索引
            session.run("CREATE INDEX entity_id IF NOT EXISTS FOR (e:Entity) ON (e.id)")

            # 实体类型索引
            session.run("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.entity_type)")

            print("  ✓ 索引创建完成")

    def get_stats(self) -> dict:
        """获取图谱统计"""
        with self.driver.session() as session:
            node_count = session.run("MATCH (e:Entity) RETURN count(e) as count").single()["count"]
            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) as count").single()["count"]

            # 按类型统计实体
            type_result = session.run("""
                MATCH (e:Entity)
                RETURN e.entity_type as type, count(e) as count
                ORDER BY count DESC
            """)
            entity_types = {r["type"]: r["count"] for r in type_result}

            # 按类型统计关系
            rel_type_result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as type, count(r) as count
                ORDER BY count DESC
            """)
            rel_types = {r["type"]: r["count"] for r in rel_type_result}

            return {
                "node_count": node_count,
                "rel_count": rel_count,
                "entity_types": entity_types,
                "rel_types": rel_types
            }


class MemoryImporter:
    """内存/文件导入器（无需 Neo4j 环境）"""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.entities = []
        self.relationships = []
        self.graph = defaultdict(list)  # 邻接表

    def import_data(self, entities: list, relationships: list):
        """导入数据"""
        print(f"\n【Memory】导入数据")
        print(f"  实体: {len(entities)}")
        print(f"  关系: {len(relationships)}")

        self.entities = entities
        self.relationships = relationships

        # 构建邻接表
        for rel in relationships:
            from_node = rel.get("from", "")
            to_node = rel.get("to", "")
            rel_type = rel.get("type", "")
            if from_node and to_node:
                self.graph[from_node].append({
                    "to": to_node,
                    "type": rel_type,
                    "detail": rel.get("detail", "")
                })

        print("  ✓ 内存图谱构建完成")

    def query(self, entity_id: str, hops: int = 1) -> dict:
        """
        查询实体的 N 跳邻居

        Args:
            entity_id: 实体ID
            hops: 跳数

        Returns:
            {"entity": {...}, "neighbors": {...}}
        """
        # 找到实体
        entity = None
        for e in self.entities:
            if e.get("id") == entity_id:
                entity = e
                break

        if not entity:
            return None

        # BFS 找邻居
        visited = {entity_id}
        current_level = {entity_id}
        all_neighbors = defaultdict(list)

        for _ in range(hops):
            next_level = set()
            for node_id in current_level:
                for neighbor in self.graph.get(node_id, []):
                    neighbor_id = neighbor["to"]
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        next_level.add(neighbor_id)
                        all_neighbors[neighbor_id].append(neighbor)
            current_level = next_level

        # 填充邻居实体详情
        neighbors_detail = []
        for nid, rels in all_neighbors.items():
            for e in self.entities:
                if e.get("id") == nid:
                    neighbors_detail.append({
                        "entity": e,
                        "relations": rels
                    })
                    break

        return {
            "entity": entity,
            "neighbors": neighbors_detail,
            "total_neighbors": len(neighbors_detail)
        }

    def save(self):
        """保存到文件"""
        os.makedirs(self.output_dir, exist_ok=True)

        # 保存实体
        entities_path = os.path.join(self.output_dir, "memory_entities.json")
        with open(entities_path, 'w', encoding='utf-8') as f:
            json.dump(self.entities, f, ensure_ascii=False, indent=2)
        print(f"  ✓ 保存实体到 {entities_path}")

        # 保存关系
        rels_path = os.path.join(self.output_dir, "memory_relationships.json")
        with open(rels_path, 'w', encoding='utf-8') as f:
            json.dump(self.relationships, f, ensure_ascii=False, indent=2)
        print(f"  ✓ 保存关系到 {rels_path}")

        # 保存邻接表
        graph_path = os.path.join(self.output_dir, "memory_graph.json")
        graph_data = {k: list(v) for k, v in self.graph.items()}
        with open(graph_path, 'w', encoding='utf-8') as f:
            json.dump(graph_data, f, ensure_ascii=False, indent=2)
        print(f"  ✓ 保存邻接表到 {graph_path}")

    def get_stats(self) -> dict:
        """获取统计"""
        entity_types = defaultdict(int)
        for e in self.entities:
            entity_types[e.get("type", "Unknown")] += 1

        rel_types = defaultdict(int)
        for r in self.relationships:
            rel_types[r.get("type", "Unknown")] += 1

        return {
            "node_count": len(self.entities),
            "rel_count": len(self.relationships),
            "entity_types": dict(entity_types),
            "rel_types": dict(rel_types)
        }


def main():
    parser = argparse.ArgumentParser(description="导入图谱数据到 Neo4j 或内存")
    parser.add_argument("--mode", choices=["neo4j", "memory"], default="memory",
                       help="导入模式: neo4j 或 memory (默认: memory)")
    parser.add_argument("--uri", default="bolt://localhost:7687",
                       help="Neo4j URI (默认: bolt://localhost:7687)")
    parser.add_argument("--user", default="neo4j",
                       help="Neo4j 用户名 (默认: neo4j)")
    parser.add_argument("--password", default="password",
                       help="Neo4j 密码 (默认: password)")
    parser.add_argument("--clear", action="store_true", default=True,
                       help="清除已有数据 (默认: True)")

    args = parser.parse_args()

    # ===== 配置 =====
    DEDUP_DIR = os.path.join(os.path.dirname(__file__), "../../script", "data", "dedup")

    print("=" * 60)
    print("Step 4: 存入图数据库")
    print(f"模式: {args.mode.upper()}")
    print("=" * 60)

    # 加载数据
    if not os.path.exists(DEDUP_DIR):
        print(f"✗ 目录不存在: {DEDUP_DIR}")
        print("请先运行 Step 3 生成去重数据")
        return

    entities, relationships = load_dedup_data(DEDUP_DIR)
    print(f"  加载实体: {len(entities)}")
    print(f"  加载关系: {len(relationships)}")

    if args.mode == "neo4j":
        if not NEO4J_AVAILABLE:
            print("✗ Neo4j 模式需要安装 neo4j 驱动")
            print("  pip install neo4j")
            return

        # Neo4j 导入
        importer = Neo4jImporter(args.uri, args.user, args.password)

        try:
            importer.create_indexes()
            importer.create_entities(entities, clear_existing=args.clear)
            importer.create_relationships(relationships)

            stats = importer.get_stats()
            print("\n" + "=" * 60)
            print("导入完成 - 统计")
            print("=" * 60)
            print(f"  节点数: {stats['node_count']}")
            print(f"  边数: {stats['rel_count']}")
            print(f"\n  实体类型分布:")
            for t, c in sorted(stats['entity_types'].items()):
                print(f"    {t}: {c}")
            print(f"\n  关系类型分布:")
            for t, c in sorted(stats['rel_types'].items()):
                print(f"    {t}: {c}")

        finally:
            importer.close()

    else:
        # Memory 导入
        MEMORY_OUTPUT = os.path.join(os.path.dirname(__file__), "../../script", "data", "memory")
        importer = MemoryImporter(MEMORY_OUTPUT)
        importer.import_data(entities, relationships)
        importer.save()

        stats = importer.get_stats()
        print("\n" + "=" * 60)
        print("导入完成 - 统计")
        print("=" * 60)
        print(f"  节点数: {stats['node_count']}")
        print(f"  边数: {stats['rel_count']}")
        print(f"\n  实体类型分布:")
        for t, c in sorted(stats['entity_types'].items()):
            print(f"    {t}: {c}")
        print(f"\n  关系类型分布:")
        for t, c in sorted(stats['rel_types'].items()):
            print(f"    {t}: {c}")

        # 示例查询
        print("\n" + "=" * 60)
        print("示例查询: SELV系统 的 2 跳邻居")
        print("=" * 60)
        result = importer.query("SELV系统", hops=2)
        if result:
            print(f"\n  实体: {result['entity']['name']} ({result['entity']['type']})")
            print(f"  定义: {result['entity'].get('definition_summary', 'N/A')}")
            print(f"  邻居数: {result['total_neighbors']}")
            for neighbor in result['neighbors'][:5]:
                e = neighbor['entity']
                rels = neighbor['relations']
                rel_str = ", ".join([f"{r['type']}→{r['to']}" for r in rels])
                print(f"    → {e['name']} ({e['type']}) [{rel_str}]")
        else:
            print("  未找到该实体")

    print("\n" + "=" * 60)
    print("✓ Step 4 完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
