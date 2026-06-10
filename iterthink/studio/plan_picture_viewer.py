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
from iterthink.services.plan_change_regions import PlanChangeRegionView
from iterthink.services.plan_text_diff import PlanTextChangeView
from iterthink.studio.plan_region_actions import (
    PlanRegionActionHandlers,
    build_plan_region_action_cube,
)
from iterthink.studio.components import action_rail_icon_button_style
from iterthink.studio.plan_text_change_ui import (
    build_inline_label_text,
    build_text_change_hover_card,
    label_colors,
    pin_color,
    plan_hover_enabled,
)
from iterthink.studio.util import ctrl_on_page


def _control_page_safe(ctrl: ft.Control) -> ft.Page | None:
    """Return ``ctrl.page`` without raising before the control is on a Page."""
    try:
        return ctrl.page
    except RuntimeError:
        return None


# Viewport height per page in compare/history stacked viewers.
_DEFAULT_PAGE_VIEWPORT_H = 520.0
_FOCUS_MIN_VIEWPORT_H = 100.0
_FOCUS_NAV_H = 40.0

_FOCUS_MIN_SCALE = 0.25
_FOCUS_MAX_SCALE = 8.0
_FOCUS_ZOOM_STEP = 1.25

_TOOLS_PILL_EST_W = 360.0
_TOOLS_PILL_EST_H = 40.0
_TOOLS_PILL_OFFSET = 8.0
_TOOLS_PILL_MARGIN = 6.0

_TEXT_CHANGE_TOOLTIP_EST_W = 360.0
_TEXT_CHANGE_TOOLTIP_EST_H = 72.0

_REGION_ACTION_EST_W = 120.0
_REGION_ACTION_EST_H = 120.0

_PANE_BORDER = ft.border.all(1, ft.Colors.with_opacity(0.35, ft.Colors.GREY_600))

PlanTextOverlayMode = Literal["candidate", "baseline"]


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


def _image_layout_size(viewer: PlanFocusViewer) -> tuple[float, float]:
    """Rendered page image size inside InteractiveViewer (image-local pixels)."""
    dw = float(viewer._image.width or 0)
    dh = float(viewer._image.height or 0)
    if dw > 0 and dh > 0:
        return dw, dh
    if viewer.page_count <= 0:
        return 1.0, 1.0
    path = viewer._paths[viewer.current_index]
    iw, ih = _read_png_pixel_size(path)
    vw = max(float(viewer._viewport_w), 1.0)
    if viewer._layout_mode == "contain" and viewer._viewport_h >= _FOCUS_MIN_VIEWPORT_H:
        vh = max(float(viewer._viewport_h), _FOCUS_MIN_VIEWPORT_H)
        return _contain_fit_size(iw, ih, vw, vh)
    return _width_fit_size(iw, ih, vw)


def _norm_bbox_to_image_rect(
    nb: tuple[float, float, float, float],
    img_w: float,
    img_h: float,
) -> tuple[float, float, float, float]:
    """Map normalized bbox to image-stack pixels (origin = top-left of page image)."""
    x0, y0, x1, y1 = nb
    lx0 = min(float(x0), float(x1)) * img_w
    ly0 = min(float(y0), float(y1)) * img_h
    w = max(abs(float(x1) - float(x0)) * img_w, 4.0)
    h = max(abs(float(y1) - float(y0)) * img_h, 4.0)
    return lx0, ly0, w, h


def compare_iv_layout_rect(iv: ft.InteractiveViewer) -> _ContainRect:
    """Layout rect for a compare-column page (contain fit inside fixed viewport)."""
    vw, vh = _viewer_viewport(iv)
    meta = _viewer_meta(iv)
    iw = int(meta.get("img_w", 1))
    ih = int(meta.get("img_h", 1))
    return _contain_rect(vw, vh, iw, ih)


def norm_bbox_to_viewport_rect(
    nb: tuple[float, float, float, float], rect: _ContainRect
) -> tuple[float, float, float, float]:
    lx0, ly0 = _normalized_to_viewport(nb[0], nb[1], rect)
    lx1, ly1 = _normalized_to_viewport(nb[2], nb[3], rect)
    return lx0, ly0, lx1, ly1


def _filter_text_changes_for_overlay(
    changes: list[PlanTextChangeView],
    page_index: int,
    mode: PlanTextOverlayMode,
) -> list[PlanTextChangeView]:
    page = [c for c in changes if int(c.page_index) == int(page_index)]
    if mode == "baseline":
        return [c for c in page if c.kind == "removed"]
    return [c for c in page if c.kind != "removed"]


def build_text_change_tooltip_host() -> ft.Container:
    return ft.Container(
        visible=False,
        bgcolor=config.SURFACE,
        border=ft.border.all(1, ft.Colors.with_opacity(0.35, config.OUTLINE)),
        border_radius=8,
        left=0,
        top=0,
        right=None,
    )


def build_text_change_overlay_controls(
    changes: list[PlanTextChangeView],
    *,
    overlay_mode: PlanTextOverlayMode,
    hover_enabled: bool,
    img_w: float | None = None,
    img_h: float | None = None,
    layout_rect: _ContainRect | None = None,
    on_pin_hover: Callable[[PlanTextChangeView], None] | None = None,
    on_pin_hover_exit: Callable[[], None] | None = None,
) -> list[ft.Control]:
    controls: list[ft.Control] = []
    use_image = img_w is not None and img_h is not None and float(img_w) > 0 and float(img_h) > 0
    for ch in changes:
        if use_image:
            lx0, ly0, w, h = _norm_bbox_to_image_rect(ch.norm_bbox, float(img_w), float(img_h))
        elif layout_rect is not None:
            lx0, ly0, lx1, ly1 = norm_bbox_to_viewport_rect(ch.norm_bbox, layout_rect)
            w = max(lx1 - lx0, 4.0)
            h = max(ly1 - ly0, 4.0)
        else:
            continue
        show_label = (
            ch.kind in ("stable", "modified", "added") and overlay_mode == "candidate"
        ) or (ch.kind == "removed" and overlay_mode == "baseline")
        if show_label:
            _fg, bg = label_colors(ch.kind)
            font_size = max(6, min(int(h * 0.82), 22))
            controls.append(
                ft.Container(
                    left=lx0,
                    top=ly0,
                    width=w,
                    height=h,
                    bgcolor=bg,
                    padding=ft.padding.all(1),
                    content=build_inline_label_text(
                        ch.kind,
                        ch.display_text,
                        ch.old_text,
                        ch.new_text,
                        font_size=font_size,
                    ),
                )
            )
        if ch.pin_norm is not None and ch.kind in ("modified", "added", "removed"):
            pu, pv = ch.pin_norm
            if use_image:
                px = float(pu) * float(img_w)
                py = float(pv) * float(img_h)
            else:
                assert layout_rect is not None
                px, py = _normalized_to_viewport(pu, pv, layout_rect)
            icon = ft.Icon(
                ft.Icons.FIBER_MANUAL_RECORD,
                size=12,
                color=pin_color(ch.kind),
            )
            pin_left = px - 6
            pin_top = py - 6

            def _hover(e: ft.ControlEvent, c: PlanTextChangeView = ch) -> None:
                if e.data == "true":
                    if on_pin_hover is not None:
                        on_pin_hover(c)
                elif on_pin_hover_exit is not None:
                    on_pin_hover_exit()

            pin_wrap: ft.Control = ft.Container(
                left=pin_left,
                top=pin_top,
                width=14,
                height=14,
                content=icon,
                on_hover=_hover if hover_enabled else None,
            )
            controls.append(pin_wrap)
    return controls


@dataclass
class ComparePageTextOverlay:
    page_index: int
    labels_stack: ft.Stack
    tooltip_host: ft.Container


@dataclass(frozen=True)
class PlanMarkerView:
    """Lightweight marker for on-screen overlay (no DB dependency)."""

    kind: str
    page_index: int
    norm_x: float
    norm_y: float
    bbox: dict[str, float] | None = None


def _map_focal_between_focus_viewers(
    src: PlanFocusViewer,
    dst: PlanFocusViewer,
    fx: float,
    fy: float,
) -> tuple[float, float]:
    """Map viewport focal on src focus viewer to the same plan-normalized point on dst."""
    s_rect = _focus_image_layout_rect(src)
    d_rect = _focus_image_layout_rect(dst)
    u, v = _viewport_to_normalized(fx, fy, s_rect)
    return _normalized_to_viewport(u, v, d_rect)


@dataclass
class _IvTransformTrack:
    """Tracked InteractiveViewer pan/zoom (matches programmatic _zoom_at_focal / pan)."""

    scale: float = 1.0
    tx: float = 0.0
    ty: float = 0.0


