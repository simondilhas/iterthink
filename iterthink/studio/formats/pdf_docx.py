"""PDF/plan overlay and DOCX preview wiring for MarkdownStudio (mixin)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft

from iterthink import config
from iterthink.persistence import version_storage
from iterthink.services import document_import
from iterthink.tools.pdf_visual_diff import diff_pdfs_to_overlay_paths

from .. import plan_compare_panel, plan_picture_viewer, ui_theme
from iterthink.db.session import session_scope
from ..constants import COMPARE_COL_FONT_SIZE, COMPARE_COL_LINE_HEIGHT, TAB_FUTURE, TAB_HISTORY
from ..util import ctrl_on_page as _ctrl_on_page

_PDF_COMPARE_SCROLL_SOURCES = ("pdf_original", "docx_original")


class MarkdownStudioAssetCompare:
    def _on_future_pdf_import_md_change(self, _e: ft.ControlEvent) -> None:
        v = self._future_pdf_import_md_tf.value or ""
        if (self.editor.value or "") == v and (self._compare_editor.value or "") == v:
            return
        self.editor.value = v
        self._compare_editor.value = v
        self._refresh_title_bar()
        self._kick_debounced_autosave()
        if _ctrl_on_page(self.editor):
            self.editor.update()

    def _on_compare_pdf_scroll_left(self, e: ft.OnScrollEvent) -> None:
        if self._compare_pdf_scroll_guard:
            return
        if self._main_tab_index != TAB_HISTORY:
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
            if self._plan_compare.overlay_list.visible:
                await self._plan_compare.overlay_list.scroll_to(offset=target, duration=0)
            else:
                await self._compare_pdf_right_lv.scroll_to(offset=target, duration=0)
        finally:
            self._compare_pdf_scroll_guard = False

    def _on_compare_pdf_scroll_right(self, e: ft.OnScrollEvent) -> None:
        if self._compare_pdf_scroll_guard:
            return
        if self._main_tab_index != TAB_HISTORY:
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
            await self._compare_pdf_left_lv.scroll_to(offset=target, duration=0)
        finally:
            self._compare_pdf_scroll_guard = False

    def _on_future_pdf_scroll_left(self, e: ft.OnScrollEvent) -> None:
        if self._compare_pdf_scroll_guard:
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
            await self._future_pdf_right_lv.scroll_to(offset=target, duration=0)
        finally:
            self._compare_pdf_scroll_guard = False

    def _on_future_pdf_scroll_right(self, e: ft.OnScrollEvent) -> None:
        if self._compare_pdf_scroll_guard:
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
            await self._future_pdf_left_lv.scroll_to(offset=target, duration=0)
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
        try:
            await left_lv.scroll_to(offset=0, duration=0)
        except BaseException:
            pass
        try:
            await right_lv.scroll_to(offset=0, duration=0)
        except BaseException:
            pass

    def _sync_future_pdf_layers_visibility(self) -> None:
        sub = int(getattr(self, "_review_subtab_index", 0) or 0)
        show = (
            self._main_tab_index == TAB_FUTURE
            and sub == 0
            and self._compare_candidate_source == "pdf_original"
        )
        self._future_pdf_layer.visible = show
        self._future_paragraph_layer.visible = not show
        if _ctrl_on_page(self._future_pdf_layer):
            self._future_pdf_layer.update()
        if _ctrl_on_page(self._future_paragraph_layer):
            self._future_paragraph_layer.update()

    def _sync_compare_pdf_layers_visibility(self) -> None:
        show_side_by_side = (
            self._compare_candidate_source in _PDF_COMPARE_SCROLL_SOURCES
            and self._main_tab_index == TAB_HISTORY
        )
        self._compare_paragraph_layer.visible = not show_side_by_side
        self._compare_pdf_layer.visible = show_side_by_side
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

    def _refresh_compose_plan_surface(self) -> None:
        """Show zoom/pan PDF strip on Compose when latest stored PDF is profile ``plan``."""
        if not self.current_path:
            self._compose_plan_host.visible = False
            self._compose_editor_shell_wrapped.expand = True
            self._compose_editor_shell_wrapped.height = None
            return
        with session_scope() as s:
            det = version_storage.latest_pdf_version_detail(s, self.current_path.resolve())
        if det is None:
            self._compose_plan_host.visible = False
            self._compose_editor_shell_wrapped.expand = True
            self._compose_editor_shell_wrapped.height = None
            return
        _vid, rel, profile = det
        if profile != "plan":
            self._compose_plan_host.visible = False
            self._compose_editor_shell_wrapped.expand = True
            self._compose_editor_shell_wrapped.height = None
            return
        try:
            pdf_abs = version_storage.pdf_asset_abs_path(rel)
        except (ValueError, OSError):
            self._compose_plan_host.visible = False
            return
        if not pdf_abs.is_file():
            self._compose_plan_host.visible = False
            return
        try:
            pages = document_import.render_pdf_to_png_pages(pdf_abs)
            col = plan_picture_viewer.plan_picture_column(pages)
            self._compose_plan_host.content = col
            self._compose_plan_host.visible = True
            self._compose_editor_shell_wrapped.expand = False
            self._compose_editor_shell_wrapped.height = 260
        except BaseException:
            self._compose_plan_host.visible = False
            self._compose_editor_shell_wrapped.expand = True
            self._compose_editor_shell_wrapped.height = None
        if _ctrl_on_page(self._compose_plan_host):
            self._compose_plan_host.update()
        if _ctrl_on_page(self._compose_editor_shell_wrapped):
            self._compose_editor_shell_wrapped.update()

    def _refresh_plan_compare_bar(self) -> None:
        if not self.current_path:
            self._plan_compare.set_bar_visible(False)
            return
        with session_scope() as s:
            pairs = version_storage.list_plan_pdf_version_options(s, self.current_path.resolve())
        opts = [(str(vid), lbl) for vid, lbl in pairs]
        plan_compare_panel.fill_pdf_dropdowns(
            self._plan_compare.baseline_dd,
            self._plan_compare.candidate_dd,
            opts,
            option_button_style=ui_theme.compare_candidate_dropdown_option_style(),
        )
        # Bar only in History PDF asset mode with two or more plan/drawing PDF snapshots.
        show_bar = len(opts) >= 2 and self._compare_candidate_source == "pdf_original"
        if not show_bar:
            self._plan_compare.overlay_switch.value = False
        self._plan_compare.baseline_dd.disabled = not show_bar
        self._plan_compare.candidate_dd.disabled = not show_bar
        self._plan_compare.overlay_switch.disabled = not show_bar
        self._plan_compare.set_bar_visible(show_bar)
        self._sync_plan_overlay_pane_visibility()
        if _ctrl_on_page(self._plan_compare.baseline_dd):
            self._plan_compare.baseline_dd.update()
        if _ctrl_on_page(self._plan_compare.candidate_dd):
            self._plan_compare.candidate_dd.update()
        if _ctrl_on_page(self._plan_compare.overlay_switch):
            self._plan_compare.overlay_switch.update()

    def _sync_plan_overlay_pane_visibility(self) -> None:
        show_ov = bool(self._plan_compare.overlay_switch.value)
        self._plan_overlay_mode = show_ov
        self._compare_pdf_right_lv.visible = not show_ov
        self._plan_compare.overlay_list.visible = show_ov

    def _on_plan_overlay_changed(self, e: ft.ControlEvent | None = None) -> None:
        self._sync_plan_overlay_pane_visibility()
        self._rebuild_compare_pdf_panes()
        self.page.run_task(self._refresh_plan_overlay_async)

    async def _on_plan_pdf_baseline_async(self, _e: ft.ControlEvent | None = None) -> None:
        if self._plan_overlay_mode:
            await self._refresh_plan_overlay_async()
        else:
            self._rebuild_compare_pdf_panes()

    async def _on_plan_pdf_candidate_async(self, _e: ft.ControlEvent | None = None) -> None:
        if self._plan_overlay_mode:
            await self._refresh_plan_overlay_async()
        else:
            self._rebuild_compare_pdf_panes()

    async def _refresh_plan_overlay_async(self) -> None:
        if not self.current_path or not self._plan_overlay_mode:
            return
        bid_s = self._plan_compare.baseline_dd.value
        cid_s = self._plan_compare.candidate_dd.value
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
            ra = version_storage.get_version_pdf_relpath(s, bid)
            rb = version_storage.get_version_pdf_relpath(s, cid)
        if not ra or not rb:
            return
        pa = version_storage.pdf_asset_abs_path(ra)
        pb = version_storage.pdf_asset_abs_path(rb)
        gen = self._plan_overlay_gen + 1
        self._plan_overlay_gen = gen

        def _run() -> tuple[list[Path], str | None]:
            return diff_pdfs_to_overlay_paths(pa, pb)

        paths, warn = await asyncio.to_thread(_run)
        if gen != self._plan_overlay_gen:
            return
        plan_compare_panel.populate_overlay_list(self._plan_compare.overlay_list, paths)
        if warn:
            self._snack(warn)
        if _ctrl_on_page(self._plan_compare.overlay_list):
            self._plan_compare.overlay_list.update()

    def _compare_resolve_pdf_asset(self) -> tuple[int, str] | None:
        """PDF version id and store relpath for the current Compare context."""
        if not self.current_path:
            return None
        rp = self.current_path.resolve()
        with session_scope() as s:
            if self._compare_pdf_peer_snapshot_id is not None:
                rel = version_storage.get_version_pdf_relpath(s, self._compare_pdf_peer_snapshot_id)
                if rel:
                    return (self._compare_pdf_peer_snapshot_id, rel)
                return None
            return version_storage.latest_pdf_version_for_document(s, rp)

    def _compare_resolve_pdf_asset_right(self) -> tuple[int, str] | None:
        """Prefer explicit PDF version from Compare bar when set."""
        if self.current_path and self._plan_compare.candidate_dd.value:
            try:
                vid = int(self._plan_compare.candidate_dd.value)
            except (TypeError, ValueError):
                return self._compare_resolve_pdf_asset()
            with session_scope() as s:
                rel = version_storage.get_version_pdf_relpath(s, vid)
                if rel:
                    return (vid, rel)
        return self._compare_resolve_pdf_asset()

    def _append_pdf_pages_to_left_list(self, left_lv: ft.ListView, resolved: tuple[int, str] | None) -> None:
        if resolved is None:
            left_lv.controls.append(
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
            pdf_abs = version_storage.pdf_asset_abs_path(rel)
        except (ValueError, OSError):
            pdf_abs = None
        if pdf_abs is None or not pdf_abs.is_file():
            left_lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(12),
                    content=ft.Text("PDF file missing on disk.", color=ft.Colors.RED_200, size=13),
                )
            )
        else:
            try:
                pages = document_import.render_pdf_to_png_pages(pdf_abs)
                pic_col = plan_picture_viewer.plan_picture_column(pages, inner_scroll=False)
                left_lv.controls.append(ft.Container(content=pic_col, expand=True))
            except BaseException as ex:
                left_lv.controls.append(
                    ft.Container(
                        padding=ft.padding.all(12),
                        content=ft.Text(f"Could not render PDF: {ex}", color=ft.Colors.RED_200, size=12),
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
        """Review tab: PDF left, editable markdown right (import snapshot)."""
        self._compare_pdf_left_max_scroll = 1.0
        self._compare_pdf_right_max_scroll = 1.0
        self._future_pdf_left_lv.controls.clear()
        self._future_pdf_right_lv.controls.clear()
        body = self.editor.value or ""
        self._compare_editor.value = body
        resolved = self._compare_resolve_pdf_asset_right()
        self._append_pdf_pages_to_left_list(self._future_pdf_left_lv, resolved)
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

    def _rebuild_compare_pdf_panes(self) -> None:
        if self._compare_candidate_source == "docx_original":
            self._rebuild_compare_docx_panes()
            return
        self._sync_plan_overlay_pane_visibility()
        self._compare_pdf_left_max_scroll = 1.0
        self._compare_pdf_right_max_scroll = 1.0
        self._compare_pdf_left_lv.controls.clear()
        self._compare_pdf_right_lv.controls.clear()
        body = self._compare_editor.value or ""
        _md_style = ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=ui_theme.editor_text_color(),
        )
        if self._plan_overlay_mode:
            # Extracted markdown reference on the left; PDF overlay occupies the right column.
            self._compare_pdf_left_lv.controls.append(
                ft.Container(
                    padding=ft.padding.all(4),
                    content=ft.Text(body, selectable=True, style=_md_style),
                )
            )
            if _ctrl_on_page(self._compare_pdf_left_lv):
                self._compare_pdf_left_lv.update()
            if _ctrl_on_page(self._compare_pdf_right_lv):
                self._compare_pdf_right_lv.update()
            self.page.run_task(self._refresh_plan_overlay_async)
            return

        resolved = self._compare_resolve_pdf_asset_right()
        self._append_pdf_pages_to_left_list(self._compare_pdf_left_lv, resolved)
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
