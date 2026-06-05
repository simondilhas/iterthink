"""OCR orchestration for scanned PDF and image imports."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from iterthink import config
from iterthink.ocr_settings import DEFAULT_OLLAMA_OCR_MODEL, is_image_import_extension

_SCANNED_STUB_MARKER = "<!-- PDF text extraction returned no content"


def _ocr_image_path(image_path: Path) -> str:
    if config.OCR_ENGINE == "ollama":
        from iterthink.ai.ollama_ocr import check_ollama_ocr_ready_sync, ocr_image_sync

        ok, reason = check_ollama_ocr_ready_sync()
        if not ok:
            raise RuntimeError(reason)
        return ocr_image_sync(image_path)
    from iterthink.ai.local_ocr import ocr_image_sync

    return ocr_image_sync(image_path)


def pdf_to_markdown_ocr(src: Path) -> str:
    """Rasterize PDF pages and OCR each page into markdown with page markers."""
    from iterthink.services.document_import import render_pdf_to_png_pages

    if config.OCR_ENGINE == "ollama":
        from iterthink.ai.ollama_ocr import check_ollama_ocr_ready_sync

        ok, reason = check_ollama_ocr_ready_sync()
        if not ok:
            return _scanned_stub_with_note(reason)

    page_pngs = render_pdf_to_png_pages(src, pdf_profile="text")
    chunks: list[str] = []
    for i, png in enumerate(page_pngs, start=1):
        try:
            text = _ocr_image_path(png).strip()
        except BaseException as ex:
            text = f"*OCR failed on page {i}: {ex}*"
        marker = f"<!-- page:{i} -->"
        chunks.append(f"{marker}\n\n{text}" if text else marker)
    body = "\n\n".join(chunks)
    if body.strip():
        return body
    return _scanned_stub_with_note("OCR returned no text")


def image_to_markdown(src: Path, dest_md: Path) -> str:
    """Copy image beside import assets and return markdown with OCR body."""
    asset_dir = config.IMPORT_ASSETS_DIR / dest_md.stem
    asset_dir.mkdir(parents=True, exist_ok=True)
    dest_image = asset_dir / src.name
    if src.resolve() != dest_image.resolve():
        shutil.copy2(src, dest_image)
    rel = os.path.relpath(dest_image, dest_md.parent).replace(os.sep, "/")
    alt = src.stem or "Image"
    try:
        text = _ocr_image_path(src).strip()
    except BaseException as ex:
        text = f"*OCR failed: {ex}*"
    parts = [text, "", f"![{alt}]({rel})"] if text else [f"![{alt}]({rel})"]
    return "\n".join(parts)


def is_scanned_pdf_stub(md: str) -> bool:
    return _SCANNED_STUB_MARKER in (md or "")


def _scanned_stub_with_note(note: str) -> str:
    return (
        "<!-- PDF text extraction returned no content. This may be a scanned PDF. -->\n\n"
        f"*Note: OCR unavailable ({note}).*"
    )


def extension_category(ext: str | None) -> str | None:
    if ext == "pdf":
        return "pdf"
    if ext == "docx":
        return "docx"
    if is_image_import_extension(ext):
        return "image"
    return None
