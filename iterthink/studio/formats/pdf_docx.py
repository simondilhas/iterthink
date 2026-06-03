"""PDF/plan overlay and DOCX preview wiring for MarkdownStudio (mixin)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft

from iterthink import config
from iterthink.persistence import content_repo, plan_pdf_annotations
from iterthink.services import document_import
from iterthink.services.plan_pdf_export import export_annotated_pdf
from iterthink.services.plan_text_extract import (
    load_plan_text_sidecar,
    page_pngs_with_text_overlay,
    write_plan_text_sidecar,
)
from ..history.candidate_state import CompareCandidateSource
from iterthink.tools.pdf_visual_diff import diff_pdfs_to_overlay_paths

from .. import plan_compare_panel, plan_picture_viewer, ui_theme
from iterthink.db.session import session_scope
from ..constants import (
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPOSE_READING_WIDTH_FRAC,
    READING_MAX_PX,
    TAB_FUTURE,
    TAB_HISTORY,
)
from ..util import ctrl_on_page as _ctrl_on_page
from ..util import safe_list_scroll as _safe_list_scroll

_PDF_COMPARE_SCROLL_SOURCES = ("pdf_original", "docx_original")


class MarkdownStudioAssetCompare:
    def _compose_plan_viewer_active(self) -> bool:
        host = getattr(self, "_compose_plan_host", None)
        return host is not None and bool(host.visible)

    def _compose_column_avail_width(self) -> float:
        row = getattr(self, "_compose_centered_row", None)
        if row is not None and float(row.width or 0) > 0:
            return max(200.0, float(row.width))
        wrap = getattr(self, "_compose_reading_wrap", None)
        if wrap is not None and float(wrap.width or 0) > 0:
            return max(200.0, float(wrap.width))
        return 0.0

    def _sync_compose_plan_viewport_width(self, avail: float) -> None:
        viewer = getattr(self, "_compose_plan_focus_viewer", None)
        if viewer is None or not self._compose_plan_viewer_active():
            return
        min_h = plan_picture_viewer._FOCUS_MIN_VIEWPORT_H
        vh = float(getattr(viewer, "_viewport_h", 0) or 0)
        if vh < min_h:
            frame = getattr(viewer, "_page_frame", None)
            if frame is not None and float(frame.height or 0) >= min_h:
                vh = float(frame.height)
        viewer.sync_viewport(avail, vh if vh >= min_h else None)

    def _apply_compose_plan_layout_mode(self) -> None:
        """Plan PDF: stretch reading column edge-to-edge; markdown: centered reading width."""
        plan_on = self._compose_plan_viewer_active()
        wrap = getattr(self, "_compose_reading_wrap", None)
        card = self._compose_reading_card
        inner = getattr(self, "_compose_reading_inner", None)
        if plan_on:
            card.width = None
            card.expand = True
            if inner is not None:
                inner.expand = True
        else:
            card.expand = False
            if wrap is not None:
                wrap.alignment = ft.Alignment.TOP_CENTER
                wrap.content = card
        for c in (wrap, card, inner):
            if c is not None and _ctrl_on_page(c):
                c.update()

    def _apply_compose_reading_card_width(self, avail: float) -> None:
        """Size the reading card; plan viewer stretches to full compose column width."""
        plan_on = self._compose_plan_viewer_active()
        card = self._compose_reading_card
        if plan_on:
            self._apply_compose_plan_layout_mode()
            self._sync_compose_plan_viewport_width(avail)
            return
        reading_w = int(
            min(float(READING_MAX_PX), max(240.0, avail * COMPOSE_READING_WIDTH_FRAC))
        )
        cur = int(card.width or 0)
        if cur == reading_w and not card.expand:
            return
        card.width = reading_w
        card.expand = False
        if _ctrl_on_page(card):
            card.update()

    def _apply_compose_plan_tab_scroll(self) -> None:
        """Disable outer compose scroll while plan viewer or preview owns vertical scroll."""
        stack = getattr(self, "_compose_tab_body_stack", None)
        if stack is None:
            return
        col = stack.content
        if not isinstance(col, ft.Column):
            return
        preview_on = getattr(self, "_focus_view_mode", "edit") == "preview"
        want = (
            None
            if self._compose_plan_viewer_active() or preview_on
            else ft.ScrollMode.AUTO
        )
        if col.scroll != want:
            col.scroll = want
            if _ctrl_on_page(col):
                col.update()

    def _restore_compose_reading_width_after_plan(self) -> None:
        wrap = getattr(self, "_compose_reading_wrap", None)
        if wrap is not None and float(wrap.width or 0) > 0:
            self._apply_compose_reading_card_width(float(wrap.width))
        if hasattr(self, "_rebuild_content_tree"):
            self._rebuild_content_tree()

    def _document_pdf_profile(self) -> str | None:
        if not self.current_path:
            return None
        with session_scope() as s:
            det = content_repo.latest_pdf_version_detail(s, self.current_path.resolve())
        if det is not None:
            prof = (det[2] or "").strip()
            if prof:
                return prof
        from iterthink.services.plan_text_extract import is_plan_stub_markdown

        if is_plan_stub_markdown(self.editor.value or ""):
            return "plan"
        return None

    def _document_ui_suffix(self) -> str:
        """UI label suffix; on-disk note files stay ``.md`` for PDF imports."""
        if self._document_pdf_profile() in ("plan", "text"):
            return ".pdf"
        if self.current_path:
            return self.current_path.suffix or ""
        return ""

    @staticmethod
    def _tree_suffix_for_path(fpath: Path) -> str:
        with session_scope() as s:
            det = content_repo.latest_pdf_version_detail(s, fpath.resolve())
        return ".pdf" if det is not None else ".md"

    def _tree_display_name(self, fpath: Path) -> str:
        return f"{fpath.stem}{self._tree_suffix_for_path(fpath)}"

    def _pdf_profile_for_version(self, vid: int | None) -> str | None:
        if vid is None:
            return None
        try:
            with session_scope() as s:
                return content_repo.get_version_pdf_profile(s, int(vid))
        except BaseException:
            return None

    def _is_plan_pdf_compare(self) -> bool:
        if self._compare_candidate_source != "pdf_original":
            return False
        vid_prof = self._pdf_profile_for_version(self._compare_snapshot_version_id)
        if vid_prof == "plan":
            return True
        if self._document_pdf_profile() == "plan":
            return True
        from iterthink.services.plan_text_extract import is_plan_stub_markdown

        return is_plan_stub_markdown(self._compare_editor.value or "")

    def _plan_pdf_version_count(self) -> int:
        if not self.current_path:
            return 0
        with session_scope() as s:
            pairs = content_repo.list_plan_pdf_version_options(s, self.current_path.resolve())
        return len(pairs)

    def _apply_plan_import_open_state(self) -> None:
        """Focus + compare defaults after importing or selecting a plan PDF."""
        self._compose_plan_editor_collapsed = True
        self._compose_plan_show_labels = False
        for pc in self._plan_compare_panels():
            pc.overlay_switch.value = False
            pc.side_by_side_switch.value = False
        self._plan_overlay_defaults_set = True
        self._sync_plan_overlay_pane_visibility()

    @staticmethod
    def _plan_display_pages_blocking(
        pdf_abs: Path,
        doc_path: Path,
        version_id: int,
        *,
        show_labels: bool,
    ) -> list[Path]:
        pages = document_import.render_pdf_to_png_pages(pdf_abs, pdf_profile="plan")
        if not show_labels:
            return pages
        geometry = load_plan_text_sidecar(doc_path, version_id) or {"pages": []}
        if geometry.get("pages"):
            return page_pngs_with_text_overlay(pages, geometry, show_labels=True)
        return pages

    def _plan_compare_panels(self) -> list:
        panels = [self._plan_compare]
        fut = getattr(self, "_plan_compare_future", None)
        if fut is not None:
            panels.append(fut)
        return panels

    def _fill_all_plan_compare_dropdowns(self, opts: list[tuple[str, str]]) -> None:
        st = ui_theme.compare_candidate_dropdown_option_style()
        for pc in self._plan_compare_panels():
            plan_compare_panel.fill_pdf_dropdowns(
                pc.baseline_dd,
                pc.candidate_dd,
                opts,
                option_button_style=st,
            )

    def _apply_compose_plan_editor_layout(self) -> None:
        if getattr(self, "_focus_view_mode", "edit") == "preview":
            return
        writing_slot = getattr(self, "_compose_writing_slot", None)
        collapsed = bool(getattr(self, "_compose_plan_editor_collapsed", False))
        if not self._compose_plan_host.visible:
            if writing_slot is not None:
                writing_slot.content = self._compose_editor_shell_wrapped
            self._compose_editor_shell_wrapped.visible = True
            self._compose_editor_shell_wrapped.expand = True
            self._compose_editor_shell_wrapped.height = None
            if writing_slot is not None and _ctrl_on_page(writing_slot):
                writing_slot.update()
            return
        self._compose_plan_host.expand = True
        if writing_slot is not None:
            writing_slot.content = self._compose_editor_shell_wrapped
        if collapsed:
            self._compose_editor_shell_wrapped.visible = False
            self._compose_editor_shell_wrapped.height = 0
        else:
            self._compose_editor_shell_wrapped.visible = True
            self._compose_editor_shell_wrapped.expand = False
            self._compose_editor_shell_wrapped.height = 220
        if writing_slot is not None and _ctrl_on_page(writing_slot):
            writing_slot.update()

    def _release_pdf_compare_disk_refs(self) -> None:
        """Drop rendered PDF page controls (``Image`` src may reference store/cache paths)."""
        for name in (
            "_compare_pdf_left_lv",
            "_compare_pdf_right_lv",
            "_future_pdf_left_lv",
            "_future_pdf_right_lv",
        ):
            lv = getattr(self, name, None)
            if lv is not None and isinstance(lv, ft.ListView):
                lv.controls.clear()
                if _ctrl_on_page(lv):
                    lv.update()
        for pc in self._plan_compare_panels():
            ov = getattr(pc, "overlay_list", None)
            if ov is not None and isinstance(ov, ft.ListView):
                ov.controls.clear()
                if _ctrl_on_page(ov):
                    ov.update()
        fut_ov = getattr(self, "_future_plan_overlay_list", None)
        if fut_ov is not None and isinstance(fut_ov, ft.ListView):
            fut_ov.controls.clear()
            if _ctrl_on_page(fut_ov):
                fut_ov.update()

    def _detach_pdf_import_ui_for_store_delete(self) -> None:
        """Release viewers before ``purge_document_store_dirs`` removes PDF assets under STORE."""
        self._compare_pdf_peer_snapshot_id = None
        if hasattr(self, "_pending_post_import_history_vid"):
            self._pending_post_import_history_vid = None
        self._compare_candidate_source = "draft"
        self._release_pdf_compare_disk_refs()
        self._sync_compare_pdf_layers_visibility()
        self._sync_future_pdf_layers_visibility()

    def _on_future_pdf_import_md_change(self, _e: ft.ControlEvent) -> None:
        v = self._future_pdf_import_md_tf.value or ""
        if (self.editor.value or "") == v and (self._compare_editor.value or "") == v:
            return
        self.editor.value = v
        self._editor_prev_for_list_continue = v
        self._compare_editor.value = v
        self._refresh_title_bar()
        self._kick_debounced_autosave()
        if _ctrl_on_page(self.editor):
            self.editor.update()

    def _pdf_compare_paired_scroll_active(self) -> bool:
        """Cross-pane sync only when two scrollable columns are shown."""
        if getattr(self, "_plan_overlay_mode", False) and not getattr(
            self, "_plan_side_by_side_mode", False
        ):
            return False
        if self._main_tab_index == TAB_HISTORY:
            split = getattr(self, "_compare_pdf_split_row", None)
            return split is not None and bool(split.visible)
        if self._main_tab_index == TAB_FUTURE:
            split = getattr(self, "_future_pdf_split_row", None)
            return split is not None and bool(split.visible)
        return True

    def _scroll_sync_left_listview(self) -> ft.ListView | None:
        if not self._pdf_compare_paired_scroll_active():
            return None
        if self._main_tab_index == TAB_FUTURE:
            lv = getattr(self, "_future_pdf_left_lv", None)
            return lv if lv is not None and lv.visible else None
        lv = getattr(self, "_compare_pdf_left_lv", None)
        return lv if lv is not None and lv.visible else None

    def _scroll_sync_right_listview(self) -> ft.ListView | None:
        if not self._pdf_compare_paired_scroll_active():
            return None
        if self._main_tab_index == TAB_FUTURE:
            fut_ov = getattr(self, "_future_plan_overlay_list", None)
            if fut_ov is not None and fut_ov.visible:
                return fut_ov
            lv = getattr(self, "_future_pdf_right_lv", None)
            return lv if lv is not None and lv.visible else None
        lv = getattr(self, "_compare_pdf_right_lv", None)
        return lv if lv is not None and lv.visible else None

    def _on_compare_pdf_scroll_left(self, e: ft.OnScrollEvent) -> None:
        if self._compare_pdf_scroll_guard:
            return
        if not self._pdf_compare_paired_scroll_active():
            return
        if self._main_tab_index not in (TAB_HISTORY, TAB_FUTURE):
            return
        if self._compare_candidate_source not in _PDF_COMPARE_SCROLL_SOURCES:
            return
        self._compare_pdf_left_max_scroll = max(float(e.max_scroll_extent), 1e-6)
        if e.event_type != ft.ScrollType.UPDATE:
            return
        ratio = max(0.0, min(1.0, e.pixels / self._compare_pdf_left_max_scroll))
        target = ratio * max(self._compare_pdf_right_max_scroll, 1e-6)
        self._compare_pdf_scroll_guard = True
        self.page.run_task(self._compare_pdf_sync_scroll_right_async, target)

    async def _compare_pdf_sync_scroll_right_async(self, target: float) -> None:
        try:
            await _safe_list_scroll(self._scroll_sync_right_listview(), target)
        finally:
            self._compare_pdf_scroll_guard = False

    def _on_compare_pdf_scroll_right(self, e: ft.OnScrollEvent) -> None:
        if self._compare_pdf_scroll_guard:
            return
        if not self._pdf_compare_paired_scroll_active():
            return
        if self._main_tab_index not in (TAB_HISTORY, TAB_FUTURE):
            return
        if self._compare_candidate_source not in _PDF_COMPARE_SCROLL_SOURCES:
            return
        self._compare_pdf_right_max_scroll = max(float(e.max_scroll_extent), 1e-6)
        if e.event_type != ft.ScrollType.UPDATE:
            return
        ratio = max(0.0, min(1.0, e.pixels / self._compare_pdf_right_max_scroll))
        target = ratio * max(self._compare_pdf_left_max_scroll, 1e-6)
        self._compare_pdf_scroll_guard = True
        self.page.run_task(self._compare_pdf_sync_scroll_left_async, target)

    async def _compare_pdf_sync_scroll_left_async(self, target: float) -> None:
        try:
            await _safe_list_scroll(self._scroll_sync_left_listview(), target)
        finally:
            self._compare_pdf_scroll_guard = False

    def _on_future_pdf_scroll_left(self, e: ft.OnScrollEvent) -> None:
        if self._compare_pdf_scroll_guard:
            return
        if not self._pdf_compare_paired_scroll_active():
            return
        if self._main_tab_index != TAB_FUTURE or self._compare_candidate_source != "pdf_original":
            return
        self._compare_pdf_left_max_scroll = max(float(e.max_scroll_extent), 1e-6)
        if e.event_type != ft.ScrollType.UPDATE:
            return
        ratio = max(0.0, min(1.0, e.pixels / self._compare_pdf_left_max_scroll))
        target = ratio * max(self._compare_pdf_right_max_scroll, 1e-6)
        self._compare_pdf_scroll_guard = True
        self.page.run_task(self._future_pdf_sync_scroll_right_async, target)

    async def _future_pdf_sync_scroll_right_async(self, target: float) -> None:
        try:
            await _safe_list_scroll(self._scroll_sync_right_listview(), target)
        finally:
            self._compare_pdf_scroll_guard = False

    def _on_future_pdf_scroll_right(self, e: ft.OnScrollEvent) -> None:
        if self._compare_pdf_scroll_guard:
            return
        if not self._pdf_compare_paired_scroll_active():
            return
        if self._main_tab_index != TAB_FUTURE or self._compare_candidate_source != "pdf_original":
            return
        self._compare_pdf_right_max_scroll = max(float(e.max_scroll_extent), 1e-6)
        if e.event_type != ft.ScrollType.UPDATE:
            return
        ratio = max(0.0, min(1.0, e.pixels / self._compare_pdf_right_max_scroll))
        target = ratio * max(self._compare_pdf_left_max_scroll, 1e-6)
        self._compare_pdf_scroll_guard = True
        self.page.run_task(self._future_pdf_sync_scroll_left_async, target)

    async def _future_pdf_sync_scroll_left_async(self, target: float) -> None:
        try:
            await _safe_list_scroll(self._scroll_sync_left_listview(), target)
        finally:
            self._compare_pdf_scroll_guard = False

    async def _seed_pdf_pair_scroll_metrics_async(
        self,
        left_lv: ft.ListView,
        right_lv: ft.ListView,
        settle_s: float = 0.0,
    ) -> None:
        """Nudge both ListViews after rebuild so each emits scroll metrics; avoids
        cross-pane sync using the default max (1.0) until the user has scrolled both
        columns manually."""
        await asyncio.sleep(0)
        if settle_s > 0:
            await asyncio.sleep(settle_s)
        if not _ctrl_on_page(left_lv) or not _ctrl_on_page(right_lv):
            return
        await _safe_list_scroll(left_lv, 0)
        await _safe_list_scroll(right_lv, 0)

    def _sync_future_pdf_layers_visibility(self) -> None:
        sub = int(getattr(self, "_review_subtab_index", 0) or 0)
        show = (
            self._main_tab_index == TAB_FUTURE
            and sub == 0
            and self._compare_candidate_source == "pdf_original"
        )
        self._future_pdf_layer.visible = show
        self._future_paragraph_layer.visible = not show
        fut_pc = getattr(self, "_plan_compare_future", None)
        if fut_pc is not None:
            show_plan_bar = show and self._is_plan_pdf_compare()
            fut_pc.set_bar_visible(show_plan_bar)
        if _ctrl_on_page(self._future_pdf_layer):
            self._future_pdf_layer.update()
        if _ctrl_on_page(self._future_paragraph_layer):
            self._future_paragraph_layer.update()

    def _sync_compare_pdf_layers_visibility(self) -> None:
        on_hist = self._main_tab_index == TAB_HISTORY
        pdf_src = self._compare_candidate_source in _PDF_COMPARE_SCROLL_SOURCES
        show_pdf = pdf_src and on_hist
        self._compare_paragraph_layer.visible = not show_pdf
        self._compare_pdf_layer.visible = show_pdf
        if _ctrl_on_page(self._compare_paragraph_layer):
            self._compare_paragraph_layer.update()
        if _ctrl_on_page(self._compare_pdf_layer):
            self._compare_pdf_layer.update()

    def _set_plan_compare_dropdown_focused(self, focused: bool) -> None:
        self._plan_compare_dropdown_focused = focused
        self._apply_plan_compare_dropdown_chrome()

    def _on_plan_baseline_dropdown_hover(self, e: ft.ControlEvent) -> None:
        self._plan_baseline_dd_hover = str(e.data).lower() == "true"
        self._apply_plan_compare_dropdown_chrome()

    def _on_plan_candidate_dropdown_hover(self, e: ft.ControlEvent) -> None:
        self._plan_candidate_dd_hover = str(e.data).lower() == "true"
        self._apply_plan_compare_dropdown_chrome()

    def _apply_plan_compare_dropdown_chrome(self) -> None:
        """Rim around plan PDF dropdowns (match History Older/Newer hover/focus)."""
        selected = self._main_tab_index == TAB_HISTORY
        for wrap, own_hover in (
            (self._plan_compare.baseline_wrap, self._plan_baseline_dd_hover),
            (self._plan_compare.candidate_wrap, self._plan_candidate_dd_hover),
        ):
            accent = selected and (own_hover or self._plan_compare_dropdown_focused)
            rim = config.PRIMARY_COLOR if accent else ui_theme.outline_muted()
            wrap.border = ft.Border.all(1, rim)
        if self._main_tab_index == TAB_HISTORY:
            if _ctrl_on_page(self._plan_compare.baseline_wrap):
                self._plan_compare.baseline_wrap.update()
            if _ctrl_on_page(self._plan_compare.candidate_wrap):
                self._plan_compare.candidate_wrap.update()

    def _hide_compose_plan_surface(self) -> None:
        self._compose_plan_host.visible = False
        self._compose_plan_load_inflight_key = None
        self._compose_editor_shell_wrapped.expand = True
        self._compose_editor_shell_wrapped.height = None
        self._apply_compose_plan_layout_mode()
        self._restore_compose_reading_width_after_plan()
        self._apply_compose_plan_tab_scroll()
        if hasattr(self, "_apply_focus_preview_mode"):
            self._apply_focus_preview_mode()
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.update()

    def _refresh_compose_plan_surface(self) -> None:
        """Schedule async plan-PDF load (never block the UI thread)."""
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(self._refresh_compose_plan_surface_async)

    def _on_compose_plan_viewer_page_changed(self, page_index: int) -> None:
        self._compose_plan_page_index = int(page_index)
        self._refresh_compose_plan_annotations_overlay()
        if hasattr(self, "_rebuild_content_tree"):
            self._rebuild_content_tree()

    def _compose_plan_go_page(self, page_index: int) -> None:
        viewer = getattr(self, "_compose_plan_focus_viewer", None)
        if viewer is None:
            return
        viewer.set_page(page_index)
        self._compose_plan_page_index = viewer.current_index

    def _compose_plan_version_context(self) -> tuple[int, int, Path] | None:
        """``(document_id, version_id, pdf_abs)`` for the active compose plan surface."""
        if not self.current_path:
            return None
        with session_scope() as s:
            doc = content_repo.get_document_by_resolved_path(s, self.current_path.resolve())
            if doc is None:
                return None
            det = content_repo.latest_pdf_version_detail(s, self.current_path.resolve())
            if det is None:
                return None
            vid, rel, profile = det
            if (profile or "").strip() != "plan" and self._document_pdf_profile() != "plan":
                return None
            try:
                pdf_abs = content_repo.pdf_asset_abs_path(rel)
            except (ValueError, OSError):
                return None
            doc_id = int(doc.id)
            version_id = int(vid)
        if not pdf_abs.is_file():
            return None
        return doc_id, version_id, pdf_abs

    def _refresh_compose_plan_annotations_overlay(self) -> None:
        viewer = getattr(self, "_compose_plan_focus_viewer", None)
        ctx = self._compose_plan_version_context()
        if viewer is None or ctx is None:
            return
        doc_id, vid, _pdf = ctx
        with session_scope() as s:
            anns = plan_pdf_annotations.list_for_plan_version(
                s, content_version_id=vid
            )
        markers: list[plan_picture_viewer.PlanMarkerView] = []
        for a in anns:
            bbox = (
                a.cloud_bbox_norm()
                if a.annotation_kind == plan_pdf_annotations.KIND_REVISION_CLOUD
                else None
            )
            markers.append(
                plan_picture_viewer.PlanMarkerView(
                    kind=a.annotation_kind,
                    page_index=int(a.plan_page_index),
                    norm_x=float(a.plan_norm_x or 0.5),
                    norm_y=float(a.plan_norm_y or 0.5),
                    bbox=bbox,
                )
            )
        viewer.set_markers(markers)

    def _on_compose_plan_place_comment(self, u: float, v: float) -> None:
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(self._compose_plan_place_comment_async, float(u), float(v))

    async def _compose_plan_place_comment_async(self, u: float, v: float) -> None:
        ctx = self._compose_plan_version_context()
        viewer = getattr(self, "_compose_plan_focus_viewer", None)
        if ctx is None or viewer is None:
            return
        doc_id, vid, _pdf = ctx
        page_ix = int(viewer.current_index)
        with session_scope() as s:
            ann = plan_pdf_annotations.insert_pin(
                s,
                content_version_id=vid,
                plan_page_index=page_ix,
                plan_norm_x=u,
                plan_norm_y=v,
                body="",
            )
            slot = int(ann.paragraph_index)
        self._compose_plan_document_id = doc_id
        self._compose_plan_version_id = vid
        self._refresh_compose_plan_annotations_overlay()
        if hasattr(self, "_rebuild_ki_comments_list"):
            self._rebuild_ki_comments_list()
        await self._open_ki_comments_for_paragraph_async(slot, True)

    def _on_compose_plan_revision_cloud(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> None:
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(
                self._compose_plan_revision_cloud_async,
                float(x0),
                float(y0),
                float(x1),
                float(y1),
            )

    async def _compose_plan_revision_cloud_async(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> None:
        ctx = self._compose_plan_version_context()
        viewer = getattr(self, "_compose_plan_focus_viewer", None)
        if ctx is None or viewer is None:
            return
        doc_id, vid, _pdf = ctx
        page_ix = int(viewer.current_index)
        with session_scope() as s:
            plan_pdf_annotations.insert_revision_cloud(
                s,
                content_version_id=vid,
                plan_page_index=page_ix,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
            )
        self._compose_plan_document_id = doc_id
        self._compose_plan_version_id = vid
        self._refresh_compose_plan_annotations_overlay()
        if hasattr(self, "_rebuild_ki_comments_list"):
            self._rebuild_ki_comments_list()

    def _on_compose_plan_export_pdf(self) -> None:
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(self._compose_plan_export_pdf_async)

    async def _compose_plan_export_pdf_async(self) -> None:
        ctx = self._compose_plan_version_context()
        if ctx is None:
            self._snack("No plan PDF to export.")
            return
        _doc_id, _vid, pdf_abs = ctx
        stem = pdf_abs.stem
        try:
            dest = await self._fp_export_plan_pdf.save_file(
                dialog_title="Export annotated plan PDF",
                file_name=f"{stem}_annotated.pdf",
                initial_directory=str(pdf_abs.parent),
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["pdf"],
            )
        except BaseException as ex:
            self._snack(f"Save dialog failed: {ex}")
            return
        if not dest:
            self._snack("Export cancelled.")
            return
        from iterthink.studio.util import normalize_save_file_path

        default_name = f"{stem}_annotated.pdf"
        try:
            out = normalize_save_file_path(
                dest, default_file_name=default_name, expected_suffix=".pdf"
            )
        except ValueError:
            self._snack("Export cancelled.")
            return
        with session_scope() as s:
            anns = plan_pdf_annotations.list_for_plan_version(
                s, content_version_id=_vid
            )

        def _run() -> None:
            export_annotated_pdf(pdf_abs, anns, out)

        try:
            await asyncio.to_thread(_run)
        except BaseException as ex:
            self._snack(f"Export failed: {ex}")
            return
        self._snack(f"Exported: {out}")

    def _apply_compose_plan_viewer(
        self,
        focus_viewer: plan_picture_viewer.PlanFocusViewer,
        *,
        surface_key: tuple[int, bool, str],
        page_ix: int,
    ) -> None:
        self._compose_plan_focus_viewer = focus_viewer
        focus_viewer._on_page_change = self._on_compose_plan_viewer_page_changed
        focus_viewer._on_place_comment = self._on_compose_plan_place_comment
        focus_viewer._on_revision_cloud = self._on_compose_plan_revision_cloud
        focus_viewer._on_export_pdf = self._on_compose_plan_export_pdf
        focus_viewer.set_page(page_ix)
        self._compose_plan_page_index = focus_viewer.current_index
        self._compose_plan_surface_key = surface_key
        ctx = self._compose_plan_version_context()
        if ctx is not None:
            self._compose_plan_document_id, self._compose_plan_version_id, _ = ctx
        self._refresh_compose_plan_annotations_overlay()
        self._compose_plan_host.content = focus_viewer.root
        self._compose_plan_host.visible = True
        self._apply_compose_plan_editor_layout()
        self._apply_compose_plan_tab_scroll()
        self._apply_compose_plan_layout_mode()
        avail = self._compose_column_avail_width()
        if avail > 0:
            self._apply_compose_reading_card_width(avail)
        if hasattr(self, "_rebuild_content_tree"):
            self._rebuild_content_tree()
        if hasattr(self, "_apply_focus_preview_mode"):
            self._apply_focus_preview_mode()
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.update()

    async def _refresh_compose_plan_surface_async(self) -> None:
        """Show zoom/pan PDF strip on Compose when latest stored PDF is profile ``plan``."""
        gen = int(getattr(self, "_compose_plan_load_gen", 0)) + 1
        self._compose_plan_load_gen = gen

        if not self.current_path:
            self._hide_compose_plan_surface()
            return
        with session_scope() as s:
            det = content_repo.latest_pdf_version_detail(s, self.current_path.resolve())
        if det is None:
            self._hide_compose_plan_surface()
            return
        vid, rel, profile = det
        with session_scope() as s:
            doc = content_repo.get_document_by_resolved_path(s, self.current_path.resolve())
            if doc is not None:
                self._compose_plan_document_id = int(doc.id)
                self._compose_plan_version_id = int(vid)
        if (profile or "").strip() != "plan":
            if self._document_pdf_profile() != "plan":
                self._hide_compose_plan_surface()
                return
        try:
            pdf_abs = content_repo.pdf_asset_abs_path(rel)
        except (ValueError, OSError):
            self._hide_compose_plan_surface()
            return
        if not pdf_abs.is_file():
            self._hide_compose_plan_surface()
            self._snack("Plan PDF file missing on disk.")
            return

        show_labels = bool(getattr(self, "_compose_plan_show_labels", True))
        surface_key = (int(vid), show_labels, str(pdf_abs.resolve()))
        if getattr(self, "_compose_plan_surface_key", None) == surface_key and self._compose_plan_viewer_active():
            if hasattr(self, "_rebuild_content_tree"):
                self._rebuild_content_tree()
            return
        if getattr(self, "_compose_plan_load_inflight_key", None) == surface_key:
            return

        doc_path = self.current_path.resolve()
        page_ix = int(getattr(self, "_compose_plan_page_index", 0) or 0)
        self._compose_plan_load_inflight_key = surface_key
        self._compose_plan_host.content = ft.Container(
            alignment=ft.Alignment.CENTER,
            expand=True,
            content=ft.ProgressRing(width=28, height=28, stroke_width=2, color=config.PRIMARY_COLOR),
        )
        self._compose_plan_host.visible = True
        self._apply_compose_plan_editor_layout()
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.update()

        def _load_base() -> tuple[list[Path], dict]:
            pages = document_import.render_pdf_to_png_pages(pdf_abs, pdf_profile="plan")
            geometry = load_plan_text_sidecar(doc_path, vid) or {"pages": []}
            return pages, geometry

        try:
            base_pages, geometry = await asyncio.to_thread(_load_base)
        except BaseException as ex:
            if gen != getattr(self, "_compose_plan_load_gen", 0):
                return
            self._hide_compose_plan_surface()
            self._snack(f"Could not load plan PDF: {ex}")
            return
        finally:
            if getattr(self, "_compose_plan_load_inflight_key", None) == surface_key:
                self._compose_plan_load_inflight_key = None

        if gen != getattr(self, "_compose_plan_load_gen", 0):
            return
        if not base_pages:
            if gen == getattr(self, "_compose_plan_load_gen", 0):
                self._hide_compose_plan_surface()
                self._snack("Plan PDF has no pages.")
            return

        try:
            focus_viewer = plan_picture_viewer.build_plan_focus_viewer(
                base_pages,
                initial_page_index=page_ix,
                on_page_change=self._on_compose_plan_viewer_page_changed,
                on_place_comment=self._on_compose_plan_place_comment,
                on_revision_cloud=self._on_compose_plan_revision_cloud,
                on_export_pdf=self._on_compose_plan_export_pdf,
            )
            self._apply_compose_plan_viewer(focus_viewer, surface_key=surface_key, page_ix=page_ix)
            if focus_viewer.page_count > 0:
                self.page.run_task(
                    self._show_compose_plan_page_async, self._compose_plan_page_index
                )
        except BaseException as ex:
            if gen != getattr(self, "_compose_plan_load_gen", 0):
                return
            self._hide_compose_plan_surface()
            self._snack(f"Could not load plan PDF: {ex}")
            return

        if gen != getattr(self, "_compose_plan_load_gen", 0):
            return

        if show_labels and (geometry.get("pages") or []):

            def _overlay() -> list[Path]:
                return page_pngs_with_text_overlay(base_pages, geometry, show_labels=True)

            try:
                labeled = await asyncio.to_thread(_overlay)
            except BaseException as ex:
                self._snack(f"Label overlay failed: {ex}")
                return
            if gen != getattr(self, "_compose_plan_load_gen", 0):
                return
            try:
                focus_viewer = plan_picture_viewer.build_plan_focus_viewer(
                    labeled,
                    initial_page_index=page_ix,
                    on_page_change=self._on_compose_plan_viewer_page_changed,
                    on_place_comment=self._on_compose_plan_place_comment,
                    on_revision_cloud=self._on_compose_plan_revision_cloud,
                    on_export_pdf=self._on_compose_plan_export_pdf,
                )
                self._apply_compose_plan_viewer(
                    focus_viewer, surface_key=surface_key, page_ix=page_ix
                )
                if focus_viewer.page_count > 0:
                    self.page.run_task(
                        self._show_compose_plan_page_async, self._compose_plan_page_index
                    )
            except BaseException as ex:
                self._snack(f"Label overlay failed: {ex}")

    async def _show_compose_plan_page_async(self, page_index: int) -> None:
        host = getattr(self, "_compose_plan_host", None)
        if host is not None and _ctrl_on_page(host):
            host.update()
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.update()
        await asyncio.sleep(0.05)

        async def _try_show() -> bool:
            viewer = getattr(self, "_compose_plan_focus_viewer", None)
            if viewer is None or not _ctrl_on_page(viewer.root):
                return False
            await viewer.show_page(page_index)
            self._compose_plan_page_index = viewer.current_index
            return True

        if await _try_show():
            if hasattr(self, "_rebuild_content_tree"):
                self._rebuild_content_tree()
            return
        await asyncio.sleep(0.1)
        if await _try_show() and hasattr(self, "_rebuild_content_tree"):
            self._rebuild_content_tree()

    def _on_compose_plan_toggle_labels(self, _e: ft.ControlEvent) -> None:
        self._compose_plan_show_labels = not bool(getattr(self, "_compose_plan_show_labels", True))
        self._compose_plan_surface_key = None
        self._refresh_compose_plan_surface()

    def _on_compose_plan_toggle_editor(self, _e: ft.ControlEvent) -> None:
        self._compose_plan_editor_collapsed = not bool(
            getattr(self, "_compose_plan_editor_collapsed", False)
        )
        self._apply_compose_plan_editor_layout()
        if _ctrl_on_page(self._compose_editor_shell_wrapped):
            self._compose_editor_shell_wrapped.update()

    def _refresh_plan_compare_bar(self) -> None:
        if not self.current_path:
            self._plan_compare.set_bar_visible(False)
            return
        with session_scope() as s:
            pairs = content_repo.list_plan_pdf_version_options(s, self.current_path.resolve())
        opts = [(str(vid), lbl) for vid, lbl in pairs]
        self._fill_all_plan_compare_dropdowns(opts)
        is_plan = self._is_plan_pdf_compare()
        show_bar = len(opts) >= 2 and is_plan
        for pc in self._plan_compare_panels():
            if not show_bar:
                pc.overlay_switch.value = False
                pc.side_by_side_switch.value = False
            pc.baseline_dd.disabled = not show_bar
            pc.candidate_dd.disabled = not show_bar
            pc.overlay_switch.disabled = not show_bar
            pc.side_by_side_switch.disabled = not show_bar
        hist_show = show_bar and self._main_tab_index == TAB_HISTORY
        self._plan_compare.set_bar_visible(hist_show)
        fut_pc = getattr(self, "_plan_compare_future", None)
        if fut_pc is not None:
            fut_show = show_bar and self._main_tab_index == TAB_FUTURE
            fut_pc.set_bar_visible(fut_show)
        if is_plan and show_bar and not getattr(self, "_plan_overlay_defaults_set", False):
            for pc in self._plan_compare_panels():
                pc.overlay_switch.value = False
            self._plan_overlay_defaults_set = True
        self._sync_plan_overlay_pane_visibility()
        for pc in self._plan_compare_panels():
            if _ctrl_on_page(pc.baseline_dd):
                pc.baseline_dd.update()
            if _ctrl_on_page(pc.candidate_dd):
                pc.candidate_dd.update()
            if _ctrl_on_page(pc.overlay_switch):
                pc.overlay_switch.update()
            if _ctrl_on_page(pc.side_by_side_switch):
                pc.side_by_side_switch.update()

    def _active_plan_compare_panel(self):
        if self._main_tab_index == TAB_FUTURE:
            return getattr(self, "_plan_compare_future", None) or self._plan_compare
        return self._plan_compare

    def _sync_plan_overlay_pane_visibility(self) -> None:
        pc = self._active_plan_compare_panel()
        side = bool(pc.side_by_side_switch.value)
        multi_version = self._plan_pdf_version_count() >= 2
        if multi_version:
            self._plan_overlay_mode = not side
            self._plan_side_by_side_mode = side
            self._compare_pdf_left_lv.visible = self._plan_side_by_side_mode
            self._compare_pdf_right_lv.visible = self._plan_side_by_side_mode
        else:
            # Single version (e.g. fresh import): full-width plan stack only.
            self._plan_overlay_mode = False
            self._plan_side_by_side_mode = False
            self._compare_pdf_left_lv.visible = True
            self._compare_pdf_right_lv.visible = False
        right_col = getattr(self, "_compare_pdf_right_column", None)
        if right_col is not None:
            right_col.visible = self._compare_pdf_right_lv.visible
        fut_right = getattr(self, "_future_pdf_right_lv", None)
        if fut_right is not None and self._main_tab_index == TAB_FUTURE:
            fut_right.visible = self._plan_side_by_side_mode
        self._plan_compare.overlay_list.visible = (
            self._plan_overlay_mode and self._main_tab_index == TAB_HISTORY
        )
        fut_host = getattr(self, "_future_plan_overlay_host", None)
        fut_split = getattr(self, "_future_pdf_split_row", None)
        on_fut = self._main_tab_index == TAB_FUTURE
        if fut_host is not None:
            fut_host.visible = self._plan_overlay_mode and on_fut
        if fut_split is not None:
            fut_split.visible = on_fut and not self._plan_overlay_mode
        hist_host = getattr(self, "_compare_pdf_overlay_host", None)
        split = getattr(self, "_compare_pdf_split_row", None)
        if hist_host is not None:
            hist_host.visible = self._plan_overlay_mode and self._main_tab_index == TAB_HISTORY
        if split is not None:
            split.visible = self._main_tab_index == TAB_HISTORY and not self._plan_overlay_mode

    def _on_plan_overlay_changed(self, e: ft.ControlEvent | None = None) -> None:
        pc = self._active_plan_compare_panel()
        for other in self._plan_compare_panels():
            if other is not pc:
                other.overlay_switch.value = pc.overlay_switch.value
                other.side_by_side_switch.value = pc.side_by_side_switch.value
        self._sync_plan_overlay_pane_visibility()
        if self._main_tab_index == TAB_HISTORY:
            self._rebuild_compare_pdf_panes()
        else:
            self._rebuild_future_pdf_import_panes()
        self.page.run_task(self._refresh_plan_overlay_async)

    def _on_plan_side_by_side_changed(self, _e: ft.ControlEvent | None = None) -> None:
        pc = self._active_plan_compare_panel()
        if pc.side_by_side_switch.value:
            pc.overlay_switch.value = False
        self._on_plan_overlay_changed()

    async def _on_plan_pdf_baseline_async(self, _e: ft.ControlEvent | None = None) -> None:
        if self._plan_overlay_mode:
            await self._refresh_plan_overlay_async()
        else:
            self._rebuild_compare_pdf_panes()

    async def _on_plan_pdf_candidate_async(self, _e: ft.ControlEvent | None = None) -> None:
        if self._plan_overlay_mode:
            await self._refresh_plan_overlay_async()
        elif self._is_plan_pdf_compare():
            if self._main_tab_index == TAB_FUTURE:
                self._rebuild_future_plan_pdf_panes()
            else:
                await self._rebuild_compare_plan_pdf_panes_async()
        else:
            self._rebuild_compare_pdf_panes()

    async def _refresh_plan_overlay_async(self) -> None:
        if not self.current_path or not self._plan_overlay_mode:
            return
        pc = self._active_plan_compare_panel()
        bid_s = pc.baseline_dd.value
        cid_s = pc.candidate_dd.value
        if not bid_s or not cid_s:
            return
        try:
            bid = int(bid_s)
            cid = int(cid_s)
        except (TypeError, ValueError):
            return
        if bid == cid:
            self._snack("Pick two different PDF versions for overlay.")
            return
        with session_scope() as s:
            ra = content_repo.get_version_pdf_relpath(s, bid)
            rb = content_repo.get_version_pdf_relpath(s, cid)
        if not ra or not rb:
            return
        pa = content_repo.pdf_asset_abs_path(ra)
        pb = content_repo.pdf_asset_abs_path(rb)
        gen = self._plan_overlay_gen + 1
        self._plan_overlay_gen = gen

        def _run() -> tuple[list[Path], str | None, list[float]]:
            return diff_pdfs_to_overlay_paths(pa, pb, pdf_profile="plan")

        paths, warn, confidences = await asyncio.to_thread(_run)
        if gen != self._plan_overlay_gen:
            return
        self._plan_overlay_confidences = confidences
        target_lv = self._plan_compare.overlay_list
        if self._main_tab_index == TAB_FUTURE:
            target_lv = getattr(self, "_future_plan_overlay_list", None) or target_lv
        if not paths:
            self._snack("Visual diff failed; showing side-by-side.")
            pc = self._active_plan_compare_panel()
            pc.side_by_side_switch.value = True
            self._on_plan_side_by_side_changed()
            return
        plan_compare_panel.populate_overlay_list(target_lv, paths)
        if confidences and min(confidences) < 0.35:
            warn = (warn + " " if warn else "") + "Weak alignment on some pages."
            pc = self._active_plan_compare_panel()
            if not pc.side_by_side_switch.value:
                pc.side_by_side_switch.value = True
                self._sync_plan_overlay_pane_visibility()
                self._rebuild_compare_pdf_panes() if self._main_tab_index == TAB_HISTORY else self._rebuild_future_pdf_import_panes()
        if warn:
            self._snack(warn)
        if _ctrl_on_page(target_lv):
            target_lv.update()

    def _compare_resolve_pdf_asset(self) -> tuple[int, str] | None:
        """PDF version id and store relpath for the current Compare context."""
        if not self.current_path:
            return None
        rp = self.current_path.resolve()
        with session_scope() as s:
            if self._compare_pdf_peer_snapshot_id is not None:
                rel = content_repo.get_version_pdf_relpath(s, self._compare_pdf_peer_snapshot_id)
                if rel:
                    return (self._compare_pdf_peer_snapshot_id, rel)
                return None
            return content_repo.latest_pdf_version_for_document(s, rp)

    def _compare_resolve_pdf_asset_right(self) -> tuple[int, str] | None:
        """Prefer explicit PDF version from Compare bar when set."""
        pc = self._active_plan_compare_panel()
        if self.current_path and pc.candidate_dd.value:
            try:
                vid = int(pc.candidate_dd.value)
            except (TypeError, ValueError):
                return self._compare_resolve_pdf_asset()
            with session_scope() as s:
                rel = content_repo.get_version_pdf_relpath(s, vid)
                if rel:
                    return (vid, rel)
        return self._compare_resolve_pdf_asset()

    def _compare_resolve_pdf_asset_baseline(self) -> tuple[int, str] | None:
        pc = self._active_plan_compare_panel()
        if self.current_path and pc.baseline_dd.value:
            try:
                vid = int(pc.baseline_dd.value)
            except (TypeError, ValueError):
                return self._compare_resolve_pdf_asset()
            with session_scope() as s:
                rel = content_repo.get_version_pdf_relpath(s, vid)
                if rel:
                    return (vid, rel)
        return self._compare_resolve_pdf_asset()

    def _plan_display_pages_for_resolved(
        self,
        resolved: tuple[int, str] | None,
    ) -> list[Path] | None:
        """Render plan PNG paths for compare; ``None`` if asset missing."""
        if resolved is None or not self.current_path:
            return None
        pdf_abs = self._pdf_abs_for_resolved(resolved)
        if pdf_abs is None:
            return None
        vid, _rel = resolved
        show_labels = bool(getattr(self, "_compose_plan_show_labels", True))
        return self._plan_display_pages_blocking(
            pdf_abs, self.current_path.resolve(), vid, show_labels=show_labels
        )

    async def _plan_display_pages_for_resolved_async(
        self,
        resolved: tuple[int, str] | None,
    ) -> list[Path] | None:
        if resolved is None or not self.current_path:
            return None
        pdf_abs = self._pdf_abs_for_resolved(resolved)
        if pdf_abs is None:
            return None
        vid, _rel = resolved
        show_labels = bool(getattr(self, "_compose_plan_show_labels", True))
        return await asyncio.to_thread(
            self._plan_display_pages_blocking,
            pdf_abs,
            self.current_path.resolve(),
            vid,
            show_labels=show_labels,
        )

    def _pdf_abs_for_resolved(self, resolved: tuple[int, str] | None) -> Path | None:
        if resolved is None:
            return None
        _, rel = resolved
        try:
            pdf_abs = content_repo.pdf_asset_abs_path(rel)
        except (ValueError, OSError):
            return None
        if pdf_abs is None or not pdf_abs.is_file():
            return None
        return pdf_abs

    def _plan_paths_for_resolved(
        self, resolved: tuple[int, str] | None
    ) -> tuple[list[Path] | None, str | None]:
        """``(paths, error_message)`` — ``paths`` is ``None`` only when render failed."""
        if resolved is None:
            return None, "No PDF asset for this comparison."
        try:
            display = self._plan_display_pages_for_resolved(resolved)
        except BaseException as ex:
            return None, f"Could not render PDF: {ex}"
        if not display:
            return None, "PDF file missing on disk."
        return display, None

    async def _plan_paths_for_resolved_async(
        self, resolved: tuple[int, str] | None
    ) -> tuple[list[Path] | None, str | None]:
        if resolved is None:
            return None, "No PDF asset for this comparison."
        try:
            display = await self._plan_display_pages_for_resolved_async(resolved)
        except BaseException as ex:
            return None, f"Could not render PDF: {ex}"
        if not display:
            return None, "PDF file missing on disk."
        return display, None

    def _append_plan_side_by_side_panes(
        self,
        left_lv: ft.ListView,
        right_lv: ft.ListView,
        base: tuple[int, str] | None,
        cand: tuple[int, str] | None,
    ) -> None:
        """Two columns: baseline left, candidate right; synced zoom/pan per page."""
        left_lv.controls.clear()
        right_lv.controls.clear()
        base_paths, base_err = self._plan_paths_for_resolved(base)
        cand_paths, cand_err = self._plan_paths_for_resolved(cand)
        if base_paths is None and cand_paths is None:
            msg = base_err or cand_err or "No PDF to compare."
            left_lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(12),
                    content=ft.Text(msg, color=ft.Colors.ORANGE_200, size=13),
                )
            )
            return

        def _append_column(
            lv: ft.ListView,
            paths: list[Path] | None,
            err: str | None,
            *,
            iv_out: list[ft.InteractiveViewer],
        ) -> None:
            if paths is None:
                lv.controls.append(
                    ft.Container(
                        padding=ft.padding.all(12),
                        content=ft.Text(err or "No PDF.", color=ft.Colors.ORANGE_200, size=13),
                    )
                )
                return
            col, ivs = plan_picture_viewer.plan_picture_compare_column(paths)
            iv_out.extend(ivs)
            lv.controls.append(ft.Container(content=col, expand=True))

        left_ivs: list[ft.InteractiveViewer] = []
        right_ivs: list[ft.InteractiveViewer] = []
        _append_column(left_lv, base_paths, base_err, iv_out=left_ivs)
        _append_column(right_lv, cand_paths, cand_err, iv_out=right_ivs)

        pg = getattr(self, "page", None)
        if pg is not None:
            for i in range(min(len(left_ivs), len(right_ivs))):
                plan_picture_viewer.wire_synced_interactive_viewer_pair(
                    left_ivs[i], right_ivs[i], pg
                )
            pg.run_task(
                self._seed_pdf_pair_scroll_metrics_async,
                left_lv,
                right_lv,
                0.06,
            )

    def _append_pdf_pages_to_list(
        self,
        lv: ft.ListView,
        resolved: tuple[int, str] | None,
        *,
        pdf_profile: str | None = None,
    ) -> None:
        if resolved is None:
            lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(12),
                    content=ft.Text(
                        "No PDF asset for this comparison.",
                        color=ft.Colors.ORANGE_200,
                        size=13,
                    ),
                )
            )
            return
        _, rel = resolved
        try:
            pdf_abs = content_repo.pdf_asset_abs_path(rel)
        except (ValueError, OSError):
            pdf_abs = None
        if pdf_abs is None or not pdf_abs.is_file():
            lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(12),
                    content=ft.Text("PDF file missing on disk.", color=ft.Colors.RED_200, size=13),
                )
            )
        else:
            try:
                prof: document_import.PdfProfileHeuristic | None = (
                    "plan" if pdf_profile == "plan" else "text" if pdf_profile == "text" else None
                )
                if prof == "plan":
                    display = self._plan_display_pages_for_resolved(resolved)
                    if not display:
                        lv.controls.append(
                            ft.Container(
                                padding=ft.padding.all(12),
                                content=ft.Text(
                                    "PDF file missing on disk.", color=ft.Colors.RED_200, size=13
                                ),
                            )
                        )
                        return
                    pic_col = plan_picture_viewer.plan_picture_column(display, inner_scroll=False)
                else:
                    pages = document_import.render_pdf_to_png_pages(pdf_abs, pdf_profile=prof)
                    pic_col = plan_picture_viewer.plan_picture_column(pages, inner_scroll=False)
                lv.controls.append(ft.Container(content=pic_col, expand=True))
            except BaseException as ex:
                lv.controls.append(
                    ft.Container(
                        padding=ft.padding.all(12),
                        content=ft.Text(f"Could not render PDF: {ex}", color=ft.Colors.RED_200, size=12),
                    )
                )

    def _append_pdf_pages_to_left_list(
        self,
        left_lv: ft.ListView,
        resolved: tuple[int, str] | None,
        *,
        pdf_profile: str | None = None,
    ) -> None:
        self._append_pdf_pages_to_list(left_lv, resolved, pdf_profile=pdf_profile)

    def _append_plan_text_list(self, lv: ft.ListView, version_id: int) -> None:
        if not self.current_path:
            return
        geometry = load_plan_text_sidecar(self.current_path.resolve(), version_id)
        if not geometry:
            lv.controls.append(
                ft.Container(
                    padding=12,
                    content=ft.Text("No extracted plan text for this version.", size=12),
                )
            )
            return
        for page in geometry.get("pages") or []:
            pn = int(page.get("page") or 0)
            lv.controls.append(
                ft.Container(
                    padding=ft.padding.only(left=8, top=6, bottom=2),
                    content=ft.Text(f"Page {pn}", weight=ft.FontWeight.W_600, size=12),
                )
            )
            for line in page.get("lines") or []:
                text = str(line.get("text") or "").strip()
                if not text:
                    continue
                lv.controls.append(
                    ft.Container(
                        padding=ft.padding.symmetric(horizontal=12, vertical=2),
                        content=ft.Text(text, size=COMPARE_COL_FONT_SIZE, selectable=True),
                    )
                )

    def _append_markdown_to_right_list(
        self,
        right_lv: ft.ListView,
        body: str,
        *,
        editable_right: bool,
    ) -> None:
        _md_style = ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=ui_theme.editor_text_color(),
        )
        if editable_right:
            self._future_pdf_import_md_tf.value = body
            right_lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(0),
                    content=self._future_pdf_import_md_tf,
                    expand=True,
                )
            )
        else:
            right_lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(8),
                    content=ft.Text(
                        body,
                        selectable=True,
                        expand=True,
                        style=_md_style,
                    ),
                )
            )

    def _rebuild_future_pdf_import_panes(self) -> None:
        """Review tab: document PDF = PDF+markdown; plan PDF = visual compare / text list."""
        if self._is_plan_pdf_compare():
            self._rebuild_future_plan_pdf_panes()
            return
        self._compare_pdf_left_max_scroll = 1.0
        self._compare_pdf_right_max_scroll = 1.0
        self._future_pdf_left_lv.controls.clear()
        self._future_pdf_right_lv.controls.clear()
        body = self.editor.value or ""
        self._compare_editor.value = body
        resolved = self._compare_resolve_pdf_asset_right()
        self._append_pdf_pages_to_left_list(self._future_pdf_left_lv, resolved, pdf_profile="text")
        self._append_markdown_to_right_list(self._future_pdf_right_lv, body, editable_right=True)
        if _ctrl_on_page(self._future_pdf_left_lv):
            self._future_pdf_left_lv.update()
        if _ctrl_on_page(self._future_pdf_right_lv):
            self._future_pdf_right_lv.update()
        if _ctrl_on_page(self._future_pdf_import_md_tf):
            self._future_pdf_import_md_tf.update()
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(
                self._seed_pdf_pair_scroll_metrics_async,
                self._future_pdf_left_lv,
                self._future_pdf_right_lv,
                0.06,
            )

    def _rebuild_future_plan_pdf_panes(self) -> None:
        self._sync_plan_overlay_pane_visibility()
        self._refresh_plan_compare_bar()
        self._future_pdf_left_lv.controls.clear()
        self._future_pdf_right_lv.controls.clear()
        fut_ov = getattr(self, "_future_plan_overlay_list", None)
        if fut_ov is not None:
            fut_ov.controls.clear()
        if self._plan_overlay_mode and self._plan_pdf_version_count() >= 2:
            self.page.run_task(self._refresh_plan_overlay_async)
        elif self._plan_side_by_side_mode:
            base = self._compare_resolve_pdf_asset_baseline()
            cand = self._compare_resolve_pdf_asset_right()
            self._append_plan_side_by_side_panes(
                self._future_pdf_left_lv, self._future_pdf_right_lv, base, cand
            )
        else:
            resolved = self._compare_resolve_pdf_asset_right()
            self._append_pdf_pages_to_list(self._future_pdf_left_lv, resolved, pdf_profile="plan")
        for lv in (self._future_pdf_left_lv, self._future_pdf_right_lv):
            if _ctrl_on_page(lv):
                lv.update()
        if fut_ov is not None and _ctrl_on_page(fut_ov):
            fut_ov.update()

    def _rebuild_compare_pdf_panes(self) -> None:
        if self._compare_candidate_source == "docx_original":
            self._rebuild_compare_docx_panes()
            return
        if self._is_plan_pdf_compare():
            self._rebuild_compare_plan_pdf_panes()
            return
        self._sync_plan_overlay_pane_visibility()
        self._compare_pdf_left_max_scroll = 1.0
        self._compare_pdf_right_max_scroll = 1.0
        self._compare_pdf_left_lv.controls.clear()
        self._compare_pdf_right_lv.controls.clear()
        body = self._compare_editor.value or ""
        resolved = self._compare_resolve_pdf_asset_right()
        self._append_pdf_pages_to_left_list(self._compare_pdf_left_lv, resolved, pdf_profile="text")
        self._append_markdown_to_right_list(self._compare_pdf_right_lv, body, editable_right=False)
        if _ctrl_on_page(self._compare_pdf_left_lv):
            self._compare_pdf_left_lv.update()
        if _ctrl_on_page(self._compare_pdf_right_lv):
            self._compare_pdf_right_lv.update()
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(
                self._seed_pdf_pair_scroll_metrics_async,
                self._compare_pdf_left_lv,
                self._compare_pdf_right_lv,
                0.0,
            )

    def _rebuild_compare_plan_pdf_panes(self) -> None:
        self._sync_plan_overlay_pane_visibility()
        self._refresh_plan_compare_bar()
        self._compare_pdf_left_max_scroll = 1.0
        self._compare_pdf_right_max_scroll = 1.0
        self._compare_pdf_left_lv.controls.clear()
        self._compare_pdf_right_lv.controls.clear()
        if self._plan_overlay_mode and self._plan_pdf_version_count() >= 2:
            self.page.run_task(self._refresh_plan_overlay_async)
        elif self._plan_side_by_side_mode:
            base = self._compare_resolve_pdf_asset_baseline()
            cand = self._compare_resolve_pdf_asset_right()
            self._append_plan_side_by_side_panes(
                self._compare_pdf_left_lv, self._compare_pdf_right_lv, base, cand
            )
        else:
            resolved = self._compare_resolve_pdf_asset_right()
            self._append_pdf_pages_to_list(self._compare_pdf_left_lv, resolved, pdf_profile="plan")
        for lv in (self._compare_pdf_left_lv, self._compare_pdf_right_lv):
            if _ctrl_on_page(lv):
                lv.update()

    async def _append_plan_side_by_side_panes_async(
        self,
        left_lv: ft.ListView,
        right_lv: ft.ListView,
        base: tuple[int, str] | None,
        cand: tuple[int, str] | None,
    ) -> None:
        left_lv.controls.clear()
        right_lv.controls.clear()
        base_paths, base_err = await self._plan_paths_for_resolved_async(base)
        cand_paths, cand_err = await self._plan_paths_for_resolved_async(cand)
        if base_paths is None and cand_paths is None:
            msg = base_err or cand_err or "No PDF to compare."
            left_lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(12),
                    content=ft.Text(msg, color=ft.Colors.ORANGE_200, size=13),
                )
            )
            return

        def _append_column(
            lv: ft.ListView,
            paths: list[Path] | None,
            err: str | None,
            *,
            iv_out: list[ft.InteractiveViewer],
        ) -> None:
            if paths is None:
                lv.controls.append(
                    ft.Container(
                        padding=ft.padding.all(12),
                        content=ft.Text(err or "No PDF.", color=ft.Colors.ORANGE_200, size=13),
                    )
                )
                return
            col, ivs = plan_picture_viewer.plan_picture_compare_column(paths)
            iv_out.extend(ivs)
            lv.controls.append(ft.Container(content=col, expand=True))

        left_ivs: list[ft.InteractiveViewer] = []
        right_ivs: list[ft.InteractiveViewer] = []
        _append_column(left_lv, base_paths, base_err, iv_out=left_ivs)
        _append_column(right_lv, cand_paths, cand_err, iv_out=right_ivs)
        pg = getattr(self, "page", None)
        if pg is not None:
            for i in range(min(len(left_ivs), len(right_ivs))):
                plan_picture_viewer.wire_synced_interactive_viewer_pair(
                    left_ivs[i], right_ivs[i], pg
                )
            pg.run_task(
                self._seed_pdf_pair_scroll_metrics_async,
                left_lv,
                right_lv,
                0.06,
            )

    async def _append_plan_pdf_list_async(
        self,
        lv: ft.ListView,
        resolved: tuple[int, str] | None,
    ) -> None:
        if resolved is None:
            lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(12),
                    content=ft.Text(
                        "No PDF asset for this comparison.",
                        color=ft.Colors.ORANGE_200,
                        size=13,
                    ),
                )
            )
            return
        try:
            display = await self._plan_display_pages_for_resolved_async(resolved)
        except BaseException as ex:
            lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(12),
                    content=ft.Text(f"Could not render PDF: {ex}", color=ft.Colors.RED_200, size=12),
                )
            )
            return
        if not display:
            lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(12),
                    content=ft.Text("PDF file missing on disk.", color=ft.Colors.RED_200, size=13),
                )
            )
            return
        pic_col = plan_picture_viewer.plan_picture_column(display, inner_scroll=False)
        lv.controls.append(ft.Container(content=pic_col, expand=True))

    async def _rebuild_compare_plan_pdf_panes_async(self) -> None:
        self._sync_plan_overlay_pane_visibility()
        self._refresh_plan_compare_bar()
        self._compare_pdf_left_max_scroll = 1.0
        self._compare_pdf_right_max_scroll = 1.0
        self._compare_pdf_left_lv.controls.clear()
        self._compare_pdf_right_lv.controls.clear()
        if self._plan_overlay_mode and self._plan_pdf_version_count() >= 2:
            self.page.run_task(self._refresh_plan_overlay_async)
        elif self._plan_side_by_side_mode:
            base = self._compare_resolve_pdf_asset_baseline()
            cand = self._compare_resolve_pdf_asset_right()
            await self._append_plan_side_by_side_panes_async(
                self._compare_pdf_left_lv, self._compare_pdf_right_lv, base, cand
            )
        else:
            resolved = self._compare_resolve_pdf_asset_right()
            await self._append_plan_pdf_list_async(self._compare_pdf_left_lv, resolved)
        for lv in (self._compare_pdf_left_lv, self._compare_pdf_right_lv):
            if _ctrl_on_page(lv):
                lv.update()

    async def _rebuild_compare_view_async(self) -> None:
        source = self._compare_candidate_source
        if source == CompareCandidateSource.PDF_ORIGINAL:
            if self._is_plan_pdf_compare():
                await self._rebuild_compare_plan_pdf_panes_async()
            else:
                self._rebuild_compare_pdf_panes()
            self._sync_compare_pdf_layers_visibility()
        elif source == CompareCandidateSource.DOCX_ORIGINAL:
            self._rebuild_compare_docx_panes()
            self._sync_compare_pdf_layers_visibility()
        elif source == CompareCandidateSource.IFC_ORIGINAL:
            self._rebuild_compare_ifc_panes()
            self._sync_compare_pdf_layers_visibility()
        else:
            self._rebuild_compare_paragraph_ui()
        if self._main_tab_index == TAB_HISTORY:
            self._refresh_tab_toolbar()

    async def _finish_plan_geometry_import_async(
        self, doc_path: Path, version_id: int, pdf_src: Path
    ) -> None:
        """Background pdfplumber pass: JSON sidecar for labels/overlays only (no markdown export)."""

        def _extract() -> None:
            from iterthink.services.plan_text_extract import extract_plan_geometry

            geometry = extract_plan_geometry(pdf_src)
            write_plan_text_sidecar(doc_path.resolve(), version_id, geometry)

        try:
            await asyncio.to_thread(_extract)
        except BaseException as ex:
            self._snack(f"Plan text extraction failed: {ex}")
            return
        if self.current_path and self.current_path.resolve() == doc_path.resolve():
            if bool(getattr(self, "_compose_plan_show_labels", False)):
                self._compose_plan_surface_key = None
                self._refresh_compose_plan_surface()

    def _rebuild_compare_docx_panes(self) -> None:
        """History: older snapshot extraction left, History newer-side text right."""
        self._compare_pdf_left_max_scroll = 1.0
        self._compare_pdf_right_max_scroll = 1.0
        self._compare_pdf_left_lv.controls.clear()
        self._compare_pdf_right_lv.controls.clear()
        _md_style = ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=ui_theme.editor_text_color(),
        )
        older = self._compare_editor.value or ""
        newer = self._history_newer_side_text() or ""
        self._compare_pdf_left_lv.controls.append(
            ft.Container(
                padding=ft.padding.all(8),
                content=ft.Text(
                    older,
                    selectable=True,
                    expand=True,
                    style=_md_style,
                ),
            )
        )
        self._compare_pdf_right_lv.controls.append(
            ft.Container(
                padding=ft.padding.all(8),
                content=ft.Text(
                    newer,
                    selectable=True,
                    expand=True,
                    style=_md_style,
                ),
            )
        )
        if _ctrl_on_page(self._compare_pdf_left_lv):
            self._compare_pdf_left_lv.update()
        if _ctrl_on_page(self._compare_pdf_right_lv):
            self._compare_pdf_right_lv.update()
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(
                self._seed_pdf_pair_scroll_metrics_async,
                self._compare_pdf_left_lv,
                self._compare_pdf_right_lv,
                0.0,
            )