def _plan_norm_from_viewport_tracked(
    pane: PlanFocusViewer,
    fx: float,
    fy: float,
    track: _IvTransformTrack,
) -> tuple[float, float]:
    """Viewport focal → normalized page (u, v), accounting for current pan/zoom."""
    img_w, img_h = _image_layout_size(pane)
    if img_w <= 1e-6 or img_h <= 1e-6 or track.scale <= 1e-9:
        return 0.5, 0.5
    cx = (float(fx) - track.tx) / track.scale
    cy = (float(fy) - track.ty) / track.scale
    return (
        max(0.0, min(1.0, cx / img_w)),
        max(0.0, min(1.0, cy / img_h)),
    )


def _viewport_from_plan_norm_tracked(
    pane: PlanFocusViewer,
    u: float,
    v: float,
    track: _IvTransformTrack,
) -> tuple[float, float]:
    """Normalized page (u, v) → viewport focal for programmatic zoom/pan."""
    img_w, img_h = _image_layout_size(pane)
    cx = float(u) * img_w
    cy = float(v) * img_h
    return cx * track.scale + track.tx, cy * track.scale + track.ty


def _track_after_zoom_at_focal(
    track: _IvTransformTrack,
    fx: float,
    fy: float,
    factor: float,
) -> None:
    if abs(factor - 1.0) < 1e-4:
        return
    track.tx = factor * track.tx + float(fx) * (1.0 - factor)
    track.ty = factor * track.ty + float(fy) * (1.0 - factor)
    track.scale *= factor


