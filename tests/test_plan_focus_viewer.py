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


def test_plan_focus_nav_row_centered() -> None:
    v = plan_picture_viewer.build_plan_focus_viewer([])
    nav_container = v.root.controls[1]
    assert isinstance(nav_container, ft.Container)
    assert nav_container.alignment == ft.Alignment.CENTER
    nav_row = nav_container.content
    assert isinstance(nav_row, ft.Row)
    assert nav_row.tight is True
    assert not nav_row.expand


def test_sync_viewport_sets_image_width(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v.sync_viewport(900.0, 500.0)
    assert v._viewport_w == 900.0
    assert float(v._image.width or 0) == 900.0
    w_before = float(v._image.width or 0)
    v.sync_viewport(900.0, 500.0)
    assert float(v._image.width or 0) == w_before


def test_apply_layout_resize_skips_during_interaction(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v.sync_viewport(640.0, 480.0)
    v._viewer_interacting = True
    v.apply_layout_resize(320.0, 240.0)
    assert v._viewport_w == 640.0
    assert v._viewport_h == 480.0


def test_zoom_step_async_skips_spurious_layout_resize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v.sync_viewport(640.0, 480.0)

    async def _slow_zoom(*_args: object) -> None:
        assert v._viewer_interacting is True
        v.apply_layout_resize(320.0, 240.0)

    monkeypatch.setattr(plan_picture_viewer, "_zoom_at_focal", _slow_zoom)
    asyncio.run(v.zoom_step_async(1.25))
    assert v._viewport_w == 640.0
    assert v._viewport_h == 480.0
    assert v._viewer_interacting is False


def test_stack_resize_drives_viewport_sync(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    handler = v._viewport_stack.on_size_change
    assert handler is not None
    ev = MagicMock()
    ev.width = 720.0
    ev.height = 540.0
    handler(ev)
    assert v._viewport_w == 720.0
    assert v._viewport_h == 540.0
    assert float(v._image.width or 0) == 720.0


def test_ensure_viewport_sync_from_root_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "p0.png"
    p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    monkeypatch.setattr(plan_picture_viewer, "ctrl_on_page", lambda _c: False)
    v.root.width = 640.0
    v.root.height = 520.0
    asyncio.run(v.ensure_viewport_sync())
    assert v._viewport_w == 640.0
    assert v._viewport_h == pytest.approx(480.0)
    assert float(v._image.width or 0) == 640.0


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


def test_set_page_skips_reset_when_requested(tmp_path: Path) -> None:
    paths = [tmp_path / f"p{i}.png" for i in range(2)]
    for p in paths:
        p.write_bytes(b"x")
    v = plan_picture_viewer.build_plan_focus_viewer(paths)
    reset_calls: list[int] = []
    v._reset_viewer_transform = lambda: reset_calls.append(1)  # type: ignore[method-assign]
    v.set_page(1, reset_transform=False)
    assert reset_calls == []
    v.set_page(0, reset_transform=True)
    assert reset_calls == [1]


def test_wire_synced_peer_page_change_skips_reset(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    paths = [tmp_path / f"p{i}.png" for i in range(3)]
    for p in paths:
        p.write_bytes(b"x")
    left = plan_picture_viewer.build_plan_focus_viewer(paths)
    right = plan_picture_viewer.build_plan_focus_viewer(paths)
    page = MagicMock()
    plan_picture_viewer.wire_synced_focus_viewer_pair(left, right, page)
    peer_reset_flags: list[bool] = []
    real_set_page = right.set_page

    def _track_set_page(ix: int, *, reset_transform: bool = True) -> None:
        peer_reset_flags.append(reset_transform)
        real_set_page(ix, reset_transform=reset_transform)

    right.set_page = _track_set_page  # type: ignore[method-assign]
    left.set_page(1)
    assert right.current_index == 1
    assert peer_reset_flags == [False]


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


def test_set_nav_trailing_appends_after_zoom() -> None:
    nav_row = ft.Row(
        [
            ft.IconButton(ft.Icons.CHEVRON_LEFT),
            ft.Text("Page 1 / 1"),
            ft.IconButton(ft.Icons.ZOOM_IN),
        ],
        tight=True,
    )
    nav_host = ft.Container(content=nav_row)
    extra = ft.IconButton(ft.Icons.CHAT_BUBBLE_OUTLINE)
    v = plan_picture_viewer.PlanFocusViewer(
        root=ft.Column([]),
        page_count=1,
        current_index=0,
        _paths=[],
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
        _viewport_stack=MagicMock(),
        _text_labels_overlay=MagicMock(),
        _text_change_tooltip_host=MagicMock(),
        _annotations_overlay=MagicMock(),
        _draw_rubber_band=MagicMock(),
        _draw_cloud_capture=MagicMock(),
        _viewport_tap_capture=MagicMock(),
        _tools_pill_host=MagicMock(),
        _change_regions_overlay=MagicMock(),
        _region_action_host=MagicMock(),
        _page_nav_row=nav_host,
        _page_nav_inner_row=nav_row,
    )
    v.set_nav_trailing([extra])
    assert extra in nav_row.controls
    assert len(v._nav_trailing) == 2
    v.set_nav_trailing([])
    assert extra not in nav_row.controls


def test_handle_viewport_tap_place_comment_idles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plan_picture_viewer,
        "viewport_local_to_page_norm",
        lambda _x, _y, _v: (0.5, 0.5),
    )
    placed: list[tuple[float, float]] = []
    tap_cap = ft.GestureDetector(visible=True, content=ft.Container(expand=True))
    v = plan_picture_viewer.PlanFocusViewer(
        root=ft.Column([]),
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
        _viewport_stack=MagicMock(),
        _text_labels_overlay=MagicMock(),
        _text_change_tooltip_host=MagicMock(visible=False),
        _annotations_overlay=MagicMock(),
        _change_regions_overlay=MagicMock(),
        _region_action_host=MagicMock(visible=False),
        _draw_rubber_band=MagicMock(visible=False),
        _draw_cloud_capture=MagicMock(visible=False),
        _viewport_tap_capture=tap_cap,
        _tools_pill_host=MagicMock(visible=False),
        interaction_mode="place_comment",
    )
    v._on_place_comment = lambda u, w: placed.append((float(u), float(w)))
    v.handle_viewport_tap(10.0, 20.0)
    assert placed == [(0.5, 0.5)]
    assert v.interaction_mode == "idle"
    assert tap_cap.visible is False


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
        _viewport_stack=MagicMock(),
        _text_labels_overlay=MagicMock(),
        _text_change_tooltip_host=MagicMock(visible=False),
        _annotations_overlay=MagicMock(),
        _change_regions_overlay=MagicMock(),
        _region_action_host=MagicMock(visible=False),
        _draw_rubber_band=MagicMock(visible=False),
        _draw_cloud_capture=MagicMock(visible=False),
        _viewport_tap_capture=MagicMock(visible=False),
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
    col, ivs, overlays = plan_picture_viewer.plan_picture_compare_column(paths)
    assert len(ivs) == 2
    assert overlays == []
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


def _write_test_png(path: Path, w: int = 200, h: int = 200) -> None:
    from PIL import Image

    Image.new("RGB", (w, h), (255, 255, 255)).save(path)


def test_text_change_tooltip_show_hide(tmp_path: Path) -> None:
    from iterthink.services.plan_text_diff import PlanTextChangeView

    p = tmp_path / "page_0001.png"
    _write_test_png(p)
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v.sync_viewport(800.0, 600.0)
    change = PlanTextChangeView(
        change_id="t1",
        page_index=0,
        kind="modified",
        norm_bbox=(0.05, 0.05, 0.2, 0.08),
        display_text="A-102",
        old_text="A-101",
        new_text="A-102",
        size_pt=10.0,
        pin_norm=(0.21, 0.05),
    )
    v.set_text_changes([change], visible=True, hover_enabled=True)
    assert len(v._text_labels_overlay.controls) >= 1
    v.show_text_change_tooltip(change, 100.0, 80.0)
    assert v._text_change_tooltip_host.visible is True
    assert v._text_change_tooltip_host.content is not None
    v.hide_text_change_tooltip()
    assert v._text_change_tooltip_host.visible is False


def test_set_page_hides_text_change_tooltip(tmp_path: Path) -> None:
    from iterthink.services.plan_text_diff import PlanTextChangeView

    p0 = tmp_path / "page_0001.png"
    p1 = tmp_path / "page_0002.png"
    _write_test_png(p0)
    _write_test_png(p1)
    v = plan_picture_viewer.build_plan_focus_viewer([p0, p1])
    v.sync_viewport(800.0, 600.0)
    change = PlanTextChangeView(
        change_id="t1",
        page_index=0,
        kind="modified",
        norm_bbox=(0.05, 0.05, 0.2, 0.08),
        display_text="X",
        old_text="X",
        new_text="Y",
        size_pt=10.0,
        pin_norm=(0.21, 0.05),
    )
    v.set_text_changes([change], visible=True)
    v.show_text_change_tooltip(change, 50.0, 50.0)
    v.set_page(1)
    assert v._text_change_tooltip_host.visible is False


def test_compare_column_with_text_changes(tmp_path: Path) -> None:
    from iterthink.services.plan_text_diff import PlanTextChangeView

    p = tmp_path / "p0.png"
    _write_test_png(p)
    ch = PlanTextChangeView(
        change_id="c1",
        page_index=0,
        kind="modified",
        norm_bbox=(0.1, 0.1, 0.3, 0.12),
        display_text="B",
        old_text="A",
        new_text="B",
        size_pt=10.0,
        pin_norm=(0.31, 0.1),
    )
    col, ivs, overlays = plan_picture_viewer.plan_picture_compare_column(
        [p],
        text_changes=[ch],
        overlay_mode="candidate",
        hover_enabled=True,
        text_overlay_visible=True,
    )
    assert len(ivs) == 1
    assert len(overlays) == 1
    assert len(overlays[0].labels_stack.controls) >= 1


def test_change_regions_overlay_controls(tmp_path: Path) -> None:
    from iterthink.services.plan_change_regions import PlanChangeRegionView

    p = tmp_path / "p0.png"
    _write_test_png(p, 400, 300)
    v = plan_picture_viewer.build_plan_focus_viewer([p])
    v.sync_viewport(640.0, 480.0)
    region = PlanChangeRegionView(
        region_id=1,
        page_index=0,
        norm_bbox=(0.1, 0.1, 0.4, 0.3),
        paragraph_index=0,
        body="",
        pixel_count=400,
        text_change_ids=(),
        dismissed=False,
        reviewed=False,
        region_key="rk1",
    )
    v.set_change_regions([region])
    assert len(v._change_regions_overlay.controls) >= 2
    rect_ctrl = v._change_regions_overlay.controls[0]
    assert isinstance(rect_ctrl, ft.Container)
    assert rect_ctrl.border is not None
    assert rect_ctrl.content is None
    assert float(rect_ctrl.width or 0) == pytest.approx(192.0, abs=1.0)
    assert float(rect_ctrl.height or 0) == pytest.approx(96.0, abs=1.0)
    assert float(rect_ctrl.left or 0) == pytest.approx(64.0, abs=1.0)
    assert float(rect_ctrl.top or 0) == pytest.approx(48.0, abs=1.0)
    assert v._page_content_stack is not None
    assert v._change_regions_overlay in v._page_content_stack.controls
    assert v._text_labels_overlay in v._page_content_stack.controls


def test_text_overlay_controls_use_image_local_coords() -> None:
    from iterthink.services.plan_text_diff import PlanTextChangeView

    ch = PlanTextChangeView(
        change_id="c1",
        page_index=0,
        kind="modified",
        norm_bbox=(0.1, 0.1, 0.4, 0.3),
        display_text="B",
        old_text="A",
        new_text="B",
        size_pt=10.0,
        pin_norm=(0.41, 0.1),
    )
    controls = plan_picture_viewer.build_text_change_overlay_controls(
        [ch],
        overlay_mode="candidate",
        hover_enabled=False,
        img_w=640.0,
        img_h=480.0,
    )
    label = controls[0]
    assert isinstance(label, ft.Container)
    assert float(label.left or 0) == pytest.approx(64.0)
    assert float(label.top or 0) == pytest.approx(48.0)


def test_norm_bbox_to_image_rect() -> None:
    left, top, w, h = plan_picture_viewer._norm_bbox_to_image_rect(
        (0.1, 0.1, 0.4, 0.3), 640.0, 480.0
    )
    assert left == pytest.approx(64.0)
    assert top == pytest.approx(48.0)
    assert w == pytest.approx(192.0)
    assert h == pytest.approx(96.0)


def test_map_focal_between_focus_viewers_same_layout(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    _write_test_png(p, 400, 300)
    left = plan_picture_viewer.build_plan_focus_viewer([p])
    right = plan_picture_viewer.build_plan_focus_viewer([p])
    left.sync_viewport(640.0, 480.0)
    right.sync_viewport(640.0, 480.0)
    px, py = plan_picture_viewer._map_focal_between_focus_viewers(left, right, 320.0, 240.0)
    assert px == pytest.approx(320.0, abs=1.0)
    assert py == pytest.approx(240.0, abs=1.0)


def test_wire_synced_focus_viewer_pair_syncs_page_and_preserves_callbacks(
    tmp_path: Path,
) -> None:
    p0 = tmp_path / "page_0001.png"
    p1 = tmp_path / "page_0002.png"
    _write_test_png(p0)
    _write_test_png(p1)
    left = plan_picture_viewer.build_plan_focus_viewer([p0, p1])
    right = plan_picture_viewer.build_plan_focus_viewer([p0, p1])
    left.sync_viewport(640.0, 480.0)
    right.sync_viewport(640.0, 480.0)
    right_calls: list[int] = []
    right._on_page_change = lambda ix: right_calls.append(ix)
    page = MagicMock()
    page.run_task = MagicMock()
    plan_picture_viewer.wire_synced_focus_viewer_pair(left, right, page)
    left.set_page(1)
    assert right.current_index == 1
    assert right_calls == [1]


def test_wire_synced_focus_viewer_pair_uses_focus_layout_for_zoom(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    _write_test_png(p, 1000, 1000)
    left = plan_picture_viewer.build_plan_focus_viewer([p])
    right = plan_picture_viewer.build_plan_focus_viewer([p])
    left.sync_viewport(400.0, 520.0)
    right.sync_viewport(400.0, 520.0)
    page = MagicMock()
    tasks: list = []

    def run_task(coro, *args):
        tasks.append((coro, args))

    page.run_task = run_task
    plan_picture_viewer.wire_synced_focus_viewer_pair(left, right, page)
    ev = MagicMock()
    ev.focal_point_delta = ft.Offset(0, 0)
    ev.local_focal_point = ft.Offset(200, 260)
    ev.scale = 1.2
    left._viewer.on_interaction_start(None)
    left._viewer.on_interaction_update(ev)
    assert len(tasks) == 1
    coro, args = tasks[0]
    assert args[0] is left and args[1] is right
    right._viewer.pan = AsyncMock()
    right._viewer.zoom = AsyncMock()
    asyncio.run(coro(*args))
    right._viewer.zoom.assert_awaited_once_with(1.2)


def test_build_plan_side_by_side_pair_single_shared_nav(tmp_path: Path) -> None:
    p0 = tmp_path / "page_0001.png"
    p1 = tmp_path / "page_0002.png"
    _write_test_png(p0)
    _write_test_png(p1)
    page = MagicMock()
    page.run_task = MagicMock()
    pair = plan_picture_viewer.build_plan_side_by_side_pair(
        [p0, p1],
        [p0, p1],
        page=page,
    )
    assert len(pair.root.controls) == 2
    assert isinstance(pair.root.controls[0], ft.Row)
    assert isinstance(pair.root.controls[1], ft.Container)
    assert pair.left._page_nav_row is None
    assert pair.right._page_nav_row is None


def test_pair_controller_set_page_syncs_both_panes(tmp_path: Path) -> None:
    p0 = tmp_path / "page_0001.png"
    p1 = tmp_path / "page_0002.png"
    _write_test_png(p0)
    _write_test_png(p1)
    page = MagicMock()
    page.run_task = MagicMock()
    pair = plan_picture_viewer.build_plan_side_by_side_pair(
        [p0, p1],
        [p0, p1],
        page=page,
    )
    pair.controller.set_page(1)
    assert pair.left.current_index == 1
    assert pair.right.current_index == 1


@pytest.mark.asyncio
async def test_pair_controller_zoom_step_async_hits_both_ivs(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    _write_test_png(p, 1000, 1000)
    page = MagicMock()
    page.run_task = MagicMock()
    pair = plan_picture_viewer.build_plan_side_by_side_pair([p], [p], page=page)
    pair.left.sync_viewport(400.0, 520.0)
    pair.right.sync_viewport(400.0, 520.0)
    pair.left._viewer.zoom = AsyncMock()
    pair.right._viewer.zoom = AsyncMock()
    pair.left._viewer.pan = AsyncMock()
    pair.right._viewer.pan = AsyncMock()
    await pair.controller.zoom_step_async(1.25)
    pair.left._viewer.zoom.assert_awaited()
    pair.right._viewer.zoom.assert_awaited()


def test_pair_controller_gesture_mirror_via_page_task(tmp_path: Path) -> None:
    p = tmp_path / "p0.png"
    _write_test_png(p, 1000, 1000)
    page = MagicMock()
    tasks: list = []

    def run_task(coro, *args):
        tasks.append((coro, args))

    page.run_task = run_task
    pair = plan_picture_viewer.build_plan_side_by_side_pair([p], [p], page=page)
    pair.left.sync_viewport(400.0, 520.0)
    pair.right.sync_viewport(400.0, 520.0)
    for iv in (pair.left._viewer, pair.right._viewer):
        iv.save_state = AsyncMock()
        iv.restore_state = AsyncMock()
        iv.pan = AsyncMock()
        iv.zoom = AsyncMock()
    pair.left._viewer.on_interaction_start(None)
    tasks.clear()
    ev = MagicMock()
    ev.focal_point_delta = ft.Offset(0, 0)
    ev.local_focal_point = ft.Offset(200, 260)
    ev.scale = 1.2
    pair.left._viewer.on_interaction_update(ev)
    assert len(tasks) == 1
    coro, args = tasks[0]
    assert args[0] is pair.left
    asyncio.run(coro(*args))
    # Source pane is driven natively by Flutter; we must not re-drive it.
    pair.left._viewer.restore_state.assert_not_awaited()
    pair.left._viewer.zoom.assert_not_awaited()
    pair.left._viewer.pan.assert_not_awaited()
    # Destination pane is mirrored programmatically.
    pair.right._viewer.zoom.assert_awaited()


def test_plan_norm_tracked_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "p.png"
    _write_test_png(p, 800, 600)
    pane = plan_picture_viewer.build_plan_focus_viewer([p])
    pane.sync_viewport(400.0, 300.0)
    track = plan_picture_viewer._IvTransformTrack(scale=2.0, tx=10.0, ty=20.0)
    u, v = plan_picture_viewer._plan_norm_from_viewport_tracked(pane, 210.0, 170.0, track)
    fx, fy = plan_picture_viewer._viewport_from_plan_norm_tracked(pane, u, v, track)
    assert fx == pytest.approx(210.0, abs=1.0)
    assert fy == pytest.approx(170.0, abs=1.0)


def test_pair_zoom_maps_same_plan_norm_to_different_viewports(tmp_path: Path) -> None:
    left_p = tmp_path / "left.png"
    right_p = tmp_path / "right.png"
    _write_test_png(left_p, 800, 600)
    _write_test_png(right_p, 800, 400)
    page = MagicMock()
    page.run_task = MagicMock()
    pair = plan_picture_viewer.build_plan_side_by_side_pair(
        [left_p], [right_p], page=page
    )
    pair.left.sync_viewport(400.0, 300.0)
    pair.right.sync_viewport(400.0, 300.0)
    u, v = 0.5, 0.5
    left_fx, left_fy = plan_picture_viewer._viewport_from_plan_norm_tracked(
        pair.left, u, v, pair.controller._left_track
    )
    right_fx, right_fy = plan_picture_viewer._viewport_from_plan_norm_tracked(
        pair.right, u, v, pair.controller._right_track
    )
    assert left_fy != pytest.approx(right_fy, abs=1.0)
    assert left_fx == pytest.approx(right_fx, abs=1.0)
