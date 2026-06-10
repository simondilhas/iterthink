"""Tests for Review-tab plan PDF compare wiring."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from iterthink.studio.constants import TAB_FUTURE, TAB_HISTORY
from iterthink.studio.formats import pdf_docx as pdf_docx_mod
from iterthink.studio.formats.pdf_docx import MarkdownStudioAssetCompare


@dataclass
class _FakePlanPanel:
    overlay_list: ft.ListView = field(default_factory=ft.ListView)
    baseline_dd: ft.Dropdown = field(default_factory=ft.Dropdown)
    candidate_dd: ft.Dropdown = field(default_factory=ft.Dropdown)
    baseline_wrap: ft.Container = field(default_factory=ft.Container)
    candidate_wrap: ft.Container = field(default_factory=ft.Container)
    host: ft.Container = field(default_factory=ft.Container)

    def set_bar_visible(self, visible: bool) -> None:
        self.host.visible = visible


class _PlanOverlayStub(MarkdownStudioAssetCompare):
    """Minimal host for overlay visibility and baseline routing tests."""

    def __init__(self) -> None:
        self.current_path = None
        self._main_tab_index = TAB_FUTURE
        self._plan_overlay_mode = False
        self._plan_side_by_side_mode = False
        self._plan_layout_mode = "overlay"
        self._compare_candidate_source = "pdf_original"
        self._plan_compare = _FakePlanPanel()
        self._plan_compare_future = _FakePlanPanel()
        self._compare_pdf_left_lv = ft.ListView(visible=True)
        self._compare_pdf_right_lv = ft.ListView(visible=True)
        self._compare_pdf_right_column = ft.Container(visible=True)
        self._compare_pdf_split_row = ft.Row(visible=True)
        self._compare_pdf_overlay_host = ft.Container(visible=False)
        self._future_plan_focus_left_slot = ft.Container(visible=True)
        self._future_plan_focus_right_slot = ft.Container(visible=True)
        self._future_plan_side_by_side_slot = ft.Container(visible=True)
        self._future_plan_single_slot = ft.Container(visible=True)
        self._future_plan_single_host = ft.Container(visible=False)
        self._future_pdf_split_row = ft.Container(visible=True)
        self._future_plan_overlay_host = ft.Container(visible=False)
        self._future_plan_overlay_focus_slot = ft.Container(visible=False)
        self._future_pdf_layer = ft.Container(visible=False)
        self._future_paragraph_layer = ft.Container(visible=True)
        self._review_difference_chrome_row = ft.Container(visible=True)
        self._review_subtab_index = 0
        self._plan_overlay_gen = 0
        self._version_count = 1
        self.layout_mode_calls: list[str] = []
        self.snacks: list[str] = []
        self.future_rebuild_calls = 0
        self.history_rebuild_calls = 0

    def _plan_pdf_version_count(self) -> int:
        return self._version_count

    def _active_plan_compare_panel(self) -> _FakePlanPanel:
        if self._main_tab_index == TAB_FUTURE:
            return self._plan_compare_future
        return self._plan_compare

    def _plan_compare_panels(self) -> list[_FakePlanPanel]:
        return [self._plan_compare, self._plan_compare_future]

    def _document_pdf_profile(self) -> str | None:
        return "plan"

    def _set_plan_layout_mode(self, mode: str, *, rebuild: bool = True) -> None:
        self.layout_mode_calls.append(mode)
        if self._plan_pdf_version_count() < 2:
            mode = "single"
        self._plan_layout_mode = mode
        self._sync_plan_overlay_pane_visibility()

    def _snack(self, msg: str) -> None:
        self.snacks.append(msg)

    def _mount_plan_focus_viewer(self, *args: object, **kwargs: object) -> None:
        pass

    def _sync_plan_focus_viewport_from_active_host(self, *, future: bool) -> None:
        pass

    def _review_plan_change_regions_enabled(self) -> bool:
        return False

    async def _sync_review_change_regions_async(self, *, snack_on_detect: bool = False) -> None:
        pass

    async def _rebuild_future_plan_pdf_panes_async(self) -> None:
        self.future_rebuild_calls += 1

    async def _rebuild_compare_plan_pdf_panes_async(self) -> None:
        self.history_rebuild_calls += 1

    async def _refresh_plan_overlay_async(self) -> None:
        pass


def test_sync_future_pdf_layers_hides_text_review_chrome_for_plan_pdf() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._review_subtab_index = 0
    stub._compare_candidate_source = "pdf_original"

    stub._sync_future_pdf_layers_visibility()

    assert stub._future_pdf_layer.visible is True
    assert stub._review_difference_chrome_row.visible is False


def test_sync_future_pdf_layers_shows_text_review_chrome_for_markdown() -> None:
    stub = _PlanOverlayStub()
    stub._main_tab_index = TAB_FUTURE
    stub._review_subtab_index = 0
    stub._compare_candidate_source = "ai_preview"

    stub._sync_future_pdf_layers_visibility()

    assert stub._future_pdf_layer.visible is False
    assert stub._review_difference_chrome_row.visible is True


def test_weak_overlay_confidence_does_not_switch_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _PlanOverlayStub()
    stub.current_path = tmp_path / "doc.md"
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_overlay_mode = True
    stub._plan_layout_mode = "overlay"
    stub._plan_compare_future.baseline_dd.value = "1"
    stub._plan_compare_future.candidate_dd.value = "2"
    overlay_png = tmp_path / "overlay.png"
    overlay_png.write_bytes(b"x")

    class _FakeSession:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(pdf_docx_mod, "session_scope", lambda: _FakeSession())
    monkeypatch.setattr(
        pdf_docx_mod.content_repo,
        "get_version_pdf_relpath",
        lambda _s, vid: f"v{vid}.pdf",
    )
    monkeypatch.setattr(
        pdf_docx_mod.content_repo,
        "pdf_asset_abs_path",
        lambda rel: tmp_path / str(rel),
    )
    monkeypatch.setattr(
        pdf_docx_mod,
        "diff_pdfs_to_overlay_paths",
        lambda _a, _b, pdf_profile="plan": ([overlay_png], None, [0.1]),
    )
    monkeypatch.setattr(
        pdf_docx_mod.plan_picture_viewer,
        "build_plan_compare_focus_viewer",
        MagicMock(),
    )
    monkeypatch.setattr(pdf_docx_mod, "_ctrl_on_page", lambda _c: False)

    asyncio.run(MarkdownStudioAssetCompare._refresh_plan_overlay_async(stub))

    assert stub._plan_layout_mode == "overlay"
    assert stub.layout_mode_calls == []
    assert any("Weak alignment" in s for s in stub.snacks)


def test_on_future_plan_overlay_host_size_syncs_viewport(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _PlanOverlayStub()
    sync_calls: list[tuple[float, float, bool]] = []

    def _sync(w: float, h: float, *, future: bool) -> None:
        sync_calls.append((w, h, future))

    monkeypatch.setattr(stub, "_sync_plan_compare_focus_viewport", _sync)
    ev = MagicMock()
    ev.width = 800.0
    ev.height = 600.0
    stub._on_future_plan_overlay_host_size(ev)
    assert sync_calls == [(800.0, 600.0, True)]


def test_sync_plan_overlay_single_version_review_shows_single_host() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 1
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "overlay"

    stub._sync_plan_overlay_pane_visibility()

    assert stub._plan_layout_mode == "single"
    assert stub._future_pdf_split_row.visible is False
    assert stub._future_plan_single_host.visible is True
    assert stub._future_plan_overlay_host.visible is False
    assert stub._plan_overlay_mode is False


def test_sync_plan_overlay_multi_version_overlay_mode_review() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "overlay"

    stub._sync_plan_overlay_pane_visibility()

    assert stub._plan_overlay_mode is True
    assert stub._plan_side_by_side_mode is False
    assert stub._future_plan_overlay_host.visible is True


def test_sync_plan_overlay_multi_version_single_pane() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "single"

    stub._sync_plan_overlay_pane_visibility()

    assert stub._plan_overlay_mode is False
    assert stub._plan_side_by_side_mode is False
    assert stub._future_pdf_split_row.visible is False
    assert stub._future_plan_single_host.visible is True
    assert stub._future_plan_overlay_host.visible is False


def test_sync_plan_overlay_multi_version_side_by_side_review() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "side_by_side"

    stub._sync_plan_overlay_pane_visibility()

    assert stub._plan_side_by_side_mode is True
    assert stub._plan_overlay_mode is False
    assert stub._future_pdf_split_row.visible is True
    assert stub._future_plan_single_host.visible is False
    assert stub._future_plan_overlay_host.visible is False


def test_set_plan_layout_mode_forces_single_with_one_version() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 1
    stub._set_plan_layout_mode("side_by_side", rebuild=False)

    assert stub._plan_layout_mode == "single"
    assert stub._plan_side_by_side_mode is False


def test_review_plan_annotations_enabled_single_and_side_by_side() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE

    stub._plan_layout_mode = "single"
    assert stub._review_plan_annotations_enabled() is True

    stub._plan_layout_mode = "side_by_side"
    assert stub._review_plan_annotations_enabled() is True


def test_review_plan_annotatable_viewer_single_uses_single_host() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "single"
    single_viewer = object()
    stub._future_plan_focus_single = single_viewer  # type: ignore[assignment]
    stub._future_plan_focus_left = object()  # type: ignore[assignment]

    assert stub._review_plan_annotatable_viewer() is single_viewer


def test_review_plan_annotations_disabled_in_overlay() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "overlay"

    assert stub._review_plan_annotations_enabled() is False


def test_review_plan_comment_viewer_overlay() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "overlay"
    stub._sync_plan_overlay_pane_visibility()
    overlay_viewer = object()
    stub._future_plan_overlay_focus = overlay_viewer  # type: ignore[assignment]

    assert stub._review_plan_comment_placement_enabled() is True
    assert stub._review_plan_comment_viewer() is overlay_viewer


def test_review_plan_comment_viewer_side_by_side() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "side_by_side"
    stub._sync_plan_overlay_pane_visibility()
    right_viewer = object()
    stub._future_plan_focus_right = right_viewer  # type: ignore[assignment]

    assert stub._review_plan_comment_viewer() is right_viewer


def test_review_plan_annotations_disabled_on_history() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_HISTORY
    stub._plan_layout_mode = "single"

    assert stub._review_plan_annotations_enabled() is False


class _RecordingPage:
    def __init__(self) -> None:
        self.tasks: list[tuple[object, tuple[object, ...]]] = []

    def run_task(self, coro_fn: object, *args: object) -> None:
        self.tasks.append((coro_fn, args))



def test_snapshot_plan_overlay_paths_copies_to_viewer_local_dir(tmp_path: Path) -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "overlay"
    stub._sync_plan_overlay_pane_visibility()

    assert stub._plan_overlay_mode is True
    assert MarkdownStudioAssetCompare._review_plan_change_regions_enabled(stub) is False


def test_review_plan_change_regions_disabled_in_single_mode() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "single"
    stub._sync_plan_overlay_pane_visibility()

    assert stub._review_plan_single_mode() is True
    assert MarkdownStudioAssetCompare._review_plan_change_regions_enabled(stub) is False


def test_review_plan_change_regions_disabled_in_side_by_side() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "side_by_side"
    stub._sync_plan_overlay_pane_visibility()

    assert stub._plan_side_by_side_mode is True
    assert MarkdownStudioAssetCompare._review_plan_change_regions_enabled(stub) is False


def test_snapshot_plan_overlay_paths_copies_to_viewer_local_dir(tmp_path: Path) -> None:
    src_dir = tmp_path / "cache"
    src_dir.mkdir()
    p1 = src_dir / "overlay_0001.png"
    p2 = src_dir / "overlay_0002.png"
    p1.write_bytes(b"page1")
    p2.write_bytes(b"page2")

    stable = MarkdownStudioAssetCompare._snapshot_plan_overlay_paths([p1, p2], mount_key=7)

    assert len(stable) == 2
    assert stable[0].parent.name == "viewer_7"
    assert stable[0].read_bytes() == b"page1"
    assert stable[1].read_bytes() == b"page2"
    assert stable[0].is_file() and stable[1].is_file()


def test_snapshot_plan_page_paths_supports_label(tmp_path: Path) -> None:
    src_dir = tmp_path / "cache"
    src_dir.mkdir()
    p1 = src_dir / "page_0001.png"
    p1.write_bytes(b"page1")

    stable = MarkdownStudioAssetCompare._snapshot_plan_page_paths(
        [p1], mount_key=3, label="cand"
    )

    assert stable[0].parent.name == "viewer_3_cand"
    assert stable[0].name == "page_0001.png"


@pytest.mark.asyncio
async def test_change_regions_side_by_side_only_on_candidate_viewer() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from iterthink.services.plan_change_regions import PlanChangeRegionView

    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "side_by_side"
    stub._sync_plan_overlay_pane_visibility()

    left = MagicMock()
    right = MagicMock()
    left.ensure_viewport_sync = AsyncMock()
    right.ensure_viewport_sync = AsyncMock()
    left.root = MagicMock()
    right.root = MagicMock()
    stub._future_plan_focus_left = left  # type: ignore[assignment]
    stub._future_plan_focus_right = right  # type: ignore[assignment]
    stub._review_plan_change_regions_enabled = lambda: True  # type: ignore[method-assign]

    views = [
        PlanChangeRegionView(
            region_id=1,
            page_index=0,
            norm_bbox=(0.1, 0.1, 0.5, 0.5),
            paragraph_index=1,
            body="change",
            pixel_count=500,
            text_change_ids=(),
            dismissed=False,
            reviewed=False,
            region_key="abc",
        )
    ]

    await MarkdownStudioAssetCompare._apply_change_regions_to_all_review_viewers_async(
        stub, views
    )

    left.set_change_regions.assert_called_once_with([])
    right.set_change_regions.assert_called_once()
    assert right.set_change_regions.call_args[0][0] == views


@pytest.mark.asyncio
async def test_rebuild_side_by_side_skips_upfront_clear_plan_focus_context() -> None:
    from unittest.mock import AsyncMock

    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "side_by_side"
    stub._sync_plan_overlay_pane_visibility()
    clear_calls: list[dict[str, bool]] = []

    def _track_clear(**kwargs: bool) -> None:
        clear_calls.append(kwargs)

    stub._clear_plan_focus_context = _track_clear  # type: ignore[method-assign]
    stub._mount_plan_focus_side_by_side_async = AsyncMock()  # type: ignore[method-assign]
    stub._sync_review_change_regions_async = AsyncMock()  # type: ignore[method-assign]

    await MarkdownStudioAssetCompare._rebuild_future_plan_pdf_panes_async(stub)

    assert clear_calls == []
    stub._mount_plan_focus_side_by_side_async.assert_awaited_once()


def test_baseline_dropdown_routes_to_future_rebuild_on_review() -> None:
    stub = _PlanOverlayStub()
    stub._main_tab_index = TAB_FUTURE
    stub._plan_overlay_mode = False

    asyncio.run(stub._on_plan_pdf_baseline_async())

    assert stub.future_rebuild_calls == 1
    assert stub.history_rebuild_calls == 0


def test_baseline_dropdown_routes_to_history_rebuild_on_history() -> None:
    stub = _PlanOverlayStub()
    stub._main_tab_index = TAB_HISTORY
    stub._plan_overlay_mode = False

    asyncio.run(stub._on_plan_pdf_baseline_async())

    assert stub.future_rebuild_calls == 0
    assert stub.history_rebuild_calls == 1


def test_is_plan_pdf_compare_true_for_plan_document_profile() -> None:
    stub = _PlanOverlayStub()
    stub._compare_candidate_source = "ai_preview"
    stub._document_pdf_profile = lambda: "plan"  # type: ignore[method-assign]

    assert stub._is_plan_pdf_compare() is True


def test_is_plan_pdf_compare_false_for_non_plan_ai_preview() -> None:
    stub = _PlanOverlayStub()
    stub._compare_candidate_source = "ai_preview"
    stub._document_pdf_profile = lambda: None  # type: ignore[method-assign]
    stub._compare_editor = ft.TextField(value="plain markdown")

    assert stub._is_plan_pdf_compare() is False


def test_ensure_plan_pdf_compare_active_switches_ai_preview_to_pdf_original() -> None:
    stub = _PlanOverlayStub()
    stub.current_path = None  # type: ignore[attr-defined]
    stub._compare_candidate_source = "ai_preview"
    stub._document_pdf_profile = lambda: "plan"  # type: ignore[method-assign]
    stub._compare_snapshot_version_id = None
    stub._compare_pdf_peer_snapshot_id = None
    stub._compare_editor = ft.TextField(value="<!-- pdf_profile:plan -->")
    stub._apply_plan_import_open_state = lambda: None  # type: ignore[method-assign]

    class _FakeSession:
        def __enter__(self):
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    stub.latest_pdf = (42, "store/pdf/v.pdf")

    def _latest(_s: object, _p: object) -> tuple[int, str]:
        return stub.latest_pdf

    def _load_body(_s: object, _vid: int) -> str:
        return "<!-- pdf_profile:plan -->"

    import iterthink.studio.formats.pdf_docx as pdf_docx_mod

    orig_scope = pdf_docx_mod.session_scope
    orig_latest = pdf_docx_mod.content_repo.latest_pdf_version_for_document
    orig_load = pdf_docx_mod.content_repo.load_version_body
    pdf_docx_mod.session_scope = lambda: _FakeSession()  # type: ignore[assignment]
    pdf_docx_mod.content_repo.latest_pdf_version_for_document = _latest
    pdf_docx_mod.content_repo.load_version_body = _load_body
    try:
        from pathlib import Path

        stub.current_path = Path("/tmp/plan.md")  # type: ignore[attr-defined]
        assert stub._ensure_plan_pdf_compare_active() is True
        assert stub._compare_candidate_source == "pdf_original"
        assert stub._compare_snapshot_version_id == 42
        assert stub._compare_pdf_peer_snapshot_id == 42
    finally:
        pdf_docx_mod.session_scope = orig_scope
        pdf_docx_mod.content_repo.latest_pdf_version_for_document = orig_latest
        pdf_docx_mod.content_repo.load_version_body = orig_load


def test_plan_version_import_open_state_uses_single_mode() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._apply_plan_import_open_state(version_import=True)
    assert stub._plan_layout_mode == "single"
    assert stub._plan_overlay_mode is False
    assert stub._future_plan_single_host.visible is True


def test_plan_first_import_open_state_uses_overlay_mode() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 1
    stub._apply_plan_import_open_state(version_import=False)
    assert stub._plan_overlay_defaults_set is True
    assert stub._plan_layout_mode == "single"  # one version → single pane until a second exists


def test_plan_second_version_import_routes_to_review_tab() -> None:
    from pathlib import Path

    src = Path("iterthink/studio/explorer.py").read_text(encoding="utf-8")
    marker = "self._apply_plan_import_open_state(version_import=import_into_existing)"
    start = src.index(marker)
    block = src[start : start + 600]
    assert "if import_into_existing:" in block
    assert "TAB_FUTURE" in block
    assert "_rebuild_future_plan_pdf_panes_async" in block
    assert "TAB_HISTORY" not in block


def test_detach_pdf_import_ui_teardowns_compose_plan_viewer() -> None:
    stub = _PlanOverlayStub()
    stub._compose_plan_load_gen = 3
    stub._compose_plan_focus_viewer = object()
    stub._compose_plan_host = ft.Container(content=ft.Text("plan"), visible=True)
    stub._compose_plan_surface_key = (1, True, "/tmp/plan.pdf")
    stub._compose_plan_load_inflight_key = (1, True, "/tmp/plan.pdf")

    stub._cancel_and_teardown_compose_plan_viewer()

    assert stub._compose_plan_focus_viewer is None
    assert stub._compose_plan_host.content is None
    assert stub._compose_plan_host.visible is False
    assert stub._compose_plan_surface_key is None
    assert stub._compose_plan_load_inflight_key is None
    assert stub._compose_plan_load_gen == 4


def test_review_plan_comment_nav_host_side_by_side_uses_pair() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "side_by_side"
    stub._sync_plan_overlay_pane_visibility()
    pair = MagicMock()
    stub._future_plan_side_by_side_pair = pair  # type: ignore[assignment]

    assert stub._review_plan_comment_nav_host() is pair


def test_active_plan_focus_viewers_review_side_by_side_pair() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "side_by_side"
    stub._sync_plan_overlay_pane_visibility()
    left = MagicMock()
    right = MagicMock()
    pair = MagicMock()
    pair.left = left
    pair.right = right
    stub._future_plan_side_by_side_pair = pair  # type: ignore[assignment]

    viewers = stub._active_plan_focus_viewers(future=True)
    assert viewers == [(left, 2), (right, 2)]


def test_sync_plan_review_comment_nav_btn_attaches_to_pair() -> None:
    stub = _PlanOverlayStub()
    stub._version_count = 2
    stub._main_tab_index = TAB_FUTURE
    stub._plan_layout_mode = "side_by_side"
    stub._sync_plan_overlay_pane_visibility()
    btn = ft.IconButton(icon=ft.Icons.CHAT_BUBBLE_OUTLINE)
    stub._plan_review_comment_btn = btn
    pair = MagicMock()
    stub._future_plan_side_by_side_pair = pair  # type: ignore[assignment]

    stub._sync_plan_review_comment_nav_btn()

    pair.set_nav_trailing.assert_called_once_with([btn])
    assert stub._plan_comment_nav_host is pair


def test_snapshot_plan_page_paths_start_index(tmp_path: Path) -> None:
    src_dir = tmp_path / "cache"
    src_dir.mkdir()
    mount_dir = src_dir / "viewer_1_base"
    mount_dir.mkdir()
    p2 = src_dir / "page_0002.png"
    p2.write_bytes(b"page2")

    stable = MarkdownStudioAssetCompare._snapshot_plan_page_paths(
        [p2],
        mount_key=1,
        mount_dir=mount_dir,
        start_index=1,
    )

    assert stable[0].parent == mount_dir
    assert stable[0].name == "page_0002.png"
    assert stable[0].read_bytes() == b"page2"


def test_plan_first_page_load_blocking_uses_max_pages_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf_abs = tmp_path / "plan.pdf"
    pdf_abs.write_bytes(b"%PDF")
    seen: list[int | None] = []

    def fake_render(
        path: Path,
        *,
        pdf_profile: str = "plan",
        max_pages: int | None = None,
        on_page_rendered: object = None,
    ) -> list[Path]:
        del path, pdf_profile, on_page_rendered
        seen.append(max_pages)
        out = tmp_path / "page_0001.png"
        out.write_bytes(b"p1")
        return [out]

    monkeypatch.setattr(pdf_docx_mod.document_import, "count_pdf_pages", lambda _p: 5)
    monkeypatch.setattr(pdf_docx_mod.document_import, "render_pdf_to_png_pages", fake_render)

    pages, total = MarkdownStudioAssetCompare._plan_first_page_load_blocking(pdf_abs)

    assert seen == [1]
    assert total == 5
    assert len(pages) == 1


@pytest.mark.asyncio
async def test_plan_first_page_load_parallel(tmp_path: Path) -> None:
    stub = _PlanOverlayStub()
    started: list[int] = []

    async def fake_load(resolved: tuple[int, str] | None) -> pdf_docx_mod._PlanPageLoad:
        started.append(int(resolved[0]) if resolved is not None else 0)
        await asyncio.sleep(0.05)
        page = tmp_path / f"page_{resolved[0] if resolved else 0}.png"
        page.write_bytes(b"p")
        return pdf_docx_mod._PlanPageLoad(
            [page],
            3,
            None,
            tmp_path / f"doc_{resolved[0] if resolved else 0}.pdf",
        )

    stub._plan_first_page_load_async = fake_load  # type: ignore[method-assign]

    t0 = asyncio.get_running_loop().time()
    base_load, cand_load = await asyncio.gather(
        stub._plan_first_page_load_async((1, "base.pdf")),
        stub._plan_first_page_load_async((2, "cand.pdf")),
    )
    elapsed = asyncio.get_running_loop().time() - t0

    assert started == [1, 2]
    assert base_load.page_total == 3
    assert cand_load.page_total == 3
    assert elapsed < 0.09


@pytest.mark.asyncio
async def test_side_by_side_mount_schedules_finish(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    stub = _PlanOverlayStub()
    stub._compare_plan_focus_left_slot = ft.Container()
    stub._compare_plan_focus_right_slot = ft.Container()
    scheduled: list[dict[str, object]] = []

    def _track_schedule(**kwargs: object) -> None:
        scheduled.append(kwargs)

    stub._schedule_side_by_side_plan_finish = _track_schedule  # type: ignore[method-assign]
    stub._plan_compare_label_options = lambda *a, **k: ([], False, False)  # type: ignore[method-assign]
    stub._mount_plan_focus_viewer = MagicMock()  # type: ignore[method-assign]
    stub._plan_viewer_mount_gen = 4

    async def fake_load(resolved: tuple[int, str] | None) -> pdf_docx_mod._PlanPageLoad:
        vid = int(resolved[0]) if resolved is not None else 0
        page = tmp_path / f"page_{vid}.png"
        page.write_bytes(b"p")
        return pdf_docx_mod._PlanPageLoad(
            [page],
            5,
            None,
            tmp_path / f"doc_{vid}.pdf",
        )

    stub._plan_first_page_load_async = fake_load  # type: ignore[method-assign]

    await MarkdownStudioAssetCompare._mount_plan_focus_side_by_side_async(
        stub,
        future=False,
        base=(1, "base.pdf"),
        cand=(2, "cand.pdf"),
    )

    assert len(scheduled) == 1
    assert scheduled[0]["mount_gen"] == 4
    assert scheduled[0]["base_rendered"] == 1
    assert scheduled[0]["cand_rendered"] == 1


@pytest.mark.asyncio
async def test_side_by_side_finish_appends_remaining_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    stub = _PlanOverlayStub()
    stub._plan_side_by_side_mode = True
    stub._plan_viewer_mount_gen = 1

    left = MagicMock()
    left.append_rendered_pages = MagicMock()
    left.set_expected_page_count = MagicMock()
    stub._compare_plan_focus_left = left  # type: ignore[assignment]

    mount_dir = tmp_path / "viewer_1_base"
    mount_dir.mkdir()
    (mount_dir / "page_0001.png").write_bytes(b"p1")
    cache_pages = []
    for i in range(1, 6):
        p = tmp_path / f"cache_{i:04d}.png"
        p.write_bytes(f"p{i}".encode())
        cache_pages.append(p)

    monkeypatch.setattr(
        pdf_docx_mod.document_import,
        "render_pdf_to_png_pages",
        lambda *_a, **_k: cache_pages,
    )

    await MarkdownStudioAssetCompare._finish_side_by_side_plan_pages_async(
        stub,
        1,
        False,
        tmp_path / "base.pdf",
        None,
        mount_dir,
        None,
        1,
        0,
        5,
        0,
    )

    left.append_rendered_pages.assert_called_once()
    appended = left.append_rendered_pages.call_args[0][0]
    assert len(appended) == 4
    assert appended[0].name == "page_0002.png"
    left.set_expected_page_count.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_side_by_side_finish_stale_gen_skips_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    stub = _PlanOverlayStub()
    stub._plan_side_by_side_mode = True
    stub._plan_viewer_mount_gen = 2

    left = MagicMock()
    left.append_rendered_pages = MagicMock()
    stub._compare_plan_focus_left = left  # type: ignore[assignment]

    mount_dir = tmp_path / "viewer_1_base"
    mount_dir.mkdir()

    monkeypatch.setattr(
        pdf_docx_mod.document_import,
        "render_pdf_to_png_pages",
        lambda *_a, **_k: [tmp_path / "cache_0002.png"],
    )

    await MarkdownStudioAssetCompare._finish_side_by_side_plan_pages_async(
        stub,
        1,
        False,
        tmp_path / "base.pdf",
        None,
        mount_dir,
        None,
        1,
        0,
        5,
        0,
    )

    left.append_rendered_pages.assert_not_called()
