"""Pixel diff between two PDFs: render, align, produce overlay PNGs (no Flet)."""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

import numpy as np

from iterthink import config
from iterthink.services.document_import import render_pdf_to_png_pages

_ALGO_VERSION = 1


@cache
def _opencv():
    """Lazy import so callers can avoid loading OpenCV until diff runs."""
    try:
        import cv2 as cv
        return cv
    except ImportError as e:
        raise ImportError(
            "PDF visual diff requires opencv-python-headless. Install with: pip install opencv-python-headless"
        ) from e


def _cache_dir(pdf_a: Path, pdf_b: Path) -> Path:
    st_a, st_b = pdf_a.stat(), pdf_b.stat()
    key_src = (
        f"{_ALGO_VERSION}:{pdf_a.resolve()}:{st_a.st_mtime_ns}:{st_a.st_size}:"
        f"{pdf_b.resolve()}:{st_b.st_mtime_ns}:{st_b.st_size}"
    ).encode()
    h = hashlib.sha256(key_src).hexdigest()[:28]
    d = config.STORE_DIR / "pdf_diff_cache" / h
    d.mkdir(parents=True, exist_ok=True)
    return d


def _to_gray(img: np.ndarray) -> np.ndarray:
    cv = _opencv()
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv.cvtColor(img, cv.COLOR_BGRA2GRAY)
    return cv.cvtColor(img, cv.COLOR_BGR2GRAY)


def _align_ecc(gray_a: np.ndarray, gray_b: np.ndarray) -> np.ndarray:
    """Warp gray_b toward gray_a; return warped gray_b (same shape as gray_a)."""
    cv = _opencv()
    h, w = gray_a.shape[:2]
    if gray_b.shape != (h, w):
        gray_b = cv.resize(gray_b, (w, h), interpolation=cv.INTER_AREA)
    wa, ha = w, h
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        criteria = (cv.TERM_CRITERIA_EPS | cv.TERM_CRITERIA_COUNT, 50, 1e-4)
        _, warp = cv.findTransformECC(gray_a, gray_b, warp, cv.MOTION_EUCLIDEAN, criteria, None, 5)
    except cv.error:
        return gray_b
    return cv.warpAffine(gray_b, warp, (wa, ha), flags=cv.INTER_LINEAR + cv.WARP_INVERSE_MAP)


def _overlay_diff(bgr_a: np.ndarray, bgr_b: np.ndarray) -> np.ndarray:
    """Return BGR image: base dimmed with green (add) / red (remove) highlights."""
    cv = _opencv()
    ga, gb = _to_gray(bgr_a), _to_gray(bgr_b)
    if ga.shape != gb.shape:
        gb = cv.resize(gb, (ga.shape[1], ga.shape[0]), interpolation=cv.INTER_AREA)
    gbw = _align_ecc(ga, gb)
    diff = cv.absdiff(ga, gbw)
    _, mask = cv.threshold(diff, 28, 255, cv.THRESH_BINARY)
    k = np.ones((3, 3), np.uint8)
    mask = cv.morphologyEx(mask, cv.MORPH_OPEN, k, iterations=1)
    mask = cv.morphologyEx(mask, cv.MORPH_CLOSE, k, iterations=1)
    # add = pixels stronger in b, remove = stronger in a
    add_m = ((gbw.astype(np.int16) - ga.astype(np.int16)) > 8) & (mask > 0)
    rem_m = ((ga.astype(np.int16) - gbw.astype(np.int16)) > 8) & (mask > 0)
    out = (bgr_a.astype(np.float32) * 0.55).clip(0, 255).astype(np.uint8)
    out[add_m] = (0, 200, 80)
    out[rem_m] = (40, 40, 220)
    return out


def diff_pdfs_to_overlay_paths(pdf_a: Path, pdf_b: Path) -> tuple[list[Path], str | None]:
    """
    Render both PDFs, align each page pair, write overlay PNGs.
    Returns (list of overlay paths, warning or None if page count mismatch / empty).
    """
    pages_a = render_pdf_to_png_pages(pdf_a)
    pages_b = render_pdf_to_png_pages(pdf_b)
    warn: str | None = None
    if len(pages_a) != len(pages_b):
        warn = f"Page count differs ({len(pages_a)} vs {len(pages_b)}); comparing first {min(len(pages_a), len(pages_b))}."
    n = min(len(pages_a), len(pages_b))
    if n == 0:
        return [], "No pages to compare."
    cache = _cache_dir(pdf_a, pdf_b)
    marker = cache / ".ok"
    expect = f"{n}\n"
    if marker.is_file() and marker.read_text(encoding="utf-8") == expect:
        existing = sorted(cache.glob("overlay_*.png"))
        if len(existing) >= n:
            return (existing[:n], warn)

    cv = _opencv()
    for old in cache.glob("overlay_*.png"):
        old.unlink(missing_ok=True)

    out_paths: list[Path] = []
    for i in range(n):
        a = cv.imread(str(pages_a[i]), cv.IMREAD_UNCHANGED)
        b = cv.imread(str(pages_b[i]), cv.IMREAD_UNCHANGED)
        if a is None or b is None:
            continue
        if a.ndim == 2:
            a = cv.cvtColor(a, cv.COLOR_GRAY2BGR)
        if b.ndim == 2:
            b = cv.cvtColor(b, cv.COLOR_GRAY2BGR)
        if a.shape[2] == 4:
            a = cv.cvtColor(a, cv.COLOR_BGRA2BGR)
        if b.shape[2] == 4:
            b = cv.cvtColor(b, cv.COLOR_BGRA2BGR)
        # Normalize size to max WxH
        ha, wa = a.shape[:2]
        hb, wb = b.shape[:2]
        tw, th = max(wa, wb), max(ha, hb)
        if (wa, ha) != (tw, th):
            a = cv.resize(a, (tw, th), interpolation=cv.INTER_AREA)
        if (wb, hb) != (tw, th):
            b = cv.resize(b, (tw, th), interpolation=cv.INTER_AREA)
        ov = _overlay_diff(a, b)
        p = cache / f"overlay_{i + 1:04d}.png"
        cv.imwrite(str(p), ov)
        out_paths.append(p)

    marker.write_text(expect, encoding="utf-8")
    return (out_paths, warn)
