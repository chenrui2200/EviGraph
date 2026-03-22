"""
File Parser Utility
Supports text extraction from PDF, Markdown, TXT files with metadata tracking
"""

import os
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class TextChunk:
    """A chunk of text with metadata for traceability"""
    text: str
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "metadata": self.metadata
        }


def _read_text_with_fallback(file_path: str) -> str:
    """
    Read text file with automatic encoding detection if UTF-8 fails.
    """
    data = Path(file_path).read_bytes()

    # First try UTF-8
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        pass

    # Try charset_normalizer for encoding detection
    encoding = None
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data).best()
        if best and best.encoding:
            encoding = best.encoding
    except Exception:
        pass

    # Fall back to chardet
    if not encoding:
        try:
            import chardet
            result = chardet.detect(data)
            encoding = result.get('encoding') if result else None
        except Exception:
            pass

    # Final fallback: use UTF-8 + replace
    if not encoding:
        encoding = 'utf-8'

    return data.decode(encoding, errors='replace')


class FileParser:
    """File Parser with traceability support"""

    SUPPORTED_EXTENSIONS = {'.pdf', '.md', '.markdown', '.txt'}

    @classmethod
    def extract_chunks(cls, file_path: str, override_filename: Optional[str] = None) -> List[TextChunk]:
        """
        Extract text chunks with metadata from file
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File does not exist: {file_path}")

        suffix = path.suffix.lower()
        filename = override_filename or path.name

        # If path has no suffix, try to get it from override_filename
        if not suffix and override_filename:
            suffix = Path(override_filename).suffix.lower()

        if suffix == '.pdf':
            return cls._extract_chunks_from_pdf(file_path, filename)
        elif suffix in {'.md', '.markdown'}:
            text = _read_text_with_fallback(file_path)
            return [TextChunk(text=text, metadata={"source": filename, "type": "markdown"})]
        elif suffix == '.txt':
            text = _read_text_with_fallback(file_path)
            return [TextChunk(text=text, metadata={"source": filename, "type": "txt"})]

        raise ValueError(f"Unsupported file format: {suffix}")

    @staticmethod
    def _extract_chunks_from_pdf(file_path: str, filename: str) -> List[TextChunk]:
        """Extract text from PDF page by page with coordinates and table support"""
        import logging
        logger = logging.getLogger('mirofish.file_parser')

        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.warning("PyMuPDF (fitz) not found, falling back to pypdf")
            return FileParser._extract_chunks_with_pypdf(file_path, filename)

        chunks = []
        try:
            with fitz.open(file_path) as doc:
                total_pages = len(doc)
                if total_pages == 0:
                    logger.warning(f"PDF {filename} has 0 pages.")
                    return []

                for i, page in enumerate(doc):
                    page_num = i + 1
                    # Get page size (width, height)
                    page_rect = page.rect
                    pw, ph = page_rect.width, page_rect.height

                    # --- Table Extraction ---
                    table_bboxes = []
                    try:
                        # Find tables on the current page
                        tabs = page.find_tables()
                        for tab in tabs:
                            # Use bounding box of the table to filter out overlapping text blocks
                            table_bboxes.append(tab.bbox)

                            # Extract table data and convert to Markdown
                            # table.extract() returns a list of lists of strings
                            table_data = tab.extract()
                            if not table_data:
                                continue

                            # Convert to simple Markdown format
                            md_rows = []
                            for row_idx, row in enumerate(table_data):
                                # Clean cells
                                clean_row = [str(cell).replace('\n', ' ').strip() if cell is not None else "" for cell in row]
                                md_rows.append("| " + " | ".join(clean_row) + " |")

                                # Add header separator after first row
                                if row_idx == 0:
                                    md_rows.append("| " + " | ".join(["---"] * len(clean_row)) + " |")

                            table_markdown = "\n".join(md_rows)
                            if table_markdown.strip():
                                chunks.append(TextChunk(
                                    text=table_markdown,
                                    metadata={
                                        "source": filename,
                                        "page": page_num,
                                        "total_pages": total_pages,
                                        "type": "table",
                                        "method": "fitz_tables",
                                        "bbox": list(tab.bbox),
                                        "page_width": pw,
                                        "page_height": ph
                                    }
                                ))
                    except Exception as te:
                        logger.warning(f"Table extraction failed on page {page_num} of {filename}: {str(te)}")

                    # --- Text Block Extraction ---
                    # Method 1 & 2 combined: Use blocks to get coordinates
                    # blocks format: (x0, y0, x1, y1, "text", block_no, block_type)
                    blocks = page.get_text("blocks")

                    page_chunks = []
                    for b in blocks:
                        if len(b) < 5 or not isinstance(b[4], str):
                            continue

                        text = b[4].strip()
                        if not text:
                            continue

                        # Check if this block's bounding box is inside any extracted table
                        # b[0:4] are x0, y0, x1, y1
                        bbox = list(b[0:4])

                        # Use a simple intersection check - if block is mostly inside table, skip it
                        is_inside_table = False
                        for t_bbox in table_bboxes:
                            # If the block's center is inside the table bbox, it's probably part of it
                            bx_center = (bbox[0] + bbox[2]) / 2
                            by_center = (bbox[1] + bbox[3]) / 2
                            if (t_bbox[0] <= bx_center <= t_bbox[2] and
                                t_bbox[1] <= by_center <= t_bbox[3]):
                                is_inside_table = True
                                break

                        if is_inside_table:
                            continue

                        page_chunks.append(TextChunk(
                            text=text,
                            metadata={
                                "source": filename,
                                "page": page_num,
                                "total_pages": total_pages,
                                "type": "pdf",
                                "method": "fitz_blocks",
                                "bbox": bbox,  # [x0, y0, x1, y1]
                                "page_width": pw,
                                "page_height": ph
                            }
                        ))

                    # Method 3: OCR fallback if suspicious (too little text on a page that has images)
                    # We check the aggregate text from blocks
                    aggregate_text = "".join([c.text for c in page_chunks])
                    if not aggregate_text or len(aggregate_text) < 20:
                        images = page.get_images()
                        if images:
                            logger.info(f"Attempting OCR on page {page_num} of {filename}...")
                            ocr_chunks = FileParser._perform_ocr_with_bboxes(page, filename, page_num, total_pages)
                            if ocr_chunks:
                                page_chunks = ocr_chunks
                                logger.info(f"OCR successful on page {page_num}, found {len(ocr_chunks)} blocks")

                    if page_chunks:
                        chunks.extend(page_chunks)

        except Exception as e:
            import traceback
            logger.error(f"PyMuPDF extraction failed for {filename}: {str(e)}\n{traceback.format_exc()}")
            return FileParser._extract_chunks_with_pypdf(file_path, filename)

        return chunks

    @staticmethod
    def _perform_ocr_with_bboxes(page, filename: str, page_num: int, total_pages: int) -> List[TextChunk]:
        """Perform OCR and return chunks with bounding boxes"""
        import logging
        from ..config import Config
        logger = logging.getLogger('mirofish.file_parser')
        try:
            import pytesseract
            from pytesseract import Output
            from PIL import Image
            import io
            import fitz

            if Config.TESSERACT_CMD:
                pytesseract.pytesseract.tesseract_cmd = Config.TESSERACT_CMD

            # Render page to image (2x zoom for better OCR)
            zoom = 2
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))

            # OCR with data output
            tess_data = pytesseract.image_to_data(img, lang='chi_sim+eng', output_type=Output.DICT)

            # Map OCR results to blocks/lines
            chunks = []
            n_boxes = len(tess_data['text'])

            # Group by 'block_num' or 'line_num' provided by Tesseract
            current_block_id = -1
            current_text = []
            current_bbox = None # [x0, y0, x1, y1]

            for i in range(n_boxes):
                text = tess_data['text'][i].strip()
                if not text:
                    continue

                # Filter out low confidence results (noise)
                try:
                    conf = int(tess_data['conf'][i])
                    if conf < 30:
                        continue
                except (ValueError, TypeError):
                    pass

                block_id = tess_data['block_num'][i]

                # Tesseract coordinates are in pixels on the rendered image
                x, y, w, h = tess_data['left'][i], tess_data['top'][i], tess_data['width'][i], tess_data['height'][i]

                # Convert to PDF coordinates
                # PDF_coord = Image_coord / zoom
                tx0, ty0, tx1, ty1 = x/zoom, y/zoom, (x+w)/zoom, (y+h)/zoom

                if block_id != current_block_id and current_text:
                    # Save previous block
                    if current_bbox:
                        chunks.append(TextChunk(
                            text=" ".join(current_text),
                            metadata={
                                "source": filename,
                                "page": page_num,
                                "total_pages": total_pages,
                                "type": "pdf",
                                "method": "ocr_tesseract",
                                "bbox": current_bbox
                            }
                        ))
                    current_text = []
                    current_bbox = None

                current_block_id = block_id
                current_text.append(text)

                if current_bbox is None:
                    current_bbox = [tx0, ty0, tx1, ty1]
                else:
                    current_bbox[0] = min(current_bbox[0], tx0)
                    current_bbox[1] = min(current_bbox[1], ty0)
                    current_bbox[2] = max(current_bbox[2], tx1)
                    current_bbox[3] = max(current_bbox[3], ty1)

            # Add last block
            if current_text and current_bbox:
                chunks.append(TextChunk(
                    text=" ".join(current_text),
                    metadata={
                        "source": filename,
                        "page": page_num,
                        "total_pages": total_pages,
                        "type": "pdf",
                        "method": "ocr_tesseract",
                        "bbox": current_bbox
                    }
                ))

            return chunks
        except Exception as e:
            logger.error(f"OCR with bboxes failed: {str(e)}")
            return []

    @staticmethod
    def _perform_ocr(page) -> Optional[str]:
        """Legacy OCR method - returns just text"""
        chunks = FileParser._perform_ocr_with_bboxes(page, "unknown", 0, 0)
        if not chunks:
            return None
        return "\n".join([c.text for c in chunks])

    @staticmethod
    def _extract_chunks_with_pypdf(file_path: str, filename: str) -> List[TextChunk]:
        """Fallback extraction using pypdf library"""
        import logging
        logger = logging.getLogger('mirofish.file_parser')
        logger.info(f"Attempting fallback extraction with pypdf for {filename}...")

        try:
            from pypdf import PdfReader
        except ImportError:
            logger.error("Neither PyMuPDF nor pypdf found. Cannot extract PDF.")
            return []

        chunks = []
        try:
            reader = PdfReader(file_path)
            total_pages = len(reader.pages)
            logger.info(f"pypdf: processing {total_pages} pages...")

            for i, page in enumerate(reader.pages):
                try:
                    text = page.extract_text()
                    if text and text.strip():
                        chunks.append(TextChunk(
                            text=text.strip(),
                            metadata={
                                "source": filename,
                                "page": i + 1,
                                "total_pages": total_pages,
                                "type": "pdf",
                                "method": "pypdf"
                            }
                        ))
                except Exception as pe:
                    logger.warning(f"pypdf failed to extract page {i+1} of {filename}: {pe}")

            logger.info(f"pypdf extraction done: {len(chunks)} chunks found.")
        except Exception as e:
            logger.error(f"pypdf failed for {filename}: {e}")

        return chunks

    @classmethod
    def extract_text(cls, file_path: str) -> str:
        """Legacy support: extract all text as single string"""
        chunks = cls.extract_chunks(file_path)
        return "\n\n".join([c.text for c in chunks])

    @classmethod
    def extract_from_multiple(cls, file_paths: List[str]) -> str:
        """Legacy support: extract from multiple files"""
        all_texts = []
        for i, file_path in enumerate(file_paths, 1):
            try:
                text = cls.extract_text(file_path)
                filename = Path(file_path).name
                # Use a less prominent separator to avoid LLM misinterpreting it as an entity
                all_texts.append(f"Source Document {i} ({filename}):\n{text}")
            except Exception as e:
                all_texts.append(f"Source Document {i} ({file_path}) [Extraction failed: {str(e)}]")
        return "\n\n".join(all_texts)


def split_text_into_chunks(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50
) -> List[str]:
    """
    Legacy text splitter (string to string list)
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            for sep in ['。', '！', '？', '.\n', '!\n', '?\n', '\n\n', '. ', '! ', '? ']:
                last_sep = text[start:end].rfind(sep)
                if last_sep != -1 and last_sep > chunk_size * 0.3:
                    end = start + last_sep + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap if end < len(text) else len(text)
    return chunks
