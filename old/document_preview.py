"""Preview extraction for document classification."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DocumentPreview:
    """Preview data extracted for classification."""

    preview_text: str  # First N pages/chars
    filename: str
    file_ext: str
    file_size: int
    page_count: Optional[int] = None  # For PDFs
    is_scanned: bool = False  # For PDFs: text-based vs scanned


def extract_preview_for_classification(
    file_path: str,
    file_ext: str,
    max_pages: int = 3,
    max_chars: int = 5000,
) -> DocumentPreview:
    """
    Extract minimal content for document classification.
    
    Args:
        file_path: Path to the document file
        file_ext: File extension (pdf, doc, docx)
        max_pages: Maximum number of pages to extract (for PDFs)
        max_chars: Maximum characters to extract (for Word docs)
        
    Returns:
        DocumentPreview with preview text and metadata
    """
    file_path_obj = Path(file_path)
    filename = file_path_obj.name
    file_size = file_path_obj.stat().st_size if file_path_obj.exists() else 0
    
    preview_text = ""
    page_count = None
    is_scanned = False
    
    if file_ext == "pdf":
        preview_text, page_count, is_scanned = _extract_pdf_preview(
            file_path, max_pages, max_chars
        )
    elif file_ext in ["doc", "docx"]:
        preview_text = _extract_word_preview(file_path, max_chars)
    else:
        logger.warning(f"Unsupported file type for preview: {file_ext}")
        preview_text = ""
    
    return DocumentPreview(
        preview_text=preview_text,
        filename=filename,
        file_ext=file_ext,
        file_size=file_size,
        page_count=page_count,
        is_scanned=is_scanned,
    )


def _extract_pdf_preview(
    pdf_path: str, max_pages: int = 3, max_chars: int = 5000
) -> tuple[str, Optional[int], bool]:
    """
    Extract preview from PDF (first N pages).
    
    Returns:
        Tuple of (preview_text, page_count, is_scanned)
    """
    try:
        import fitz  # PyMuPDF
        
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        
        # Extract text from first N pages
        preview_pages = min(max_pages, page_count)
        preview_text = ""
        has_text = False
        
        for page_num in range(preview_pages):
            page = doc[page_num]
            page_text = page.get_text()
            
            if page_text and page_text.strip():
                has_text = True
                preview_text += page_text + "\n\n"
                
                # Stop if we've reached max_chars
                if len(preview_text) >= max_chars:
                    preview_text = preview_text[:max_chars]
                    break
        
        doc.close()
        
        # Check if scanned (no extractable text)
        is_scanned = not has_text
        
        return preview_text.strip(), page_count, is_scanned
        
    except ImportError:
        logger.error("PyMuPDF (fitz) not available for PDF preview extraction")
        return "", None, False
    except Exception as e:
        logger.error(f"Error extracting PDF preview: {e}", exc_info=True)
        return "", None, False


def _extract_word_preview(file_path: str, max_chars: int = 5000) -> str:
    """Extract preview from Word document (first N characters)."""
    try:
        from docx import Document as DocxDocument
        
        doc = DocxDocument(file_path)
        preview_text = ""
        
        # Extract text from paragraphs
        for para in doc.paragraphs:
            if para.text.strip():
                preview_text += para.text + "\n\n"
                
                # Stop if we've reached max_chars
                if len(preview_text) >= max_chars:
                    preview_text = preview_text[:max_chars]
                    break
        
        return preview_text.strip()
        
    except ImportError:
        logger.error("python-docx not available for Word preview extraction")
        return ""
    except Exception as e:
        logger.error(f"Error extracting Word preview: {e}", exc_info=True)
        return ""

