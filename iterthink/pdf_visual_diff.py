"""Pixel diff between two PDFs: render, align, produce overlay PNGs (no Flet)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np

from iterthink import config
from iterthink.document_import import render_pdf_to_png_pages

_ALGO_VERSION = 1


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
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _align_ecc(gray_a: np.ndarray, gray_b: np.ndarray) -> np.ndarray:
    """Warp gray_b toward gray_a; return warped gray_b (same shape as gray_a)."""
    h, w = gray_a.shape[:2]
    if gray_b.shape != (h, w):
        gray_b = cv2.resize(gray_b, (w, h), interpolation=cv2.INTER_AREA)
    wa, ha = w, h
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-4)
        _, warp = cv2.findTransformECC(gray_a, gray_b, warp, cv2.MOTION_EUCLIDEAN, criteria, None, 5)
    except cv2.error:
        return gray_b
    return cv2.warpAffine(gray_b, warp, (wa, ha), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)


def _overlay_diff(bgr_a: np.ndarray, bgr_b: np.ndarray) -> np.ndarray:
    """Return BGR image: base dimmed with green (add) / red (remove) highlights."""
    ga, gb = _to_gray(bgr_a), _to_gray(bgr_b)
    if ga.shape != gb.shape:
        gb = cv2.resize(gb, (ga.shape[1], ga.shape[0]), interpolation=cv2.INTER_AREA)
    gbw = _align_ecc(ga, gb)
    diff = cv2.absdiff(ga, gbw)
    _, mask = cv2.threshold(diff, 28, 255, cv2.THRESH_BINARY)
    k = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=1)
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

    for old in cache.glob("overlay_*.png"):
        old.unlink(missing_ok=True)

    out_paths: list[Path] = []
    for i in range(n):
        a = cv2.imread(str(pages_a[i]), cv2.IMREAD_UNCHANGED)
        b = cv2.imread(str(pages_b[i]), cv2.IMREAD_UNCHANGED)
        if a is None or b is None:
            continue
        if a.ndim == 2:
            a = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
        if b.ndim == 2:
            b = cv2.cvtColor(b, cv2.COLOR_GRAY2BGR)
        if a.shape[2] == 4:
            a = cv2.cvtColor(a, cv2.COLOR_BGRA2BGR)
        if b.shape[2] == 4:
            b = cv2.cvtColor(b, cv2.COLOR_BGRA2BGR)
        # Normalize size to max WxH
        ha, wa = a.shape[:2]
        hb, wb = b.shape[:2]
        tw, th = max(wa, wb), max(ha, hb)
        if (wa, ha) != (tw, th):
            a = cv2.resize(a, (tw, th), interpolation=cv2.INTER_AREA)
        if (wb, hb) != (tw, th):
            b = cv2.resize(b, (tw, th), interpolation=cv2.INTER_AREA)
        ov = _overlay_diff(a, b)
        p = cache / f"overlay_{i + 1:04d}.png"
        cv2.imwrite(str(p), ov)
        out_paths.append(p)

    marker.write_text(expect, encoding="utf-8")
    return (out_paths, warn)
