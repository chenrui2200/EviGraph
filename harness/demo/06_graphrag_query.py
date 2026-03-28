"""
GraphRAG Query Interface — 图谱增强检索界面
基于已构建的知识图谱，支持多跳推理检索

用法:
    python 06_graphrag_query.py --query "TN系统下如何选择过电流保护？"
"""

import json
import os
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# 导入内存图谱
sys.path.insert(0, os.path.dirname(__file__))
from 04_neo4j_import import MemoryImporter


# ===== GraphRAG 查询模板 =====

QUERY_TEMPLATES = {
    "术语定义查询": {
        "template": "解释一下{term}的定义",
        "graph_query": "直接返回术语节点的 definition 字段",
        "example": "解释一下 SELV系统"
    },
    "多跳关系查询": {
        "template": "{entity}和{entity2}之间有什么关系？",
        "graph_query": "找两实体间的最短路径",
        "example": "特低电压和SELV系统有什么关系？"
    },
    "保护措施适用系统查询": {
        "template": "哪些系统需要使用{protection}？",
        "graph_query": "找保护措施关联的电气系统",
        "example": "哪些系统需要使用间接接触防护？"
    },
    "设备功能查询": {
        "template": "{device}有什么保护功能？",
        "graph_query": "找设备执行的所有保护功能",
        "example": "剩余电流保护器有什么保护功能？"
    },
    "条款引用查询": {
        "template": "条款{clause}引用了哪些术语？",
        "graph_query": "返回条款引用的所有术语实体",
        "example": "条款5.2.1引用了哪些术语？"
    },
    "参数约束查询": {
        "template": "{parameter}的限制是多少？",
        "graph_query": "返回参数的定义和约束值",
        "example": "约定接触电压限值的限制是多少？"
    }
}


