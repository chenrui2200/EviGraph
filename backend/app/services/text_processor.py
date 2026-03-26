"""
Textatmanageserveservice
"""

from typing import List, Optional, Union
from ..utils.file_parser import FileParser, split_text_into_chunks, TextChunk


class TextProcessor:
    """Text processor with chunk metadata support"""

    @staticmethod
    def extract_from_files(file_paths: List[str]) -> str:
        """Extract text from multiple files"""
        return FileParser.extract_from_multiple(file_paths)

    @staticmethod
    def extract_chunks_from_files(file_paths: List[str]) -> List[TextChunk]:
        """Extract all chunks with metadata from multiple files"""
        all_chunks = []
        for path in file_paths:
            try:
                chunks = FileParser.extract_chunks(path)
                all_chunks.extend(chunks)
            except Exception as e:
                import logging
                logging.error(f"Failed to extract chunks from {path}: {e}")
        return all_chunks

    @staticmethod
    def split_text(
        text: str,
        chunk_size: int = 500,
        overlap: int = 50
    ) -> List[str]:
        """
        Split text (Legacy string-based)
        """
        return split_text_into_chunks(text, chunk_size, overlap)

    @staticmethod
    def split_chunks(
        chunks: List[TextChunk],
        chunk_size: int = 1000,
        overlap: int = 100,
        semantic: bool = False
    ) -> List[TextChunk]:
        """
        Split list of TextChunks into smaller chunks while preserving metadata.
        Crucial: Merges consecutive text blocks to avoid cutting by page boundary.

        Args:
            chunks: List of TextChunks (e.g., from PDF fitz_blocks)
            chunk_size: Target chunk size
            overlap: Overlap size
            semantic: Whether to use semantic similarity
        """
        # 1. Group chunks by source and type
        # Special types like 'table' should be kept separate

        processed_groups = []
        current_text_group = [] # List of TextChunks to be merged

        for chunk in chunks:
            if chunk.metadata.get("type") == "table":
                # Tables are always their own group
                if current_text_group:
                    processed_groups.append(("text", current_text_group))
                    current_text_group = []
                processed_groups.append(("table", [chunk]))
            else:
                # Text blocks - merge if same source
                if current_text_group and current_text_group[0].metadata.get("source") != chunk.metadata.get("source"):
                    processed_groups.append(("text", current_text_group))
                    current_text_group = [chunk]
                else:
                    current_text_group.append(chunk)

        if current_text_group:
            processed_groups.append(("text", current_text_group))

        result_chunks = []
        source_counters = {}

        # 2. Process each group
        for gtype, gchunks in processed_groups:
            if not gchunks: continue

            source = gchunks[0].metadata.get("source", "unknown")
            if source not in source_counters:
                source_counters[source] = 0

            if gtype == "table":
                # Handle table (usually just one chunk per call, but handled as list for safety)
                for tab_chunk in gchunks:
                    new_metadata = tab_chunk.metadata.copy()
                    new_metadata["chunk_index"] = source_counters[source]
                    source_counters[source] += 1
                    result_chunks.append(TextChunk(text=tab_chunk.text, metadata=new_metadata))
                continue

            # Merge text from text group to avoid page-centric cutting
            merged_text = "\n".join([c.text for c in gchunks])
            # Keep representative metadata (from first chunk of group)
            base_metadata = gchunks[0].metadata.copy()

            # Choose splitting strategy
            if semantic and len(merged_text) > chunk_size:
                from ..storage.embedding_service import EmbeddingService
                embed_service = EmbeddingService()
                split_texts = TextProcessor._semantic_split(merged_text, embed_service, chunk_size, overlap)
            else:
                split_texts = split_text_into_chunks(merged_text, chunk_size, overlap)

            for split_text in split_texts:
                new_metadata = base_metadata.copy()
                new_metadata["chunk_index"] = source_counters[source]
                source_counters[source] += 1

                # Detect chunk type based on GB standard patterns
                import re
                # Clean up text for detection
                clean_text = split_text.lstrip()

                if re.match(r'^第[一二三四五六七八九十\d]+[章篇]', clean_text):
                    new_metadata["hierarchy_type"] = "chapter"
                elif re.match(r'^\d+\.\d+\s+', clean_text):
                    new_metadata["hierarchy_type"] = "section"
                elif re.match(r'^\d+\.\d+\.\d+', clean_text):
                    if clean_text.startswith("2.0."):
                        new_metadata["hierarchy_type"] = "term"
                    else:
                        new_metadata["hierarchy_type"] = "clause"
                elif re.match(r'^\(\s*[A-Z]?\.\d+\.\d+(?:-\d+)?\s*\)', clean_text):
                    new_metadata["hierarchy_type"] = "formula"

                result_chunks.append(TextChunk(
                    text=split_text,
                    metadata=new_metadata
                ))

        return result_chunks

    @staticmethod
    def _semantic_split(text: str, embed_service, chunk_size: int, overlap: int) -> List[str]:
        """
        Implementation of Semantic Similarity Chunking.
        1. Split into sentences
        2. Group into chunks based on embedding similarity
        """
        import re
        import numpy as np

        # 1. Split into sentences (handles Chinese and English)
        sentence_ends = r'([。！？?！!；;]|\n\n|\.\s+)'
        parts = re.split(sentence_ends, text)

        sentences = []
        for i in range(0, len(parts)-1, 2):
            s = parts[i] + parts[i+1]
            if s.strip():
                sentences.append(s.strip())
        if len(parts) % 2 == 1 and parts[-1].strip():
            sentences.append(parts[-1].strip())

        if not sentences:
            return [text]

        # 2. Generate embeddings for sentences
        try:
            embeddings = embed_service.embed_batch(sentences)
            embeddings = [np.array(e) for e in embeddings]
        except Exception as e:
            import logging
            logging.error(f"Semantic split failed (embedding error): {e}")
            return split_text_into_chunks(text, chunk_size, overlap)

        # 3. Calculate cosine similarities between adjacent sentences
        similarities = []
        for i in range(len(embeddings) - 1):
            norm_a = np.linalg.norm(embeddings[i])
            norm_b = np.linalg.norm(embeddings[i+1])
            if norm_a > 0 and norm_b > 0:
                sim = np.dot(embeddings[i], embeddings[i+1]) / (norm_a * norm_b)
            else:
                sim = 0.0
            similarities.append(sim)

        # 4. Identify break points (where similarity is low)
        chunks = []
        current_chunk_sentences = [sentences[0]]
        current_chunk_len = len(sentences[0])

        if similarities:
            # We look for "valleys" in similarity. A low percentile indicates a potential break.
            threshold = np.percentile(similarities, 20)
        else:
            threshold = 0.8

        for i in range(len(similarities)):
            next_sentence = sentences[i+1]
            sim = similarities[i]

            # Conditions to break:
            # 1. Low similarity AND current chunk is large enough
            # 2. OR current chunk would exceed a maximum safe size (e.g., 2x chunk_size)
            should_break = (sim < threshold and current_chunk_len > chunk_size * 0.8) or \
                           (current_chunk_len + len(next_sentence) > chunk_size * 2)

            if should_break and current_chunk_len > 0:
                chunks.append(" ".join(current_chunk_sentences))
                current_chunk_sentences = [next_sentence]
                current_chunk_len = len(next_sentence)
            else:
                current_chunk_sentences.append(next_sentence)
                current_chunk_len += len(next_sentence)

        if current_chunk_sentences:
            chunks.append(" ".join(current_chunk_sentences))

        return chunks
    
    @staticmethod
    def preprocess_text(text: str) -> str:
        """
        preatmanageText
        - moveexceptmanyremainingspacewhite
        - markprepareconvertswitchperform
        
        Args:
            text: sourcestartText
            
        Returns:
            atmanageaftersText
        """
        import re
        
        # Normalize line breaks
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Remove consecutive blank lines (keep at most two line breaks)
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Remove leading/trailing whitespace
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)
        
        return text.strip()
    
    @staticmethod
    def get_text_stats(text: str) -> dict:
        """Get text statistics"""
        return {
            "total_chars": len(text),
            "total_lines": text.count('\n') + 1,
            "total_words": len(text.split()),
        }

    # ========================================================================
    # 多层级语义分块（新增）
    # ========================================================================

    @staticmethod
    def hierarchical_chunk(
        text_chunks: List[TextChunk],
        strategy: str = "full",
        use_llm: bool = True,
        progress_callback=None
    ) -> "HierarchicalChunkResult":
        """
        多层级语义分块 - 基于 LLM 的智能分块

        实现三级分块策略：
        - Level-1: 章节级（导航层）
        - Level-2: 条文级（主检索层）
        - Level-3: 要素级（精准层）

        Args:
            text_chunks: 原始TextChunk列表
            strategy: 分块策略（暂未使用，保留兼容性）
            use_llm: 是否使用 LLM 驱动的分块器（必须，默认为 True）
            progress_callback: 进度回调函数，格式: callback(progress, message)

        Returns:
            HierarchicalChunkResult: 包含所有层级分块的结果
        """
        import logging
        logger = logging.getLogger('mirofish.text_processor')

        # 使用 LLM 驱动的分块器（不支持回退到正则）
        if use_llm:
            from .llm_driven_chunker import LLMDrivenChunker
            logger.info(f"使用 LLM 驱动的分块器")

            chunker = LLMDrivenChunker(progress_callback=progress_callback)
            return chunker.chunk(text_chunks, progress_callback)

        # 不再支持正则分块器作为回退
        raise NotImplementedError(
            "LLM 驱动的分块是必须的，不再支持正则分块器。"
            "请设置 use_llm=True 或确保 Ollama 服务正在运行。"
        )

    @staticmethod
    def hierarchical_chunk_text(
        text: str,
        use_llm: bool = True,
        progress_callback=None
    ) -> "HierarchicalChunkResult":
        """
        对单个文本进行多层级分块

        Args:
            text: 输入文本
            use_llm: 是否使用 LLM 驱动的分块器（必须，默认为 True）
            progress_callback: 进度回调函数

        Returns:
            HierarchicalChunkResult
        """
        import logging
        logger = logging.getLogger('mirofish.text_processor')

        # 使用 LLM 驱动的分块器
        if use_llm:
            from .llm_driven_chunker import LLMDrivenChunker
            logger.info(f"使用 LLM 驱动的分块器")

            chunker = LLMDrivenChunker(progress_callback=progress_callback)
            return chunker.chunk_single_text(text, progress_callback)

        raise NotImplementedError(
            "LLM 驱动的分块是必须的，不再支持正则分块器。"
        )

    @staticmethod
    def get_fixed_ontology() -> dict:
        """
        获取固定本体定义 - 替代OntologyGenerator

        Returns:
            dict: 标准工程规范本体定义
        """
        from .normative_ontology import NormativeOntology
        return NormativeOntology.get_ontology()

    @staticmethod
    def get_ontology_prompt_context() -> str:
        """
        获取本体提示词上下文

        用于LLM抽取时的本体说明
        """
        from .normative_ontology import NormativeOntology
        return NormativeOntology.get_prompt_context()

