"""
Document processor: extracts plain text from PDF, DOCX, TXT, and HTML files.
"""
import re
import asyncio
import aiofiles
from pathlib import Path

from backend.core.logger import logger


async def extract_text(file_path: str, content_type: str = "") -> str:
    ext = Path(file_path).suffix.lower()
    ct = content_type.lower()
    logger.debug(f"extract_text: file={file_path!r}, ext={ext!r}, content_type={ct!r}")

    if ext == ".pdf" or "pdf" in ct:
        return await _extract_pdf(file_path)
    if ext in (".docx", ".doc") or "word" in ct or "docx" in ct:
        return await _extract_docx(file_path)
    if ext in (".html", ".htm") or "html" in ct:
        return await _extract_html(file_path)
    # default: read as UTF-8 text
    try:
        async with aiofiles.open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = await f.read()
        logger.debug(f"Plain-text extraction complete: {len(text)} chars from {file_path!r}")
        return text
    except Exception as exc:
        logger.error(f"Failed to read plain-text file {file_path!r}: {exc}", exc_info=True)
        return ""


async def _extract_pdf(path: str) -> str:
    logger.debug(f"Extracting PDF: {path!r}")

    def _read() -> str:
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(path)
            text = "\n".join(page.get_text() for page in doc)
            logger.debug(f"PyMuPDF extracted {len(text)} chars from {path!r}")
            return text
        except ImportError:
            logger.debug("PyMuPDF not available, falling back to PyPDF2")
        except Exception as exc:
            logger.warning(f"PyMuPDF extraction failed for {path!r}: {exc}", exc_info=True)
        try:
            import PyPDF2
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = "\n".join((page.extract_text() or "") for page in reader.pages)
            logger.debug(f"PyPDF2 extracted {len(text)} chars from {path!r}")
            return text
        except Exception as exc:
            logger.error(f"PyPDF2 extraction also failed for {path!r}: {exc}", exc_info=True)
            return ""

    return await asyncio.to_thread(_read)


async def _extract_docx(path: str) -> str:
    logger.debug(f"Extracting DOCX: {path!r}")

    def _read() -> str:
        try:
            from docx import Document
            doc = Document(path)
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            logger.debug(f"python-docx extracted {len(text)} chars from {path!r}")
            return text
        except Exception as exc:
            logger.error(f"DOCX extraction failed for {path!r}: {exc}", exc_info=True)
            return ""

    return await asyncio.to_thread(_read)


async def _extract_html(path: str) -> str:
    logger.debug(f"Extracting HTML: {path!r}")
    try:
        async with aiofiles.open(path, "r", encoding="utf-8", errors="replace") as f:
            html = await f.read()
        text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&[a-zA-Z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        logger.debug(f"HTML extraction complete: {len(text)} chars from {path!r}")
        return text
    except Exception as exc:
        logger.error(f"HTML extraction failed for {path!r}: {exc}", exc_info=True)
        return ""