class GraphRAGQuerier:
    """GraphRAG 查询器"""

    def __init__(self, memory_importer: MemoryImporter):
        self.graph = memory_importer
        self.entities = {e["id"]: e for e in memory_importer.entities}
        self.entity_index = self._build_entity_index()

    def _build_entity_index(self) -> dict:
        """构建实体名称索引（支持模糊匹配）"""
        index = defaultdict(list)

        for entity in self.entities.values():
            name = entity.get("name", "")
            entity_id = entity.get("id", "")

            # 按名称分词索引
            for char in range(len(name)):
                for length in range(1, len(name) - char + 1):
                    key = name[char:char + length]
                    if len(key) >= 2:  # 至少2个字符
                        index[key].append(entity_id)

            # 原始名称索引
            index[name].append(entity_id)

        return index

    def find_entity(self, query: str) -> list:
        """根据查询字符串找到匹配的实体"""
        query = query.strip()

        # 精确匹配
        if query in self.entities:
            return [self.entities[query]]

        # 模糊匹配
        candidates = set()
        for key, entity_ids in self.entity_index.items():
            if key in query or query in key:
                candidates.update(entity_ids)

        results = []
        for eid in candidates:
            results.append(self.entities[eid])

        return results[:5]  # 最多返回5个

    def query_definition(self, entity_name: str) -> dict:
        """查询术语定义"""
        results = self.find_entity(entity_name)

        if not results:
            return {"found": False, "query": entity_name}

        entity = results[0]
        return {
            "found": True,
            "entity": entity,
            "definition": entity.get("definition_summary", "无定义"),
            "source_clause": entity.get("source_clause", "未知"),
            "type": entity.get("type", "Unknown")
        }

    def query_neighbors(self, entity_name: str, hops: int = 2) -> dict:
        """查询实体的 N 跳邻居"""
        results = self.find_entity(entity_name)

        if not results:
            return {"found": False, "query": entity_name}

        entity = results[0]
        entity_id = entity.get("id", "")

        # 使用 MemoryImporter 的查询
        graph_result = self.graph.query(entity_id, hops=hops)

        if not graph_result:
            return {"found": True, "entity": entity, "neighbors": []}

        # 格式化输出
        formatted_neighbors = []
        for neighbor in graph_result.get("neighbors", []):
            e = neighbor["entity"]
            rels = neighbor["relations"]
            rel_descriptions = []

            for r in rels:
                direction = "→" if r["type"] == "定义" else "—"
                rel_descriptions.append(
                    f"{r['type']}{direction}{r['to']} ({r.get('detail', '')})"
                )

            formatted_neighbors.append({
                "entity": e,
                "relationships": rel_descriptions,
                "total_relations": len(rels)
            })

        # 按关系数排序
        formatted_neighbors.sort(key=lambda x: x["total_relations"], reverse=True)

        return {
            "found": True,
            "entity": entity,
            "neighbors": formatted_neighbors,
            "total_neighbors": graph_result.get("total_neighbors", 0)
        }

    def query_path(self, entity_a: str, entity_b: str) -> dict:
        """查询两实体间的最短路径"""
        results_a = self.find_entity(entity_a)
        results_b = self.find_entity(entity_b)

        if not results_a:
            return {"found": False, "error": f"未找到实体: {entity_a}"}
        if not results_b:
            return {"found": False, "error": f"未找到实体: {entity_b}"}

        entity_id_a = results_a[0].get("id", "")
        entity_id_b = results_b[0].get("id", "")

        # BFS 找最短路径
        if entity_id_a == entity_id_b:
            return {
                "found": True,
                "path": [entity_id_a],
                "entities": [results_a[0]]
            }

        visited = {entity_id_a}
        queue = [(entity_id_a, [entity_id_a])]

        while queue:
            current, path = queue.pop(0)

            for neighbor in self.graph.graph.get(current, []):
                neighbor_id = neighbor.get("to", "")
                if neighbor_id == entity_id_b:
                    full_path = path + [neighbor_id]
                    path_entities = [self.entities.get(eid, {}) for eid in full_path]
                    return {
                        "found": True,
                        "path": full_path,
                        "entities": path_entities,
                        "path_relations": [neighbor.get("type", "") for neighbor in path]
                    }

                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, path + [neighbor_id]))

        return {
            "found": False,
            "error": f"未找到 {entity_a} 到 {entity_b} 的路径"
        }

    def query_by_type(self, entity_type: str, limit: int = 10) -> list:
        """按类型查询实体"""
        results = [
            e for e in self.entities.values()
            if e.get("type") == entity_type
        ][:limit]

        return results

    def auto_query(self, query: str) -> dict:
        """
        自动分析查询类型并执行

        规则：
        1. 包含"解释"、"定义"、"是什么" → 定义查询
        2. 包含"关系"、"联系"、"什么关系" → 路径查询
        3. 包含"哪些"、"适用"、"用于" → 邻居查询
        4. 其他 → 模糊搜索
        """
        query = query.strip()

        # 规则1: 定义查询
        if any(k in query for k in ["解释", "定义", "是什么", "什么叫"]):
            # 提取术语名（去掉前面的动词）
            term = query.replace("解释一下", "").replace("解释", "").replace("定义", "").replace("是什么", "").replace("什么叫", "").strip()
            return self.query_definition(term)

        # 规则2: 路径查询
        if any(k in query for k in ["关系", "联系", "什么关系", "和", "与"]):
            parts = query.replace("什么关系", "").replace("和", " ").replace("与", " ").split()
            if len(parts) >= 2:
                entity_a = parts[0]
                entity_b = parts[-1]
                return self.query_path(entity_a, entity_b)

        # 规则3: 邻居查询
        if any(k in query for k in ["哪些", "适用", "用于", "有什么", "功能"]):
            # 简单处理：返回查询词的匹配实体邻居
            return self.query_neighbors(query)

        # 默认: 模糊搜索
        results = self.find_entity(query)
        if results:
            return {
                "found": True,
                "type": "search",
                "query": query,
                "results": results[:5]
            }

        return {
            "found": False,
            "query": query,
            "error": "未找到匹配的实体"
        }


