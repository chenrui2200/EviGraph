#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LightRAG + PDF 快速 Demo
处理 ./JTS 217-2018.pdf 文件进行 RAG 问答
"""

import os
import asyncio
from pathlib import Path

# LightRAG 核心导入
from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import gpt_4o_mini_complete, openai_embed
from lightrag.utils import setup_logger

# PDF 解析工具（二选一）
try:
    from pypdf import PdfReader  # 推荐：纯 Python，轻量

    PDF_BACKEND = "pypdf"
except ImportError:
    import textract  # 备选：支持更多格式

    PDF_BACKEND = "textract"

# ============ 配置区域 ============
WORKING_DIR = "./rag_storage_jts217"  # LightRAG 缓存目录
PDF_PATH = "./JTS 217-2018.pdf"  # 你的 PDF 文件路径
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-xxx")  # 替换为你的 API Key
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
# ==================================

setup_logger("lightrag", level="INFO")


def extract_text_from_pdf(pdf_path: str) -> str:
    """从 PDF 提取纯文本（支持两种后端）"""
    if PDF_BACKEND == "pypdf":
        reader = PdfReader(pdf_path)
        text = "\n\n".join([page.extract_text() or "" for page in reader.pages])
        return text.strip()
    else:
        # textract 方式（需安装额外依赖）
        content = textract.process(pdf_path)
        return content.decode("utf-8", errors="ignore").strip()


async def initialize_rag():
    """初始化 LightRAG 实例"""
    rag = LightRAG(
        working_dir=WORKING_DIR,
        embedding_func=openai_embed,
        llm_model_func=gpt_4o_mini_complete,
        # 可选配置：调整分块大小适应技术文档
        chunk_token_size=1200,
        chunk_overlap_token_size=100,
        addon_params={"language": "Simplified Chinese"}  # 中文优化
    )
    # ⚠️ 必须调用：初始化存储后端
    await rag.initialize_storages()
    return rag


async def main():
    # 检查环境
    if not os.path.exists(PDF_PATH):
        print(f"❌ PDF 文件不存在: {PDF_PATH}")
        return
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️ 请设置 OPENAI_API_KEY 环境变量")
        return

    try:
        # 1️⃣ 初始化 RAG
        print("🔄 初始化 LightRAG...")
        rag = await initialize_rag()

        # 2️⃣ 提取并插入 PDF 内容
        print(f"📖 解析 PDF: {PDF_PATH}")
        pdf_text = extract_text_from_pdf(PDF_PATH)
        print(f"✅ 提取文本长度: {len(pdf_text):,} 字符")

        # 插入文档（支持批量/流式）
        print("🔗 构建知识索引（首次可能较慢）...")
        await rag.ainsert(pdf_text)
        print("✅ 索引构建完成")

        # 3️⃣ 执行查询测试
        test_queries = [
            "JTS 217-2018 标准的主要适用范围是什么？",
            "该标准对试验方法有哪些要求？",
            "关键的技术参数有哪些？"
        ]

        print("\n" + "=" * 60)
        print("🔍 测试查询（hybrid 模式）")
        print("=" * 60)

        for query in test_queries:
            print(f"\n❓ Q: {query}")
            result = await rag.aquery(
                query,
                param=QueryParam(
                    mode="hybrid",  # 推荐：结合本地+全局检索
                    response_type="Multiple Paragraphs",
                    top_k=20
                )
            )
            print(f"💡 A: {result}\n")

        # 4️⃣ 交互式查询（可选）
        print("\n" + "=" * 60)
        print("💬 进入交互模式（输入 'quit' 退出）")
        print("=" * 60)

        while True:
            user_input = input("\n👤 请输入问题: ").strip()
            if user_input.lower() in ['quit', 'exit', 'q']:
                break
            if not user_input:
                continue

            response = await rag.aquery(
                user_input,
                param=QueryParam(mode="hybrid", top_k=15)
            )
            print(f"🤖 {response}")

    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 清理资源
        if 'rag' in locals():
            await rag.finalize_storages()
        print("\n✅ 程序结束")


if __name__ == "__main__":
    asyncio.run(main())