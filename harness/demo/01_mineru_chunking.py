"""
Step 1: MinerU 输出解析与 Chunk 清洗
将 MinerU 的 content 列表转换为独立的术语 chunks

用法:
    python 01_mineru_chunking.py
"""

import json
import re
import os
from pathlib import Path


def parse_mineru_content(content_list: list) -> list[dict]:
    """
    将 MinerU content 列表解析为独立术语 chunks
    """
    chunks = []

    for item in content_list:
        if item.get("type") != "text":
            continue

        text = item.get("text", "").strip()
        page_idx = item.get("page_idx", 0)

        if not text:
            continue

        # 跳过纯标题行（如 "2 术语"）
        if re.match(r'^\d+\s+[术语章节]', text):
            continue

        # 提取条款编号和内容
        clause_pattern = r'^(\d+\.\d+)\s+(.+?)(?=\s{2,}|(?=\d+\.\d+\s)|$)'
        matches = re.findall(clause_pattern, text)

        if matches:
            for clause_id, rest in matches:
                parts = rest.split('\n')

                if len(parts) >= 1:
                    first_part = parts[0].strip()
                    definition_match = re.match(r'^(.{2,15}?)\s+(.+)$', first_part)

                    if definition_match:
                        term_name = definition_match.group(1).strip()
                        definition = definition_match.group(2).strip()
                    else:
                        term_name = first_part[:15].strip()
                        definition = first_part[15:].strip()

                    if len(parts) > 1:
                        definition += ' '.join(parts[1:])

                    chunks.append({
                        "chunk_id": clause_id,
                        "term_name": term_name.strip(),
                        "definition": definition.strip(),
                        "page_idx": page_idx,
                        "section": "第2章 术语"
                    })

    # 合并同一 chunk_id 的跨页内容
    merged = {}
    for chunk in chunks:
        cid = chunk["chunk_id"]
        if cid in merged:
            merged[cid]["definition"] += chunk["definition"]
        else:
            merged[cid] = chunk

    return sorted(list(merged.values()), key=lambda x: x['chunk_id'])


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)

    MINERU_OUTPUT = os.path.join(project_dir, "data", "mineru_output.json")
    OUTPUT_DIR = os.path.join(project_dir, "data", "chunks")
    OUTPUT_PATH = os.path.join(OUTPUT_DIR, "terms_chunks.json")

    print("=" * 60)
    print("Step 1: MinerU 输出解析与 Chunk 清洗")
    print("=" * 60)

    if not os.path.exists(MINERU_OUTPUT):
        print(f"✗ 文件不存在: {MINERU_OUTPUT}")
        print("请先将 MinerU 输出保存到该路径")
        return

    with open(MINERU_OUTPUT, 'r', encoding='utf-8') as f:
        mineru_data = json.load(f)

    content_list = mineru_data.get("content", [])
    print(f"  输入: {len(content_list)} 个 content 元素")

    chunks = parse_mineru_content(content_list)
    print(f"  解析: {len(chunks)} 个术语 chunks")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    print(f"✓ 保存到 {OUTPUT_PATH}")

    print("\n【Preview - 前5个chunks】")
    for chunk in chunks[:5]:
        print(f"\n  [{chunk['chunk_id']}] {chunk['term_name']}")
        definition_preview = chunk['definition'][:80].replace('\n', ' ')
        print(f"    定义: {definition_preview}...")


if __name__ == "__main__":
    main()
