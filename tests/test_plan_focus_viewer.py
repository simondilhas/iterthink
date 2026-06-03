"""Tests for Focus plan single-page viewer."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import flet as ft
import pytest

from iterthink.studio import plan_picture_viewer


def test_build_plan_focus_viewer_empty() -> None:
    v = plan_picture_viewer.build_plan_focus_viewer([])
    assert v.page_count == 0
    assert v.current_index == 0
    assert v._prev_btn.disabled is True
    assert v._next_btn.disabled is True
    assert v._page_label.value == "No pages"


def test_sync_viewport_sets_image_width(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v.sync_viewport(900.0, 500.0)
    assert v._viewport_w == 900.0
    assert float(v._image.width or 0) == 900.0


def test_clamp_tools_pill_position() -> None:
    left, top = plan_picture_viewer._clamp_tools_pill_position(
        400.0, 300.0, stack_w=800.0, stack_h=600.0
    )
    assert left == pytest.approx(408.0)
    assert top == pytest.approx(308.0)
    left2, top2 = plan_picture_viewer._clamp_tools_pill_position(
        900.0, 900.0, stack_w=800.0, stack_h=600.0
    )
    assert left2 <= 800.0 - plan_picture_viewer._TOOLS_PILL_EST_W - plan_picture_viewer._TOOLS_PILL_MARGIN
    assert top2 <= 600.0 - plan_picture_viewer._TOOLS_PILL_EST_H - plan_picture_viewer._TOOLS_PILL_MARGIN


def test_show_tools_pill_positions_host(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v._stack_w = 800.0
    v._stack_h = 600.0
    v.show_tools_pill(100.0, 120.0)
    assert v._tools_pill_host.visible is True
    assert float(v._tools_pill_host.left or 0) == pytest.approx(108.0)
    assert float(v._tools_pill_host.top or 0) == pytest.approx(128.0)


def test_on_viewport_tap_outside_hides_pill(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v._stack_w = 800.0
    v._stack_h = 600.0
    v.show_tools_pill(100.0, 120.0)
    v.on_viewport_tap(500.0, 400.0)
    assert v._tools_pill_host.visible is False


def test_width_fit_size_uses_full_viewport_width() -> None:
    dw, dh = plan_picture_viewer._width_fit_size(2000, 1000, 800.0)
    assert dw == 800.0
    assert dh == 400.0


def test_contain_fit_size_fits_inside_viewport() -> None:
    dw, dh = plan_picture_viewer._contain_fit_size(2000, 3000, 800.0, 600.0)
    assert dw <= 800.0
    assert dh <= 600.0
    assert dw == pytest.approx(400.0)
    assert dh == pytest.approx(600.0)


def test_fit_to_viewport_uses_contain_mode(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v.sync_viewport(800.0, 600.0)
    v.fit_to_viewport()
    assert v._layout_mode == "contain"
    assert float(v._image.width or 0) <= 800.0
    assert float(v._image.height or 0) <= 600.0


def test_fit_to_width_mode(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v.sync_viewport(800.0, 600.0)
    v.fit_to_width()
    assert v._layout_mode == "width"
    assert float(v._image.width or 0) == 800.0


def test_build_plan_focus_viewer_pages(tmp_path: Path) -> None:
    paths = []
    for i in range(3):
        p = tmp_path / f"p{i}.png"
        p.write_bytes(b"x")
        paths.append(p)
    v = plan_picture_viewer.build_plan_focus_viewer(paths, initial_page_index=1)
    assert v.page_count == 3
    assert v.current_index == 1
    assert v._image.src == str(paths[1])
    assert v._viewer.constrained is False
    assert v._prev_btn.disabled is False
    assert v._next_btn.disabled is False
    assert v._page_label.value == "Page 2 / 3"


def test_set_page_updates_image_and_nav(tmp_path: Path) -> None:
    paths = [tmp_path / f"p{i}.png" for i in range(3)]
    for p in paths:
        p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer(paths)
    v.set_page(2)
    assert v.current_index == 2
    assert v._image.src == str(paths[2])
    assert v._page_label.value == "Page 3 / 3"
    assert v._prev_btn.disabled is False
    assert v._next_btn.disabled is True


def test_go_relative_clamps(tmp_path: Path) -> None:
    paths = [tmp_path / "p0.png", tmp_path / "p1.png"]
    for p in paths:
        p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer(paths)
    v.go_relative(-1)
    assert v.current_index == 0
    v.go_relative(99)
    assert v.current_index == 1


def test_on_page_change_callback(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    seen: list[int] = []

    def _cb(ix: int) -> None:
        seen.append(ix)

    v = plan_picture_viewer.build_plan_focus_viewer([p], on_page_change=_cb)
    v.set_page(0)
    assert seen == [0]


def test_show_page_skips_when_not_on_page() -> None:
    col = MagicMock()
    col.visible = True
    type(col).page = PropertyMock(side_effect=RuntimeError("not mounted"))
    v = plan_picture_viewer.PlanFocusViewer(
        root=col,
        page_count=1,
        current_index=0,
        _paths=[Path("/x.png")],
        _image=MagicMock(),
        _viewer=MagicMock(),
        _page_frame=MagicMock(),
        _page_label=MagicMock(),
        _prev_btn=MagicMock(),
        _next_btn=MagicMock(),
        _fit_page_btn=MagicMock(),
        _fit_width_btn=MagicMock(),
        _pan_btn=MagicMock(),
        _zoom_out_btn=MagicMock(),
        _zoom_in_btn=MagicMock(),
        _comment_btn=MagicMock(),
        _draw_cloud_btn=MagicMock(),
        _export_btn=MagicMock(),
        _viewport_stack=MagicMock(),
        _annotations_overlay=MagicMock(),
        _draw_rubber_band=MagicMock(visible=False),
        _tools_pill_host=MagicMock(visible=False),
    )
    asyncio.run(v.show_page(0))


def test_page_frame_resize_sets_height() -> None:
    frame = ft.Container(expand=True)
    events: list[float] = []

    def _on_resize(e: ft.LayoutSizeChangeEvent) -> None:
        h = max(plan_picture_viewer._FOCUS_MIN_VIEWPORT_H, float(e.height))
        frame.height = h
        events.append(h)

    frame.on_size_change = _on_resize
    ev = MagicMock()
    ev.width = 400.0
    ev.height = 612.0
    _on_resize(ev)
    assert events == [612.0]
    assert frame.height == 612.0


def test_plan_picture_compare_column(tmp_path: Path) -> None:
    paths = [tmp_path / f"p{i}.png" for i in range(2)]
    for p in paths:
        p.write_bytes(b"x")
    col, ivs = plan_picture_viewer.plan_picture_compare_column(paths)
    assert len(ivs) == 2
    assert col.scroll is None
    assert ivs[0].constrained is True
    assert ivs[0].data["img_w"] >= 1


def test_map_focal_across_different_aspect_ratios() -> None:
    left = MagicMock()
    right = MagicMock()
    left.width, left.height = 400.0, 520.0
    right.width, right.height = 400.0, 520.0
    left.data = {"img_w": 2000, "img_h": 1000, "viewport_h": 520}
    right.data = {"img_w": 1000, "img_h": 2000, "viewport_h": 520}
    s_rect = plan_picture_viewer._contain_rect(400, 520, 2000, 1000)
    cx, cy = s_rect.x0 + s_rect.w * 0.5, s_rect.y0 + s_rect.h * 0.5
    px, py = plan_picture_viewer._map_focal_to_peer(left, right, cx, cy)
    d_rect = plan_picture_viewer._contain_rect(400, 520, 1000, 2000)
    assert abs(px - (d_rect.x0 + d_rect.w * 0.5)) < 2.0
    assert abs(py - (d_rect.y0 + d_rect.h * 0.5)) < 2.0


def test_zoom_at_focal_pans_before_scale() -> None:
    iv = MagicMock()
    iv.pan = AsyncMock()
    iv.zoom = AsyncMock()
    asyncio.run(plan_picture_viewer._zoom_at_focal(iv, 100.0, 50.0, 1.25))
    iv.pan.assert_awaited_once_with(-25.0, -12.5)
    iv.zoom.assert_awaited_once_with(1.25)


def test_wire_sync_zoom_uses_focal_mapping() -> None:
    left = MagicMock()
    right = MagicMock()
    left.width, left.height = 400.0, 520.0
    right.width, right.height = 400.0, 520.0
    left.data = {"img_w": 1000, "img_h": 1000, "viewport_h": 520}
    right.data = {"img_w": 1000, "img_h": 1000, "viewport_h": 520}
    left.pan = AsyncMock()
    left.zoom = AsyncMock()
    right.pan = AsyncMock()
    right.zoom = AsyncMock()
    page = MagicMock()
    tasks: list = []

    def run_task(coro, *args):
        tasks.append((coro, args))

    page.run_task = run_task
    plan_picture_viewer.wire_synced_interactive_viewer_pair(left, right, page)
    ev = MagicMock()
    ev.focal_point_delta = ft.Offset(0, 0)
    ev.local_focal_point = ft.Offset(200, 260)
    ev.scale = 1.2
    ev.pointer_count = 2
    left.on_interaction_start(None)
    left.on_interaction_update(ev)
    assert len(tasks) == 1
    coro, args = tasks[0]
    assert args[0] is left and args[1] is right
    asyncio.run(coro(*args))
    right.pan.assert_awaited_once()
    pan_args = right.pan.await_args[0]
    assert pan_args[0] == pytest.approx(-40.0, abs=0.01)
    assert pan_args[1] == pytest.approx(-52.0, abs=0.01)
    right.zoom.assert_awaited_once_with(1.2)


def test_viewport_norm_round_trip_width_fit(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v.sync_viewport(800.0, 600.0)
    norm = plan_picture_viewer.viewport_local_to_page_norm(400.0, 200.0, v)
    assert norm is not None
    u, nv = norm
    lx, ly = plan_picture_viewer.page_norm_to_viewport_px(u, nv, v)
    assert lx == pytest.approx(400.0, abs=2.0)
    assert ly == pytest.approx(200.0, abs=2.0)


def test_place_comment_mode_invokes_callback(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    seen: list[tuple[float, float]] = []

    def _cb(u: float, v: float) -> None:
        seen.append((u, v))

    v = plan_picture_viewer.build_plan_focus_viewer([p], on_place_comment=_cb)
    v.sync_viewport(800.0, 600.0)
    v.set_interaction_mode("place_comment")
    v.handle_viewport_tap(400.0, 200.0)
    assert len(seen) == 1
    assert v.interaction_mode == "idle"


def test_wire_sync_pan_only() -> None:
    left = MagicMock()
    right = MagicMock()
    left.width, left.height = 400.0, 520.0
    right.width, right.height = 400.0, 520.0
    left.data = {"img_w": 1000, "img_h": 1000, "viewport_h": 520}
    right.data = {"img_w": 1000, "img_h": 1000, "viewport_h": 520}
    right.pan = AsyncMock()
    right.zoom = AsyncMock()
    page = MagicMock()
    tasks: list = []

    def run_task(coro, *args):
        tasks.append((coro, args))

    page.run_task = run_task
    plan_picture_viewer.wire_synced_interactive_viewer_pair(left, right, page)
    ev = MagicMock()
    ev.focal_point_delta = ft.Offset(3, -2)
    ev.local_focal_point = ft.Offset(200, 260)
    ev.scale = 1.0
    ev.pointer_count = 1
    left.on_interaction_start(None)
    left.on_interaction_update(ev)
    asyncio.run(tasks[0][0](*tasks[0][1]))
    right.pan.assert_awaited_once_with(3.0, -2.0)
    right.zoom.assert_not_called()
