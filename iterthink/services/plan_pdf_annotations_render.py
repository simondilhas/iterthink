"""On-screen revision-cloud overlay rendering (Pillow → PNG bytes)."""

from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw


def revision_cloud_png(
    width: int,
    height: int,
    *,
    color: tuple[int, int, int, int] = (179, 143, 193, 220),
    stroke: int = 2,
) -> bytes:
    """Transparent PNG with a scalloped rectangle outline."""
    w = max(int(width), 8)
    h = max(int(height), 8)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    bump = max(6, min(w, h) // 12)
    pts: list[tuple[float, float]] = []

    def _edge(x0: float, y0: float, x1: float, y1: float, horizontal: bool) -> None:
        length = abs(x1 - x0) if horizontal else abs(y1 - y0)
        n = max(2, int(length / bump))
        for i in range(n + 1):
            t = i / n
            if horizontal:
                x = x0 + (x1 - x0) * t
                wave = bump * math.sin(t * math.pi * 4)
                pts.append((x, y0 + wave))
            else:
                y = y0 + (y1 - y0) * t
                wave = bump * math.sin(t * math.pi * 4)
                pts.append((x0 + wave, y))

    pad = stroke + bump
    _edge(pad, pad, w - pad, pad, True)
    _edge(w - pad, pad, w - pad, h - pad, False)
    _edge(w - pad, h - pad, pad, h - pad, True)
    _edge(pad, h - pad, pad, pad, False)
    if len(pts) >= 2:
        draw.line(pts + [pts[0]], fill=color, width=stroke, joint="curve")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()
