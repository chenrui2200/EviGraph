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
        chunk_size: int = 500,
        overlap: int = 50
    ) -> List[TextChunk]:
        """
        Split list of TextChunks into smaller chunks while preserving metadata
        """
        result_chunks = []
        for original_chunk in chunks:
            text = original_chunk.text
            metadata = original_chunk.metadata

            # Use existing splitter logic but wrap in TextChunk
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

