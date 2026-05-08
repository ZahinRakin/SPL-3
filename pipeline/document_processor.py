"""
Document processor: extracts plain text from PDF, DOCX, TXT, and HTML files.
"""
import re
import asyncio
import aiofiles
from pathlib import Path


async def extract_text(file_path: str, content_type: str = "") -> str:
    ext = Path(file_path).suffix.lower()
    ct = content_type.lower()

    if ext == ".pdf" or "pdf" in ct:
        return await _extract_pdf(file_path)
    if ext in (".docx", ".doc") or "word" in ct or "docx" in ct:
        return await _extract_docx(file_path)
    if ext in (".html", ".htm") or "html" in ct:
        return await _extract_html(file_path)
    # default: read as UTF-8 text
    try:
        async with aiofiles.open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return await f.read()
    except Exception:
        return ""


async def _extract_pdf(path: str) -> str:
    def _read() -> str:
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(path)
            return "\n".join(page.get_text() for page in doc)
        except ImportError:
            pass
        try:
            import PyPDF2
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                return "\n".join(
                    (page.extract_text() or "") for page in reader.pages
                )
        except Exception:
            return ""

    return await asyncio.to_thread(_read)


async def _extract_docx(path: str) -> str:
    def _read() -> str:
        try:
            from docx import Document
            doc = Document(path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            return ""

    return await asyncio.to_thread(_read)


async def _extract_html(path: str) -> str:
    async with aiofiles.open(path, "r", encoding="utf-8", errors="replace") as f:
        html = await f.read()
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()