def format_result(result: dict, query: str) -> str:
    """格式化查询结果为可读文本"""
    if not result.get("found", False):
        return f"❓ 未找到: {result.get('query', query)}\n\n{result.get('error', '')}"

    output = []

    if result.get("type") == "search":
        output.append(f"🔍 搜索: {result['query']}")
        output.append(f"找到 {len(result['results'])} 个相关实体:\n")
        for e in result["results"]:
            output.append(f"  • {e.get('name', e.get('id'))} ({e.get('type', 'Unknown')})")
        return "\n".join(output)

    entity = result.get("entity", {})
    entity_name = entity.get("name", entity.get("id", ""))

    output.append(f"📖 查询: {query}")
    output.append(f"\n{'='*50}")
    output.append(f"实体: {entity_name}")
    output.append(f"类型: {entity.get('type', 'Unknown')}")
    output.append(f"来源条款: {entity.get('source_clause', '未知')}")

    if "definition" in result:
        output.append(f"\n定义: {result['definition']}")

    if "neighbors" in result:
        neighbors = result["neighbors"]
        output.append(f"\n相关实体 ({result['total_neighbors']} 个):")

        for neighbor in neighbors[:10]:
            e = neighbor["entity"]
            rels = neighbor["relationships"]
            output.append(f"\n  → {e.get('name', e.get('id'))} ({e.get('type', '')})")
            for rel in rels[:3]:
                output.append(f"     {rel}")

    if "path" in result:
        path = result["path"]
        output.append(f"\n🔗 路径: {' → '.join(path)}")

    if "entities" in result and "path" in result:
        output.append("\n路径详情:")
        for i, e in enumerate(result["entities"]):
            if i > 0:
                rel_type = result.get("path_relations", [{}])[i - 1] if i - 1 < len(result.get("path_relations", [])) else ""
                output.append(f"  {i}. {e.get('name', e.get('id'))} [{rel_type}]")
            else:
                output.append(f"  {i}. {e.get('name', e.get('id'))}")

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description="GraphRAG 查询界面")
    parser.add_argument("--query", "-q", type=str,
                       help="查询内容")
    parser.add_argument("--interactive", "-i", action="store_true",
                       help="交互模式")
    parser.add_argument("--entity", "-e", type=str,
                       help="查询实体定义")
    parser.add_argument("--neighbors", "-n", type=str,
                       help="查询实体的邻居")
    parser.add_argument("--hops", type=int, default=2,
                       help="邻居跳数 (默认: 2)")
    parser.add_argument("--type", "-t", type=str,
                       help="按类型查询实体")
    parser.add_argument("--path", nargs=2, metavar=("A", "B"),
                       help="查询两实体间的路径")

    args = parser.parse_args()

    # ===== 加载图谱 =====
    MEMORY_DIR = os.path.join(os.path.dirname(__file__), "../../script", "data", "memory")

    print("=" * 60)
    print("GraphRAG 查询界面")
    print("=" * 60)

    if not os.path.exists(MEMORY_DIR):
        print(f"✗ 图谱数据不存在: {MEMORY_DIR}")
        print("请先运行 scripts/01-04 构建图谱")
        return

    # 加载数据
    with open(os.path.join(MEMORY_DIR, "memory_entities.json"), 'r', encoding='utf-8') as f:
        entities = json.load(f)
    with open(os.path.join(MEMORY_DIR, "memory_relationships.json"), 'r', encoding='utf-8') as f:
        relationships = json.load(f)

    importer = MemoryImporter(MEMORY_DIR)
    importer.import_data(entities, relationships)
    querier = GraphRAGQuerier(importer)

    stats = importer.get_stats()
    print(f"\n📊 图谱统计:")
    print(f"   实体: {stats['node_count']}")
    print(f"   关系: {stats['rel_count']}")
    print(f"   类型: {', '.join(stats['entity_types'].keys())}")

    # ===== 处理查询 =====
    if args.entity:
        result = querier.query_definition(args.entity)
        print("\n" + format_result(result, args.entity))

    elif args.neighbors:
        result = querier.query_neighbors(args.neighbors, hops=args.hops)
        print("\n" + format_result(result, args.neighbors))

    elif args.type:
        results = querier.query_by_type(args.type)
        print(f"\n📂 {args.type} 类型实体 ({len(results)} 个):")
        for e in results:
            print(f"   • {e.get('name', e.get('id'))}")

    elif args.path:
        entity_a, entity_b = args.path
        result = querier.query_path(entity_a, entity_b)
        print("\n" + format_result(result, f"{entity_a} → {entity_b}"))

    elif args.query:
        result = querier.auto_query(args.query)
        print("\n" + format_result(result, args.query))

    else:
        # 交互模式
        print("\n💬 交互模式 (输入 'quit' 退出)")
        print("示例查询:")
        print("  • 解释一下 SELV系统")
        print("  • 特低电压和SELV系统有什么关系")
        print("  • 哪些系统需要使用间接接触防护")
        print("  • 剩余电流保护器有什么保护功能")

        while True:
            try:
                query = input("\n> ").strip()
                if query.lower() in ["quit", "exit", "q"]:
                    break
                if not query:
                    continue

                result = querier.auto_query(query)
                print("\n" + format_result(result, query))

            except (KeyboardInterrupt, EOFError):
                break

    print("\n" + "=" * 60)
    print("✓ 查询完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
