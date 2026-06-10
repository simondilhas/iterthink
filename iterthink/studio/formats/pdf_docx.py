"""PDF/plan overlay and DOCX preview wiring for MarkdownStudio (mixin)."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

import flet as ft

from iterthink import config
from iterthink.persistence import content_repo, plan_pdf_annotations
from iterthink.services import document_import
from iterthink.services.plan_pdf_export import export_annotated_pdf
from iterthink.services.plan_text_diff import diff_plan_geometry, geometry_to_label_views
from iterthink.services.plan_change_region_sync import sync_detected_change_regions
from iterthink.studio.plan_region_actions import PlanRegionActionHandlers
from iterthink.services.plan_text_extract import (
    load_plan_text_sidecar,
    write_plan_text_sidecar,
)
from iterthink.studio.plan_text_change_ui import plan_hover_enabled
from ..history.candidate_state import CompareCandidateSource
from iterthink.tools.pdf_visual_diff import diff_pdfs_to_overlay_paths

from .. import plan_compare_panel, plan_picture_viewer, ui_theme
from iterthink.db.session import session_scope
from ..constants import (
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPOSE_READING_WIDTH_FRAC,
    KI_TOPIC_COMMENTS,
    READING_MAX_PX,
    TAB_FUTURE,
    TAB_HISTORY,
)
from ..util import ctrl_on_page as _ctrl_on_page
from ..util import safe_list_scroll as _safe_list_scroll

_PDF_COMPARE_SCROLL_SOURCES = ("pdf_original", "docx_original")
_PLAN_LAYOUT_MODES = frozenset({"single", "overlay", "side_by_side"})
_PLAN_LAYOUT_ORDER = ("single", "overlay", "side_by_side")
_PLAN_LAYOUT_META: dict[str, tuple[str, str]] = {
    "single": ("ARTICLE_OUTLINED", "Single plan"),
    "overlay": ("LAYERS_OUTLINED", "Overlay old and new"),
    "side_by_side": ("__side_by_side__", "Old left, new right"),
}
_TEXT_LAYOUT_MODES = frozenset({"single", "side_by_side"})
_TEXT_LAYOUT_ORDER = ("single", "side_by_side")
_TEXT_LAYOUT_META: dict[str, tuple[str, str]] = {
    "single": ("ARTICLE_OUTLINED", "Single document"),
    "side_by_side": ("__side_by_side__", "Compare old and new"),
}


@dataclass(frozen=True)
class _PlanPageLoad:
    paths: list[Path] | None
    page_total: int
    error: str | None
    pdf_abs: Path | None


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

    def _compose_column_avail_height(self) -> float:
        row = getattr(self, "_compose_centered_row", None)
        if row is not None and float(row.height or 0) > 0:
            return max(plan_picture_viewer._FOCUS_MIN_VIEWPORT_H, float(row.height))
        wrap = getattr(self, "_compose_reading_wrap", None)
        if wrap is not None and float(wrap.height or 0) > 0:
            return max(plan_picture_viewer._FOCUS_MIN_VIEWPORT_H, float(wrap.height))
        return 0.0

    def _sync_compose_plan_viewport_size(self, avail_w: float, avail_h: float) -> None:
        viewer = getattr(self, "_compose_plan_focus_viewer", None)
        if viewer is None or not self._compose_plan_viewer_active():
            return
        collapsed = bool(getattr(self, "_compose_plan_editor_collapsed", False))
        editor_reserve = 0.0 if collapsed else 220.0
        col_spacing = 0.0 if collapsed else 8.0
        nav_h = 40.0
        plan_h = max(
            plan_picture_viewer._FOCUS_MIN_VIEWPORT_H,
            float(avail_h) - editor_reserve - col_spacing - nav_h,
        )
        viewer.sync_viewport(max(200.0, float(avail_w)), plan_h)

    def _sync_compose_plan_viewport_width(self, avail: float) -> None:
        avail_h = self._compose_column_avail_height()
        if avail_h <= 0:
            avail_h = plan_picture_viewer._FOCUS_MIN_VIEWPORT_H + 48.0
        self._sync_compose_plan_viewport_size(avail, avail_h)

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
        wysiwyg_on = getattr(self, "_focus_view_mode", "wysiwyg") == "wysiwyg"
        want = (
            None
            if self._compose_plan_viewer_active() or wysiwyg_on
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
        if self._document_pdf_profile() == "plan":
            return True
        if self._compare_candidate_source != "pdf_original":
            return False
        vid_prof = self._pdf_profile_for_version(self._compare_snapshot_version_id)
        if vid_prof == "plan":
            return True
        from iterthink.services.plan_text_extract import is_plan_stub_markdown

        return is_plan_stub_markdown(self._compare_editor.value or "")

    def _ensure_plan_pdf_compare_active(self) -> bool:
        """Route History/Review to plan-PDF panes instead of stub-markdown paragraph diff."""
        if not self.current_path or self._document_pdf_profile() != "plan":
            return False
        if self._compare_candidate_source in (
            CompareCandidateSource.SPELL_PREVIEW,
            CompareCandidateSource.DOCX_ORIGINAL,
            CompareCandidateSource.IFC_ORIGINAL,
        ):
            return False
        if self._compare_candidate_source == CompareCandidateSource.PDF_ORIGINAL:
            return True
        with session_scope() as s:
            latest = content_repo.latest_pdf_version_for_document(s, self.current_path.resolve())
        if latest is None:
            return False
        vid, _rel = latest
        self._compare_snapshot_version_id = vid
        self._compare_pdf_peer_snapshot_id = vid
        self._pending_ai_accept_action_id = None
        self._compare_candidate_source = CompareCandidateSource.PDF_ORIGINAL
        with session_scope() as s:
            self._compare_editor.value = content_repo.load_version_body(s, vid)
        self._apply_plan_import_open_state()
        return True

    def _plan_pdf_version_count(self) -> int:
        if not self.current_path:
            return 0
        with session_scope() as s:
            pairs = content_repo.list_plan_pdf_version_options(s, self.current_path.resolve())
        return len(pairs)

    def _apply_plan_import_open_state(self, *, version_import: bool = False) -> None:
        """Focus + compare defaults after importing or selecting a plan PDF."""
        self._compose_plan_editor_collapsed = True
        self._compose_plan_show_labels = False
        self._plan_compare_show_labels = False
        if version_import:
            # Review after import version: show the plan viewer, not overlay diff.
            self._plan_layout_mode = "single"
        else:
            self._plan_layout_mode = "overlay"
            self._plan_overlay_defaults_set = True
        self._sync_plan_overlay_pane_visibility()
        self._sync_plan_filename_chrome()

    _PLAN_FOCUS_NAV_H = 40.0

    def _on_compare_plan_pdf_layer_size(self, e: ft.LayoutSizeChangeEvent) -> None:
        self._sync_plan_compare_focus_viewport(float(e.width), float(e.height), future=False)

    def _on_compare_plan_overlay_host_size(self, e: ft.LayoutSizeChangeEvent) -> None:
        self._sync_plan_compare_focus_viewport(float(e.width), float(e.height), future=False)

    def _on_future_plan_overlay_host_size(self, e: ft.LayoutSizeChangeEvent) -> None:
        self._sync_plan_compare_focus_viewport(float(e.width), float(e.height), future=True)

    def _on_future_plan_single_host_size(self, e: ft.LayoutSizeChangeEvent) -> None:
        self._sync_plan_compare_focus_viewport(float(e.width), float(e.height), future=True)

    def _on_future_plan_split_row_size(self, e: ft.LayoutSizeChangeEvent) -> None:
        self._sync_plan_compare_focus_viewport(float(e.width), float(e.height), future=True)

    def _sync_plan_focus_viewport_from_active_host(self, *, future: bool) -> None:
        """Best-effort viewport sync from the visible plan focus host after mount."""
        if future:
            if getattr(self, "_plan_overlay_mode", False):
                host = getattr(self, "_future_plan_overlay_host", None)
            elif getattr(self, "_plan_side_by_side_mode", False):
                host = getattr(self, "_future_pdf_split_row", None)
            elif self._review_plan_single_mode():
                host = getattr(self, "_future_plan_single_host", None)
            else:
                host = getattr(self, "_future_plan_single_host", None)
        elif getattr(self, "_plan_overlay_mode", False):
            host = getattr(self, "_compare_pdf_overlay_host", None)
        elif getattr(self, "_plan_side_by_side_mode", False):
            host = getattr(self, "_compare_pdf_split_row", None)
        else:
            host = getattr(self, "_compare_pdf_split_row", None)
        if host is None:
            return
        w = float(getattr(host, "width", 0) or 0)
        h = float(getattr(host, "height", 0) or 0)
        if w > 1.0 and h >= plan_picture_viewer._FOCUS_MIN_VIEWPORT_H:
            self._sync_plan_compare_focus_viewport(w, h, future=future)

    def _plan_focus_slots(self, *, future: bool) -> tuple[tuple[str, str], tuple[str, str], tuple[str, str]]:
        if future:
            return (
                ("_future_plan_focus_left_slot", "_future_plan_focus_left"),
                ("_future_plan_focus_right_slot", "_future_plan_focus_right"),
                ("_future_plan_overlay_focus_slot", "_future_plan_overlay_focus"),
            )
        return (
            ("_compare_plan_focus_left_slot", "_compare_plan_focus_left"),
            ("_compare_plan_focus_right_slot", "_compare_plan_focus_right"),
            ("_compare_plan_overlay_focus_slot", "_compare_plan_overlay_focus"),
        )

    def _future_plan_single_slot_pair(self) -> tuple[str, str]:
        return ("_future_plan_single_slot", "_future_plan_focus_single")

    def _review_plan_single_mode(self) -> bool:
        if self._main_tab_index != TAB_FUTURE:
            return False
        mode = getattr(self, "_plan_layout_mode", "overlay")
        if self._plan_pdf_version_count() < 2:
            mode = "single"
        return mode == "single" and not getattr(self, "_plan_overlay_mode", False)

    def _active_plan_focus_viewers(self, *, future: bool) -> list[tuple[plan_picture_viewer.PlanFocusViewer, int]]:
        left_s, right_s, overlay_s = self._plan_focus_slots(future=future)
        out: list[tuple[plan_picture_viewer.PlanFocusViewer, int]] = []
        if getattr(self, "_plan_overlay_mode", False):
            viewer = getattr(self, overlay_s[1], None)
            if viewer is not None:
                out.append((viewer, 1))
        elif getattr(self, "_plan_side_by_side_mode", False):
            pair = getattr(self, "_future_plan_side_by_side_pair", None)
            if pair is not None:
                out.append((pair.left, 2))
                out.append((pair.right, 2))
                return out
            for store in (left_s[1], right_s[1]):
                viewer = getattr(self, store, None)
                if viewer is not None:
                    out.append((viewer, 2))
        elif future and self._review_plan_single_mode():
            _, single_store = self._future_plan_single_slot_pair()
            viewer = getattr(self, single_store, None)
            if viewer is not None:
                out.append((viewer, 1))
        else:
            viewer = getattr(self, left_s[1], None)
            if viewer is not None:
                out.append((viewer, 1))
        return out

    def _sync_plan_compare_focus_viewport(
        self, layer_w: float, layer_h: float, *, future: bool
    ) -> None:
        active = self._active_plan_focus_viewers(future=future)
        if not active:
            return
        viewport_h = max(
            plan_picture_viewer._FOCUS_MIN_VIEWPORT_H,
            float(layer_h) - self._PLAN_FOCUS_NAV_H,
        )
        layer_w = max(200.0, float(layer_w))
        cols = active[0][1]
        spacing = 8.0 if cols > 1 else 0.0
        col_w = max(200.0, (layer_w - spacing) / cols)
        for viewer, _ in active:
            if bool(getattr(viewer, "_viewer_interacting", False)):
                continue
            if (
                viewer._viewport_w > 0
                and abs(col_w - viewer._viewport_w) <= 0.5
                and abs(viewport_h - viewer._viewport_h) <= 0.5
                and float(viewer._image.width or 0) > 0
            ):
                continue
            viewer.sync_viewport(col_w, viewport_h)

    def _clear_plan_focus_pane(self, slot: ft.Container, store_name: str) -> None:
        setattr(self, store_name, None)
        slot.content = None

    def _mount_plan_focus_viewer(
        self,
        slot: ft.Container,
        store_name: str,
        viewer: plan_picture_viewer.PlanFocusViewer,
        *,
        future: bool = False,
        annotatable: bool = False,
        defer_viewport_sync: bool = False,
    ) -> None:
        if future and annotatable:
            viewer._on_place_comment = self._on_review_plan_place_comment
            viewer._on_revision_cloud = self._on_review_plan_revision_cloud
            orig_page_change = viewer._on_page_change

            def _on_page(ix: int) -> None:
                if orig_page_change is not None:
                    orig_page_change(ix)
                self._refresh_review_plan_annotations_overlay()

            viewer._on_page_change = _on_page
        setattr(self, store_name, viewer)
        slot.content = viewer.root
        pg = getattr(self, "page", None)
        if pg is not None and not defer_viewport_sync:
            pg.run_task(viewer.ensure_viewport_sync)
        if future and annotatable:
            self._refresh_review_plan_annotations_overlay()
            if hasattr(self, "_sync_plan_review_comment_nav_btn"):
                self._sync_plan_review_comment_nav_btn()
        if hasattr(self, "_sync_plan_compare_labels_nav_btn"):
            self._sync_plan_compare_labels_nav_btn()

    def _mount_plan_focus_message(
        self, slot: ft.Container, store_name: str, message: str
    ) -> None:
        self._clear_plan_focus_pane(slot, store_name)
        slot.content = ft.Container(
            padding=ft.padding.all(12),
            content=ft.Text(message, color=ft.Colors.ORANGE_200, size=13),
            expand=True,
        )

    def _clear_plan_focus_context(self, *, future: bool) -> None:
        for slot_name, store_name in self._plan_focus_slots(future=future):
            slot = getattr(self, slot_name, None)
            if slot is not None:
                self._clear_plan_focus_pane(slot, store_name)
        if future:
            single_slot_name, single_store = self._future_plan_single_slot_pair()
            single_slot = getattr(self, single_slot_name, None)
            if single_slot is not None:
                self._clear_plan_focus_pane(single_slot, single_store)

    def _plan_compare_label_options(
        self,
        base: tuple[int, str] | None,
        cand: tuple[int, str] | None,
    ) -> tuple[list, bool, bool]:
        show_labels = bool(getattr(self, "_plan_compare_show_labels", False))
        doc_path = self.current_path.resolve() if self.current_path else None
        base_vid = int(base[0]) if base is not None else None
        cand_vid = int(cand[0]) if cand is not None else None
        text_changes: list = []
        if doc_path is not None:
            text_changes = self._plan_text_changes_for_versions(doc_path, base_vid, cand_vid)
            self._plan_compare_text_changes = list(text_changes)
        pg = getattr(self, "page", None)
        return text_changes, show_labels, plan_hover_enabled(pg)

    @staticmethod
    def _plan_raw_pages_blocking(pdf_abs: Path) -> list[Path]:
        return document_import.render_pdf_to_png_pages(pdf_abs, pdf_profile="plan")

    def _plan_text_changes_for_versions(
        self,
        doc_path: Path,
        base_vid: int | None,
        cand_vid: int | None,
    ) -> list:
        if base_vid is None or cand_vid is None:
            return []
        base_geo = load_plan_text_sidecar(doc_path, base_vid) or {"pages": []}
        cand_geo = load_plan_text_sidecar(doc_path, cand_vid) or {"pages": []}
        if not base_geo.get("pages") and not cand_geo.get("pages"):
            return []
        if int(base_vid) == int(cand_vid):
            return geometry_to_label_views(cand_geo)
        return diff_plan_geometry(base_geo, cand_geo)

    def _compose_plan_text_changes(
        self,
        doc_path: Path,
        vid: int,
        geometry: dict,
    ) -> list:
        with session_scope() as s:
            pairs = content_repo.list_plan_pdf_version_options(s, doc_path)
        if len(pairs) >= 2 and int(pairs[0][0]) == int(vid):
            prev_vid = int(pairs[1][0])
            return self._plan_text_changes_for_versions(doc_path, prev_vid, vid)
        if geometry.get("pages"):
            return geometry_to_label_views(geometry)
        return []

    @staticmethod
    def _plan_display_pages_blocking(
        pdf_abs: Path,
        doc_path: Path,
        version_id: int,
        *,
        show_labels: bool,
    ) -> list[Path]:
        del doc_path, version_id, show_labels
        return document_import.render_pdf_to_png_pages(pdf_abs, pdf_profile="plan")

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
        if getattr(self, "_focus_view_mode", "wysiwyg") == "wysiwyg":
            return
        writing_slot = getattr(self, "_compose_writing_slot", None)
        inner = getattr(self, "_compose_reading_inner", None)
        collapsed = bool(getattr(self, "_compose_plan_editor_collapsed", False))
        if not self._compose_plan_host.visible:
            if writing_slot is not None:
                writing_slot.content = self._compose_editor_shell_wrapped
                writing_slot.expand = True
                writing_slot.height = None
                writing_slot.visible = True
            if inner is not None:
                inner.spacing = 8
            self._compose_editor_shell_wrapped.visible = True
            self._compose_editor_shell_wrapped.expand = True
            self._compose_editor_shell_wrapped.height = None
            if writing_slot is not None and _ctrl_on_page(writing_slot):
                writing_slot.update()
            return
        self._compose_plan_host.expand = True
        if writing_slot is not None:
            writing_slot.content = self._compose_editor_shell_wrapped
            writing_slot.expand = False
            if collapsed:
                writing_slot.height = 0
                writing_slot.visible = False
                self._compose_editor_shell_wrapped.visible = False
                self._compose_editor_shell_wrapped.height = 0
            else:
                writing_slot.height = 220
                writing_slot.visible = True
                self._compose_editor_shell_wrapped.visible = True
                self._compose_editor_shell_wrapped.expand = False
                self._compose_editor_shell_wrapped.height = 220
        if inner is not None:
            inner.spacing = 0 if collapsed else 8
        if writing_slot is not None and _ctrl_on_page(writing_slot):
            writing_slot.update()
        if inner is not None and _ctrl_on_page(inner):
            inner.update()
        avail_w = self._compose_column_avail_width()
        avail_h = self._compose_column_avail_height()
        if avail_w > 0 and avail_h > 0:
            self._sync_compose_plan_viewport_size(avail_w, avail_h)

    def _release_pdf_compare_disk_refs(self) -> None:
        """Drop rendered PDF page controls (``Image`` src may reference store/cache paths)."""
        self._clear_plan_focus_context(future=False)
        self._clear_plan_focus_context(future=True)
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

    def _detach_pdf_import_ui_for_store_delete(self) -> None:
        """Release viewers before ``purge_document_store_dirs`` removes PDF assets under STORE."""
        self._compare_pdf_peer_snapshot_id = None
        if hasattr(self, "_pending_post_import_history_vid"):
            self._pending_post_import_history_vid = None
        self._compare_candidate_source = "draft"
        self._cancel_and_teardown_compose_plan_viewer()
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
        if hasattr(self, "_is_plan_pdf_compare") and self._is_plan_pdf_compare():
            return False
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
        chrome = getattr(self, "_review_difference_chrome_row", None)
        if chrome is not None:
            on_diff = self._main_tab_index == TAB_FUTURE and sub == 0
            show_plan_pdf = show and self._is_plan_pdf_compare()
            chrome.visible = on_diff and not show_plan_pdf
            if _ctrl_on_page(chrome):
                chrome.update()
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

    def _release_compose_plan_viewer_refs(self) -> None:
        """Drop compose plan controls that reference PDF/PNG paths under STORE."""
        self._compose_plan_load_inflight_key = None
        self._compose_plan_surface_key = None
        self._compose_plan_focus_viewer = None
        self._compose_plan_document_id = None
        self._compose_plan_version_id = None
        host = getattr(self, "_compose_plan_host", None)
        if host is not None:
            host.content = None

    def _cancel_compose_plan_load(self) -> None:
        self._compose_plan_load_gen = int(getattr(self, "_compose_plan_load_gen", 0)) + 1

    def _cancel_and_teardown_compose_plan_viewer(self) -> None:
        """Abort async plan load and unmount viewer before store assets are removed."""
        self._cancel_compose_plan_load()
        self._release_compose_plan_viewer_refs()
        host = getattr(self, "_compose_plan_host", None)
        if host is not None:
            host.visible = False

    def _hide_compose_plan_surface(self) -> None:
        self._release_compose_plan_viewer_refs()
        self._compose_plan_host.visible = False
        writing_slot = getattr(self, "_compose_writing_slot", None)
        if writing_slot is not None:
            writing_slot.expand = True
            writing_slot.height = None
            writing_slot.visible = True
        inner = getattr(self, "_compose_reading_inner", None)
        if inner is not None:
            inner.spacing = 8
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
            if a.annotation_kind == plan_pdf_annotations.KIND_PIN:
                markers.append(
                    plan_picture_viewer.PlanMarkerView(
                        kind=a.annotation_kind,
                        page_index=int(a.plan_page_index),
                        norm_x=float(a.plan_norm_x or 0.5),
                        norm_y=float(a.plan_norm_y or 0.5),
                        bbox=None,
                    )
                )
            elif a.annotation_kind == plan_pdf_annotations.KIND_REVISION_CLOUD:
                bbox = a.cloud_bbox_norm()
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
        if hasattr(self, "_set_ki_comment_pick_mode"):
            self._set_ki_comment_pick_mode(False)
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
            pg.run_task(focus_viewer.ensure_viewport_sync)

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

        def _load_first_page() -> tuple[list[Path], int, dict, list]:
            total = document_import.count_pdf_pages(pdf_abs)
            pages = document_import.render_pdf_to_png_pages(
                pdf_abs, pdf_profile="plan", max_pages=1
            )
            geometry = load_plan_text_sidecar(doc_path, vid) or {"pages": []}
            text_changes = self._compose_plan_text_changes(doc_path, vid, geometry)
            return pages, total, geometry, text_changes

        try:
            base_pages, page_total, geometry, text_changes = await asyncio.to_thread(
                _load_first_page
            )
        except BaseException as ex:
            if gen != getattr(self, "_compose_plan_load_gen", 0):
                return
            self._hide_compose_plan_surface()
            self._snack(f"Could not load plan PDF: {ex}")
            return

        if gen != getattr(self, "_compose_plan_load_gen", 0):
            if getattr(self, "_compose_plan_load_inflight_key", None) == surface_key:
                self._compose_plan_load_inflight_key = None
            return
        if not base_pages:
            if getattr(self, "_compose_plan_load_inflight_key", None) == surface_key:
                self._compose_plan_load_inflight_key = None
            self._hide_compose_plan_surface()
            self._snack("Plan PDF has no pages.")
            return

        try:
            focus_viewer = plan_picture_viewer.build_plan_focus_viewer(
                base_pages,
                initial_page_index=page_ix,
                expected_page_count=page_total,
                on_page_change=self._on_compose_plan_viewer_page_changed,
            )
            pg = getattr(self, "page", None)
            focus_viewer.set_text_changes(
                text_changes,
                visible=show_labels and bool(text_changes),
                hover_enabled=plan_hover_enabled(pg),
            )
            self._apply_compose_plan_viewer(focus_viewer, surface_key=surface_key, page_ix=page_ix)
            if focus_viewer.page_count > 0:
                self.page.run_task(
                    self._show_compose_plan_page_async, self._compose_plan_page_index
                )
        except BaseException as ex:
            if getattr(self, "_compose_plan_load_inflight_key", None) == surface_key:
                self._compose_plan_load_inflight_key = None
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

        if page_total > len(base_pages):
            self.page.run_task(
                self._finish_compose_plan_pages_async,
                gen,
                pdf_abs,
                page_total,
                len(base_pages),
            )

    async def _finish_compose_plan_pages_async(
        self,
        gen: int,
        pdf_abs: Path,
        page_total: int,
        rendered_count: int,
    ) -> None:
        """Rasterize remaining plan pages after the first page is on screen."""
        if gen != getattr(self, "_compose_plan_load_gen", 0):
            return
        import_progress = getattr(self, "_import_plan_progress", None)

        def _render_rest() -> list[Path]:
            return document_import.render_pdf_to_png_pages(pdf_abs, pdf_profile="plan")

        try:
            if import_progress is not None:
                await import_progress.set_message(
                    f"Rendering page {rendered_count + 1}/{page_total}…"
                )
            all_pages = await asyncio.to_thread(_render_rest)
        except BaseException as ex:
            if gen == getattr(self, "_compose_plan_load_gen", 0):
                self._snack(f"Could not finish rendering plan pages: {ex}")
            return

        if gen != getattr(self, "_compose_plan_load_gen", 0):
            return
        viewer = getattr(self, "_compose_plan_focus_viewer", None)
        if viewer is None:
            return
        if len(all_pages) > rendered_count:
            viewer.append_rendered_pages(all_pages[rendered_count:])
        viewer.set_expected_page_count(page_total)
        if import_progress is not None:
            await import_progress.set_message(f"Rendered {page_total} pages.")

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
            pc.baseline_dd.disabled = not show_bar
            pc.candidate_dd.disabled = not show_bar
        hist_show = show_bar and self._main_tab_index == TAB_HISTORY
        self._plan_compare.set_bar_visible(hist_show)
        fut_pc = getattr(self, "_plan_compare_future", None)
        if fut_pc is not None:
            fut_show = show_bar and self._main_tab_index == TAB_FUTURE
            fut_pc.set_bar_visible(fut_show)
        if is_plan and show_bar and not getattr(self, "_plan_overlay_defaults_set", False):
            self._plan_layout_mode = "overlay"
            self._plan_overlay_defaults_set = True
        self._sync_plan_overlay_pane_visibility()
        self._sync_plan_filename_chrome()
        self._sync_plan_compare_labels_nav_btn()
        for pc in self._plan_compare_panels():
            if _ctrl_on_page(pc.baseline_dd):
                pc.baseline_dd.update()
            if _ctrl_on_page(pc.candidate_dd):
                pc.candidate_dd.update()

    def _active_plan_compare_panel(self):
        if self._main_tab_index == TAB_FUTURE:
            return getattr(self, "_plan_compare_future", None) or self._plan_compare
        return self._plan_compare

    def _is_review_text_compare(self) -> bool:
        return (
            self._main_tab_index == TAB_FUTURE
            and int(getattr(self, "_review_subtab_index", 0)) == 0
            and self._compare_candidate_source != CompareCandidateSource.PDF_ORIGINAL
        )

    def _review_text_single_mode(self) -> bool:
        if not self._is_review_text_compare():
            return False
        mode = getattr(self, "_plan_layout_mode", "side_by_side")
        if mode not in _TEXT_LAYOUT_MODES:
            mode = "side_by_side"
        return mode == "single"

    def _active_compare_layout_order(self) -> tuple[str, ...]:
        if self._is_review_text_compare():
            return _TEXT_LAYOUT_ORDER
        return _PLAN_LAYOUT_ORDER

    def _layout_mode_meta(self, mode: str) -> tuple[str, str]:
        if self._is_review_text_compare():
            return _TEXT_LAYOUT_META.get(mode, _TEXT_LAYOUT_META["side_by_side"])
        return _PLAN_LAYOUT_META.get(mode, _PLAN_LAYOUT_META["overlay"])

    def _compare_layout_multi(self) -> bool:
        if self._is_review_text_compare():
            return True
        return self._plan_pdf_version_count() >= 2 if self.current_path else False

    def _normalized_compare_layout_mode(self) -> str:
        mode = getattr(self, "_plan_layout_mode", "overlay")
        if self._is_review_text_compare():
            if mode not in _TEXT_LAYOUT_MODES:
                mode = "side_by_side"
                self._plan_layout_mode = mode
            return mode
        multi = self._plan_pdf_version_count() >= 2 if self.current_path else False
        if not multi:
            mode = "single"
            self._plan_layout_mode = "single"
        return mode

    def _plan_layout_chrome_active(self) -> bool:
        if self._is_review_text_compare():
            return True
        return (
            self._is_plan_pdf_compare()
            and self._main_tab_index in (TAB_HISTORY, TAB_FUTURE)
        )

    def _review_plan_comment_nav_host(
        self,
    ) -> plan_picture_viewer.PlanFocusViewer | plan_picture_viewer.PlanFocusPairViewer | None:
        if not self._review_plan_comment_placement_enabled():
            return None
        if getattr(self, "_plan_side_by_side_mode", False):
            return getattr(self, "_future_plan_side_by_side_pair", None)
        return self._review_plan_comment_viewer()

    def _sync_plan_review_comment_nav_btn(self) -> None:
        """Attach Review place-comment control to the active plan viewer bottom nav."""
        btn = getattr(self, "_plan_review_comment_btn", None)
        if btn is None:
            return
        show = self._plan_layout_chrome_active() and self._main_tab_index == TAB_FUTURE
        enabled = show and self._review_plan_comment_placement_enabled()
        nav_host = self._review_plan_comment_nav_host() if show else None
        prev_host = getattr(self, "_plan_comment_nav_host", None)
        if prev_host is not None and prev_host is not nav_host:
            prev_host.set_nav_trailing([])
        if nav_host is not None and enabled:
            nav_host.set_nav_trailing([btn])
            self._plan_comment_nav_host = nav_host
            btn.visible = True
            btn.disabled = False
        else:
            if prev_host is not None:
                prev_host.set_nav_trailing([])
            self._plan_comment_nav_host = None
            btn.visible = False
            btn.disabled = True
        if _ctrl_on_page(btn):
            btn.update()

    def _plan_compare_labels_nav_host(
        self,
    ) -> plan_picture_viewer.PlanFocusViewer | plan_picture_viewer.PlanFocusPairViewer | None:
        if not self._is_plan_pdf_compare():
            return None
        future = self._main_tab_index == TAB_FUTURE
        if getattr(self, "_plan_side_by_side_mode", False):
            if future:
                return getattr(self, "_future_plan_side_by_side_pair", None)
            return None
        if getattr(self, "_plan_overlay_mode", False):
            _, overlay_store = self._plan_focus_slots(future=future)[2]
            return getattr(self, overlay_store, None)
        if future and self._review_plan_single_mode():
            _, single_store = self._future_plan_single_slot_pair()
            return getattr(self, single_store, None)
        left_store = self._plan_focus_slots(future=future)[0][1]
        return getattr(self, left_store, None)

    def _sync_plan_compare_labels_nav_btn(self) -> None:
        """Attach extracted-text toggle to the active plan compare bottom nav."""
        btn = getattr(self, "_plan_compare_labels_btn", None)
        if btn is None:
            return
        show = self._plan_layout_chrome_active() and self._is_plan_pdf_compare()
        enabled = show and self._plan_pdf_version_count() >= 2
        active = bool(getattr(self, "_plan_compare_show_labels", False))
        btn.icon_color = config.PRIMARY_COLOR if active else config.ON_SURFACE_VARIANT
        prev_hosts: list = list(getattr(self, "_plan_compare_labels_nav_hosts", []) or [])
        for host in prev_hosts:
            host.set_nav_trailing([])
        hosts: list = []
        if enabled:
            nav_host = self._plan_compare_labels_nav_host()
            if nav_host is not None:
                nav_host.set_nav_trailing([btn])
                hosts.append(nav_host)
            elif getattr(self, "_plan_side_by_side_mode", False):
                future = self._main_tab_index == TAB_FUTURE
                right_store = self._plan_focus_slots(future=future)[1][1]
                right_viewer = getattr(self, right_store, None)
                if right_viewer is not None and hasattr(right_viewer, "set_nav_trailing"):
                    right_viewer.set_nav_trailing([btn])
                    hosts.append(right_viewer)
            btn.visible = True
            btn.disabled = False
        else:
            btn.visible = False
            btn.disabled = True
        self._plan_compare_labels_nav_hosts = hosts
        if _ctrl_on_page(btn):
            btn.update()

    def _on_plan_compare_toggle_labels(self, _e: ft.ControlEvent) -> None:
        self._plan_compare_show_labels = not bool(
            getattr(self, "_plan_compare_show_labels", False)
        )
        self._sync_plan_compare_labels_nav_btn()
        self._refresh_plan_compare_text_overlays()

    def _refresh_plan_compare_text_overlays(self) -> None:
        if not self._is_plan_pdf_compare() or self._plan_pdf_version_count() < 2:
            return
        show = bool(getattr(self, "_plan_compare_show_labels", False))
        changes = list(getattr(self, "_plan_compare_text_changes", []) or []) if show else []
        hover = plan_hover_enabled(getattr(self, "page", None))
        future = self._main_tab_index == TAB_FUTURE
        if getattr(self, "_plan_side_by_side_mode", False):
            pair = getattr(self, "_future_plan_side_by_side_pair", None)
            if pair is not None:
                pair.left.set_text_changes(
                    changes,
                    overlay_mode="baseline",
                    visible=bool(changes),
                    hover_enabled=hover,
                )
                pair.right.set_text_changes(
                    changes,
                    overlay_mode="candidate",
                    visible=bool(changes),
                    hover_enabled=hover,
                )
                return
            left_store, right_store = (
                self._plan_focus_slots(future=future)[0][1],
                self._plan_focus_slots(future=future)[1][1],
            )
            left_viewer = getattr(self, left_store, None)
            right_viewer = getattr(self, right_store, None)
            if left_viewer is not None:
                left_viewer.set_text_changes(
                    changes,
                    overlay_mode="baseline",
                    visible=bool(changes),
                    hover_enabled=hover,
                )
            if right_viewer is not None:
                right_viewer.set_text_changes(
                    changes,
                    overlay_mode="candidate",
                    visible=bool(changes),
                    hover_enabled=hover,
                )
            return
        for viewer, _cols in self._active_plan_focus_viewers(future=future):
            viewer.set_text_changes(
                changes,
                overlay_mode="candidate",
                visible=bool(changes),
                hover_enabled=hover,
            )

    def _plan_layout_menu_icon(self, mode: str) -> str:
        icon_name, _label = self._layout_mode_meta(mode)
        if icon_name == "__side_by_side__":
            return ft.Icons.VIEW_COLUMN
        return getattr(ft.Icons, icon_name, ft.Icons.LAYERS_OUTLINED)

    def _plan_layout_mode_icon_control(self, layout_mode: str, *, size: int = 16) -> ft.Control:
        icon_name, _ = self._layout_mode_meta(layout_mode)
        if icon_name == "__side_by_side__":
            return plan_picture_viewer.build_plan_side_by_side_icon(size=size)
        return ft.Icon(
            getattr(ft.Icons, icon_name, ft.Icons.LAYERS_OUTLINED),
            size=size,
            color=config.ON_SURFACE_VARIANT,
        )

    def _build_plan_layout_menu_items(self, mode: str, *, multi: bool) -> list[ft.PopupMenuItem]:
        items: list[ft.PopupMenuItem] = []
        for layout_mode in self._active_compare_layout_order():
            if not multi and layout_mode != "single":
                continue
            _icon_name, label = self._layout_mode_meta(layout_mode)
            active = layout_mode == mode
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.CHECK,
                                size=16,
                                color=config.PRIMARY_COLOR if active else ft.Colors.TRANSPARENT,
                            ),
                            self._plan_layout_mode_icon_control(layout_mode, size=16),
                            ft.Text(label, size=13),
                        ],
                        spacing=8,
                        tight=True,
                    ),
                    on_click=lambda _e, m=layout_mode: self._set_plan_layout_mode(
                        m, user_chosen=True
                    ),
                )
            )
        return items

    def _sync_plan_layout_menu_btn(self, *, multi: bool | None = None) -> None:
        btn = getattr(self, "_plan_layout_menu_btn", None)
        if btn is None:
            return
        if multi is None:
            multi = self._compare_layout_multi()
        mode = self._normalized_compare_layout_mode()
        if not multi:
            mode = "single"
            self._plan_layout_mode = "single"
        _icon_name, label = self._layout_mode_meta(mode)
        if mode == "side_by_side":
            btn.icon = None
            btn.content = plan_picture_viewer.build_plan_side_by_side_icon(size=18)
        else:
            btn.content = None
            btn.icon = self._plan_layout_menu_icon(mode)
        btn.tooltip = label
        btn.items = self._build_plan_layout_menu_items(mode, multi=multi)
        if _ctrl_on_page(btn):
            btn.update()

    def _sync_plan_compare_baseline_chrome(self) -> None:
        fut_pc = getattr(self, "_plan_compare_future", None)
        if fut_pc is None:
            return
        hide = self._review_plan_single_mode()
        for ctrl in (
            getattr(fut_pc, "baseline_label", None),
            getattr(fut_pc, "baseline_wrap", None),
        ):
            if ctrl is not None:
                ctrl.visible = not hide
                if _ctrl_on_page(ctrl):
                    ctrl.update()

    def _sync_review_text_layout_chrome(self) -> None:
        hide_current = self._review_text_single_mode()
        col = getattr(self, "_review_baseline_chrome_col", None)
        if col is not None:
            col.visible = not hide_current
            if _ctrl_on_page(col):
                col.update()

    def _sync_plan_filename_chrome(self) -> None:
        show_layout = self._plan_layout_chrome_active()
        multi = self._compare_layout_multi()
        menu_btn = getattr(self, "_plan_layout_menu_btn", None)
        if menu_btn is not None:
            menu_btn.visible = show_layout
            if show_layout:
                self._sync_plan_layout_menu_btn(multi=multi)
            elif _ctrl_on_page(menu_btn):
                menu_btn.update()
        impact_btn = getattr(self, "_plan_region_impact_btn", None)
        if impact_btn is not None:
            impact_enabled = (
                show_layout
                and self._main_tab_index == TAB_FUTURE
                and self._review_plan_change_regions_enabled()
            )
            impact_btn.visible = impact_enabled
            impact_btn.disabled = not impact_enabled
            if _ctrl_on_page(impact_btn):
                impact_btn.update()
        self._sync_plan_compare_baseline_chrome()
        self._sync_review_text_layout_chrome()
        self._sync_plan_review_comment_nav_btn()

    def _set_plan_layout_mode(
        self, mode: str, *, rebuild: bool = True, user_chosen: bool = False
    ) -> None:
        if self._is_review_text_compare():
            if mode not in _TEXT_LAYOUT_MODES:
                mode = "side_by_side"
            self._plan_layout_mode = mode
            if user_chosen:
                self._text_review_user_layout_mode = mode
            self._sync_plan_layout_menu_btn()
            self._sync_plan_filename_chrome()
            if rebuild and hasattr(self, "_rebuild_future_paragraph_ui"):
                self._rebuild_future_paragraph_ui()
            return
        if mode not in _PLAN_LAYOUT_MODES:
            mode = "overlay"
        multi = self._plan_pdf_version_count() >= 2
        if not multi:
            mode = "single"
        self._plan_layout_mode = mode
        self._sync_plan_layout_menu_btn(multi=multi)
        self._sync_plan_overlay_pane_visibility()
        if mode == "overlay":
            self._reset_review_plan_annotation_tool_modes()
        self._sync_plan_filename_chrome()
        if hasattr(self, "_sync_plan_compare_labels_nav_btn"):
            self._sync_plan_compare_labels_nav_btn()
        if not rebuild:
            return
        pg = getattr(self, "page", None)
        if pg is None:
            return
        if self._main_tab_index == TAB_HISTORY:
            pg.run_task(self._rebuild_compare_plan_pdf_panes_async)
        elif self._main_tab_index == TAB_FUTURE:
            pg.run_task(self._rebuild_future_plan_pdf_panes_async)

    def _review_plan_annotations_enabled(self) -> bool:
        if self._main_tab_index != TAB_FUTURE or not self._is_plan_pdf_compare():
            return False
        mode = getattr(self, "_plan_layout_mode", "overlay")
        if self._plan_pdf_version_count() < 2:
            mode = "single"
        return mode in ("single", "side_by_side")

    def _review_plan_change_regions_enabled(self) -> bool:
        if self._main_tab_index != TAB_FUTURE or not self._is_plan_pdf_compare():
            return False
        if self._plan_pdf_version_count() < 2:
            return False
        # Region/impact overlay disabled in Review until stable across all layout
        # modes (overlay, single, side-by-side). The boxes fight page nav and zoom.
        return False

    @staticmethod
    def _snapshot_plan_page_paths(
        paths: list[Path],
        *,
        mount_key: int,
        prefix: str = "page",
        label: str = "",
        start_index: int = 0,
        mount_dir: Path | None = None,
    ) -> list[Path]:
        """Copy plan PNGs to a viewer-local dir so cache rebuilds cannot unlink active src."""
        if not paths:
            return []
        if mount_dir is None:
            dir_name = f"viewer_{int(mount_key)}"
            if label:
                dir_name = f"{dir_name}_{label}"
            mount_dir = paths[0].parent / dir_name
        mount_dir.mkdir(parents=True, exist_ok=True)
        stable: list[Path] = []
        for i, src in enumerate(paths):
            dest = mount_dir / f"{prefix}_{start_index + i + 1:04d}.png"
            if (
                not dest.is_file()
                or dest.stat().st_mtime_ns < src.stat().st_mtime_ns
                or dest.stat().st_size != src.stat().st_size
            ):
                shutil.copy2(src, dest)
            stable.append(dest)
        return stable

    @staticmethod
    def _snapshot_plan_overlay_paths(paths: list[Path], *, mount_key: int) -> list[Path]:
        return MarkdownStudioAssetCompare._snapshot_plan_page_paths(
            paths, mount_key=mount_key, prefix="overlay"
        )

    def _clear_stale_plan_slots_for_single(self, *, future: bool) -> None:
        left_slot_name, left_store = self._plan_focus_slots(future=future)[0]
        right_slot_name, right_store = self._plan_focus_slots(future=future)[1]
        overlay_slot_name, overlay_store = self._plan_focus_slots(future=future)[2]
        for slot_name, store_name in (
            (left_slot_name, left_store),
            (right_slot_name, right_store),
            (overlay_slot_name, overlay_store),
        ):
            slot = getattr(self, slot_name, None)
            if slot is not None:
                self._clear_plan_focus_pane(slot, store_name)

    def _clear_stale_plan_slots_for_side_by_side(self, *, future: bool) -> None:
        overlay_slot_name, overlay_store = self._plan_focus_slots(future=future)[2]
        overlay_slot = getattr(self, overlay_slot_name, None)
        if overlay_slot is not None:
            self._clear_plan_focus_pane(overlay_slot, overlay_store)
        if future:
            single_slot_name, single_store = self._future_plan_single_slot_pair()
            single_slot = getattr(self, single_slot_name, None)
            if single_slot is not None:
                self._clear_plan_focus_pane(single_slot, single_store)
            pair_slot = getattr(self, "_future_plan_side_by_side_slot", None)
            if pair_slot is not None:
                setattr(self, "_future_plan_side_by_side_pair", None)
                setattr(self, "_future_plan_focus_left", None)
                setattr(self, "_future_plan_focus_right", None)
                pair_slot.content = None

    def _plan_region_action_factory(
        self, region: object
    ) -> PlanRegionActionHandlers:
        from iterthink.services.plan_change_regions import PlanChangeRegionView

        assert isinstance(region, PlanChangeRegionView)
        pg = getattr(self, "page", None)
        rid = int(region.region_id)
        pi = int(region.paragraph_index)

        def _run_task(coro_fn, *args: object) -> None:
            if pg is not None:
                pg.run_task(coro_fn, *args)

        return PlanRegionActionHandlers(
            on_approve=lambda: _run_task(self._on_plan_region_approve_async, rid),
            on_reject=lambda: _run_task(self._on_plan_region_reject_async, rid),
            on_comment=lambda: _run_task(
                self._open_ki_comments_for_paragraph_async, pi, False
            ),
            on_act=lambda: _run_task(self._on_plan_region_act_async, rid),
        )

    async def _reload_review_change_regions_from_db_async(self) -> None:
        if not self._review_plan_change_regions_enabled():
            await self._apply_change_regions_to_all_review_viewers_async([])
            return
        cand = self._compare_resolve_pdf_asset_right()
        if cand is None:
            await self._apply_change_regions_to_all_review_viewers_async([])
            return
        cand_vid, _rel = cand
        with session_scope() as s:
            anns = plan_pdf_annotations.list_change_regions_for_version(
                s, content_version_id=int(cand_vid)
            )
            views = plan_pdf_annotations.annotations_to_region_views(anns)
        await self._apply_change_regions_to_all_review_viewers_async(views)

    async def _apply_change_regions_to_all_review_viewers_async(
        self, views: list
    ) -> None:
        if not self._review_plan_change_regions_enabled():
            return
        factory = self._plan_region_action_factory
        action_factory = factory if views else None

        if getattr(self, "_plan_side_by_side_mode", False):
            left_store = self._plan_focus_slots(future=True)[0][1]
            right_store = self._plan_focus_slots(future=True)[1][1]
            left_viewer = getattr(self, left_store, None)
            right_viewer = getattr(self, right_store, None)
            if left_viewer is not None:
                left_viewer.set_change_regions([])
                if _ctrl_on_page(left_viewer.root):
                    left_viewer.root.update()
            if right_viewer is not None:
                await right_viewer.ensure_viewport_sync()
                right_viewer.set_change_regions(views, action_factory=action_factory)
                if _ctrl_on_page(right_viewer.root):
                    right_viewer.root.update()
            return

        if self._review_plan_single_mode():
            _, single_store = self._future_plan_single_slot_pair()
            viewer = getattr(self, single_store, None)
            if viewer is not None:
                await viewer.ensure_viewport_sync()
                viewer.set_change_regions(views, action_factory=action_factory)
                if _ctrl_on_page(viewer.root):
                    viewer.root.update()
            return

        for viewer, _cols in self._active_plan_focus_viewers(future=True):
            await viewer.ensure_viewport_sync()
            viewer.set_change_regions(views, action_factory=action_factory)
            if _ctrl_on_page(viewer.root):
                viewer.root.update()

    async def _sync_review_change_regions_async(
        self, *, snack_on_detect: bool = False
    ) -> None:
        if not self._review_plan_change_regions_enabled() or not self.current_path:
            await self._apply_change_regions_to_all_review_viewers_async([])
            return
        base = self._compare_resolve_pdf_asset_baseline()
        cand = self._compare_resolve_pdf_asset_right()
        if base is None or cand is None:
            await self._apply_change_regions_to_all_review_viewers_async([])
            return
        base_vid, _base_rel = base
        cand_vid, _cand_rel = cand
        if int(base_vid) == int(cand_vid):
            await self._apply_change_regions_to_all_review_viewers_async([])
            return
        doc_path = self.current_path.resolve()

        def _sync() -> tuple[int | None, list]:
            with session_scope() as s:
                doc = content_repo.get_document_by_resolved_path(s, doc_path)
                if doc is None:
                    return None, []
                anns = sync_detected_change_regions(
                    s,
                    doc_path=doc_path,
                    baseline_version_id=int(base_vid),
                    candidate_version_id=int(cand_vid),
                )
                views = plan_pdf_annotations.annotations_to_region_views(anns)
                return int(doc.id), views

        doc_id, views = await asyncio.to_thread(_sync)
        if doc_id is None:
            return
        self._review_plan_document_id = int(doc_id)
        self._review_plan_version_id = int(cand_vid)
        await self._apply_change_regions_to_all_review_viewers_async(views)
        if hasattr(self, "_rebuild_ki_comments_list"):
            self._rebuild_ki_comments_list()
        if snack_on_detect:
            active = sum(1 for v in views if not v.dismissed)
            if active:
                self._snack(f"Detected {active} changed area{'s' if active != 1 else ''}.")

    async def _on_plan_region_approve_async(self, region_id: int) -> None:
        with session_scope() as s:
            plan_pdf_annotations.update_change_region_flags(
                s, annotation_id=int(region_id), reviewed=True
            )
        await self._reload_review_change_regions_from_db_async()

    async def _on_plan_region_reject_async(self, region_id: int) -> None:
        with session_scope() as s:
            plan_pdf_annotations.update_change_region_flags(
                s, annotation_id=int(region_id), dismissed=True
            )
        await self._reload_review_change_regions_from_db_async()
        if hasattr(self, "_rebuild_ki_comments_list"):
            self._rebuild_ki_comments_list()

    async def _run_plan_region_impact_batch_async(self) -> None:
        await self._analyze_plan_regions_impact_async(region_ids=None)

    async def _on_plan_region_act_async(self, region_id: int) -> None:
        await self._analyze_plan_regions_impact_async(region_ids=[int(region_id)])

    async def _analyze_plan_regions_impact_async(
        self, *, region_ids: list[int] | None
    ) -> None:
        if not self._review_plan_change_regions_enabled() or not self.current_path:
            self._snack("Plan compare with change regions is not active.")
            return
        base = self._compare_resolve_pdf_asset_baseline()
        cand = self._compare_resolve_pdf_asset_right()
        if base is None or cand is None:
            self._snack("Select baseline and candidate plan versions.")
            return
        base_vid, _ = base
        cand_vid, _ = cand
        if int(base_vid) == int(cand_vid):
            self._snack("Baseline and candidate must differ.")
            return
        ollama = getattr(self, "ollama", None)
        if ollama is None:
            self._snack("Ollama client is not available.")
            return
        from iterthink.services.plan_region_impact_runner import analyze_plan_change_regions

        doc_path = self.current_path.resolve()
        self._snack("Analyzing region impact…")

        try:
            with session_scope() as s:
                results = await analyze_plan_change_regions(
                    ollama,
                    s,
                    doc_path=doc_path,
                    baseline_version_id=int(base_vid),
                    candidate_version_id=int(cand_vid),
                    region_ids=region_ids,
                )
        except BaseException as ex:
            self._snack(f"Region impact failed: {ex}")
            return
        if hasattr(self, "_rebuild_ki_comments_list"):
            self._rebuild_ki_comments_list()
        await self._reload_review_change_regions_from_db_async()
        if not results:
            self._snack("No regions analyzed.")
            return
        self._snack(f"Analyzed {len(results)} changed area{'s' if len(results) != 1 else ''}.")

    def _focus_review_plan_region(self, paragraph_index: int) -> None:
        if not self._review_plan_change_regions_enabled():
            return
        ki_ctx = self._active_plan_ki_context()
        if ki_ctx is None:
            return
        _doc_id, vid = ki_ctx
        with session_scope() as s:
            ann = plan_pdf_annotations.get_by_paragraph_index(
                s,
                content_version_id=int(vid),
                paragraph_index=int(paragraph_index),
            )
        if ann is None or ann.annotation_kind != plan_pdf_annotations.KIND_CHANGE_REGION:
            return
        page_ix = int(ann.plan_page_index)
        region_id = int(ann.id)
        for viewer, _cols in self._active_plan_focus_viewers(future=True):
            if int(viewer.current_index) != page_ix:
                viewer.set_page(page_ix)
            viewer.set_highlighted_region(region_id)

    def _review_plan_comment_placement_enabled(self) -> bool:
        return self._main_tab_index == TAB_FUTURE and self._is_plan_pdf_compare()

    def _review_plan_comment_viewer(
        self,
    ) -> plan_picture_viewer.PlanFocusViewer | None:
        """Active Review plan pane for pin placement (overlay, single, side-by-side)."""
        if not self._review_plan_comment_placement_enabled():
            return None
        if getattr(self, "_plan_overlay_mode", False):
            return getattr(self, "_future_plan_overlay_focus", None)
        if self._plan_side_by_side_mode:
            return getattr(self, "_future_plan_focus_right", None)
        if self._review_plan_single_mode():
            _, single_store = self._future_plan_single_slot_pair()
            return getattr(self, single_store, None)
        return getattr(self, "_future_plan_focus_left", None)

    def _review_plan_annotatable_viewer(
        self,
    ) -> plan_picture_viewer.PlanFocusViewer | None:
        if not self._review_plan_annotations_enabled():
            return None
        if self._plan_side_by_side_mode:
            return getattr(self, "_future_plan_focus_right", None)
        if self._review_plan_single_mode():
            _, single_store = self._future_plan_single_slot_pair()
            return getattr(self, single_store, None)
        return getattr(self, "_future_plan_focus_left", None)

    def _active_plan_ki_context(self) -> tuple[int, int] | None:
        """``(document_id, version_id)`` for plan pins/clouds in KI Comments."""
        if self._compose_plan_viewer_active():
            doc_id = getattr(self, "_compose_plan_document_id", None)
            vid = getattr(self, "_compose_plan_version_id", None)
            if doc_id is not None and vid is not None:
                return int(doc_id), int(vid)
            ctx = self._compose_plan_version_context()
            if ctx is not None:
                return ctx[0], ctx[1]
            return None
        if self._main_tab_index == TAB_FUTURE and self._is_plan_pdf_compare():
            doc_id = getattr(self, "_review_plan_document_id", None)
            vid = getattr(self, "_review_plan_version_id", None)
            if doc_id is not None and vid is not None:
                return int(doc_id), int(vid)
            ctx = self._review_plan_version_context()
            if ctx is not None:
                return ctx[0], ctx[1]
        return None

    def _review_plan_version_context(self) -> tuple[int, int, Path] | None:
        if not self.current_path:
            return None
        resolved = self._compare_resolve_pdf_asset_right()
        if resolved is None:
            return None
        vid, rel = resolved
        try:
            pdf_abs = content_repo.pdf_asset_abs_path(rel)
        except (ValueError, OSError):
            return None
        if not pdf_abs.is_file():
            return None
        with session_scope() as s:
            doc = content_repo.get_document_by_resolved_path(s, self.current_path.resolve())
            if doc is None:
                return None
            doc_id = int(doc.id)
        return doc_id, int(vid), pdf_abs

    def _refresh_review_plan_annotations_overlay(self) -> None:
        viewer = self._review_plan_comment_viewer()
        ctx = self._review_plan_version_context()
        if viewer is None or ctx is None:
            return
        _doc_id, vid, _pdf = ctx
        with session_scope() as s:
            anns = plan_pdf_annotations.list_for_plan_version(
                s, content_version_id=vid
            )
        markers: list[plan_picture_viewer.PlanMarkerView] = []
        for a in anns:
            if a.annotation_kind == plan_pdf_annotations.KIND_PIN:
                markers.append(
                    plan_picture_viewer.PlanMarkerView(
                        kind=a.annotation_kind,
                        page_index=int(a.plan_page_index),
                        norm_x=float(a.plan_norm_x or 0.5),
                        norm_y=float(a.plan_norm_y or 0.5),
                        bbox=None,
                    )
                )
            elif a.annotation_kind == plan_pdf_annotations.KIND_REVISION_CLOUD:
                bbox = a.cloud_bbox_norm()
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

    def _on_review_plan_place_comment(self, u: float, v: float) -> None:
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(self._review_plan_place_comment_async, float(u), float(v))

    async def _review_plan_place_comment_async(self, u: float, v: float) -> None:
        ctx = self._review_plan_version_context()
        viewer = self._review_plan_comment_viewer()
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
        self._review_plan_document_id = doc_id
        self._review_plan_version_id = vid
        if hasattr(self, "_set_ki_comment_pick_mode"):
            self._set_ki_comment_pick_mode(False)
        self._refresh_review_plan_annotations_overlay()
        if hasattr(self, "_rebuild_ki_comments_list"):
            self._rebuild_ki_comments_list()
        await self._open_ki_comments_for_paragraph_async(slot, True)

    def _on_review_plan_revision_cloud(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> None:
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(
                self._review_plan_revision_cloud_async,
                float(x0),
                float(y0),
                float(x1),
                float(y1),
            )

    async def _review_plan_revision_cloud_async(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> None:
        ctx = self._review_plan_version_context()
        viewer = self._review_plan_annotatable_viewer()
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
        self._review_plan_document_id = doc_id
        self._review_plan_version_id = vid
        self._refresh_review_plan_annotations_overlay()
        if hasattr(self, "_rebuild_ki_comments_list"):
            self._rebuild_ki_comments_list()
        self._reset_review_plan_annotation_tool_modes()

    def _reset_review_plan_annotation_tool_modes(self) -> None:
        if hasattr(self, "_set_ki_comment_pick_mode"):
            self._set_ki_comment_pick_mode(False)
            return
        viewer = self._review_plan_annotatable_viewer()
        if viewer is not None:
            viewer.set_interaction_mode("idle")
        comment_btn = getattr(self, "_plan_review_comment_btn", None)
        if comment_btn is not None:
            comment_btn.icon_color = config.ON_SURFACE_VARIANT
            if _ctrl_on_page(comment_btn):
                comment_btn.update()

    def _on_plan_review_comment_toggle(self, _e: ft.ControlEvent) -> None:
        if self._review_plan_comment_viewer() is None and (
            not hasattr(self, "_ki_comment_plan_viewer") or self._ki_comment_plan_viewer() is None
        ):
            return
        active = not getattr(self, "_ki_comment_pick_mode", False)
        if active:
            if not self.right_open:
                self.toggle_right()
            if hasattr(self, "_set_ki_topic"):
                self._set_ki_topic(KI_TOPIC_COMMENTS)
            self._set_ki_comment_pick_mode(True)
            self._snack("Click the plan to place a comment.")
        else:
            self._set_ki_comment_pick_mode(False)

    def _ensure_text_review_compare_layout_default(self) -> None:
        """Review markdown text defaults to side-by-side compare unless the user chose Single."""
        if not self._is_review_text_compare():
            return
        user_mode = getattr(self, "_text_review_user_layout_mode", None)
        target = user_mode if user_mode in _TEXT_LAYOUT_MODES else "side_by_side"
        if getattr(self, "_plan_layout_mode", "overlay") != target:
            self._set_plan_layout_mode(target, rebuild=False)

    def _sync_plan_overlay_pane_visibility(self) -> None:
        multi_version = self._plan_pdf_version_count() >= 2
        mode = getattr(self, "_plan_layout_mode", "overlay")
        plan_compare = self._is_plan_pdf_compare() or (
            self._compare_candidate_source == CompareCandidateSource.PDF_ORIGINAL
        )
        if not multi_version and plan_compare:
            mode = "single"
            self._plan_layout_mode = "single"
        elif self._is_review_text_compare():
            mode = self._normalized_compare_layout_mode()
        self._plan_overlay_mode = multi_version and mode == "overlay"
        self._plan_side_by_side_mode = multi_version and mode == "side_by_side"
        single_mode = mode == "single"
        right_col = getattr(self, "_compare_pdf_right_column", None)
        if right_col is not None:
            right_col.visible = self._plan_side_by_side_mode
        on_fut = self._main_tab_index == TAB_FUTURE
        fut_single_host = getattr(self, "_future_plan_single_host", None)
        if fut_single_host is not None:
            fut_single_host.visible = on_fut and single_mode
        fut_host = getattr(self, "_future_plan_overlay_host", None)
        fut_split = getattr(self, "_future_pdf_split_row", None)
        if fut_host is not None:
            fut_host.visible = self._plan_overlay_mode and on_fut
        if fut_split is not None:
            fut_split.visible = on_fut and self._plan_side_by_side_mode
        hist_host = getattr(self, "_compare_pdf_overlay_host", None)
        split = getattr(self, "_compare_pdf_split_row", None)
        if hist_host is not None:
            hist_host.visible = self._plan_overlay_mode and self._main_tab_index == TAB_HISTORY
        if split is not None:
            split.visible = self._main_tab_index == TAB_HISTORY and not self._plan_overlay_mode

    async def _on_plan_pdf_baseline_async(self, _e: ft.ControlEvent | None = None) -> None:
        if self._plan_overlay_mode:
            await self._refresh_plan_overlay_async()
        elif self._is_plan_pdf_compare():
            if self._main_tab_index == TAB_FUTURE:
                await self._rebuild_future_plan_pdf_panes_async()
            else:
                await self._rebuild_compare_plan_pdf_panes_async()
        else:
            self._rebuild_compare_pdf_panes()

    async def _on_plan_pdf_candidate_async(self, _e: ft.ControlEvent | None = None) -> None:
        if self._plan_overlay_mode:
            await self._refresh_plan_overlay_async()
        elif self._is_plan_pdf_compare():
            if self._main_tab_index == TAB_FUTURE:
                await self._rebuild_future_plan_pdf_panes_async()
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
        if gen != self._plan_overlay_gen or not self._plan_overlay_mode:
            return
        self._plan_overlay_confidences = confidences
        future = self._main_tab_index == TAB_FUTURE
        _, _, overlay_slot = self._plan_focus_slots(future=future)
        slot = getattr(self, overlay_slot[0])
        if not paths:
            # Only the current (non-stale) build may tear down the live overlay.
            if gen != self._plan_overlay_gen or not self._plan_overlay_mode:
                return
            self._snack("Visual diff failed; showing side-by-side.")
            self._set_plan_layout_mode("side_by_side")
            return
        display_paths = self._snapshot_plan_overlay_paths(paths, mount_key=gen)
        base = self._compare_resolve_pdf_asset_baseline()
        cand = self._compare_resolve_pdf_asset_right()
        text_changes, show_labels, hover = self._plan_compare_label_options(base, cand)
        viewer = plan_picture_viewer.build_plan_compare_focus_viewer(
            display_paths,
            text_changes=text_changes if show_labels else None,
            text_overlay_visible=show_labels,
            hover_enabled=hover,
        )
        self._mount_plan_focus_viewer(
            slot, overlay_slot[1], viewer, future=future, annotatable=future
        )
        self._sync_plan_focus_viewport_from_active_host(future=future)
        if confidences and min(confidences) < 0.35:
            warn = (warn + " " if warn else "") + "Weak alignment on some pages."
        if warn:
            self._snack(warn)
        if _ctrl_on_page(slot):
            slot.update()

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

    @staticmethod
    def _plan_first_page_load_blocking(pdf_abs: Path) -> tuple[list[Path], int]:
        total = document_import.count_pdf_pages(pdf_abs)
        pages = document_import.render_pdf_to_png_pages(
            pdf_abs, pdf_profile="plan", max_pages=1
        )
        return pages, total

    async def _plan_first_page_load_async(
        self, resolved: tuple[int, str] | None
    ) -> _PlanPageLoad:
        if resolved is None:
            return _PlanPageLoad(None, 0, "No PDF asset for this comparison.", None)
        pdf_abs = self._pdf_abs_for_resolved(resolved)
        if pdf_abs is None:
            return _PlanPageLoad(None, 0, "PDF file missing on disk.", None)
        try:
            pages, total = await asyncio.to_thread(
                self._plan_first_page_load_blocking, pdf_abs
            )
        except BaseException as ex:
            return _PlanPageLoad(None, 0, f"Could not render PDF: {ex}", pdf_abs)
        if not pages:
            return _PlanPageLoad(None, total, "PDF file missing on disk.", pdf_abs)
        return _PlanPageLoad(pages, total, None, pdf_abs)

    def _schedule_side_by_side_plan_finish(
        self,
        *,
        mount_gen: int,
        future: bool,
        base_load: _PlanPageLoad,
        cand_load: _PlanPageLoad,
        base_mount_dir: Path | None,
        cand_mount_dir: Path | None,
        base_rendered: int,
        cand_rendered: int,
    ) -> None:
        need_base = (
            base_load.pdf_abs is not None
            and base_mount_dir is not None
            and base_load.page_total > base_rendered
        )
        need_cand = (
            cand_load.pdf_abs is not None
            and cand_mount_dir is not None
            and cand_load.page_total > cand_rendered
        )
        if not need_base and not need_cand:
            return
        pg = getattr(self, "page", None)
        if pg is None:
            return
        pg.run_task(
            self._finish_side_by_side_plan_pages_async,
            mount_gen,
            future,
            base_load.pdf_abs if need_base else None,
            cand_load.pdf_abs if need_cand else None,
            base_mount_dir,
            cand_mount_dir,
            base_rendered,
            cand_rendered,
            base_load.page_total if need_base else 0,
            cand_load.page_total if need_cand else 0,
        )

    async def _finish_side_by_side_plan_pages_async(
        self,
        mount_gen: int,
        future: bool,
        base_pdf_abs: Path | None,
        cand_pdf_abs: Path | None,
        base_mount_dir: Path | None,
        cand_mount_dir: Path | None,
        base_rendered: int,
        cand_rendered: int,
        base_page_total: int,
        cand_page_total: int,
    ) -> None:
        if mount_gen != int(getattr(self, "_plan_viewer_mount_gen", 0)):
            return
        if not getattr(self, "_plan_side_by_side_mode", False):
            return

        async def _render_full(pdf_abs: Path | None) -> list[Path] | None:
            if pdf_abs is None:
                return None
            return await asyncio.to_thread(
                document_import.render_pdf_to_png_pages, pdf_abs, pdf_profile="plan"
            )

        base_all, cand_all = await asyncio.gather(
            _render_full(base_pdf_abs),
            _render_full(cand_pdf_abs),
        )
        if mount_gen != int(getattr(self, "_plan_viewer_mount_gen", 0)):
            return
        if not getattr(self, "_plan_side_by_side_mode", False):
            return

        if future:
            pair = getattr(self, "_future_plan_side_by_side_pair", None)
            left_viewer = pair.left if pair is not None else None
            right_viewer = pair.right if pair is not None else None
        else:
            _, left_store = self._plan_focus_slots(future=False)[0]
            _, right_store = self._plan_focus_slots(future=False)[1]
            left_viewer = getattr(self, left_store, None)
            right_viewer = getattr(self, right_store, None)

        if (
            base_all
            and base_mount_dir is not None
            and left_viewer is not None
            and base_rendered < len(base_all)
        ):
            extra = self._snapshot_plan_page_paths(
                base_all[base_rendered:],
                mount_key=mount_gen,
                mount_dir=base_mount_dir,
                start_index=base_rendered,
            )
            left_viewer.append_rendered_pages(extra)
            if base_page_total > 0:
                left_viewer.set_expected_page_count(base_page_total)

        if (
            cand_all
            and cand_mount_dir is not None
            and right_viewer is not None
            and cand_rendered < len(cand_all)
        ):
            extra = self._snapshot_plan_page_paths(
                cand_all[cand_rendered:],
                mount_key=mount_gen,
                mount_dir=cand_mount_dir,
                start_index=cand_rendered,
            )
            right_viewer.append_rendered_pages(extra)
            if cand_page_total > 0:
                right_viewer.set_expected_page_count(cand_page_total)

        if future and pair is not None:
            pair.controller.sync_nav_chrome()

    def _schedule_active_plan_viewport_sync(self, *, future: bool) -> None:
        pg = getattr(self, "page", None)
        if pg is None:
            return
        if future and getattr(self, "_plan_side_by_side_mode", False):
            pair = getattr(self, "_future_plan_side_by_side_pair", None)
            if pair is not None:
                pg.run_task(pair.ensure_viewport_sync)
                return
        for viewer, _ in self._active_plan_focus_viewers(future=future):
            pg.run_task(viewer.ensure_viewport_sync)

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

    async def _mount_plan_focus_side_by_side_async(
        self,
        *,
        future: bool,
        base: tuple[int, str] | None,
        cand: tuple[int, str] | None,
    ) -> None:
        self._clear_stale_plan_slots_for_side_by_side(future=future)
        if future:
            await self._mount_review_plan_side_by_side_pair_async(base=base, cand=cand)
            return
        left_slot_name, left_store = self._plan_focus_slots(future=future)[0]
        right_slot_name, right_store = self._plan_focus_slots(future=future)[1]
        left_slot = getattr(self, left_slot_name)
        right_slot = getattr(self, right_slot_name)
        base_load, cand_load = await asyncio.gather(
            self._plan_first_page_load_async(base),
            self._plan_first_page_load_async(cand),
        )
        mount_key = int(getattr(self, "_plan_viewer_mount_gen", 0))
        if base_load.paths is None and cand_load.paths is None:
            msg = base_load.error or cand_load.error or "No PDF to compare."
            self._mount_plan_focus_message(left_slot, left_store, msg)
            self._clear_plan_focus_pane(right_slot, right_store)
            return
        text_changes, show_labels, hover = self._plan_compare_label_options(base, cand)
        base_mount_dir: Path | None = None
        cand_mount_dir: Path | None = None
        if base_load.paths is None:
            self._mount_plan_focus_message(
                left_slot, left_store, base_load.error or "No PDF."
            )
        else:
            stable_base = self._snapshot_plan_page_paths(
                base_load.paths, mount_key=mount_key, label="base"
            )
            base_mount_dir = stable_base[0].parent if stable_base else None
            left_viewer = plan_picture_viewer.build_plan_compare_focus_viewer(
                stable_base,
                expected_page_count=base_load.page_total,
                text_changes=text_changes if show_labels else None,
                overlay_mode="baseline",
                text_overlay_visible=show_labels,
                hover_enabled=hover,
            )
            self._mount_plan_focus_viewer(
                left_slot,
                left_store,
                left_viewer,
                future=future,
                defer_viewport_sync=True,
            )
        if cand_load.paths is None:
            self._mount_plan_focus_message(
                right_slot, right_store, cand_load.error or "No PDF."
            )
        else:
            stable_cand = self._snapshot_plan_page_paths(
                cand_load.paths, mount_key=mount_key, label="cand"
            )
            cand_mount_dir = stable_cand[0].parent if stable_cand else None
            right_viewer = plan_picture_viewer.build_plan_compare_focus_viewer(
                stable_cand,
                expected_page_count=cand_load.page_total,
                text_changes=text_changes if show_labels else None,
                overlay_mode="candidate",
                text_overlay_visible=show_labels,
                hover_enabled=hover,
            )
            self._mount_plan_focus_viewer(
                right_slot,
                right_store,
                right_viewer,
                future=future,
                annotatable=future,
                defer_viewport_sync=True,
            )
        left_viewer = getattr(self, left_store, None)
        right_viewer = getattr(self, right_store, None)
        pg = getattr(self, "page", None)
        if pg is not None and left_viewer is not None and right_viewer is not None:
            plan_picture_viewer.wire_synced_focus_viewer_pair(
                left_viewer, right_viewer, pg
            )
        self._schedule_side_by_side_plan_finish(
            mount_gen=mount_key,
            future=False,
            base_load=base_load,
            cand_load=cand_load,
            base_mount_dir=base_mount_dir,
            cand_mount_dir=cand_mount_dir,
            base_rendered=len(base_load.paths or []),
            cand_rendered=len(cand_load.paths or []),
        )
        self._sync_plan_focus_viewport_from_active_host(future=future)

    async def _mount_review_plan_side_by_side_pair_async(
        self,
        *,
        base: tuple[int, str] | None,
        cand: tuple[int, str] | None,
    ) -> None:
        slot = getattr(self, "_future_plan_side_by_side_slot", None)
        base_load, cand_load = await asyncio.gather(
            self._plan_first_page_load_async(base),
            self._plan_first_page_load_async(cand),
        )
        mount_key = int(getattr(self, "_plan_viewer_mount_gen", 0))
        if base_load.paths is None and cand_load.paths is None:
            msg = base_load.error or cand_load.error or "No PDF to compare."
            if slot is not None:
                setattr(self, "_future_plan_side_by_side_pair", None)
                setattr(self, "_future_plan_focus_left", None)
                setattr(self, "_future_plan_focus_right", None)
                slot.content = ft.Container(
                    padding=ft.padding.all(12),
                    content=ft.Text(msg, color=ft.Colors.ORANGE_200, size=13),
                    expand=True,
                )
            return
        text_changes, show_labels, hover = self._plan_compare_label_options(base, cand)
        stable_base: list[Path] = []
        stable_cand: list[Path] = []
        base_mount_dir: Path | None = None
        cand_mount_dir: Path | None = None
        if base_load.paths is not None:
            stable_base = self._snapshot_plan_page_paths(
                base_load.paths, mount_key=mount_key, label="base"
            )
            base_mount_dir = stable_base[0].parent if stable_base else None
        if cand_load.paths is not None:
            stable_cand = self._snapshot_plan_page_paths(
                cand_load.paths, mount_key=mount_key, label="cand"
            )
            cand_mount_dir = stable_cand[0].parent if stable_cand else None

        def _on_pair_page(_ix: int) -> None:
            self._refresh_review_plan_annotations_overlay()

        pair = plan_picture_viewer.build_plan_side_by_side_pair(
            stable_base,
            stable_cand,
            left_expected_page_count=base_load.page_total if base_load.paths else None,
            right_expected_page_count=cand_load.page_total if cand_load.paths else None,
            left_text_changes=text_changes if show_labels else None,
            right_text_changes=text_changes if show_labels else None,
            left_overlay_mode="baseline",
            right_overlay_mode="candidate",
            text_overlay_visible=show_labels,
            hover_enabled=hover,
            on_page_change=_on_pair_page,
            page=getattr(self, "page", None),
        )
        pair.right._on_place_comment = self._on_review_plan_place_comment
        pair.right._on_revision_cloud = self._on_review_plan_revision_cloud
        setattr(self, "_future_plan_side_by_side_pair", pair)
        setattr(self, "_future_plan_focus_left", pair.left)
        setattr(self, "_future_plan_focus_right", pair.right)
        if slot is not None:
            slot.content = pair.root
        self._schedule_side_by_side_plan_finish(
            mount_gen=mount_key,
            future=True,
            base_load=base_load,
            cand_load=cand_load,
            base_mount_dir=base_mount_dir,
            cand_mount_dir=cand_mount_dir,
            base_rendered=len(base_load.paths or []),
            cand_rendered=len(cand_load.paths or []),
        )
        self._refresh_review_plan_annotations_overlay()
        if hasattr(self, "_sync_plan_review_comment_nav_btn"):
            self._sync_plan_review_comment_nav_btn()
        self._sync_plan_focus_viewport_from_active_host(future=True)

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
        self._clear_plan_focus_context(future=True)
        self._future_pdf_left_lv.controls.clear()
        self._future_pdf_right_lv.controls.clear()
        self._future_plan_focus_left_slot.content = self._future_pdf_left_lv
        self._future_plan_focus_right_slot.content = self._future_pdf_right_lv
        body = self.editor.value or ""
        self._compare_editor.value = body
        resolved = self._compare_resolve_pdf_asset_right()
        self._append_pdf_pages_to_left_list(self._future_pdf_left_lv, resolved, pdf_profile="text")
        self._append_markdown_to_right_list(self._future_pdf_right_lv, body, editable_right=True)
        for c in (
            self._future_plan_focus_left_slot,
            self._future_plan_focus_right_slot,
            self._future_pdf_left_lv,
            self._future_pdf_right_lv,
        ):
            if _ctrl_on_page(c):
                c.update()
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
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(self._rebuild_future_plan_pdf_panes_async)

    async def _rebuild_future_plan_pdf_panes_async(self) -> None:
        self._sync_plan_overlay_pane_visibility()
        self._refresh_plan_compare_bar()
        self._plan_viewer_mount_gen = int(getattr(self, "_plan_viewer_mount_gen", 0)) + 1
        if self._plan_overlay_mode and self._plan_pdf_version_count() >= 2:
            await self._refresh_plan_overlay_async()
        elif self._plan_side_by_side_mode:
            base = self._compare_resolve_pdf_asset_baseline()
            cand = self._compare_resolve_pdf_asset_right()
            await self._mount_plan_focus_side_by_side_async(future=True, base=base, cand=cand)
        else:
            resolved = self._compare_resolve_pdf_asset_right()
            await self._mount_plan_focus_single_async(future=True, resolved=resolved)
        for slot_name, _ in self._plan_focus_slots(future=True):
            slot = getattr(self, slot_name, None)
            if slot is not None and _ctrl_on_page(slot):
                slot.update()
        pair_slot = getattr(self, "_future_plan_side_by_side_slot", None)
        if pair_slot is not None and _ctrl_on_page(pair_slot):
            pair_slot.update()
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.update()
        if getattr(self, "_plan_side_by_side_mode", False):
            self._schedule_active_plan_viewport_sync(future=True)
        if self._review_plan_change_regions_enabled():
            await self._sync_review_change_regions_async()

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
        self._clear_plan_focus_context(future=False)
        self._compare_pdf_left_lv.controls.clear()
        self._compare_pdf_right_lv.controls.clear()
        self._compare_plan_focus_left_slot.content = self._compare_pdf_left_lv
        self._compare_plan_focus_right_slot.content = self._compare_pdf_right_lv
        body = self._compare_editor.value or ""
        resolved = self._compare_resolve_pdf_asset_right()
        self._append_pdf_pages_to_left_list(self._compare_pdf_left_lv, resolved, pdf_profile="text")
        self._append_markdown_to_right_list(self._compare_pdf_right_lv, body, editable_right=False)
        for c in (
            self._compare_plan_focus_left_slot,
            self._compare_plan_focus_right_slot,
            self._compare_pdf_left_lv,
            self._compare_pdf_right_lv,
        ):
            if _ctrl_on_page(c):
                c.update()
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(
                self._seed_pdf_pair_scroll_metrics_async,
                self._compare_pdf_left_lv,
                self._compare_pdf_right_lv,
                0.0,
            )

    def _rebuild_compare_plan_pdf_panes(self) -> None:
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.run_task(self._rebuild_compare_plan_pdf_panes_async)

    async def _mount_plan_focus_single_async(
        self,
        *,
        future: bool,
        resolved: tuple[int, str] | None,
    ) -> None:
        self._clear_stale_plan_slots_for_single(future=future)
        if future:
            slot_name, store_name = self._future_plan_single_slot_pair()
        else:
            slot_name, store_name = self._plan_focus_slots(future=False)[0]
            right_slot_name, right_store = self._plan_focus_slots(future=False)[1]
            self._clear_plan_focus_pane(
                getattr(self, right_slot_name), right_store
            )
        slot = getattr(self, slot_name)
        if resolved is None:
            self._mount_plan_focus_message(
                slot, store_name, "No PDF asset for this comparison."
            )
            return
        try:
            display = await self._plan_display_pages_for_resolved_async(resolved)
        except BaseException as ex:
            self._mount_plan_focus_message(slot, store_name, f"Could not render PDF: {ex}")
            return
        if not display:
            self._mount_plan_focus_message(slot, store_name, "PDF file missing on disk.")
            return
        mount_key = int(getattr(self, "_plan_viewer_mount_gen", 0))
        stable = self._snapshot_plan_page_paths(display, mount_key=mount_key, label="single")
        base = self._compare_resolve_pdf_asset_baseline()
        cand = self._compare_resolve_pdf_asset_right()
        text_changes, show_labels, hover = self._plan_compare_label_options(base, cand)
        viewer = plan_picture_viewer.build_plan_compare_focus_viewer(
            stable,
            text_changes=text_changes if show_labels else None,
            text_overlay_visible=show_labels,
            hover_enabled=hover,
        )
        self._mount_plan_focus_viewer(
            slot,
            store_name,
            viewer,
            future=future,
            annotatable=future,
        )
        self._sync_plan_focus_viewport_from_active_host(future=future)

    async def _rebuild_compare_plan_pdf_panes_async(self) -> None:
        self._sync_plan_overlay_pane_visibility()
        self._refresh_plan_compare_bar()
        self._plan_viewer_mount_gen = int(getattr(self, "_plan_viewer_mount_gen", 0)) + 1
        if self._plan_overlay_mode and self._plan_pdf_version_count() >= 2:
            await self._refresh_plan_overlay_async()
        elif self._plan_side_by_side_mode:
            base = self._compare_resolve_pdf_asset_baseline()
            cand = self._compare_resolve_pdf_asset_right()
            await self._mount_plan_focus_side_by_side_async(future=False, base=base, cand=cand)
        else:
            resolved = self._compare_resolve_pdf_asset_right()
            await self._mount_plan_focus_single_async(future=False, resolved=resolved)
        for slot_name, _ in self._plan_focus_slots(future=False):
            slot = getattr(self, slot_name, None)
            if slot is not None and _ctrl_on_page(slot):
                slot.update()
        pg = getattr(self, "page", None)
        if pg is not None:
            pg.update()
        if getattr(self, "_plan_side_by_side_mode", False):
            self._schedule_active_plan_viewport_sync(future=False)

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
            if self._review_plan_change_regions_enabled():
                await self._sync_review_change_regions_async(snack_on_detect=True)

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
