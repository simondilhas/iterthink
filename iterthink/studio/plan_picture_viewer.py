"""Reusable Flet plan / PDF page strip (Focus, History, Review)."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import flet as ft

from iterthink import config
from iterthink.studio.components import action_rail_icon_button_style
from iterthink.studio.util import ctrl_on_page

# Viewport height per page in compare/history stacked viewers.
_DEFAULT_PAGE_VIEWPORT_H = 520.0
_FOCUS_MIN_VIEWPORT_H = 100.0

_FOCUS_MIN_SCALE = 0.25
_FOCUS_MAX_SCALE = 8.0
_FOCUS_ZOOM_STEP = 1.25

_TOOLS_PILL_EST_W = 360.0
_TOOLS_PILL_EST_H = 40.0
_TOOLS_PILL_OFFSET = 8.0
_TOOLS_PILL_MARGIN = 6.0

_PANE_BORDER = ft.border.all(1, ft.Colors.with_opacity(0.35, ft.Colors.GREY_600))


def _clamp_tools_pill_position(
    local_x: float,
    local_y: float,
    *,
    stack_w: float,
    stack_h: float,
    pill_w: float = _TOOLS_PILL_EST_W,
    pill_h: float = _TOOLS_PILL_EST_H,
) -> tuple[float, float]:
    """Place pill near pointer, kept inside the viewport stack."""
    sw = max(float(stack_w), pill_w + 2 * _TOOLS_PILL_MARGIN)
    sh = max(float(stack_h), pill_h + 2 * _TOOLS_PILL_MARGIN)
    left = float(local_x) + _TOOLS_PILL_OFFSET
    top = float(local_y) + _TOOLS_PILL_OFFSET
    left = max(
        _TOOLS_PILL_MARGIN,
        min(left, sw - pill_w - _TOOLS_PILL_MARGIN),
    )
    top = max(
        _TOOLS_PILL_MARGIN,
        min(top, sh - pill_h - _TOOLS_PILL_MARGIN),
    )
    return left, top


@dataclass(frozen=True)
class _ContainRect:
    x0: float
    y0: float
    w: float
    h: float


def _read_png_pixel_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        return 1, 1


def _width_fit_size(img_w: int, img_h: int, viewport_w: float) -> tuple[float, float]:
    """Scale page image so it spans the full viewport width (height may exceed viewport)."""
    iw, ih = max(int(img_w), 1), max(int(img_h), 1)
    vw = max(float(viewport_w), 1.0)
    scale = vw / iw
    return vw, max(ih * scale, 1.0)


def _contain_fit_size(
    img_w: int, img_h: int, viewport_w: float, viewport_h: float
) -> tuple[float, float]:
    """Scale so the whole page is visible inside the viewport (letterbox via pan/zoom reset)."""
    rect = _contain_rect(viewport_w, viewport_h, img_w, img_h)
    return rect.w, rect.h


def _layout_focus_page_image_width(
    img: ft.Image,
    path: Path,
    *,
    viewport_w: float,
) -> tuple[float, float]:
    iw, ih = _read_png_pixel_size(path)
    dw, dh = _width_fit_size(iw, ih, viewport_w)
    img.width = dw
    img.height = dh
    return dw, dh


def _layout_focus_page_image_contain(
    img: ft.Image,
    path: Path,
    *,
    viewport_w: float,
    viewport_h: float,
) -> tuple[float, float]:
    iw, ih = _read_png_pixel_size(path)
    dw, dh = _contain_fit_size(iw, ih, viewport_w, viewport_h)
    img.width = dw
    img.height = dh
    return dw, dh


def _contain_rect(viewport_w: float, viewport_h: float, img_w: int, img_h: int) -> _ContainRect:
    iw, ih = max(int(img_w), 1), max(int(img_h), 1)
    vw, vh = max(float(viewport_w), 1.0), max(float(viewport_h), 1.0)
    scale = min(vw / iw, vh / ih)
    dw, dh = iw * scale, ih * scale
    return _ContainRect((vw - dw) * 0.5, (vh - dh) * 0.5, dw, dh)


def _viewer_meta(iv: ft.InteractiveViewer) -> dict[str, Any]:
    return iv.data if isinstance(iv.data, dict) else {}


def _viewer_viewport(iv: ft.InteractiveViewer) -> tuple[float, float]:
    w = float(iv.width or 0)
    h = float(iv.height or 0)
    meta = _viewer_meta(iv)
    if h <= 0:
        h = float(meta.get("viewport_h", _DEFAULT_PAGE_VIEWPORT_H))
    if w <= 0:
        w = float(meta.get("viewport_w", h * 1.35))
    return w, h


def _viewport_to_normalized(fx: float, fy: float, rect: _ContainRect) -> tuple[float, float]:
    if rect.w <= 1e-6 or rect.h <= 1e-6:
        return 0.5, 0.5
    u = (fx - rect.x0) / rect.w
    v = (fy - rect.y0) / rect.h
    return max(0.0, min(1.0, u)), max(0.0, min(1.0, v))


def _normalized_to_viewport(u: float, v: float, rect: _ContainRect) -> tuple[float, float]:
    return rect.x0 + u * rect.w, rect.y0 + v * rect.h


def _focus_image_layout_rect(viewer: PlanFocusViewer) -> _ContainRect:
    vw = max(float(viewer._viewport_w), 1.0)
    if viewer.page_count <= 0:
        return _ContainRect(0.0, 0.0, vw, 1.0)
    path = viewer._paths[viewer.current_index]
    iw, ih = _read_png_pixel_size(path)
    if viewer._layout_mode == "contain" and viewer._viewport_h >= _FOCUS_MIN_VIEWPORT_H:
        vh = max(float(viewer._viewport_h), _FOCUS_MIN_VIEWPORT_H)
        return _contain_rect(vw, vh, iw, ih)
    dw, dh = _width_fit_size(iw, ih, vw)
    return _ContainRect(0.0, 0.0, dw, dh)


def viewport_local_to_page_norm(
    local_x: float, local_y: float, viewer: PlanFocusViewer
) -> tuple[float, float] | None:
    """Map stack-local click to normalized (u, v) on the current page image."""
    rect = _focus_image_layout_rect(viewer)
    if rect.w <= 1e-6 or rect.h <= 1e-6:
        return None
    u, v = _viewport_to_normalized(float(local_x), float(local_y), rect)
    if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0:
        return None
    return u, v


def page_norm_to_viewport_px(
    u: float, v: float, viewer: PlanFocusViewer
) -> tuple[float, float]:
    """Map normalized page coords to stack-local pixels for the current page."""
    rect = _focus_image_layout_rect(viewer)
    return _normalized_to_viewport(float(u), float(v), rect)


@dataclass(frozen=True)
class PlanMarkerView:
    """Lightweight marker for on-screen overlay (no DB dependency)."""

    kind: str
    page_index: int
    norm_x: float
    norm_y: float
    bbox: dict[str, float] | None = None


def _map_focal_to_peer(
    src: ft.InteractiveViewer,
    dst: ft.InteractiveViewer,
    fx: float,
    fy: float,
) -> tuple[float, float]:
    """Map viewport focal on src to the same plan-normalized point on dst."""
    sw, sh = _viewer_viewport(src)
    dw, dh = _viewer_viewport(dst)
    sm = _viewer_meta(src)
    dm = _viewer_meta(dst)
    s_rect = _contain_rect(sw, sh, int(sm.get("img_w", 1)), int(sm.get("img_h", 1)))
    d_rect = _contain_rect(dw, dh, int(dm.get("img_w", 1)), int(dm.get("img_h", 1)))
    u, v = _viewport_to_normalized(fx, fy, s_rect)
    return _normalized_to_viewport(u, v, d_rect)


async def _zoom_at_focal(
    iv: ft.InteractiveViewer,
    fx: float,
    fy: float,
    factor: float,
) -> None:
    """Scale around viewport point (fx, fy), not the viewer center."""
    if abs(factor - 1.0) < 1e-4:
        return
    await iv.pan(float(fx) * (1.0 - factor), float(fy) * (1.0 - factor))
    await iv.zoom(factor)


InteractionMode = Literal["idle", "place_comment", "draw_cloud"]


@dataclass
class PlanFocusViewer:
    """Focus: one full-height page with bottom navigation."""

    root: ft.Column
    page_count: int
    current_index: int
    _paths: list[Path] = field(repr=False)
    _image: ft.Image = field(repr=False)
    _viewer: ft.InteractiveViewer = field(repr=False)
    _page_frame: ft.Container = field(repr=False)
    _page_label: ft.Text = field(repr=False)
    _prev_btn: ft.IconButton = field(repr=False)
    _next_btn: ft.IconButton = field(repr=False)
    _fit_page_btn: ft.IconButton = field(repr=False)
    _fit_width_btn: ft.IconButton = field(repr=False)
    _pan_btn: ft.IconButton = field(repr=False)
    _zoom_out_btn: ft.IconButton = field(repr=False)
    _zoom_in_btn: ft.IconButton = field(repr=False)
    _comment_btn: ft.IconButton = field(repr=False)
    _draw_cloud_btn: ft.IconButton = field(repr=False)
    _export_btn: ft.IconButton = field(repr=False)
    _viewport_stack: ft.Stack = field(repr=False)
    _annotations_overlay: ft.Stack = field(repr=False)
    _draw_rubber_band: ft.Container = field(repr=False)
    _tools_pill_host: ft.Container = field(repr=False)
    _on_page_change: Callable[[int], None] | None = field(default=None, repr=False)
    _on_place_comment: Callable[[float, float], None] | None = field(default=None, repr=False)
    _on_revision_cloud: Callable[[float, float, float, float], None] | None = field(
        default=None, repr=False
    )
    _on_export_pdf: Callable[[], None] | None = field(default=None, repr=False)
    _viewport_w: float = field(default=0.0, repr=False)
    _viewport_h: float = field(default=0.0, repr=False)
    _stack_w: float = field(default=0.0, repr=False)
    _stack_h: float = field(default=0.0, repr=False)
    _layout_mode: str = field(default="width", repr=False)
    interaction_mode: InteractionMode = field(default="idle", repr=False)
    _markers: list[PlanMarkerView] = field(default_factory=list, repr=False)
    _draw_start: tuple[float, float] | None = field(default=None, repr=False)

    def _point_in_tools_pill(self, local_x: float, local_y: float) -> bool:
        host = self._tools_pill_host
        if not bool(getattr(host, "visible", False)):
            return False
        left = float(host.left or 0)
        top = float(host.top or 0)
        return (
            left <= float(local_x) <= left + _TOOLS_PILL_EST_W
            and top <= float(local_y) <= top + _TOOLS_PILL_EST_H
        )

    def _reposition_tools_pill(self, local_x: float, local_y: float) -> None:
        sw = max(self._stack_w, self._viewport_w, 1.0)
        sh = max(self._stack_h, self._viewport_h, _FOCUS_MIN_VIEWPORT_H)
        left, top = _clamp_tools_pill_position(local_x, local_y, stack_w=sw, stack_h=sh)
        self._tools_pill_host.left = left
        self._tools_pill_host.top = top
        self._tools_pill_host.right = None

    def show_tools_pill(self, local_x: float, local_y: float) -> None:
        if self.page_count <= 0:
            return
        self._reposition_tools_pill(local_x, local_y)
        self._tools_pill_host.visible = True
        if ctrl_on_page(self._tools_pill_host):
            self._tools_pill_host.update()

    def hide_tools_pill(self) -> None:
        if not bool(getattr(self._tools_pill_host, "visible", False)):
            return
        self._tools_pill_host.visible = False
        if ctrl_on_page(self._tools_pill_host):
            self._tools_pill_host.update()

    def set_interaction_mode(self, mode: InteractionMode) -> None:
        self.interaction_mode = mode
        if mode != "draw_cloud":
            self._clear_draw_rubber_band()
        if mode == "idle":
            return
        self.hide_tools_pill()

    def set_markers(self, markers: list[PlanMarkerView]) -> None:
        self._markers = list(markers)
        self.refresh_annotations_overlay()

    def refresh_annotations_overlay(self) -> None:
        from iterthink.services.plan_pdf_annotations_render import revision_cloud_png

        controls: list[ft.Control] = []
        page_ix = self.current_index
        for m in self._markers:
            if int(m.page_index) != page_ix:
                continue
            if m.kind == "pin":
                lx, ly = page_norm_to_viewport_px(m.norm_x, m.norm_y, self)
                controls.append(
                    ft.Container(
                        left=lx - 12,
                        top=ly - 24,
                        content=ft.Icon(ft.Icons.COMMENT, size=22, color=config.PRIMARY_COLOR),
                    )
                )
            elif m.kind == "revision_cloud" and m.bbox:
                x0, y0, x1, y1 = m.bbox["x0"], m.bbox["y0"], m.bbox["x1"], m.bbox["y1"]
                lx0, ly0 = page_norm_to_viewport_px(x0, y0, self)
                lx1, ly1 = page_norm_to_viewport_px(x1, y1, self)
                w = max(int(lx1 - lx0), 8)
                h = max(int(ly1 - ly0), 8)
                b64 = base64.b64encode(revision_cloud_png(w, h)).decode("ascii")
                controls.append(
                    ft.Container(
                        left=min(lx0, lx1),
                        top=min(ly0, ly1),
                        width=w,
                        height=h,
                        content=ft.Image(
                            src_base64=b64,
                            width=w,
                            height=h,
                            fit=ft.BoxFit.FILL,
                        ),
                    )
                )
        self._annotations_overlay.controls = controls
        if ctrl_on_page(self._annotations_overlay):
            self._annotations_overlay.update()

    def _clear_draw_rubber_band(self) -> None:
        self._draw_start = None
        self._draw_rubber_band.visible = False
        self._draw_rubber_band.width = None
        self._draw_rubber_band.height = None
        if ctrl_on_page(self._draw_rubber_band):
            self._draw_rubber_band.update()

    def _update_draw_rubber_band(self, x0: float, y0: float, x1: float, y1: float) -> None:
        left = min(x0, x1)
        top = min(y0, y1)
        w = max(abs(x1 - x0), 2.0)
        h = max(abs(y1 - y0), 2.0)
        self._draw_rubber_band.left = left
        self._draw_rubber_band.top = top
        self._draw_rubber_band.width = w
        self._draw_rubber_band.height = h
        self._draw_rubber_band.visible = True
        if ctrl_on_page(self._draw_rubber_band):
            self._draw_rubber_band.update()

    def handle_viewport_tap(self, local_x: float, local_y: float) -> None:
        if self.interaction_mode == "place_comment":
            norm = viewport_local_to_page_norm(local_x, local_y, self)
            self.interaction_mode = "idle"
            if norm is not None and self._on_place_comment is not None:
                self._on_place_comment(norm[0], norm[1])
            return
        if self.interaction_mode == "draw_cloud":
            return
        self.on_viewport_tap(local_x, local_y)

    def on_viewport_tap(self, local_x: float, local_y: float) -> None:
        """Show pill at click; hide when clicking outside an already-visible pill."""
        if self._tools_pill_host.visible:
            if self._point_in_tools_pill(local_x, local_y):
                return
            self.hide_tools_pill()
            return
        self.show_tools_pill(local_x, local_y)

    def handle_draw_pan_start(self, local_x: float, local_y: float) -> None:
        if self.interaction_mode != "draw_cloud":
            return
        self._draw_start = (float(local_x), float(local_y))
        self._update_draw_rubber_band(local_x, local_y, local_x, local_y)

    def handle_draw_pan_update(self, local_x: float, local_y: float) -> None:
        if self.interaction_mode != "draw_cloud" or self._draw_start is None:
            return
        x0, y0 = self._draw_start
        self._update_draw_rubber_band(x0, y0, local_x, local_y)

    def handle_draw_pan_end(self, local_x: float, local_y: float) -> None:
        if self.interaction_mode != "draw_cloud" or self._draw_start is None:
            return
        x0, y0 = self._draw_start
        self._clear_draw_rubber_band()
        self.interaction_mode = "idle"
        n0 = viewport_local_to_page_norm(x0, y0, self)
        n1 = viewport_local_to_page_norm(local_x, local_y, self)
        if n0 is None or n1 is None or self._on_revision_cloud is None:
            return
        if abs(n0[0] - n1[0]) < 0.01 and abs(n0[1] - n1[1]) < 0.01:
            return
        self._on_revision_cloud(n0[0], n0[1], n1[0], n1[1])

    def _apply_image_layout(self) -> None:
        if self.page_count <= 0 or self._viewport_w <= 0:
            return
        path = self._paths[self.current_index]
        if self._layout_mode == "contain" and self._viewport_h >= _FOCUS_MIN_VIEWPORT_H:
            _layout_focus_page_image_contain(
                self._image,
                path,
                viewport_w=self._viewport_w,
                viewport_h=self._viewport_h,
            )
        else:
            _layout_focus_page_image_width(
                self._image,
                path,
                viewport_w=self._viewport_w,
            )

    def sync_viewport(self, viewport_w: float, viewport_h: float | None = None) -> None:
        """Size page frame and image from the compose column (not intrinsic image width)."""
        vw = max(1.0, float(viewport_w))
        vh = max(
            _FOCUS_MIN_VIEWPORT_H,
            float(viewport_h if viewport_h is not None else self._viewport_h),
        )
        self._viewport_w = vw
        self._viewport_h = vh
        self.hide_tools_pill()
        frame = self._page_frame
        frame.width = vw
        frame.height = vh
        if self.page_count > 0:
            self._apply_image_layout()
        self.refresh_annotations_overlay()
        for c in (frame, self._image, self._viewer):
            if ctrl_on_page(c):
                c.update()

    def _clamp_index(self, page_index: int) -> int:
        if self.page_count <= 0:
            return 0
        return max(0, min(int(page_index), self.page_count - 1))

    def _sync_nav_chrome(self) -> None:
        n = self.page_count
        i = self.current_index
        if n <= 0:
            self._page_label.value = "No pages"
            self._prev_btn.disabled = True
            self._next_btn.disabled = True
        else:
            self._page_label.value = f"Page {i + 1} / {n}"
            self._prev_btn.disabled = i <= 0
            self._next_btn.disabled = i >= n - 1
        tool_disabled = n <= 0
        for btn in (
            self._fit_page_btn,
            self._fit_width_btn,
            self._pan_btn,
            self._zoom_out_btn,
            self._zoom_in_btn,
            self._comment_btn,
            self._draw_cloud_btn,
            self._export_btn,
        ):
            btn.disabled = tool_disabled
        for c in (
            self._page_label,
            self._prev_btn,
            self._next_btn,
            self._fit_page_btn,
            self._fit_width_btn,
            self._pan_btn,
            self._zoom_out_btn,
            self._zoom_in_btn,
            self._comment_btn,
            self._draw_cloud_btn,
            self._export_btn,
        ):
            if ctrl_on_page(c):
                c.update()

    def _sync_pan_button(self) -> None:
        from iterthink import config

        active = bool(self._viewer.pan_enabled)
        self._pan_btn.icon_color = config.PRIMARY_COLOR if active else config.ON_SURFACE_VARIANT
        if ctrl_on_page(self._pan_btn):
            self._pan_btn.update()

    def fit_to_viewport(self) -> None:
        """Fit whole page in the viewport (contain) and reset pan/zoom."""
        if self.page_count <= 0:
            return
        self._layout_mode = "contain"
        self._apply_image_layout()
        self._reset_viewer_transform()
        if ctrl_on_page(self._image):
            self._image.update()

    def fit_to_width(self) -> None:
        """Span the viewport width; pan vertically for tall pages."""
        if self.page_count <= 0:
            return
        self._layout_mode = "width"
        self._apply_image_layout()
        self._reset_viewer_transform()
        if ctrl_on_page(self._image):
            self._image.update()

    def toggle_pan(self) -> None:
        if self.page_count <= 0:
            return
        self._viewer.pan_enabled = not bool(self._viewer.pan_enabled)
        self._sync_pan_button()
        if ctrl_on_page(self._viewer):
            self._viewer.update()

    async def zoom_step_async(self, factor: float) -> None:
        if self.page_count <= 0 or abs(factor - 1.0) < 1e-4:
            return
        w = max(self._viewport_w, 1.0)
        h = max(self._viewport_h, _FOCUS_MIN_VIEWPORT_H)
        await _zoom_at_focal(self._viewer, w * 0.5, h * 0.5, factor)

    def _reset_viewer_transform(self) -> None:
        reset = getattr(self._viewer, "reset", None)
        if callable(reset):
            try:
                pg = getattr(self._viewer, "page", None)
                if pg is not None:
                    pg.run_task(reset)
            except (RuntimeError, TypeError, AttributeError, ValueError):
                pass

    def set_page(self, page_index: int) -> None:
        """Show ``page_index`` and refresh nav chrome."""
        self.hide_tools_pill()
        ix = self._clamp_index(page_index)
        self.current_index = ix
        if self.page_count <= 0:
            self._image.src = None
        else:
            self._image.src = str(self._paths[ix])
            self._apply_image_layout()
            self._reset_viewer_transform()
        if ctrl_on_page(self._image):
            self._image.update()
        if ctrl_on_page(self._viewer):
            self._viewer.update()
        self.refresh_annotations_overlay()
        self._sync_nav_chrome()
        if self._on_page_change is not None:
            self._on_page_change(ix)

    def go_relative(self, delta: int) -> None:
        if delta == 0 or self.page_count <= 0:
            return
        self.set_page(self.current_index + delta)

    async def show_page(self, page_index: int) -> None:
        if not ctrl_on_page(self.root) or not bool(getattr(self.root, "visible", True)):
            return
        self.set_page(page_index)
        await asyncio.sleep(0.02)


def build_plan_focus_viewer(
    page_png_paths: list[Path],
    *,
    initial_page_index: int = 0,
    on_page_change: Callable[[int], None] | None = None,
    on_place_comment: Callable[[float, float], None] | None = None,
    on_revision_cloud: Callable[[float, float, float, float], None] | None = None,
    on_export_pdf: Callable[[], None] | None = None,
    min_scale: float = _FOCUS_MIN_SCALE,
    max_scale: float = _FOCUS_MAX_SCALE,
) -> PlanFocusViewer:
    """
    Single-page plan viewport (fills column height) with ‹ Page N / M › navigation.
    """
    paths = list(page_png_paths)
    n = len(paths)
    ix0 = max(0, min(int(initial_page_index), max(n - 1, 0)))

    if n > 0:
        img = ft.Image(
            src=str(paths[ix0]),
            fit=ft.BoxFit.FILL,
            filter_quality=ft.FilterQuality.MEDIUM,
        )
    else:
        img = ft.Image(src="", fit=ft.BoxFit.FILL, visible=False)

    viewer = ft.InteractiveViewer(
        content=img if n > 0 else ft.Text("No pages", color=ft.Colors.GREY_500),
        pan_enabled=n > 0,
        scale_enabled=n > 0,
        min_scale=min_scale,
        max_scale=max_scale,
        constrained=False,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        boundary_margin=ft.margin.all(48),
        expand=True,
    )

    holder: dict[str, PlanFocusViewer | None] = {"v": None}

    def _on_frame_resize(e: ft.LayoutSizeChangeEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        vw = max(1.0, float(e.width))
        vh = max(_FOCUS_MIN_VIEWPORT_H, float(e.height))
        if vw > 1.0:
            v.sync_viewport(vw, vh)

    page_frame = ft.Container(
        content=viewer,
        expand=True,
        width=None,
        alignment=ft.Alignment.TOP_LEFT,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        on_size_change=_on_frame_resize,
    )

    def _on_viewport_tap_down(e: ft.TapEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        lp = e.local_position
        if lp is None:
            return
        v.handle_viewport_tap(float(lp.x), float(lp.y))

    viewport_gesture = ft.GestureDetector(
        content=page_frame,
        expand=True,
        on_tap_down=_on_viewport_tap_down,
    )

    annotations_overlay = ft.Stack([], expand=True)

    def _on_draw_start(e: ft.DragStartEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        lp = e.local_position
        if lp is None:
            return
        v.handle_draw_pan_start(float(lp.x), float(lp.y))

    def _on_draw_update(e: ft.DragUpdateEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        lp = e.local_position
        if lp is None:
            return
        v.handle_draw_pan_update(float(lp.x), float(lp.y))

    def _on_draw_end(e: ft.DragEndEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        lp = getattr(e, "local_position", None)
        if lp is not None:
            v.handle_draw_pan_end(float(lp.x), float(lp.y))
            return
        if v._draw_start is not None:
            x0, y0 = v._draw_start
            v.handle_draw_pan_end(x0, y0)

    draw_gesture = ft.GestureDetector(
        content=annotations_overlay,
        expand=True,
        on_pan_start=_on_draw_start,
        on_pan_update=_on_draw_update,
        on_pan_end=_on_draw_end,
    )

    draw_rubber_band = ft.Container(
        visible=False,
        bgcolor=ft.Colors.with_opacity(0.18, config.PRIMARY_COLOR),
        border=ft.border.all(1, config.PRIMARY_COLOR),
    )

    page_label = ft.Text(
        f"Page {ix0 + 1} / {n}" if n > 0 else "No pages",
        size=13,
        color=ft.Colors.GREY_400,
    )
    def _prev(_e: ft.ControlEvent) -> None:
        v = holder["v"]
        if v is not None:
            v.go_relative(-1)

    def _next(_e: ft.ControlEvent) -> None:
        v = holder["v"]
        if v is not None:
            v.go_relative(1)

    prev_btn = ft.IconButton(
        ft.Icons.CHEVRON_LEFT,
        tooltip="Previous page",
        disabled=n <= 0 or ix0 <= 0,
        on_click=_prev,
    )
    next_btn = ft.IconButton(
        ft.Icons.CHEVRON_RIGHT,
        tooltip="Next page",
        disabled=n <= 0 or ix0 >= n - 1,
        on_click=_next,
    )

    def _fit_page(_e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is not None:
            v.fit_to_viewport()

    def _fit_width(_e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is not None:
            v.fit_to_width()

    def _pan(_e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is not None:
            v.toggle_pan()

    def _zoom_out(_e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        pg = getattr(v._viewer, "page", None)
        if pg is not None:
            pg.run_task(v.zoom_step_async, 1.0 / _FOCUS_ZOOM_STEP)

    def _zoom_in(_e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        pg = getattr(v._viewer, "page", None)
        if pg is not None:
            pg.run_task(v.zoom_step_async, _FOCUS_ZOOM_STEP)

    _tool_disabled = n <= 0
    fit_page_btn = ft.IconButton(
        ft.Icons.FIT_SCREEN,
        tooltip="Fit page in viewport",
        disabled=_tool_disabled,
        on_click=_fit_page,
    )
    fit_width_btn = ft.IconButton(
        ft.Icons.WIDTH_FULL,
        tooltip="Fit to width",
        disabled=_tool_disabled,
        on_click=_fit_width,
    )
    pan_btn = ft.IconButton(
        ft.Icons.PAN_TOOL,
        tooltip="Pan (toggle)",
        disabled=_tool_disabled,
        on_click=_pan,
    )
    zoom_out_btn = ft.IconButton(
        ft.Icons.ZOOM_OUT,
        tooltip="Zoom out",
        disabled=_tool_disabled,
        on_click=_zoom_out,
    )
    zoom_in_btn = ft.IconButton(
        ft.Icons.ZOOM_IN,
        tooltip="Zoom in",
        disabled=_tool_disabled,
        on_click=_zoom_in,
    )

    def _comment_mode(_e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is not None:
            v.set_interaction_mode("place_comment")

    def _draw_mode(_e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is not None:
            v.set_interaction_mode("draw_cloud")

    def _export(_e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is not None and v._on_export_pdf is not None:
            v.hide_tools_pill()
            v._on_export_pdf()

    comment_btn = ft.IconButton(
        ft.Icons.COMMENT,
        tooltip="Place comment",
        disabled=_tool_disabled,
        on_click=_comment_mode,
    )
    draw_cloud_btn = ft.IconButton(
        ft.Icons.CLOUD,
        tooltip="Draw revision cloud",
        disabled=_tool_disabled,
        on_click=_draw_mode,
    )
    export_btn = ft.IconButton(
        ft.Icons.FILE_DOWNLOAD,
        tooltip="Export annotated PDF",
        disabled=_tool_disabled,
        on_click=_export,
    )
    _tool_btn_style = action_rail_icon_button_style()
    for btn in (
        fit_page_btn,
        fit_width_btn,
        pan_btn,
        zoom_out_btn,
        zoom_in_btn,
        comment_btn,
        draw_cloud_btn,
        export_btn,
    ):
        btn.style = _tool_btn_style

    tools_pill_row = ft.Row(
        [
            fit_page_btn,
            fit_width_btn,
            pan_btn,
            zoom_out_btn,
            zoom_in_btn,
            comment_btn,
            draw_cloud_btn,
            export_btn,
        ],
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
    )
    tools_pill_host = ft.Container(
        visible=False,
        bgcolor=ft.Colors.with_opacity(0.94, config.SURFACE),
        border=ft.border.all(1, ft.Colors.with_opacity(0.35, config.OUTLINE)),
        border_radius=20,
        padding=ft.padding.symmetric(horizontal=4, vertical=0),
        content=tools_pill_row,
        left=0,
        top=0,
        right=None,
    )

    def _on_stack_resize(e: ft.LayoutSizeChangeEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        v._stack_w = max(1.0, float(e.width))
        v._stack_h = max(_FOCUS_MIN_VIEWPORT_H, float(e.height))

    viewport_stack = ft.Stack(
        [viewport_gesture, draw_gesture, draw_rubber_band, tools_pill_host],
        expand=True,
        clip_behavior=ft.ClipBehavior.NONE,
    )
    viewport_stack.on_size_change = _on_stack_resize

    page_nav_row = ft.Container(
        height=40,
        content=ft.Row(
            [prev_btn, page_label, next_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
    )
    col = ft.Column(
        [viewport_stack, page_nav_row],
        expand=True,
        spacing=0,
    )
    focus = PlanFocusViewer(
        root=col,
        page_count=n,
        current_index=ix0,
        _paths=paths,
        _image=img,
        _viewer=viewer,
        _page_frame=page_frame,
        _page_label=page_label,
        _prev_btn=prev_btn,
        _next_btn=next_btn,
        _fit_page_btn=fit_page_btn,
        _fit_width_btn=fit_width_btn,
        _pan_btn=pan_btn,
        _zoom_out_btn=zoom_out_btn,
        _zoom_in_btn=zoom_in_btn,
        _comment_btn=comment_btn,
        _draw_cloud_btn=draw_cloud_btn,
        _export_btn=export_btn,
        _viewport_stack=viewport_stack,
        _annotations_overlay=annotations_overlay,
        _draw_rubber_band=draw_rubber_band,
        _tools_pill_host=tools_pill_host,
        _on_page_change=on_page_change,
        _on_place_comment=on_place_comment,
        _on_revision_cloud=on_revision_cloud,
        _on_export_pdf=on_export_pdf,
    )
    holder["v"] = focus
    focus._sync_pan_button()
    return focus


def plan_picture_compare_column(
    page_png_paths: list[Path],
    *,
    page_viewport_height: float = _DEFAULT_PAGE_VIEWPORT_H,
    min_scale: float = _FOCUS_MIN_SCALE,
    max_scale: float = _FOCUS_MAX_SCALE,
) -> tuple[ft.Column, list[ft.InteractiveViewer]]:
    """
    One ``InteractiveViewer`` per page for a compare column (baseline or candidate).
    Outer ``ListView`` scrolls; wire pairs with :func:`wire_synced_interactive_viewer_pair`.
    """
    viewers: list[ft.InteractiveViewer] = []
    rows: list[ft.Control] = []
    paths = list(page_png_paths)
    for i, p in enumerate(paths):
        iw, ih = _read_png_pixel_size(p)
        img = ft.Image(
            src=str(p),
            fit=ft.BoxFit.CONTAIN,
            filter_quality=ft.FilterQuality.MEDIUM,
        )
        iv = ft.InteractiveViewer(
            content=img,
            pan_enabled=True,
            scale_enabled=True,
            min_scale=min_scale,
            max_scale=max_scale,
            constrained=True,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            boundary_margin=ft.margin.all(120),
            interaction_update_interval=50,
            data={"img_w": iw, "img_h": ih, "viewport_h": page_viewport_height},
        )
        viewers.append(iv)
        rows.append(
            ft.Container(
                content=ft.Container(
                    content=iv,
                    height=page_viewport_height,
                    alignment=ft.Alignment.TOP_CENTER,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    border=_PANE_BORDER,
                    border_radius=8,
                ),
                padding=ft.padding.only(bottom=8 if i + 1 < len(paths) else 0),
            )
        )
    col = ft.Column(
        rows,
        spacing=0,
        tight=True,
        scroll=None,
        expand=True,
    )
    return col, viewers


def wire_synced_interactive_viewer_pair(
    left: ft.InteractiveViewer,
    right: ft.InteractiveViewer,
    page: ft.Page,
) -> None:
    """
    Mirror pan/zoom between two viewers at the same plan-normalized coordinate.

    Flet does not expose transform matrices; sync is delta-based. Slight drift is
    possible when baseline and candidate page aspect ratios differ strongly.
    """
    state: dict[str, Any] = {"guard": False, "gesture_scale": 1.0}

    async def _mirror(
        src: ft.InteractiveViewer,
        peer: ft.InteractiveViewer,
        e: ft.ScaleUpdateEvent,
    ) -> None:
        if state["guard"]:
            return
        state["guard"] = True
        try:
            lp = e.local_focal_point
            fx, fy = float(lp.x), float(lp.y)
            pfx, pfy = _map_focal_to_peer(src, peer, fx, fy)
            d = e.focal_point_delta
            cur = float(e.scale)
            prev = float(state.get("gesture_scale", 1.0))
            factor = cur / prev if prev > 1e-9 else 1.0
            if abs(factor - 1.0) > 1e-4:
                await _zoom_at_focal(peer, pfx, pfy, factor)
            else:
                await peer.pan(float(d.x), float(d.y))
            state["gesture_scale"] = cur
        except (RuntimeError, TypeError, AttributeError, ValueError):
            pass
        finally:
            state["guard"] = False

    def _reset(_e: ft.ControlEvent | None = None) -> None:
        state["gesture_scale"] = 1.0

    def _schedule(
        src: ft.InteractiveViewer,
        peer: ft.InteractiveViewer,
        e: ft.ScaleUpdateEvent,
    ) -> None:
        page.run_task(_mirror, src, peer, e)

    left.on_interaction_start = _reset
    right.on_interaction_start = _reset
    left.on_interaction_end = _reset
    right.on_interaction_end = _reset
    left.on_interaction_update = lambda e: _schedule(left, right, e)
    right.on_interaction_update = lambda e: _schedule(right, left, e)


def plan_picture_column(
    page_png_paths: list[Path],
    *,
    page_viewport_height: float = _DEFAULT_PAGE_VIEWPORT_H,
    max_scale: float = 4.0,
    min_scale: float = 0.6,
    inner_scroll: bool = True,
) -> ft.Column:
    """
    Vertical strip of page images.

    With ``inner_scroll=True`` (default), each page uses ``InteractiveViewer`` for
    zoom/pan and the column scrolls itself.

    With ``inner_scroll=False``, each page is a plain ``Image`` so an outer
    ``ListView`` receives vertical wheel scroll.
    """
    rows: list[ft.Control] = []
    for i, p in enumerate(page_png_paths):
        img = ft.Image(
            src=str(p),
            fit=ft.BoxFit.CONTAIN,
            filter_quality=ft.FilterQuality.MEDIUM,
        )
        if inner_scroll:
            page_content: ft.Control = ft.InteractiveViewer(
                content=img,
                pan_enabled=True,
                scale_enabled=True,
                min_scale=min_scale,
                max_scale=max_scale,
                constrained=True,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            )
        else:
            page_content = img
        rows.append(
            ft.Container(
                content=page_content,
                height=page_viewport_height,
                alignment=ft.Alignment.TOP_CENTER,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                border=_PANE_BORDER,
                border_radius=8,
            )
        )
        if i + 1 < len(page_png_paths):
            rows.append(ft.Container(height=8))
    return ft.Column(
        rows,
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO if inner_scroll else None,
        expand=True,
    )


def plan_picture_single_viewport(
    page_png_paths: list[Path],
    *,
    page_index: int,
    page_viewport_height: float = _DEFAULT_PAGE_VIEWPORT_H,
    max_scale: float = 4.0,
    min_scale: float = 0.6,
) -> ft.Container:
    """One page by index; empty placeholder if index out of range."""
    if page_index < 0 or page_index >= len(page_png_paths):
        return ft.Container(
            height=page_viewport_height,
            alignment=ft.Alignment.CENTER,
            content=ft.Text("No page", color=ft.Colors.GREY_500),
        )
    p = page_png_paths[page_index]
    img = ft.Image(
        src=str(p),
        fit=ft.BoxFit.CONTAIN,
        filter_quality=ft.FilterQuality.MEDIUM,
    )
    viewer = ft.InteractiveViewer(
        content=img,
        pan_enabled=True,
        scale_enabled=True,
        min_scale=min_scale,
        max_scale=max_scale,
        constrained=True,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )
    return ft.Container(
        content=viewer,
        height=page_viewport_height,
        alignment=ft.Alignment.TOP_CENTER,
        border=_PANE_BORDER,
        border_radius=8,
    )
