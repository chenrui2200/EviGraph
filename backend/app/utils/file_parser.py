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
        """Extract text from PDF page by page with multiple fallback methods"""
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
                    # Method 1: Standard text extraction
                    text = page.get_text("text").strip()

                    # Method 2: If standard failed, try blocks (handles some complex layouts)
                    if not text:
                        blocks = page.get_text("blocks")
                        text = "\n".join([b[4] for b in blocks if isinstance(b[4], str) and b[4].strip()]).strip()

                    # Method 3: OCR fallback if suspicious (too little text on a page that has images)
                    if not text or len(text) < 20:
                        images = page.get_images()
                        if images:
                            logger.info(f"Attempting OCR on page {i+1} of {filename}...")
                            ocr_text = FileParser._perform_ocr(page)
                            if ocr_text:
                                text = ocr_text
                                logger.info(f"OCR successful on page {i+1}")

                    if not text:
                        continue

                    chunks.append(TextChunk(
                        text=text,
                        metadata={
                            "source": filename,
                            "page": i + 1,
                            "total_pages": total_pages,
                            "type": "pdf",
                            "method": "fitz" + ("+ocr" if "ocr_text" in locals() and ocr_text else "")
                        }
                    ))
        except Exception as e:
            import traceback
            logger.error(f"PyMuPDF extraction failed for {filename}: {str(e)}\n{traceback.format_exc()}")
            return FileParser._extract_chunks_with_pypdf(file_path, filename)

        return chunks

    @staticmethod
    def _perform_ocr(page) -> Optional[str]:
        """Perform OCR on a single page using pytesseract if possible"""
        import logging
        from ..config import Config
        logger = logging.getLogger('mirofish.file_parser')
        try:
            import pytesseract
            from PIL import Image
            import io
            import fitz

            # Set tesseract path from config
            if Config.TESSERACT_CMD:
                pytesseract.pytesseract.tesseract_cmd = Config.TESSERACT_CMD

            # Check if tesseract is installed
            try:
                # This call will raise an exception if tesseract binary is not found
                tess_version = pytesseract.get_tesseract_version()
                logger.debug(f"Tesseract version found: {tess_version}")
            except Exception:
                # Try a few more common locations before giving up
                fallback_paths = [
                    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
                    r'C:\Users\\' + os.getlogin() + r'\AppData\Local\Tesseract-OCR\tesseract.exe',
                    'tesseract' # Assume in PATH
                ]
                found = False
                for path in fallback_paths:
                    if os.path.exists(path):
                        pytesseract.pytesseract.tesseract_cmd = path
                        try:
                            pytesseract.get_tesseract_version()
                            found = True
                            logger.info(f"Tesseract found at fallback path: {path}")
                            break
                        except:
                            continue

                if not found:
                    logger.error("Tesseract engine not found. Please install Tesseract-OCR and ensure it is in PATH or Config.TESSERACT_CMD.")
                    return None

            # Render page to image with higher resolution for better OCR
            matrix = fitz.Matrix(2, 2)  # 2x zoom
            pix = page.get_pixmap(matrix=matrix)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))

            # OCR
            # Try both Chinese and English.
            # Note: requires tesseract-ocr binary and chi_sim data installed on system.
            # Use project-provided tessdata if available
            tessdata_dir = os.path.abspath(Config.TESSDATA_DIR)
            if os.path.exists(tessdata_dir):
                # Use forward slashes for better Tesseract compatibility on Windows
                tessdata_dir_clean = tessdata_dir.replace('\\', '/')
                # Set TESSDATA_PREFIX as it's more robust than command line args in some cases
                os.environ['TESSDATA_PREFIX'] = tessdata_dir_clean
                # Still pass it in config to be sure, but remove quotes if no spaces
                # Note: Tesseract on Windows often misinterprets quotes in --tessdata-dir
                if ' ' in tessdata_dir_clean:
                    config = f'--tessdata-dir "{tessdata_dir_clean}"'
                else:
                    config = f'--tessdata-dir {tessdata_dir_clean}'
            else:
                config = ''

            text = pytesseract.image_to_string(img, lang='chi_sim+eng', config=config)

            if text and text.strip():
                logger.info(f"OCR successful: extracted {len(text)} characters.")
                return text.strip()
            else:
                logger.warning("OCR returned empty text. Page might be too blurry or contain no text.")
                return None
        except Exception as e:
            logger.error(f"OCR execution failed: {str(e)}")
            return None

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
                all_texts.append(f"=== Document {i}: {filename} ===\n{text}")
            except Exception as e:
                all_texts.append(f"=== Document {i}: {file_path} (extraction failed: {str(e)}) ===")
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
