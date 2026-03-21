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
        chunk_size: int = 800,
        overlap: int = 80,
        semantic: bool = False
    ) -> List[TextChunk]:
        """
        Split list of TextChunks into smaller chunks while preserving metadata.
        Skip splitting for special types like 'table'.

        Args:
            chunks: List of TextChunks
            chunk_size: Target chunk size (chars)
            overlap: Overlap size (chars)
            semantic: Whether to use semantic similarity for splitting
        """
        result_chunks = []

        # Initialize embedding service for semantic split if needed
        embed_service = None
        if semantic:
            from ..storage.embedding_service import EmbeddingService
            embed_service = EmbeddingService()

        for original_chunk in chunks:
            text = original_chunk.text
            metadata = original_chunk.metadata

            # If it's a table, keep it whole as requested by user
            if metadata.get("type") == "table":
                result_chunks.append(original_chunk)
                continue

            # Choose splitting strategy
            if semantic and len(text) > chunk_size and embed_service:
                split_texts = TextProcessor._semantic_split(text, embed_service, chunk_size, overlap)
            else:
                # Use existing rule-based splitter logic
                split_texts = split_text_into_chunks(text, chunk_size, overlap)

            for i, split_text in enumerate(split_texts):
                # Copy metadata and add sub-chunk info
                new_metadata = metadata.copy()
                new_metadata["chunk_index"] = i

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

