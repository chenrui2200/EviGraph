"""
Word to PDF Converter
Supports .doc and .docx conversion to PDF using LibreOffice (soffice).
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


def convert_word_to_pdf(word_path: str, output_dir: Optional[str] = None) -> str:
    """
    将 Word 文档 (.doc/.docx) 转换为 PDF。

    优先使用 LibreOffice (soffice) headless 模式进行转换。
    如果 soffice 不可用，尝试降级使用 pypandoc + wkhtmltopdf。

    Args:
        word_path: Word 文件绝对路径
        output_dir: PDF 输出目录，默认与源文件同目录

    Returns:
        生成的 PDF 文件绝对路径

    Raises:
        RuntimeError: 转换失败或依赖缺失
    """
    word_path = os.path.abspath(word_path)
    if not os.path.isfile(word_path):
        raise FileNotFoundError(f"Word file not found: {word_path}")

    src_dir = os.path.dirname(word_path)
    out_dir = os.path.abspath(output_dir) if output_dir else src_dir
    os.makedirs(out_dir, exist_ok=True)

    base_name = Path(word_path).stem
    expected_pdf = os.path.join(out_dir, f"{base_name}.pdf")

    # 如果目标文件已存在，先删除（避免 LibreOffice 生成带数字后缀的文件）
    if os.path.exists(expected_pdf):
        os.remove(expected_pdf)

    # 方案 1: LibreOffice (soffice)
    soffice_err = None
    try:
        _convert_with_soffice(word_path, out_dir)
        if os.path.exists(expected_pdf):
            return expected_pdf
        # LibreOffice 有时会改变文件名，兜底查找
        for f in os.listdir(out_dir):
            if f.lower().startswith(base_name.lower()) and f.lower().endswith('.pdf'):
                found = os.path.join(out_dir, f)
                if os.path.exists(found):
                    return found
    except Exception as e:
        soffice_err = e  # 降级到方案 2

    # 方案 2: pypandoc + wkhtmltopdf
    try:
        _convert_with_pypandoc(word_path, out_dir, expected_pdf)
        if os.path.exists(expected_pdf):
            return expected_pdf
    except Exception as pandoc_err:
        raise RuntimeError(
            f"Word to PDF conversion failed for {word_path}. "
            f"LibreOffice error: {getattr(soffice_err, 'args', [soffice_err])}, "
            f"pypandoc error: {getattr(pandoc_err, 'args', [pandoc_err])}. "
            f"Please install LibreOffice (soffice) or pandoc+wkhtmltopdf."
        )

    return expected_pdf


def _convert_with_soffice(word_path: str, output_dir: str):
    """使用 LibreOffice headless 模式转换 Word 为 PDF"""
    cmd = [
        "soffice",
        "--headless",
        "--convert-to", "pdf",
        "--outdir", output_dir,
        word_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)


def _convert_with_pypandoc(word_path: str, output_dir: str, pdf_path: str):
    """使用 pypandoc + wkhtmltopdf 转换 Word 为 PDF"""
    try:
        import pypandoc
    except ImportError:
        raise RuntimeError("pypandoc not installed")

    # 先用 pandoc 转成 HTML
    html_path = os.path.join(output_dir, f"{Path(word_path).stem}.html")
    pypandoc.convert_file(word_path, 'html', outputfile=html_path)

    # 再用 wkhtmltopdf 转成 PDF
    cmd = ["wkhtmltopdf", html_path, pdf_path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)

    # 清理临时 HTML
    if os.path.exists(html_path):
        os.remove(html_path)