def _track_after_pan(track: _IvTransformTrack, dx: float, dy: float) -> None:
    track.tx += float(dx)
    track.ty += float(dy)


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

    root: ft.Control
    page_count: int
    current_index: int
    _paths: list[Path] = field(repr=False)
    _image: ft.Image = field(repr=False)
    _viewer: ft.InteractiveViewer = field(repr=False)
    _page_frame: ft.Container = field(repr=False)
    _viewport_stack: ft.Stack = field(repr=False)
    _text_labels_overlay: ft.Stack = field(repr=False)
    _text_change_tooltip_host: ft.Container = field(repr=False)
    _annotations_overlay: ft.Stack = field(repr=False)
    _draw_rubber_band: ft.Container = field(repr=False)
    _draw_cloud_capture: ft.GestureDetector = field(repr=False)
    _viewport_tap_capture: ft.GestureDetector = field(repr=False)
    _tools_pill_host: ft.Container = field(repr=False)
    _change_regions_overlay: ft.Stack = field(repr=False)
    _region_action_host: ft.Container = field(repr=False)
    _region_action_factory: Callable[[PlanChangeRegionView], PlanRegionActionHandlers] | None = (
        field(default=None, repr=False)
    )
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
    _expected_page_count: int | None = field(default=None, repr=False)
    _layout_mode: str = field(default="width", repr=False)
    interaction_mode: InteractionMode = field(default="idle", repr=False)
    _markers: list[PlanMarkerView] = field(default_factory=list, repr=False)
    _text_changes: list[PlanTextChangeView] = field(default_factory=list, repr=False)
    _text_overlay_mode: PlanTextOverlayMode = field(default="candidate", repr=False)
    _text_overlay_visible: bool = field(default=True, repr=False)
    _hover_enabled: bool = field(default=True, repr=False)
    _draw_start: tuple[float, float] | None = field(default=None, repr=False)
    _change_regions: list[PlanChangeRegionView] = field(default_factory=list, repr=False)
    _page_label: ft.Text | None = field(default=None, repr=False)
    _prev_btn: ft.IconButton | None = field(default=None, repr=False)
    _next_btn: ft.IconButton | None = field(default=None, repr=False)
    _fit_page_btn: ft.IconButton | None = field(default=None, repr=False)
    _fit_width_btn: ft.IconButton | None = field(default=None, repr=False)
    _pan_btn: ft.IconButton | None = field(default=None, repr=False)
    _zoom_out_btn: ft.IconButton | None = field(default=None, repr=False)
    _zoom_in_btn: ft.IconButton | None = field(default=None, repr=False)
    _page_nav_row: ft.Container | None = field(default=None, repr=False)
    _page_nav_inner_row: ft.Row | None = field(default=None, repr=False)
    _nav_trailing: list[ft.Control] = field(default_factory=list, repr=False)
    _highlighted_region_id: int | None = field(default=None, repr=False)
    _active_region_hover_id: int | None = field(default=None, repr=False)
    _viewer_interacting: bool = field(default=False, repr=False)
    _page_content_stack: ft.Stack | None = field(default=None, repr=False)
    _iv_track: _IvTransformTrack = field(default_factory=_IvTransformTrack, repr=False)

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

    def set_nav_trailing(self, controls: list[ft.Control]) -> None:
        """Append controls after zoom (e.g. Review comment tool). Replaces prior trailing."""
        row = self._page_nav_inner_row
        if row is None:
            return
        for c in list(self._nav_trailing):
            if c in row.controls:
                row.controls.remove(c)
        self._nav_trailing = []
        if not controls:
            nav = self._page_nav_row
            if nav is not None and ctrl_on_page(nav):
                nav.update()
            return
        spacer = ft.Container(width=8)
        row.controls.extend([spacer, *controls])
        self._nav_trailing = [spacer, *controls]
        nav = self._page_nav_row
        if nav is not None and ctrl_on_page(nav):
            nav.update()

    def set_interaction_mode(self, mode: InteractionMode) -> None:
        self.interaction_mode = mode
        if mode != "draw_cloud":
            self._clear_draw_rubber_band()
        draw_cap = getattr(self, "_draw_cloud_capture", None)
        if draw_cap is not None:
            draw_cap.visible = mode == "draw_cloud"
            if ctrl_on_page(draw_cap):
                draw_cap.update()
        tap_cap = getattr(self, "_viewport_tap_capture", None)
        if tap_cap is not None:
            tap_cap.visible = mode == "place_comment"
            tap_cap.mouse_cursor = (
                ft.MouseCursor.CLICK if mode == "place_comment" else ft.MouseCursor.BASIC
            )
            if ctrl_on_page(tap_cap):
                tap_cap.update()
        if mode == "idle":
            return
        self.hide_tools_pill()

    def set_markers(self, markers: list[PlanMarkerView]) -> None:
        self._markers = list(markers)
        self.refresh_annotations_overlay()

    def set_text_changes(
        self,
        changes: list[PlanTextChangeView] | None,
        *,
        overlay_mode: PlanTextOverlayMode = "candidate",
        visible: bool = True,
        hover_enabled: bool | None = None,
    ) -> None:
        self._text_changes = list(changes or [])
        self._text_overlay_mode = overlay_mode
        self._text_overlay_visible = visible
        if hover_enabled is not None:
            self._hover_enabled = hover_enabled
        self.hide_text_change_tooltip()
        self.refresh_text_change_overlay()

    def hide_text_change_tooltip(self) -> None:
        host = self._text_change_tooltip_host
        if not bool(getattr(host, "visible", False)):
            return
        host.visible = False
        if ctrl_on_page(host):
            host.update()

    def show_text_change_tooltip(
        self, change: PlanTextChangeView, anchor_x: float, anchor_y: float
    ) -> None:
        if not self._hover_enabled:
            return
        host = self._text_change_tooltip_host
        host.content = build_text_change_hover_card(
            change.old_text,
            change.new_text,
            kind=change.kind,
        )
        sw = max(self._stack_w, self._viewport_w, 1.0)
        sh = max(self._stack_h, self._viewport_h, _FOCUS_MIN_VIEWPORT_H)
        left, top = _clamp_tools_pill_position(
            anchor_x,
            anchor_y,
            stack_w=sw,
            stack_h=sh,
            pill_w=_TEXT_CHANGE_TOOLTIP_EST_W,
            pill_h=_TEXT_CHANGE_TOOLTIP_EST_H,
        )
        host.left = left
        host.top = top
        host.right = None
        host.visible = True
        if ctrl_on_page(host):
            host.update()

    def _reset_iv_track(self) -> None:
        self._iv_track = _IvTransformTrack()

    def _pin_norm_viewport_xy(self, change: PlanTextChangeView) -> tuple[float, float]:
        if change.pin_norm is not None:
            u, v = change.pin_norm
        else:
            nb = change.norm_bbox
            u = (float(nb[0]) + float(nb[2])) * 0.5
            v = (float(nb[1]) + float(nb[3])) * 0.5
        return _viewport_from_plan_norm_tracked(self, u, v, self._iv_track)

    def refresh_text_change_overlay(self) -> None:
        if not self._text_overlay_visible or not self._text_changes:
            self._text_labels_overlay.controls = []
            if ctrl_on_page(self._text_labels_overlay):
                self._text_labels_overlay.update()
            return
        img_w, img_h = _image_layout_size(self)
        page_changes = _filter_text_changes_for_overlay(
            self._text_changes, self.current_index, self._text_overlay_mode
        )

        def _on_hover(ch: PlanTextChangeView) -> None:
            ax, ay = self._pin_norm_viewport_xy(ch)
            self.show_text_change_tooltip(ch, ax, ay)

        self._text_labels_overlay.controls = build_text_change_overlay_controls(
            page_changes,
            overlay_mode=self._text_overlay_mode,
            hover_enabled=self._hover_enabled,
            img_w=img_w,
            img_h=img_h,
            on_pin_hover=_on_hover,
            on_pin_hover_exit=self.hide_text_change_tooltip,
        )
        if ctrl_on_page(self._text_labels_overlay):
            self._text_labels_overlay.update()

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
                            src=f"data:image/png;base64,{b64}",
                            width=w,
                            height=h,
                            fit=ft.BoxFit.FILL,
                        ),
                    )
                )
        self._annotations_overlay.controls = controls
        if ctrl_on_page(self._annotations_overlay):
            self._annotations_overlay.update()

    def set_change_regions(
        self,
        regions: list[PlanChangeRegionView] | None,
        *,
        action_factory: Callable[[PlanChangeRegionView], PlanRegionActionHandlers] | None = None,
    ) -> None:
        self._change_regions = list(regions or [])
        if action_factory is not None:
            self._region_action_factory = action_factory
        self.hide_region_action_cube()
        self.refresh_change_regions_overlay()

    def set_highlighted_region(self, region_id: int | None) -> None:
        self._highlighted_region_id = region_id
        self.refresh_change_regions_overlay()

    def hide_region_action_cube(self) -> None:
        self._active_region_hover_id = None
        host = self._region_action_host
        if not bool(getattr(host, "visible", False)):
            return
        host.visible = False
        if ctrl_on_page(host):
            host.update()

    def show_region_action_cube(
        self, region: PlanChangeRegionView, anchor_x: float, anchor_y: float
    ) -> None:
        if self._region_action_factory is None:
            return
        self._active_region_hover_id = int(region.region_id)
        host = self._region_action_host
        handlers = self._region_action_factory(region)
        host.content = build_plan_region_action_cube(handlers)
        dw, dh = _image_layout_size(self)
        left, top = _clamp_tools_pill_position(
            anchor_x,
            anchor_y,
            stack_w=dw,
            stack_h=dh,
            pill_w=_REGION_ACTION_EST_W,
            pill_h=_REGION_ACTION_EST_H,
        )
        host.left = left
        host.top = top
        host.right = None
        host.visible = True
        if ctrl_on_page(host):
            host.update()

    def refresh_change_regions_overlay(self) -> None:
        controls: list[ft.Control] = []
        page_ix = self.current_index
        img_w, img_h = _image_layout_size(self)
        for reg in self._change_regions:
            if int(reg.page_index) != page_ix or reg.dismissed:
                continue
            left, top, w, h = _norm_bbox_to_image_rect(reg.norm_bbox, img_w, img_h)
            highlight = (
                self._highlighted_region_id is not None
                and int(reg.region_id) == int(self._highlighted_region_id)
            )
            fill_opacity = 0.08 if reg.reviewed else 0.14
            border_color = (
                config.HIGHLIGHT
                if highlight
                else ft.Colors.with_opacity(0.55 if reg.reviewed else 0.85, config.PRIMARY_COLOR)
            )
            controls.append(
                ft.Container(
                    left=left,
                    top=top,
                    width=w,
                    height=h,
                    bgcolor=ft.Colors.with_opacity(fill_opacity, config.PRIMARY_COLOR),
                    border=ft.border.all(2, border_color),
                )
            )
            ax = left + w
            ay = top

            def _hover(
                e: ft.ControlEvent,
                r: PlanChangeRegionView = reg,
                anchor_x: float = ax,
                anchor_y: float = ay,
            ) -> None:
                if e.data == "true":
                    self.show_region_action_cube(r, anchor_x, anchor_y)
                else:
                    self.hide_region_action_cube()

            controls.append(
                ft.Container(
                    left=left,
                    top=top,
                    width=w,
                    height=h,
                    bgcolor=ft.Colors.TRANSPARENT,
                    on_hover=_hover,
                )
            )
        self._change_regions_overlay.controls = controls
        if ctrl_on_page(self._change_regions_overlay):
            self._change_regions_overlay.update()

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
            if norm is not None and self._on_place_comment is not None:
                self._on_place_comment(norm[0], norm[1])
            self.set_interaction_mode("idle")
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
        if (
            self._viewport_w > 0
            and float(self._image.width or 0) > 0
            and abs(vw - self._viewport_w) <= 0.5
            and abs(vh - self._viewport_h) <= 0.5
        ):
            return
        self._viewport_w = vw
        self._viewport_h = vh
        self.hide_tools_pill()
        self.hide_text_change_tooltip()
        self.hide_region_action_cube()
        frame = self._page_frame
        frame.width = vw
        frame.height = vh
        if self.page_count > 0:
            self._apply_image_layout()
        self.refresh_text_change_overlay()
        self.refresh_annotations_overlay()
        self.refresh_change_regions_overlay()
        for c in (frame, self._image):
            if ctrl_on_page(c):
                c.update()

    def apply_layout_resize(self, viewport_w: float, viewport_h: float) -> None:
        """Apply a real container resize; ignore spurious events during pan/zoom."""
        if self._viewer_interacting:
            return
        vw = max(1.0, float(viewport_w))
        vh = max(_FOCUS_MIN_VIEWPORT_H, float(viewport_h))
        if vw <= 1.0 or vh < _FOCUS_MIN_VIEWPORT_H:
            return
        if self._viewport_w > 1.0 and (
            vw <= self._viewport_w * 0.5 or vh <= self._viewport_h * 0.5
        ):
            return
        self.sync_viewport(vw, vh)

    def _rendered_page_count(self) -> int:
        return len(self._paths)

    def _nav_page_total(self) -> int:
        exp = self._expected_page_count
        if exp is not None and exp > 0:
            return exp
        return self.page_count

    def set_expected_page_count(self, total: int) -> None:
        self._expected_page_count = max(0, int(total))
        self.page_count = max(self._rendered_page_count(), self._expected_page_count)
        self._sync_nav_chrome()

    def append_rendered_pages(self, paths: list[Path]) -> None:
        """Extend rendered PNG paths (progressive import); refreshes nav chrome."""
        if not paths:
            return
        self._paths.extend(paths)
        rendered = self._rendered_page_count()
        total = self._nav_page_total()
        self.page_count = max(rendered, total)
        self._sync_nav_chrome()

    def _clamp_index(self, page_index: int) -> int:
        rendered = self._rendered_page_count()
        if rendered <= 0:
            return 0
        return max(0, min(int(page_index), rendered - 1))

    def _sync_nav_chrome(self) -> None:
        if self._page_label is None:
            return
        total = self._nav_page_total()
        rendered = self._rendered_page_count()
        i = self.current_index
        if total <= 0 and rendered <= 0:
            self._page_label.value = "No pages"
            if self._prev_btn is not None:
                self._prev_btn.disabled = True
            if self._next_btn is not None:
                self._next_btn.disabled = True
        else:
            show_total = max(total, rendered)
            self._page_label.value = f"Page {i + 1} / {show_total}"
            if self._prev_btn is not None:
                self._prev_btn.disabled = i <= 0
            if self._next_btn is not None:
                self._next_btn.disabled = i >= rendered - 1
        tool_disabled = rendered <= 0
        for btn in (
            self._fit_page_btn,
            self._fit_width_btn,
            self._pan_btn,
            self._zoom_out_btn,
            self._zoom_in_btn,
        ):
            if btn is not None:
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
        ):
            if c is not None and ctrl_on_page(c):
                c.update()

    def _sync_pan_button(self) -> None:
        if self._pan_btn is None:
            return
        from iterthink import config

        active = bool(self._viewer.pan_enabled)
        self._pan_btn.icon_color = config.PRIMARY_COLOR if active else config.ON_SURFACE_VARIANT
        if ctrl_on_page(self._pan_btn):
            self._pan_btn.update()

    def fit_to_viewport(self) -> None:
        """Fit whole page in the viewport (contain) and reset pan/zoom."""
        if self.page_count <= 0:
            return
        self.hide_text_change_tooltip()
        self._layout_mode = "contain"
        self._apply_image_layout()
        self._reset_viewer_transform()
        self.refresh_text_change_overlay()
        self.refresh_change_regions_overlay()
        if ctrl_on_page(self._image):
            self._image.update()

    def fit_to_width(self) -> None:
        """Span the viewport width; pan vertically for tall pages."""
        if self.page_count <= 0:
            return
        self.hide_text_change_tooltip()
        self._layout_mode = "width"
        self._apply_image_layout()
        self._reset_viewer_transform()
        self.refresh_text_change_overlay()
        self.refresh_change_regions_overlay()
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
        self._viewer_interacting = True
        try:
            w = max(self._viewport_w, 1.0)
            h = max(self._viewport_h, _FOCUS_MIN_VIEWPORT_H)
            fx, fy = w * 0.5, h * 0.5
            await _zoom_at_focal(self._viewer, fx, fy, factor)
            _track_after_zoom_at_focal(self._iv_track, fx, fy, factor)
        finally:
            self._viewer_interacting = False

    def _reset_viewer_transform(self) -> None:
        self._reset_iv_track()
        if not ctrl_on_page(self._viewer):
            return
        reset = getattr(self._viewer, "reset", None)
        if not callable(reset):
            return
        pg = getattr(self._viewer, "page", None)
        if pg is None:
            return

        async def _safe_reset() -> None:
            try:
                await reset()
            except (RuntimeError, TypeError, AttributeError, ValueError):
                pass

        try:
            pg.run_task(_safe_reset)
        except (RuntimeError, TypeError, AttributeError, ValueError):
            pass

    def set_page(
        self,
        page_index: int,
        *,
        reset_transform: bool = True,
        update_nav_chrome: bool = True,
        notify_page_change: bool = True,
    ) -> None:
        """Show ``page_index`` and refresh nav chrome."""
        self.hide_tools_pill()
        self.hide_text_change_tooltip()
        self.hide_region_action_cube()
        ix = self._clamp_index(page_index)
        self.current_index = ix
        if self.page_count <= 0:
            self._image.src = None
        else:
            self._image.src = str(self._paths[ix])
            self._apply_image_layout()
            if reset_transform:
                self._reset_viewer_transform()
        if ctrl_on_page(self._image):
            self._image.update()
        if ctrl_on_page(self._viewer):
            self._viewer.update()
        self.refresh_text_change_overlay()
        self.refresh_annotations_overlay()
        self.refresh_change_regions_overlay()
        if update_nav_chrome:
            self._sync_nav_chrome()
        if notify_page_change and self._on_page_change is not None:
            self._on_page_change(ix)

    def go_relative(
        self,
        delta: int,
        *,
        reset_transform: bool = True,
        update_nav_chrome: bool = True,
        notify_page_change: bool = True,
    ) -> None:
        if delta == 0 or self.page_count <= 0:
            return
        self.set_page(
            self.current_index + delta,
            reset_transform=reset_transform,
            update_nav_chrome=update_nav_chrome,
            notify_page_change=notify_page_change,
        )

    def _measure_viewport_size(self) -> tuple[float, float]:
        """Best-effort viewport (w, h) from laid-out controls; (0, 0) if unknown."""
        nav_h = float(self._page_nav_row.height or _FOCUS_NAV_H) if self._page_nav_row else 0.0
        for ctrl, subtract_nav in (
            (self._page_frame, 0.0),
            (self._viewport_stack, 0.0),
            (self.root, nav_h),
        ):
            w = float(getattr(ctrl, "width", 0) or 0)
            h = float(getattr(ctrl, "height", 0) or 0)
            if subtract_nav > 0:
                h = max(0.0, h - subtract_nav)
            if w > 1.0 and h >= _FOCUS_MIN_VIEWPORT_H:
                return w, h
        return 0.0, 0.0

    async def ensure_viewport_sync(self, *, max_attempts: int = 12) -> None:
        """Size the page image once parent layout has non-zero dimensions (first paint)."""
        for attempt in range(max_attempts):
            if not bool(getattr(self.root, "visible", True)):
                await asyncio.sleep(0.04)
                continue
            vw, vh = self._measure_viewport_size()
            if vw > 1.0 and vh >= _FOCUS_MIN_VIEWPORT_H:
                needs_sync = (
                    self._viewport_w <= 0
                    or abs(vw - self._viewport_w) > 0.5
                    or abs(vh - self._viewport_h) > 0.5
                    or float(self._image.width or 0) <= 0
                )
                if needs_sync:
                    self.sync_viewport(vw, vh)
                self._stack_w = max(self._stack_w, vw)
                self._stack_h = max(self._stack_h, vh)
                return
            await asyncio.sleep(0.04 if attempt < 4 else 0.07)

    async def show_page(self, page_index: int) -> None:
        if not ctrl_on_page(self.root) or not bool(getattr(self.root, "visible", True)):
            return
        self.set_page(page_index)
        await self.ensure_viewport_sync()


