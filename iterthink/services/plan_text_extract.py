"""Plan PDF: geometry extraction, JSON sidecar, text-on-PNG overlays."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iterthink import config
from iterthink.persistence.content_repo import path_key_for
from iterthink.services import document_import

_PLAN_PROFILE_MARKER = "<!-- pdf_profile:plan -->"


def plan_text_sidecar_path(resolved_doc: Path, version_id: int) -> Path:
    pk = path_key_for(resolved_doc.resolve())
    d = config.STORE_DIR / "plan_text" / pk
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{version_id}.json"


def extract_plan_geometry(src: Path) -> dict[str, Any]:
    """Per-page line geometry from pdfplumber (no markdown flattening)."""
    return document_import.extract_pdf_pages_geometry(src)


def plan_stub_markdown_minimal() -> str:
    """Library-card body for a plan PDF (profile marker only; geometry lives in JSON sidecar)."""
    return f"{_PLAN_PROFILE_MARKER}\n"


def plan_stub_markdown(geometry: dict[str, Any] | None = None) -> str:
    """Plan notes do not flatten PDF text into markdown; geometry is stored separately."""
    del geometry
    return plan_stub_markdown_minimal()


def write_plan_text_sidecar(resolved_doc: Path, version_id: int, geometry: dict[str, Any]) -> Path:
    p = plan_text_sidecar_path(resolved_doc, version_id)
    p.write_text(json.dumps(geometry, indent=0), encoding="utf-8")
    return p


def load_plan_text_sidecar(resolved_doc: Path, version_id: int) -> dict[str, Any] | None:
    p = plan_text_sidecar_path(resolved_doc, version_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_plan_stub_markdown(body: str) -> bool:
    return _PLAN_PROFILE_MARKER in (body or "")


_OVERLAY_TEXT_RGBA = (35, 15, 51, 255)
_OVERLAY_MIN_FONT_PX = 6
_OVERLAY_PAD_PX = 1
_DEJAVU_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


def _overlay_font(size_px: int):
    from PIL import ImageFont

    size_px = max(_OVERLAY_MIN_FONT_PX, int(size_px))
    for path in _DEJAVU_PATHS:
        p = Path(path)
        if p.is_file():
            return ImageFont.truetype(str(p), size_px)
    return ImageFont.load_default()


def _sample_page_fill(img, x0: float, y0: float) -> tuple[int, int, int]:
    sx = max(0, min(int(x0) - 1, img.width - 1))
    sy = max(0, min(int(y0) - 1, img.height - 1))
    px = img.getpixel((sx, sy))
    if isinstance(px, (tuple, list)) and len(px) >= 3:
        return (int(px[0]), int(px[1]), int(px[2]))
    return (255, 255, 255)


def _fit_font_and_draw(
    draw,
    text: str,
    box: tuple[float, float, float, float],
    start_size_px: int,
) -> None:
    x0, y0, x1, y1 = box
    max_w = max(1, int(x1 - x0) - 2 * _OVERLAY_PAD_PX)
    max_h = max(1, int(y1 - y0) - 2 * _OVERLAY_PAD_PX)
    display = text
    size_px = max(_OVERLAY_MIN_FONT_PX, int(start_size_px))
    font = _overlay_font(size_px)

    while size_px > _OVERLAY_MIN_FONT_PX:
        bb = draw.textbbox((0, 0), display, font=font)
        if (bb[2] - bb[0]) <= max_w and (bb[3] - bb[1]) <= max_h:
            break
        size_px -= 1
        font = _overlay_font(size_px)

    while display:
        bb = draw.textbbox((0, 0), display, font=font)
        if (bb[2] - bb[0]) <= max_w:
            break
        if len(display) <= 1:
            display = "…"
            break
        display = display[:-1].rstrip()
        if not display.endswith("…"):
            display = f"{display}…"

    bb = draw.textbbox((0, 0), display, font=font)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]
    tx = x0 + _OVERLAY_PAD_PX
    ty = y0 + _OVERLAY_PAD_PX + max(0, (max_h - th) // 2)
    draw.text((tx, ty), display, fill=_OVERLAY_TEXT_RGBA, font=font)


def composite_overlay_png(
    page_png: Path,
    page_geom: dict[str, Any],
    *,
    scale: float,
    show_labels: bool = True,
    show_boxes: bool = False,
) -> Path:
    """Draw in-bbox text masks (+ optional bbox outlines) onto a cached composite PNG."""
    from PIL import Image, ImageDraw

    cache_dir = page_png.parent
    tag = f"{page_png.name}:l={int(show_labels)}:b={int(show_boxes)}:s={scale}:v=inbbox1"
    out = cache_dir / f"{page_png.stem}_textov_{hash(tag) & 0xFFFFFFFF:08x}.png"
    if out.is_file() and out.stat().st_mtime >= page_png.stat().st_mtime:
        return out

    base = Image.open(page_png).convert("RGBA")
    draw = ImageDraw.Draw(base, "RGBA")

    for line in page_geom.get("lines") or []:
        bbox = line.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        x0, y0, x1, y1 = (
            float(bbox[0]) * scale,
            float(bbox[1]) * scale,
            float(bbox[2]) * scale,
            float(bbox[3]) * scale,
        )
        if show_boxes:
            draw.rectangle([x0, y0, x1, y1], outline=(179, 143, 193, 200), width=1)
        if show_labels:
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            fill_rgb = _sample_page_fill(base, x0, y0)
            draw.rectangle([x0, y0, x1, y1], fill=fill_rgb + (255,))
            start_px = max(_OVERLAY_MIN_FONT_PX, int(float(line.get("size") or 11) * scale))
            _fit_font_and_draw(draw, text, (x0, y0, x1, y1), start_px)

    base.convert("RGB").save(out, "PNG")
    return out


def page_pngs_with_text_overlay(
    page_pngs: list[Path],
    geometry: dict[str, Any],
    *,
    scale: float | None = None,
    show_labels: bool = True,
) -> list[Path]:
    """Return display paths: composite when geometry exists, else raw PNGs."""
    if scale is None:
        scale = document_import.PDF_RENDER_SCALE_PLAN
    pages = geometry.get("pages") or []
    by_num = {int(p.get("page") or 0): p for p in pages}
    out: list[Path] = []
    for i, png in enumerate(page_pngs):
        geom = by_num.get(i + 1)
        if geom and (geom.get("lines") or []):
            out.append(
                composite_overlay_png(
                    png,
                    geom,
                    scale=scale,
                    show_labels=show_labels,
                    show_boxes=not show_labels,
                )
            )
        else:
            out.append(png)
    return out
