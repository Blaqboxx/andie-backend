from typing import List, Tuple
from pathlib import Path
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


def read_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def read_pdf(path: str) -> Tuple[str, List[int]]:
    """
    Extract text from PDF file.
    Returns: (full_text, page_numbers_for_each_section)
    This maintains page info in metadata during chunking.
    """
    if PdfReader is None:
        raise ImportError("pypdf is required for PDF extraction. Install with: pip install pypdf")
    
    reader = PdfReader(path)
    text_sections = []
    page_map = []
    
    for page_num, page in enumerate(reader.pages, 1):
        text = page.extract_text()
        if text.strip():
            text_sections.append(text)
            page_map.append(page_num)
    
    full_text = "\n\n---PAGE BREAK---\n\n".join(text_sections)
    return full_text, page_map


def chunk_text(text: str, size: int, overlap: int) -> List[str]:
    chunks = []
    start = 0
    n = len(text)
    if size <= 0:
        raise ValueError("Chunk size must be greater than zero")
    if overlap >= size:
        overlap = max(size // 5, 0)

    while start < n:
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
        if start < 0:
            start = 0

    return [c.strip() for c in chunks if c and c.strip()]