@dataclass
class FocusNavControls:
    page_nav_row: ft.Container
    page_nav_inner_row: ft.Row
    page_label: ft.Text
    prev_btn: ft.IconButton
    next_btn: ft.IconButton
    fit_page_btn: ft.IconButton
    fit_width_btn: ft.IconButton
    pan_btn: ft.IconButton
    zoom_out_btn: ft.IconButton
    zoom_in_btn: ft.IconButton


def _build_focus_nav_row(
    *,
    page_count: int,
    current_index: int,
    nav_total: int,
    on_prev: Callable[[ft.ControlEvent], None],
    on_next: Callable[[ft.ControlEvent], None],
    on_fit_page: Callable[[ft.ControlEvent], None],
    on_fit_width: Callable[[ft.ControlEvent], None],
    on_pan: Callable[[ft.ControlEvent], None],
    on_zoom_out: Callable[[ft.ControlEvent], None],
    on_zoom_in: Callable[[ft.ControlEvent], None],
) -> FocusNavControls:
    n = page_count
    ix0 = current_index
    label_total = max(nav_total, n)
    page_label = ft.Text(
        f"Page {ix0 + 1} / {label_total}" if n > 0 else "No pages",
        size=13,
        color=ft.Colors.GREY_400,
    )
    prev_btn = ft.IconButton(
        ft.Icons.CHEVRON_LEFT,
        tooltip="Previous page",
        disabled=n <= 0 or ix0 <= 0,
        on_click=on_prev,
    )
    next_btn = ft.IconButton(
        ft.Icons.CHEVRON_RIGHT,
        tooltip="Next page",
        disabled=n <= 0 or ix0 >= n - 1,
        on_click=on_next,
    )
    _tool_disabled = n <= 0
    fit_page_btn = ft.IconButton(
        ft.Icons.FIT_SCREEN,
        tooltip="Fit page in viewport",
        disabled=_tool_disabled,
        on_click=on_fit_page,
    )
    fit_width_btn = ft.IconButton(
        ft.Icons.WIDTH_FULL,
        tooltip="Fit to width",
        disabled=_tool_disabled,
        on_click=on_fit_width,
    )
    pan_btn = ft.IconButton(
        ft.Icons.PAN_TOOL,
        tooltip="Pan (toggle)",
        disabled=_tool_disabled,
        on_click=on_pan,
    )
    zoom_out_btn = ft.IconButton(
        ft.Icons.ZOOM_OUT,
        tooltip="Zoom out",
        disabled=_tool_disabled,
        on_click=on_zoom_out,
    )
    zoom_in_btn = ft.IconButton(
        ft.Icons.ZOOM_IN,
        tooltip="Zoom in",
        disabled=_tool_disabled,
        on_click=on_zoom_in,
    )
    _tool_btn_style = action_rail_icon_button_style()
    for btn in (fit_page_btn, fit_width_btn, pan_btn, zoom_out_btn, zoom_in_btn):
        btn.style = _tool_btn_style
    page_nav_inner_row = ft.Row(
        [
            prev_btn,
            page_label,
            next_btn,
            ft.Container(width=8),
            fit_page_btn,
            fit_width_btn,
            pan_btn,
            zoom_out_btn,
            zoom_in_btn,
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
    )
    page_nav_row = ft.Container(
        height=_FOCUS_NAV_H,
        alignment=ft.Alignment.CENTER,
        content=page_nav_inner_row,
    )
    return FocusNavControls(
        page_nav_row=page_nav_row,
        page_nav_inner_row=page_nav_inner_row,
        page_label=page_label,
        prev_btn=prev_btn,
        next_btn=next_btn,
        fit_page_btn=fit_page_btn,
        fit_width_btn=fit_width_btn,
        pan_btn=pan_btn,
        zoom_out_btn=zoom_out_btn,
        zoom_in_btn=zoom_in_btn,
    )


def _build_plan_focus_pane(
    page_png_paths: list[Path],
    *,
    initial_page_index: int = 0,
    expected_page_count: int | None = None,
    on_page_change: Callable[[int], None] | None = None,
    min_scale: float = _FOCUS_MIN_SCALE,
    max_scale: float = _FOCUS_MAX_SCALE,
) -> PlanFocusViewer:
    """Viewport-only plan pane (no bottom navigation row)."""
    paths = list(page_png_paths)
    n = len(paths)
    nav_total = int(expected_page_count) if expected_page_count is not None else n
    ix0 = max(0, min(int(initial_page_index), max(n - 1, 0)))

    if n > 0:
        img = ft.Image(
            src=str(paths[ix0]),
            fit=ft.BoxFit.FILL,
            filter_quality=ft.FilterQuality.MEDIUM,
        )
    else:
        img = ft.Image(src="", fit=ft.BoxFit.FILL, visible=False)

    change_regions_overlay = ft.Stack([], fit=ft.StackFit.PASS_THROUGH)
    region_action_host = ft.Container(visible=False, left=0, top=0, right=None)
    text_labels_overlay = ft.Stack([], fit=ft.StackFit.PASS_THROUGH)
    if n > 0:
        page_content_stack = ft.Stack(
            [img, text_labels_overlay, change_regions_overlay, region_action_host],
            fit=ft.StackFit.PASS_THROUGH,
        )
        iv_content: ft.Control = page_content_stack
    else:
        page_content_stack = None
        iv_content = ft.Text("No pages", color=ft.Colors.GREY_500)

    viewer = ft.InteractiveViewer(
        content=iv_content,
        pan_enabled=n > 0,
        scale_enabled=n > 0,
        trackpad_scroll_causes_scale=n > 0,
        min_scale=min_scale,
        max_scale=max_scale,
        constrained=False,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        boundary_margin=ft.margin.all(48),
        expand=True,
    )

    holder: dict[str, PlanFocusViewer | None] = {"v": None}

    page_frame = ft.Container(
        content=viewer,
        expand=True,
        width=None,
        alignment=ft.Alignment.TOP_LEFT,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )

    def _on_viewport_tap_down(e: ft.TapEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        lp = e.local_position
        if lp is None:
            return
        v.handle_viewport_tap(float(lp.x), float(lp.y))

    annotations_overlay = ft.Stack([], expand=True, fit=ft.StackFit.PASS_THROUGH)
    text_change_tooltip_host = build_text_change_tooltip_host()

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

    draw_cloud_capture = ft.GestureDetector(
        visible=False,
        content=ft.Container(expand=True),
        on_pan_start=_on_draw_start,
        on_pan_update=_on_draw_update,
        on_pan_end=_on_draw_end,
    )
    viewport_tap_capture = ft.GestureDetector(
        visible=False,
        content=ft.Container(expand=True),
        on_tap_down=_on_viewport_tap_down,
    )
    draw_rubber_band = ft.Container(
        visible=False,
        bgcolor=ft.Colors.with_opacity(0.18, config.PRIMARY_COLOR),
        border=ft.border.all(1, config.PRIMARY_COLOR),
    )
    tools_pill_host = ft.Container(visible=False, left=0, top=0, right=None)

    def _on_stack_resize(e: ft.LayoutSizeChangeEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        sw = max(1.0, float(e.width))
        sh = max(_FOCUS_MIN_VIEWPORT_H, float(e.height))
        v._stack_w = sw
        v._stack_h = sh
        v.apply_layout_resize(sw, sh)
        v.refresh_text_change_overlay()
        v.refresh_change_regions_overlay()

    def _on_viewer_interaction_start(_e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        v._viewer_interacting = True
        v.hide_text_change_tooltip()
        v.hide_region_action_cube()

    def _on_viewer_interaction_end(_e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is not None:
            v._viewer_interacting = False

    _gesture_scale: dict[str, float] = {"scale": 1.0}

    def _on_viewer_interaction_update(e: ft.ControlEvent) -> None:
        v = holder.get("v")
        if v is None:
            return
        try:
            lp = e.local_focal_point
            fx, fy = float(lp.x), float(lp.y)
            cur = float(e.scale)
            prev = float(_gesture_scale.get("scale", 1.0))
            factor = cur / prev if prev > 1e-9 else 1.0
            if abs(factor - 1.0) > 1e-4:
                _track_after_zoom_at_focal(v._iv_track, fx, fy, factor)
            else:
                d = e.focal_point_delta
                _track_after_pan(v._iv_track, float(d.x), float(d.y))
            _gesture_scale["scale"] = cur
        except (AttributeError, TypeError, ValueError):
            pass

    def _on_iv_interaction_start(e: ft.ControlEvent | None = None) -> None:
        _gesture_scale["scale"] = 1.0
        _on_viewer_interaction_start(e)

    viewer.on_interaction_start = _on_iv_interaction_start
    viewer.on_interaction_end = _on_viewer_interaction_end
    viewer.on_interaction_update = _on_viewer_interaction_update
    viewer.interaction_update_interval = 50

    viewport_stack = ft.Stack(
        [
            page_frame,
            annotations_overlay,
            draw_cloud_capture,
            draw_rubber_band,
            viewport_tap_capture,
            tools_pill_host,
            text_change_tooltip_host,
        ],
        expand=True,
        clip_behavior=ft.ClipBehavior.NONE,
    )
    viewport_stack.on_size_change = _on_stack_resize

    focus = PlanFocusViewer(
        root=viewport_stack,
        page_count=max(n, nav_total),
        current_index=ix0,
        _paths=paths,
        _expected_page_count=expected_page_count if expected_page_count is not None else None,
        _image=img,
        _viewer=viewer,
        _page_frame=page_frame,
        _viewport_stack=viewport_stack,
        _text_labels_overlay=text_labels_overlay,
        _text_change_tooltip_host=text_change_tooltip_host,
        _annotations_overlay=annotations_overlay,
        _change_regions_overlay=change_regions_overlay,
        _page_content_stack=page_content_stack,
        _region_action_host=region_action_host,
        _draw_rubber_band=draw_rubber_band,
        _draw_cloud_capture=draw_cloud_capture,
        _viewport_tap_capture=viewport_tap_capture,
        _tools_pill_host=tools_pill_host,
        _on_page_change=on_page_change,
    )
    holder["v"] = focus
    return focus


def build_plan_focus_viewer(
    page_png_paths: list[Path],
    *,
    initial_page_index: int = 0,
    expected_page_count: int | None = None,
    on_page_change: Callable[[int], None] | None = None,
    min_scale: float = _FOCUS_MIN_SCALE,
    max_scale: float = _FOCUS_MAX_SCALE,
) -> PlanFocusViewer:
    """
    Single-page plan viewport (fills column height) with ‹ Page N / M › navigation.
    """
    focus = _build_plan_focus_pane(
        page_png_paths,
        initial_page_index=initial_page_index,
        expected_page_count=expected_page_count,
        on_page_change=on_page_change,
        min_scale=min_scale,
        max_scale=max_scale,
    )
    holder: dict[str, PlanFocusViewer | None] = {"v": focus}

    def _prev(_e: ft.ControlEvent) -> None:
        v = holder["v"]
        if v is not None:
            v.go_relative(-1)

    def _next(_e: ft.ControlEvent) -> None:
        v = holder["v"]
        if v is not None:
            v.go_relative(1)

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

    nav = _build_focus_nav_row(
        page_count=focus.page_count,
        current_index=focus.current_index,
        nav_total=focus._nav_page_total(),
        on_prev=_prev,
        on_next=_next,
        on_fit_page=_fit_page,
        on_fit_width=_fit_width,
        on_pan=_pan,
        on_zoom_out=_zoom_out,
        on_zoom_in=_zoom_in,
    )
    col = ft.Column([focus.root, nav.page_nav_row], expand=True, spacing=0)
    focus.root = col
    focus._page_label = nav.page_label
    focus._prev_btn = nav.prev_btn
    focus._next_btn = nav.next_btn
    focus._fit_page_btn = nav.fit_page_btn
    focus._fit_width_btn = nav.fit_width_btn
    focus._pan_btn = nav.pan_btn
    focus._zoom_out_btn = nav.zoom_out_btn
    focus._zoom_in_btn = nav.zoom_in_btn
    focus._page_nav_row = nav.page_nav_row
    focus._page_nav_inner_row = nav.page_nav_inner_row
    _tool_btn_style = action_rail_icon_button_style()
    pill_fit = ft.IconButton(
        ft.Icons.FIT_SCREEN, tooltip="Fit page in viewport", on_click=_fit_page, style=_tool_btn_style
    )
    pill_width = ft.IconButton(
        ft.Icons.WIDTH_FULL, tooltip="Fit to width", on_click=_fit_width, style=_tool_btn_style
    )
    pill_pan = ft.IconButton(ft.Icons.PAN_TOOL, tooltip="Pan (toggle)", on_click=_pan, style=_tool_btn_style)
    pill_zout = ft.IconButton(
        ft.Icons.ZOOM_OUT, tooltip="Zoom out", on_click=_zoom_out, style=_tool_btn_style
    )
    pill_zin = ft.IconButton(ft.Icons.ZOOM_IN, tooltip="Zoom in", on_click=_zoom_in, style=_tool_btn_style)
    focus._tools_pill_host.content = ft.Row(
        [pill_fit, pill_width, pill_pan, pill_zout, pill_zin],
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
    )
    focus._tools_pill_host.bgcolor = ft.Colors.with_opacity(0.94, config.SURFACE)
    focus._tools_pill_host.border = ft.border.all(
        1, ft.Colors.with_opacity(0.35, config.OUTLINE)
    )
    focus._tools_pill_host.border_radius = 20
    focus._tools_pill_host.padding = ft.padding.symmetric(horizontal=4, vertical=0)
    focus._sync_pan_button()
    return focus


@dataclass
class PlanFocusPairController:
    """Dual-apply page/zoom/pan for side-by-side plan panes."""

    left: PlanFocusViewer
    right: PlanFocusViewer
    page: ft.Page
    nav: FocusNavControls
    on_page_change: Callable[[int], None] | None = None
    _page_guard: dict[str, bool] = field(default_factory=lambda: {"active": False}, repr=False)
    _gesture_state: dict[str, float] = field(default_factory=lambda: {"scale": 1.0}, repr=False)
    _gesture_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _left_track: _IvTransformTrack = field(default_factory=_IvTransformTrack, repr=False)
    _right_track: _IvTransformTrack = field(default_factory=_IvTransformTrack, repr=False)

    def _track_for(self, pane: PlanFocusViewer) -> _IvTransformTrack:
        return self._left_track if pane is self.left else self._right_track

    def _reset_transform_tracking(self) -> None:
        for pane, track in (
            (self.left, self._left_track),
            (self.right, self._right_track),
        ):
            track.scale = 1.0
            track.tx = 0.0
            track.ty = 0.0
            pane._reset_iv_track()

    async def _dual_zoom_at_plan_norm(
        self,
        u: float,
        v: float,
        factor: float,
    ) -> None:
        for pane in (self.left, self.right):
            track = self._track_for(pane)
            fx, fy = _viewport_from_plan_norm_tracked(pane, u, v, track)
            await _zoom_at_focal(pane._viewer, fx, fy, factor)
            _track_after_zoom_at_focal(track, fx, fy, factor)

    @property
    def current_index(self) -> int:
        return self.left.current_index

    def _nav_page_total(self) -> int:
        return max(self.left._nav_page_total(), self.right._nav_page_total())

    def _rendered_page_count(self) -> int:
        return max(self.left._rendered_page_count(), self.right._rendered_page_count())

    def sync_nav_chrome(self) -> None:
        n = self._rendered_page_count()
        total = self._nav_page_total()
        i = self.current_index
        nav = self.nav
        if total <= 0 and n <= 0:
            nav.page_label.value = "No pages"
            nav.prev_btn.disabled = True
            nav.next_btn.disabled = True
        else:
            show_total = max(total, n)
            nav.page_label.value = f"Page {i + 1} / {show_total}"
            nav.prev_btn.disabled = i <= 0
            nav.next_btn.disabled = i >= n - 1
        tool_disabled = n <= 0
        for btn in (
            nav.fit_page_btn,
            nav.fit_width_btn,
            nav.pan_btn,
            nav.zoom_out_btn,
            nav.zoom_in_btn,
        ):
            btn.disabled = tool_disabled
        for c in (
            nav.page_label,
            nav.prev_btn,
            nav.next_btn,
            nav.fit_page_btn,
            nav.fit_width_btn,
            nav.pan_btn,
            nav.zoom_out_btn,
            nav.zoom_in_btn,
        ):
            if ctrl_on_page(c):
                c.update()

    def sync_pan_button(self) -> None:
        active = bool(self.left._viewer.pan_enabled)
        self.nav.pan_btn.icon_color = (
            config.PRIMARY_COLOR if active else config.ON_SURFACE_VARIANT
        )
        if ctrl_on_page(self.nav.pan_btn):
            self.nav.pan_btn.update()

    def set_page(self, page_index: int, *, reset_transform: bool = True) -> None:
        if self._page_guard["active"]:
            return
        self._page_guard["active"] = True
        try:
            ix = int(page_index)
            for pane in (self.left, self.right):
                pane.set_page(
                    ix,
                    reset_transform=reset_transform,
                    update_nav_chrome=False,
                    notify_page_change=False,
                )
            if reset_transform:
                self._reset_transform_tracking()
            self.sync_nav_chrome()
            if self.on_page_change is not None:
                self.on_page_change(self.left.current_index)
        finally:
            self._page_guard["active"] = False

    def go_relative(self, delta: int) -> None:
        if delta == 0:
            return
        self.set_page(self.current_index + delta)

    def fit_to_viewport(self) -> None:
        for pane in (self.left, self.right):
            pane.fit_to_viewport()
        self._reset_transform_tracking()

    def fit_to_width(self) -> None:
        for pane in (self.left, self.right):
            pane.fit_to_width()
        self._reset_transform_tracking()

    def toggle_pan(self) -> None:
        if self.left.page_count <= 0:
            return
        enabled = not bool(self.left._viewer.pan_enabled)
        for pane in (self.left, self.right):
            pane._viewer.pan_enabled = enabled
            if ctrl_on_page(pane._viewer):
                pane._viewer.update()
        self.sync_pan_button()

    async def zoom_step_async(self, factor: float) -> None:
        if abs(factor - 1.0) < 1e-4:
            return
        for pane in (self.left, self.right):
            pane._viewer_interacting = True
        try:
            w = max(self.left._viewport_w, 1.0)
            h = max(self.left._viewport_h, _FOCUS_MIN_VIEWPORT_H)
            u, v = _plan_norm_from_viewport_tracked(
                self.left, w * 0.5, h * 0.5, self._left_track
            )
            await self._dual_zoom_at_plan_norm(u, v, factor)
        finally:
            for pane in (self.left, self.right):
                pane._viewer_interacting = False

    async def ensure_viewport_sync(self) -> None:
        await self.left.ensure_viewport_sync()
        await self.right.ensure_viewport_sync()

    def sync_viewport(self, col_w: float, viewport_h: float) -> None:
        for pane in (self.left, self.right):
            if bool(getattr(pane, "_viewer_interacting", False)):
                continue
            if (
                pane._viewport_w > 0
                and abs(col_w - pane._viewport_w) <= 0.5
                and abs(viewport_h - pane._viewport_h) <= 0.5
                and float(pane._image.width or 0) > 0
            ):
                continue
            pane.sync_viewport(col_w, viewport_h)

    def _reset_gesture_scale(self) -> None:
        self._gesture_state["scale"] = 1.0

    async def mirror_gesture(
        self,
        src: PlanFocusViewer,
        e: ft.ScaleUpdateEvent,
    ) -> None:
        """Mirror the live pan/zoom from ``src`` onto ``dst``.

        Flutter already applies the gesture natively to ``src`` (the pane under the
        cursor); we must not re-drive ``src`` programmatically or it fights the
        native gesture. Instead advance ``track_src`` to model the native motion,
        then apply the same increment to ``dst`` only.
        """
        dst = self.right if src is self.left else self.left
        async with self._gesture_lock:
            try:
                lp = e.local_focal_point
                fx, fy = float(lp.x), float(lp.y)
                d = e.focal_point_delta
                cur = float(e.scale)
                prev = float(self._gesture_state.get("scale", 1.0))
                factor = cur / prev if prev > 1e-9 else 1.0
                track_src = self._track_for(src)
                track_dst = self._track_for(dst)
                if abs(factor - 1.0) > 1e-4:
                    u, v = _plan_norm_from_viewport_tracked(src, fx, fy, track_src)
                    dst_fx, dst_fy = _viewport_from_plan_norm_tracked(
                        dst, u, v, track_dst
                    )
                    await _zoom_at_focal(dst._viewer, dst_fx, dst_fy, factor)
                    _track_after_zoom_at_focal(track_src, fx, fy, factor)
                    _track_after_zoom_at_focal(track_dst, dst_fx, dst_fy, factor)
                else:
                    dx, dy = float(d.x), float(d.y)
                    await dst._viewer.pan(dx, dy)
                    _track_after_pan(track_src, dx, dy)
                    _track_after_pan(track_dst, dx, dy)
                self._gesture_state["scale"] = cur
                src._iv_track.scale = track_src.scale
                src._iv_track.tx = track_src.tx
                src._iv_track.ty = track_src.ty
                dst._iv_track.scale = track_dst.scale
                dst._iv_track.tx = track_dst.tx
                dst._iv_track.ty = track_dst.ty
            except (RuntimeError, TypeError, AttributeError, ValueError):
                pass

    def wire_gesture_sync(self) -> None:
        for iv in (self.left._viewer, self.right._viewer):
            iv.interaction_update_interval = 50

        def _on_start(_e: ft.ControlEvent | None = None) -> None:
            self._reset_gesture_scale()
            self.left._viewer_interacting = True
            self.right._viewer_interacting = True
            self.left.hide_text_change_tooltip()
            self.right.hide_text_change_tooltip()
            self.left.hide_region_action_cube()
            self.right.hide_region_action_cube()

        def _on_end(_e: ft.ControlEvent | None = None) -> None:
            self._reset_gesture_scale()
            self.left._viewer_interacting = False
            self.right._viewer_interacting = False

        def _schedule(src: PlanFocusViewer, e: ft.ScaleUpdateEvent) -> None:
            self.page.run_task(self.mirror_gesture, src, e)

        for pane in (self.left, self.right):
            iv = pane._viewer
            orig_start = iv.on_interaction_start
            orig_end = iv.on_interaction_end

            def _start(e: ft.ControlEvent | None = None, _orig=orig_start) -> None:
                if _orig is not None:
                    _orig(e)
                _on_start(e)

            def _end(e: ft.ControlEvent | None = None, _orig=orig_end) -> None:
                if _orig is not None:
                    _orig(e)
                _on_end(e)

            iv.on_interaction_start = _start
            iv.on_interaction_end = _end
            iv.on_interaction_update = lambda e, s=pane: _schedule(s, e)


@dataclass
class PlanFocusPairViewer:
    """Side-by-side plan compare: two viewport panes + shared bottom nav."""

    root: ft.Column
    left: PlanFocusViewer
    right: PlanFocusViewer
    controller: PlanFocusPairController
    _page_nav_row: ft.Container = field(repr=False)
    _page_nav_inner_row: ft.Row = field(repr=False)
    _nav_trailing: list[ft.Control] = field(default_factory=list, repr=False)

    @property
    def current_index(self) -> int:
        return self.controller.current_index

    @property
    def page_count(self) -> int:
        return max(self.left.page_count, self.right.page_count)

    def set_nav_trailing(self, controls: list[ft.Control]) -> None:
        row = self._page_nav_inner_row
        for c in list(self._nav_trailing):
            if c in row.controls:
                row.controls.remove(c)
        self._nav_trailing = []
        if not controls:
            if ctrl_on_page(self._page_nav_row):
                self._page_nav_row.update()
            return
        spacer = ft.Container(width=8)
        row.controls.extend([spacer, *controls])
        self._nav_trailing = [spacer, *controls]
        if ctrl_on_page(self._page_nav_row):
            self._page_nav_row.update()

    async def ensure_viewport_sync(self) -> None:
        await self.controller.ensure_viewport_sync()

    def sync_viewport(self, col_w: float, viewport_h: float) -> None:
        self.controller.sync_viewport(col_w, viewport_h)


def build_plan_side_by_side_icon(*, size: int = 20) -> ft.Control:
    """Two rectangles side by side (layout menu / toolbar)."""
    gap = max(2, size // 10)
    rect_w = max(4, (size - gap) // 2)
    rect_h = max(6, int(size * 0.75))
    border_c = ft.Colors.with_opacity(0.55, config.ON_SURFACE_VARIANT)
    return ft.Row(
        [
            ft.Container(
                width=rect_w,
                height=rect_h,
                border=ft.border.all(1, border_c),
                border_radius=2,
            ),
            ft.Container(
                width=rect_w,
                height=rect_h,
                border=ft.border.all(1, border_c),
                border_radius=2,
            ),
        ],
        spacing=gap,
        tight=True,
        alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def build_plan_compare_focus_pane(
    page_png_paths: list[Path],
    *,
    initial_page_index: int = 0,
    expected_page_count: int | None = None,
    text_changes: list[PlanTextChangeView] | None = None,
    overlay_mode: PlanTextOverlayMode = "candidate",
    text_overlay_visible: bool = False,
    hover_enabled: bool = True,
    on_page_change: Callable[[int], None] | None = None,
    on_place_comment: Callable[[float, float], None] | None = None,
    on_revision_cloud: Callable[[float, float, float, float], None] | None = None,
) -> PlanFocusViewer:
    """Compare pane without nav (for side-by-side pair)."""
    pane = _build_plan_focus_pane(
        page_png_paths,
        initial_page_index=initial_page_index,
        expected_page_count=expected_page_count,
        on_page_change=on_page_change,
    )
    if on_place_comment is not None:
        pane._on_place_comment = on_place_comment
    if on_revision_cloud is not None:
        pane._on_revision_cloud = on_revision_cloud
    pane.set_text_changes(
        text_changes,
        overlay_mode=overlay_mode,
        visible=text_overlay_visible,
        hover_enabled=hover_enabled,
    )
    return pane


def build_plan_side_by_side_pair(
    left_paths: list[Path],
    right_paths: list[Path],
    *,
    initial_page_index: int = 0,
    left_expected_page_count: int | None = None,
    right_expected_page_count: int | None = None,
    left_text_changes: list[PlanTextChangeView] | None = None,
    right_text_changes: list[PlanTextChangeView] | None = None,
    left_overlay_mode: PlanTextOverlayMode = "baseline",
    right_overlay_mode: PlanTextOverlayMode = "candidate",
    text_overlay_visible: bool = False,
    hover_enabled: bool = True,
    on_page_change: Callable[[int], None] | None = None,
    pane_border: ft.Border | None = None,
    page: ft.Page | None = None,
) -> PlanFocusPairViewer:
    """Two synced plan panes with one shared bottom navigation row."""
    left = build_plan_compare_focus_pane(
        left_paths,
        initial_page_index=initial_page_index,
        expected_page_count=left_expected_page_count,
        text_changes=left_text_changes,
        overlay_mode=left_overlay_mode,
        text_overlay_visible=text_overlay_visible,
        hover_enabled=hover_enabled,
    )
    right = build_plan_compare_focus_pane(
        right_paths,
        initial_page_index=initial_page_index,
        expected_page_count=right_expected_page_count,
        text_changes=right_text_changes,
        overlay_mode=right_overlay_mode,
        text_overlay_visible=text_overlay_visible,
        hover_enabled=hover_enabled,
    )
    if pane_border is None:
        pane_border = _PANE_BORDER

    holder: dict[str, PlanFocusPairController | None] = {"c": None}

    def _prev(_e: ft.ControlEvent) -> None:
        c = holder["c"]
        if c is not None:
            c.go_relative(-1)

    def _next(_e: ft.ControlEvent) -> None:
        c = holder["c"]
        if c is not None:
            c.go_relative(1)

    def _fit_page(_e: ft.ControlEvent) -> None:
        c = holder.get("c")
        if c is not None:
            c.fit_to_viewport()

    def _fit_width(_e: ft.ControlEvent) -> None:
        c = holder.get("c")
        if c is not None:
            c.fit_to_width()

    def _pan(_e: ft.ControlEvent) -> None:
        c = holder.get("c")
        if c is not None:
            c.toggle_pan()

    def _zoom_out(_e: ft.ControlEvent) -> None:
        c = holder.get("c")
        if c is None:
            return
        c.page.run_task(c.zoom_step_async, 1.0 / _FOCUS_ZOOM_STEP)

    def _zoom_in(_e: ft.ControlEvent) -> None:
        c = holder.get("c")
        if c is None:
            return
        c.page.run_task(c.zoom_step_async, _FOCUS_ZOOM_STEP)

    page_count = max(left.page_count, right.page_count)
    nav = _build_focus_nav_row(
        page_count=page_count,
        current_index=left.current_index,
        nav_total=page_count,
        on_prev=_prev,
        on_next=_next,
        on_fit_page=_fit_page,
        on_fit_width=_fit_width,
        on_pan=_pan,
        on_zoom_out=_zoom_out,
        on_zoom_in=_zoom_in,
    )

    pg = page or _control_page_safe(left._viewer) or _control_page_safe(right._viewer)
    if pg is None:
        raise RuntimeError("Plan side-by-side pair requires a Flet page (pass page= from the host)")

    controller = PlanFocusPairController(
        left=left,
        right=right,
        page=pg,
        nav=nav,
        on_page_change=on_page_change,
    )
    holder["c"] = controller
    controller.sync_nav_chrome()
    controller.sync_pan_button()
    controller.wire_gesture_sync()

    panes_row = ft.Row(
        [
            ft.Container(content=left.root, expand=True, border=pane_border, border_radius=8),
            ft.Container(content=right.root, expand=True, border=pane_border, border_radius=8),
        ],
        expand=True,
        spacing=8,
    )
    root = ft.Column([panes_row, nav.page_nav_row], expand=True, spacing=0)
    return PlanFocusPairViewer(
        root=root,
        left=left,
        right=right,
        controller=controller,
        _page_nav_row=nav.page_nav_row,
        _page_nav_inner_row=nav.page_nav_inner_row,
    )


def build_plan_compare_focus_viewer(
    page_png_paths: list[Path],
    *,
    initial_page_index: int = 0,
    expected_page_count: int | None = None,
    text_changes: list[PlanTextChangeView] | None = None,
    overlay_mode: PlanTextOverlayMode = "candidate",
    text_overlay_visible: bool = False,
    hover_enabled: bool = True,
    on_page_change: Callable[[int], None] | None = None,
    on_place_comment: Callable[[float, float], None] | None = None,
    on_revision_cloud: Callable[[float, float, float, float], None] | None = None,
) -> PlanFocusViewer:
    """History/Review plan pane: same full-height viewport + nav + zoom as Focus."""
    viewer = build_plan_focus_viewer(
        page_png_paths,
        initial_page_index=initial_page_index,
        expected_page_count=expected_page_count,
        on_page_change=on_page_change,
    )
    if on_place_comment is not None:
        viewer._on_place_comment = on_place_comment
    if on_revision_cloud is not None:
        viewer._on_revision_cloud = on_revision_cloud
    viewer.set_text_changes(
        text_changes,
        overlay_mode=overlay_mode,
        visible=text_overlay_visible,
        hover_enabled=hover_enabled,
    )
    return viewer


def refresh_compare_page_text_overlay(
    overlay: ComparePageTextOverlay,
    iv: ft.InteractiveViewer,
    changes: list[PlanTextChangeView],
    *,
    overlay_mode: PlanTextOverlayMode,
    hover_enabled: bool,
) -> None:
    rect = compare_iv_layout_rect(iv)
    page_changes = _filter_text_changes_for_overlay(changes, overlay.page_index, overlay_mode)
    host = overlay.tooltip_host

    def _on_hover(ch: PlanTextChangeView) -> None:
        if not hover_enabled:
            return
        host.content = build_text_change_hover_card(ch.old_text, ch.new_text, kind=ch.kind)
        vw, vh = _viewer_viewport(iv)
        meta = _viewer_meta(iv)
        iw = float(meta.get("img_w", 1))
        ih = float(meta.get("img_h", 1))
        if ch.pin_norm is not None:
            u, v = ch.pin_norm
        else:
            nb = ch.norm_bbox
            u = (float(nb[0]) + float(nb[2])) * 0.5
            v = (float(nb[1]) + float(nb[3])) * 0.5
        ax, ay = u * iw, v * ih
        left, top = _clamp_tools_pill_position(
            ax,
            ay,
            stack_w=vw,
            stack_h=vh,
            pill_w=_TEXT_CHANGE_TOOLTIP_EST_W,
            pill_h=_TEXT_CHANGE_TOOLTIP_EST_H,
        )
        host.left = left
        host.top = top
        host.visible = True
        if ctrl_on_page(host):
            host.update()

    def _on_exit() -> None:
        if bool(getattr(host, "visible", False)):
            host.visible = False
            if ctrl_on_page(host):
                host.update()

    overlay.labels_stack.controls = build_text_change_overlay_controls(
        page_changes,
        overlay_mode=overlay_mode,
        hover_enabled=hover_enabled,
        layout_rect=rect,
        on_pin_hover=_on_hover,
        on_pin_hover_exit=_on_exit,
    )
    if ctrl_on_page(overlay.labels_stack):
        overlay.labels_stack.update()


def plan_picture_compare_column(
    page_png_paths: list[Path],
    *,
    page_viewport_height: float = _DEFAULT_PAGE_VIEWPORT_H,
    min_scale: float = _FOCUS_MIN_SCALE,
    max_scale: float = _FOCUS_MAX_SCALE,
    text_changes: list[PlanTextChangeView] | None = None,
    overlay_mode: PlanTextOverlayMode = "candidate",
    hover_enabled: bool = True,
    text_overlay_visible: bool = True,
) -> tuple[ft.Column, list[ft.InteractiveViewer], list[ComparePageTextOverlay]]:
    """
    One ``InteractiveViewer`` per page for a compare column (baseline or candidate).
    Outer ``ListView`` scrolls; wire pairs with :func:`wire_synced_interactive_viewer_pair`.
    """
    viewers: list[ft.InteractiveViewer] = []
    overlays: list[ComparePageTextOverlay] = []
    rows: list[ft.Control] = []
    paths = list(page_png_paths)
    show_text = bool(text_overlay_visible and text_changes)
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
        if show_text:
            labels_stack = ft.Stack([], expand=True)
            tooltip_host = build_text_change_tooltip_host()
            page_overlay = ComparePageTextOverlay(
                page_index=i,
                labels_stack=labels_stack,
                tooltip_host=tooltip_host,
            )

            def _hide_tip(_e: ft.ControlEvent, h: ft.Container = tooltip_host) -> None:
                if bool(getattr(h, "visible", False)):
                    h.visible = False
                    if ctrl_on_page(h):
                        h.update()

            iv.on_interaction_start = _hide_tip
            refresh_compare_page_text_overlay(
                page_overlay,
                iv,
                text_changes or [],
                overlay_mode=overlay_mode,
                hover_enabled=hover_enabled,
            )
            overlays.append(page_overlay)
            page_stack = ft.Stack(
                [iv, labels_stack, tooltip_host],
                expand=True,
            )
            page_content: ft.Control = ft.Container(
                content=page_stack,
                height=page_viewport_height,
                alignment=ft.Alignment.TOP_CENTER,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                border=_PANE_BORDER,
                border_radius=8,
            )
        else:
            page_content = ft.Container(
                content=iv,
                height=page_viewport_height,
                alignment=ft.Alignment.TOP_CENTER,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                border=_PANE_BORDER,
                border_radius=8,
            )
        viewers.append(iv)
        rows.append(
            ft.Container(
                content=page_content,
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
    return col, viewers, overlays


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


def wire_synced_focus_viewer_pair(
    left: PlanFocusViewer,
    right: PlanFocusViewer,
    page: ft.Page,
) -> None:
    """Keep page index and pan/zoom aligned between two focus plan viewers."""
    page_guard: dict[str, bool] = {"active": False}
    left_orig = left._on_page_change
    right_orig = right._on_page_change

    def _sync_page(target: PlanFocusViewer, source_ix: int) -> None:
        if page_guard["active"]:
            return
        page_guard["active"] = True
        try:
            if target.current_index != source_ix:
                target._viewer_interacting = True
                try:
                    target.set_page(source_ix, reset_transform=False)
                finally:
                    target._viewer_interacting = False
        finally:
            page_guard["active"] = False

    def _on_left_page(ix: int) -> None:
        if left_orig is not None:
            left_orig(ix)
        _sync_page(right, ix)

    def _on_right_page(ix: int) -> None:
        if right_orig is not None:
            right_orig(ix)
        _sync_page(left, ix)

    left._on_page_change = _on_left_page
    right._on_page_change = _on_right_page

    state: dict[str, Any] = {"guard": False, "gesture_scale": 1.0}
    l_iv = left._viewer
    r_iv = right._viewer

    async def _mirror(
        src: PlanFocusViewer,
        dst: PlanFocusViewer,
        dst_iv: ft.InteractiveViewer,
        e: ft.ScaleUpdateEvent,
    ) -> None:
        if state["guard"]:
            return
        state["guard"] = True
        try:
            lp = e.local_focal_point
            fx, fy = float(lp.x), float(lp.y)
            pfx, pfy = _map_focal_between_focus_viewers(src, dst, fx, fy)
            d = e.focal_point_delta
            cur = float(e.scale)
            prev = float(state.get("gesture_scale", 1.0))
            factor = cur / prev if prev > 1e-9 else 1.0
            if abs(factor - 1.0) > 1e-4:
                await _zoom_at_focal(dst_iv, pfx, pfy, factor)
            else:
                await dst_iv.pan(float(d.x), float(d.y))
            state["gesture_scale"] = cur
        except (RuntimeError, TypeError, AttributeError, ValueError):
            pass
        finally:
            state["guard"] = False

    def _reset(_e: ft.ControlEvent | None = None) -> None:
        state["gesture_scale"] = 1.0

    def _on_interaction_start(_e: ft.ControlEvent | None = None) -> None:
        _reset(_e)
        left._viewer_interacting = True
        right._viewer_interacting = True

    def _on_interaction_end(_e: ft.ControlEvent | None = None) -> None:
        _reset(_e)
        left._viewer_interacting = False
        right._viewer_interacting = False

    def _schedule(
        src: PlanFocusViewer,
        dst: PlanFocusViewer,
        dst_iv: ft.InteractiveViewer,
        e: ft.ScaleUpdateEvent,
    ) -> None:
        page.run_task(_mirror, src, dst, dst_iv, e)

    for iv in (l_iv, r_iv):
        iv.interaction_update_interval = 50
    l_iv.on_interaction_start = _on_interaction_start
    r_iv.on_interaction_start = _on_interaction_start
    l_iv.on_interaction_end = _on_interaction_end
    r_iv.on_interaction_end = _on_interaction_end
    l_iv.on_interaction_update = lambda e: _schedule(left, right, r_iv, e)
    r_iv.on_interaction_update = lambda e: _schedule(right, left, l_iv, e)


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
