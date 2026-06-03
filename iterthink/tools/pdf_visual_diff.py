"""Pixel diff between two PDFs: render, align, produce overlay PNGs (no Flet)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np

from iterthink import config
from iterthink.services.document_import import PdfProfileHeuristic, render_pdf_to_png_pages

_ALGO_VERSION = 4
_MIN_CC_AREA_PLAN = 48
_MIN_CC_SPAN_PLAN = 14
_MIN_CC_AREA_DEFAULT = 36
_DIFF_PERCENTILE_PLAN = 92.0
_DIFF_PERCENTILE_DEFAULT = 90.0
_DIFF_THRESH_FLOOR = 18.0
_DIFF_THRESH_CAP = 52.0


@dataclass
class PageDiffResult:
    overlay_path: Path
    confidence: float
    alignment: str
    warn: str | None = None


@cache
def _opencv():
    try:
        import cv2 as cv
        return cv
    except ImportError as e:
        raise ImportError(
            "PDF visual diff requires opencv-python-headless. Install with: pip install opencv-python-headless"
        ) from e


def _cache_dir(pdf_a: Path, pdf_b: Path, profile: PdfProfileHeuristic | None) -> Path:
    st_a, st_b = pdf_a.stat(), pdf_b.stat()
    key_src = (
        f"{_ALGO_VERSION}:{profile or 'text'}:{pdf_a.resolve()}:{st_a.st_mtime_ns}:{st_a.st_size}:"
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


def _normalize_gray(gray: np.ndarray) -> np.ndarray:
    cv = _opencv()
    clahe = cv.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _pixel_diff_mask(
    gray_a: np.ndarray,
    gray_b: np.ndarray,
    *,
    percentile: float,
) -> np.ndarray:
    """CLAHE-normalized blur diff; adaptive percentile threshold."""
    cv = _opencv()
    ga = _normalize_gray(gray_a)
    gb = _normalize_gray(gray_b)
    ba = cv.GaussianBlur(ga, (5, 5), 0)
    bb = cv.GaussianBlur(gb, (5, 5), 0)
    diff = cv.absdiff(ba, bb)
    nz = diff[diff > 0]
    if nz.size:
        thr = float(np.percentile(nz, percentile))
    else:
        thr = 28.0
    thr = max(_DIFF_THRESH_FLOOR, min(thr, _DIFF_THRESH_CAP))
    _, mask = cv.threshold(diff, thr, 255, cv.THRESH_BINARY)
    k = np.ones((3, 3), np.uint8)
    mask = cv.morphologyEx(mask, cv.MORPH_OPEN, k, iterations=1)
    mask = cv.morphologyEx(mask, cv.MORPH_CLOSE, k, iterations=1)
    return mask


def _filter_mask_by_cc(mask: np.ndarray, min_area: int) -> np.ndarray:
    cv = _opencv()
    n, labels, stats, _ = cv.connectedComponentsWithStats(mask, connectivity=8)
    clean = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv.CC_STAT_AREA] >= min_area:
            clean[labels == i] = 255
    return clean


def _filter_mask_by_cc_plan(
    mask: np.ndarray,
    *,
    min_area: int = _MIN_CC_AREA_PLAN,
    min_span: int = _MIN_CC_SPAN_PLAN,
) -> np.ndarray:
    """
    Drop tiny square specks (labels) but keep thin strokes (walls/doors)
    via minimum bbox width or height.
    """
    cv = _opencv()
    n, labels, stats, _ = cv.connectedComponentsWithStats(mask, connectivity=8)
    clean = np.zeros_like(mask)
    for i in range(1, n):
        area = stats[i, cv.CC_STAT_AREA]
        w = stats[i, cv.CC_STAT_WIDTH]
        h = stats[i, cv.CC_STAT_HEIGHT]
        if area >= min_area or max(w, h) >= min_span:
            clean[labels == i] = 255
    return clean


def _filter_diff_mask(mask: np.ndarray, pdf_profile: PdfProfileHeuristic | None) -> np.ndarray:
    if pdf_profile == "plan":
        return _filter_mask_by_cc_plan(mask)
    return _filter_mask_by_cc(mask, _MIN_CC_AREA_DEFAULT)


def _pad_to_canvas(bgr: np.ndarray, tw: int, th: int) -> np.ndarray:
    cv = _opencv()
    h, w = bgr.shape[:2]
    if w == tw and h == th:
        return bgr
    canvas = np.full((th, tw, 3), 255, dtype=np.uint8)
    canvas[0:h, 0:w] = bgr[:, :, :3] if bgr.shape[2] >= 3 else cv.cvtColor(bgr, cv.COLOR_GRAY2BGR)
    return canvas


def _align_ecc(gray_a: np.ndarray, gray_b: np.ndarray, motion: int) -> tuple[np.ndarray, float]:
    cv = _opencv()
    h, w = gray_a.shape[:2]
    if gray_b.shape != (h, w):
        gray_b = cv.resize(gray_b, (w, h), interpolation=cv.INTER_AREA)
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        criteria = (cv.TERM_CRITERIA_EPS | cv.TERM_CRITERIA_COUNT, 60, 1e-5)
        cc, warp = cv.findTransformECC(gray_a, gray_b, warp, motion, criteria, None, 5)
        warped = cv.warpAffine(gray_b, warp, (w, h), flags=cv.INTER_LINEAR + cv.WARP_INVERSE_MAP)
        return warped, float(cc)
    except cv.error:
        return gray_b, 0.0


def _align_orb(gray_a: np.ndarray, gray_b: np.ndarray) -> tuple[np.ndarray, float]:
    cv = _opencv()
    h, w = gray_a.shape[:2]
    if gray_b.shape != (h, w):
        gray_b = cv.resize(gray_b, (w, h), interpolation=cv.INTER_AREA)
    orb = cv.ORB_create(800)
    kpa, desa = orb.detectAndCompute(gray_a, None)
    kpb, desb = orb.detectAndCompute(gray_b, None)
    if desa is None or desb is None or len(kpa) < 4 or len(kpb) < 4:
        return gray_b, 0.0
    bf = cv.BFMatcher(cv.NORM_HAMMING, crossCheck=True)
    matches = bf.match(desa, desb)
    if len(matches) < 8:
        return gray_b, 0.0
    matches = sorted(matches, key=lambda m: m.distance)[:80]
    src = np.float32([kpb[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([kpa[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    H, mask = cv.findHomography(src, dst, cv.RANSAC, 4.0)
    if H is None or mask is None:
        return gray_b, 0.0
    inliers = float(mask.sum()) / max(len(mask), 1)
    if inliers < 0.25:
        return gray_b, inliers
    det = float(np.linalg.det(H[:2, :2]))
    if det < 0.5 or det > 2.0:
        return gray_b, inliers
    warped = cv.warpPerspective(gray_b, H, (w, h))
    return warped, inliers


def _align_page(gray_a: np.ndarray, gray_b: np.ndarray) -> tuple[np.ndarray, float, str]:
    cv = _opencv()
    ga = _normalize_gray(gray_a)
    gb = _normalize_gray(gray_b)
    wb, c0 = _align_ecc(ga, gb, cv.MOTION_EUCLIDEAN)
    if c0 >= 0.82:
        return wb, c0, "ecc_translation"
    wb, c1 = _align_ecc(ga, gb, cv.MOTION_AFFINE)
    if c1 >= 0.78:
        return wb, c1, "ecc_affine"
    wb, c2 = _align_orb(ga, gb)
    if c2 >= 0.30:
        return wb, c2, "orb_homography"
    return gb, max(c0, c1, c2), "none"


def _overlay_diff(
    bgr_a: np.ndarray,
    bgr_b_aligned: np.ndarray,
    gray_a: np.ndarray,
    gray_b: np.ndarray,
    *,
    pdf_profile: PdfProfileHeuristic | None = "plan",
) -> np.ndarray:
    ga = _normalize_gray(gray_a)
    gb = _normalize_gray(gray_b)
    pct = _DIFF_PERCENTILE_PLAN if pdf_profile == "plan" else _DIFF_PERCENTILE_DEFAULT
    mask = _pixel_diff_mask(ga, gb, percentile=pct)
    mask = _filter_diff_mask(mask, pdf_profile)
    add_m = ((gb.astype(np.int16) - ga.astype(np.int16)) > 8) & (mask > 0)
    rem_m = ((ga.astype(np.int16) - gb.astype(np.int16)) > 8) & (mask > 0)
    out = (bgr_a.astype(np.float32) * 0.55).clip(0, 255).astype(np.uint8)
    out[add_m] = (0, 200, 80)
    out[rem_m] = (40, 40, 220)
    return out


def _aspect_mismatch(a: np.ndarray, b: np.ndarray) -> bool:
    ha, wa = a.shape[:2]
    hb, wb = b.shape[:2]
    ra, rb = wa / max(ha, 1), wb / max(hb, 1)
    if ra <= 0 or rb <= 0:
        return True
    ratio = ra / rb
    return ratio < 0.55 or ratio > 1.8


def diff_page_pair_pngs(
    path_a: Path,
    path_b: Path,
    *,
    pdf_profile: PdfProfileHeuristic | None = "plan",
) -> PageDiffResult:
    cv = _opencv()
    a = cv.imread(str(path_a), cv.IMREAD_UNCHANGED)
    b = cv.imread(str(path_b), cv.IMREAD_UNCHANGED)
    if a is None or b is None:
        raise ValueError("Could not read page PNG")
    if a.ndim == 2:
        a = cv.cvtColor(a, cv.COLOR_GRAY2BGR)
    if b.ndim == 2:
        b = cv.cvtColor(b, cv.COLOR_GRAY2BGR)
    if a.shape[2] == 4:
        a = cv.cvtColor(a, cv.COLOR_BGRA2BGR)
    if b.shape[2] == 4:
        b = cv.cvtColor(b, cv.COLOR_BGRA2BGR)
    ha, wa = a.shape[:2]
    hb, wb = b.shape[:2]
    tw, th = max(wa, wb), max(ha, hb)
    a = _pad_to_canvas(a, tw, th)
    b = _pad_to_canvas(b, tw, th)
    warn: str | None = None
    if _aspect_mismatch(a, b):
        warn = "Page aspect ratios differ; alignment may be weak."
    ga, gb = _to_gray(a), _to_gray(b)
    gb_aligned, conf, method = _align_page(ga, gb)
    ov = _overlay_diff(a, b, ga, gb_aligned, pdf_profile=pdf_profile)
    out = path_a.parent / f"{path_a.stem}_diff_overlay.png"
    cv.imwrite(str(out), ov)
    meta_path = out.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps({"confidence": conf, "alignment": method, "warn": warn}),
        encoding="utf-8",
    )
    return PageDiffResult(out, conf, method, warn)


def diff_pdfs_to_overlay_paths(
    pdf_a: Path,
    pdf_b: Path,
    *,
    pdf_profile: PdfProfileHeuristic | None = "plan",
) -> tuple[list[Path], str | None, list[float]]:
    """
    Render both PDFs, align each page pair, write overlay PNGs.
    Returns (overlay paths, global warning or None, per-page confidence scores).
    """
    pages_a = render_pdf_to_png_pages(pdf_a, pdf_profile=pdf_profile or "plan")
    pages_b = render_pdf_to_png_pages(pdf_b, pdf_profile=pdf_profile or "plan")
    warn: str | None = None
    if len(pages_a) != len(pages_b):
        warn = f"Page count differs ({len(pages_a)} vs {len(pages_b)}); comparing first {min(len(pages_a), len(pages_b))}."
    n = min(len(pages_a), len(pages_b))
    if n == 0:
        return [], "No pages to compare.", []
    cache = _cache_dir(pdf_a, pdf_b, pdf_profile)
    marker = cache / ".ok"
    expect = f"{n}\n"
    if marker.is_file() and marker.read_text(encoding="utf-8") == expect:
        existing = sorted(cache.glob("overlay_*.png"))
        if len(existing) >= n:
            confs: list[float] = []
            for p in existing[:n]:
                mp = p.with_suffix(".meta.json")
                if mp.is_file():
                    try:
                        confs.append(float(json.loads(mp.read_text(encoding="utf-8")).get("confidence", 0.5)))
                    except (json.JSONDecodeError, TypeError, ValueError):
                        confs.append(0.5)
                else:
                    confs.append(0.5)
            return existing[:n], warn, confs

    for old in cache.glob("overlay_*"):
        if old.is_file():
            old.unlink(missing_ok=True)

    prof = pdf_profile or "plan"
    out_paths: list[Path] = []
    confidences: list[float] = []
    cv = _opencv()
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
        ha, wa = a.shape[:2]
        hb, wb = b.shape[:2]
        tw, th = max(wa, wb), max(ha, hb)
        a = _pad_to_canvas(a, tw, th)
        b = _pad_to_canvas(b, tw, th)
        ga, gb = _to_gray(a), _to_gray(b)
        gb_aligned, conf, method = _align_page(ga, gb)
        ov = _overlay_diff(a, b, ga, gb_aligned, pdf_profile=prof)
        p = cache / f"overlay_{i + 1:04d}.png"
        cv.imwrite(str(p), ov)
        p.with_suffix(".meta.json").write_text(
            json.dumps({"confidence": conf, "alignment": method}),
            encoding="utf-8",
        )
        out_paths.append(p)
        confidences.append(conf)

    marker.write_text(expect, encoding="utf-8")
    low = [c for c in confidences if c < 0.35]
    if low and warn is None:
        warn = f"Weak alignment on {len(low)} page(s); use side-by-side view."
    return out_paths, warn, confidences
