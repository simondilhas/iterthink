"""Virtualized History / Review paragraph compare rows (large documents)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import flet as ft

from iterthink import config
from iterthink.compare import paragraph_compare
from iterthink.compare.paragraph_align import compute_alignment, compute_hash
from iterthink.compare.paragraph_compare import HistoryRow
from iterthink.db.session import session_scope
from iterthink.persistence import content_repo, paragraph_user_comments

from .. import ui_theme
from ..constants import (
    COMPARE_ACTION_COL_W,
    COMPARE_ACTION_RAIL_HOVER_WRAP_MIN_H,
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPARE_EVAL_COL_W,
    COMPARE_PILL_COL_W,
    DIFF_SPAN_CHAR_CAP as _DIFF_SPAN_CHAR_CAP,
    TAB_FUTURE,
    TAB_HISTORY,
)
from ..util import ctrl_on_page as _ctrl_on_page, safe_list_scroll
from .candidate_state import CompareCandidateSource
from .compare_virtual import (
    COMPARE_VIRTUAL_HEIGHT_REFINE_THRESHOLD_PX,
    build_future_comp_display_index,
    compare_build_row_heights,
    compare_row_scroll_ft_key,
    compare_should_virtualize,
    compare_spacer_heights,
    compare_text_column_width,
    compare_visible_window_for_heights,
)

_log = logging.getLogger(__name__)

_COMPARE_HISTORY_CELL_PAD = ft.padding.all(8)


@dataclass
class _FutureVirtualFieldMeta:
    kind: str
    cand_idx: int | None
    stable_text: str
    old_index: int
    insert_after_old: int
    text: str = ""


class _CompareVirtualMixin:
    """Windowed ListView mounting for large paragraph compare grids."""

    def _compare_tab_is_active(self) -> bool:
        return self._main_tab_index in (TAB_HISTORY, TAB_FUTURE)

    def _mark_compare_rebuild_pending(self) -> None:
        self._compare_rebuild_pending = True

    def _clear_compare_rebuild_pending(self) -> None:
        self._compare_rebuild_pending = False

    def _compare_should_virtualize(self, display_row_count: int) -> bool:
        return compare_should_virtualize(display_row_count)

    def _compare_comp_slot_count(self) -> int:
        if getattr(self, "_compare_virtual_active", False):
            return len(getattr(self, "_compare_virtual_comp_right", []) or [])
        return len(getattr(self, "_compare_right_fields", []) or [])

    def _compare_candidate_para_text(self, index: int) -> str:
        texts = getattr(self, "_compare_virtual_comp_right", None)
        if getattr(self, "_compare_virtual_active", False) and texts is not None:
            if 0 <= index < len(texts):
                return texts[index]
            return ""
        fields = getattr(self, "_compare_right_fields", None) or []
        if 0 <= index < len(fields):
            return fields[index].value or ""
        return ""

    def _compare_set_candidate_para_text(self, index: int, text: str) -> None:
        texts = getattr(self, "_compare_virtual_comp_right", None)
        if getattr(self, "_compare_virtual_active", False) and texts is not None:
            if 0 <= index < len(texts):
                texts[index] = text
        fields = getattr(self, "_compare_right_fields", None) or []
        if 0 <= index < len(fields):
            fields[index].value = text
            if _ctrl_on_page(fields[index]):
                fields[index].update()

    def _compare_reset_virtual_state(self) -> None:
        self._compare_virtual_active = False
        self._compare_virtual_display_rows = []
        self._compare_virtual_comp_left = []
        self._compare_virtual_comp_right = []
        self._compare_virtual_pill_kind = []
        self._compare_virtual_pill_disp: list[int | None] = []
        self._compare_virtual_pill_badge: list[bool] = []
        self._compare_virtual_comp_by_display: list[int | None] = []
        self._compare_virtual_left_widgets: dict[int, ft.Text] = {}
        self._compare_virtual_right_widgets: dict[int, ft.Text] = {}
        self._compare_virtual_pill_hosts: dict[int, ft.Container] = {}
        self._compare_virtual_eval_hosts: dict[int, ft.Container] = {}
        self._compare_virtual_scroll_offset = 0.0
        self._compare_virtual_mount_gen = 0
        self._compare_virtual_row_heights: list[float] = []
        self._compare_virtual_window: tuple[int, int] | None = None
        self._compare_virtual_height_remount_pending = False
        self._future_virtual_active = False
        self._future_virtual_display_rows: list[HistoryRow] = []
        self._future_virtual_field_meta: list[_FutureVirtualFieldMeta] = []
        self._future_virtual_pill_kind: list[str] = []
        self._future_virtual_pill_disp: list[int | None] = []
        self._future_virtual_pill_badge: list[bool] = []
        self._future_virtual_left_widgets: dict[int, ft.Text] = {}
        self._future_virtual_pill_hosts: dict[int, ft.Container] = {}
        self._future_virtual_eval_hosts: dict[int, ft.Container] = {}
        self._future_virtual_field_widgets: dict[int, ft.TextField] = {}
        self._future_virtual_scroll_offset = 0.0
        self._future_virtual_mount_gen = 0
        self._future_virtual_user_comments: dict[int, str] = {}
        self._future_comp_display_index: dict[int, int] = {}
        self._future_comp_row_hosts: dict[int, ft.Control] = {}
        self._future_row_measured_heights: dict[int, float] = {}
        self._future_virtual_row_heights: list[float] = []
        self._future_virtual_window: tuple[int, int] | None = None
        self._future_virtual_height_remount_pending = False

    def _register_future_result_card_row(self, comp_idx: int, row_control: ft.Control) -> None:
        from iterthink.studio import ui_theme

        highlight_cand = getattr(self, "_ki_compare_row_highlight_cand_idx", None)
        comp_cand = None
        ev = getattr(self, "_future_eval_cand_indices", None)
        if ev and 0 <= int(comp_idx) < len(ev):
            comp_cand = ev[int(comp_idx)]
        active = highlight_cand is not None and comp_cand == highlight_cand
        wrap = ft.Container(
            content=row_control,
            padding=0,
            border=(
                ft.border.all(2, config.HIGHLIGHT)
                if active
                else ft.border.all(1, ui_theme.outline_muted(alpha=0.0))
            ),
            bgcolor=(
                ft.Colors.with_opacity(0.08, config.HIGHLIGHT) if active else None
            ),
            border_radius=4,
        )
        self._future_comp_row_hosts[comp_idx] = wrap

    def _on_future_body_stack_resize(self, e: ft.LayoutSizeChangeEvent) -> None:
        self._future_body_stack_height = max(60.0, float(e.height))

    def _compare_listview_viewport_height(self, lv: ft.ListView) -> float:
        h = float(getattr(lv, "height", 0) or 0)
        if h > 0:
            return h
        parent = getattr(lv, "parent", None)
        while parent is not None:
            ph = float(getattr(parent, "height", 0) or 0)
            if ph > 0:
                return ph
            parent = getattr(parent, "parent", None)
        return 640.0

    def _compare_listview_viewport_width(self, lv: ft.ListView) -> float:
        w = float(getattr(lv, "width", 0) or 0)
        if w > 0:
            return w
        parent = getattr(lv, "parent", None)
        while parent is not None:
            pw = float(getattr(parent, "width", 0) or 0)
            if pw > 0:
                return pw
            parent = getattr(parent, "parent", None)
        return 900.0

    def _compare_virtual_text_column_width(
        self,
        lv: ft.ListView,
        *,
        show_actions: bool = True,
    ) -> float:
        return compare_text_column_width(
            self._compare_listview_viewport_width(lv),
            show_actions=show_actions,
        )

    def _virtual_row_shell(
        self,
        content: ft.Control,
        display_index: int,
        which: str,
        *,
        scroll_key: ft.ScrollKey | None = None,
    ) -> ft.Container:
        return ft.Container(
            content=content,
            key=scroll_key,
            on_size_change=lambda e, di=display_index, w=which: self._on_virtual_row_size_change(
                di, w, e
            ),
        )

    def _on_virtual_row_size_change(
        self,
        display_index: int,
        which: str,
        e: ft.LayoutSizeChangeEvent,
    ) -> None:
        measured = max(1.0, float(e.height))
        if which == "future":
            heights = getattr(self, "_future_virtual_row_heights", None) or []
            if display_index >= len(heights):
                return
            if abs(heights[display_index] - measured) <= COMPARE_VIRTUAL_HEIGHT_REFINE_THRESHOLD_PX:
                return
            heights[display_index] = measured
            if self._future_virtual_height_remount_pending:
                return
            self._future_virtual_height_remount_pending = True
            self._future_virtual_window = None
            self.page.run_task(self._compare_virtual_height_remount_async, "future")
            return
        heights = getattr(self, "_compare_virtual_row_heights", None) or []
        if display_index >= len(heights):
            return
        if abs(heights[display_index] - measured) <= COMPARE_VIRTUAL_HEIGHT_REFINE_THRESHOLD_PX:
            return
        heights[display_index] = measured
        if self._compare_virtual_height_remount_pending:
            return
        self._compare_virtual_height_remount_pending = True
        self._compare_virtual_window = None
        self.page.run_task(self._compare_virtual_height_remount_async, "history")

    async def _compare_virtual_height_remount_async(self, which: str) -> None:
        await asyncio.sleep(0.05)
        if which == "future":
            self._future_virtual_height_remount_pending = False
            if not getattr(self, "_future_virtual_active", False):
                return
            self._compare_commit_virtual_future_fields()
            await self._compare_mount_virtual_future_listview(force=True)
        else:
            self._compare_virtual_height_remount_pending = False
            if not getattr(self, "_compare_virtual_active", False):
                return
            await self._compare_mount_virtual_history_listview(force=True)

    def _scroll_event_is_update(self, e: ft.ControlEvent) -> bool:
        event_type = getattr(e, "event_type", None)
        if event_type is None:
            return True
        return event_type == ft.ScrollType.UPDATE

    def _on_compare_rows_virtual_scroll(self, e: ft.ControlEvent) -> None:
        if not getattr(self, "_compare_virtual_active", False):
            return
        if not self._scroll_event_is_update(e):
            return
        self._compare_virtual_scroll_offset = float(getattr(e, "pixels", 0) or 0)
        self._compare_virtual_mount_gen += 1
        gen = self._compare_virtual_mount_gen
        self.page.run_task(self._debounced_compare_virtual_remount, "history", gen)

    def _on_future_rows_virtual_scroll(self, e: ft.ControlEvent) -> None:
        self._future_compare_scroll_offset = float(getattr(e, "pixels", 0) or 0)
        self._future_virtual_scroll_offset = self._future_compare_scroll_offset
        if not getattr(self, "_future_virtual_active", False):
            return
        if not self._scroll_event_is_update(e):
            return
        self._future_virtual_mount_gen += 1
        gen = self._future_virtual_mount_gen
        self.page.run_task(self._debounced_compare_virtual_remount, "future", gen)

    async def _debounced_compare_virtual_remount(self, which: str, gen: int) -> None:
        await asyncio.sleep(0.05)
        if which == "history":
            if gen != self._compare_virtual_mount_gen:
                return
            await self._compare_mount_virtual_history_listview()
        else:
            if gen != self._future_virtual_mount_gen:
                return
            self._compare_commit_virtual_future_fields()
            await self._compare_mount_virtual_future_listview()

    def _compare_commit_virtual_future_fields(self) -> None:
        for idx, tf in self._future_virtual_field_widgets.items():
            if idx < len(self._future_virtual_field_meta):
                self._future_virtual_field_meta[idx].text = tf.value or ""

    def _compare_prepare_virtual_history(
        self,
        display_rows: list[HistoryRow],
        comparison_rows: list[HistoryRow],
    ) -> None:
        self._compare_reset_virtual_state()
        self._compare_virtual_active = True
        self._compare_virtual_display_rows = list(display_rows)
        self._compare_virtual_comp_left = [r.old_text for r in comparison_rows]
        self._compare_virtual_comp_right = [r.new_text for r in comparison_rows]
        self._compare_row_stable_texts = [r.new_text for r in comparison_rows]
        self._compare_row_pill_hosts = []
        self._compare_left_diff_texts = []
        self._compare_right_diff_texts = []
        self._compare_right_fields = []
        self._compare_eval_hosts = []
        comp_idx = 0
        for row in display_rows:
            if row.row_type in ("ghost_moved", "removed"):
                pill_disp = row.displacement
                pill_badge = True
            elif row.is_true_mover:
                pill_disp = None
                pill_badge = False
            else:
                pill_disp = row.displacement
                pill_badge = False
            self._compare_virtual_pill_kind.append(row.slot_kind)
            self._compare_virtual_pill_disp.append(pill_disp)
            self._compare_virtual_pill_badge.append(pill_badge)
            if row.row_type in ("ghost_moved", "removed"):
                self._compare_virtual_comp_by_display.append(None)
            else:
                self._compare_virtual_comp_by_display.append(comp_idx)
                carrier = ft.TextField(value=row.new_text, visible=False, height=0, width=0)
                self._compare_right_fields.append(carrier)
                comp_idx += 1

    def _compare_history_pill_meta(self, row: HistoryRow) -> tuple[int | None, bool]:
        if row.row_type in ("ghost_moved", "removed"):
            return row.displacement, True
        if row.is_true_mover:
            return None, False
        return row.displacement, False

    async def _compare_mount_virtual_history_listview(
        self,
        *,
        scroll_offset: float | None = None,
        force: bool = False,
    ) -> None:
        rows = self._compare_virtual_display_rows
        if not rows:
            return
        lv = self._compare_rows_listview
        if scroll_offset is not None:
            self._compare_virtual_scroll_offset = scroll_offset
        saved_offset = self._compare_virtual_scroll_offset
        viewport = self._compare_listview_viewport_height(lv)
        heights = getattr(self, "_compare_virtual_row_heights", None) or []
        if len(heights) != len(rows):
            heights = compare_build_row_heights(
                rows,
                content_width=self._compare_virtual_text_column_width(lv, show_actions=False),
                text_single=False,
            )
            self._compare_virtual_row_heights = heights
        first, last = compare_visible_window_for_heights(
            scroll_offset=saved_offset,
            heights=heights,
            viewport_height=viewport,
        )
        window = (first, last)
        if not force and getattr(self, "_compare_virtual_window", None) == window:
            return
        self._compare_virtual_window = window
        top_px, tail_px = compare_spacer_heights(first, last, heights)
        para_style = self._compare_para_text_style()
        _MOVED_OPACITY = 0.55
        _ghost_fg = ui_theme.editor_text_color()
        ghost_text_style = ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=_ghost_fg,
            decoration=ft.TextDecoration.LINE_THROUGH,
            decoration_color=_ghost_fg,
        )
        self._compare_virtual_left_widgets.clear()
        self._compare_virtual_right_widgets.clear()
        self._compare_virtual_pill_hosts.clear()
        self._compare_virtual_eval_hosts.clear()
        self._compare_row_pill_hosts = []
        self._compare_left_diff_texts = []
        self._compare_right_diff_texts = []
        self._compare_eval_hosts = []
        controls: list[ft.Control] = []
        if top_px > 0:
            controls.append(ft.Container(height=top_px))
        comp_idx = 0
        for di, row in enumerate(rows):
            if di < first:
                if row.row_type not in ("ghost_moved", "removed"):
                    comp_idx += 1
                continue
            if di >= last:
                break
            pill_disp, pill_badge = self._compare_history_pill_meta(row)
            pill_host = ft.Container(
                content=self._make_compare_pill_row(
                    self._compare_virtual_pill_kind[di],
                    pill_disp,
                    show_moved_badge=pill_badge,
                ),
                width=COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_LEFT,
                padding=ft.padding.only(top=4),
            )
            self._compare_virtual_pill_hosts[di] = pill_host
            if row.row_type in ("ghost_moved", "removed"):
                ghost_left = ft.Text(
                    row.old_text,
                    style=ghost_text_style,
                    selectable=True,
                    expand=True,
                    no_wrap=False,
                )
                left_cell = ft.Container(
                    content=ghost_left,
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                    opacity=_MOVED_OPACITY,
                )
                right_cell = ft.Container(expand=1, padding=_COMPARE_HISTORY_CELL_PAD)
                eval_spacer = ft.Container(width=36)
                controls.append(
                    self._virtual_row_shell(
                        ft.Row(
                            [eval_spacer, left_cell, pill_host, right_cell],
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                        di,
                        "history",
                    )
                )
            else:
                ci = comp_idx
                old_txt = self._compare_virtual_comp_left[ci]
                cur_txt = self._compare_virtual_comp_right[ci]
                left_diff = ft.Text(
                    spans=self._compare_old_side_spans(old_txt, cur_txt),
                    style=para_style,
                    selectable=True,
                    expand=True,
                    no_wrap=False,
                )
                right_diff = ft.Text(
                    spans=self._compare_new_side_spans(old_txt, cur_txt),
                    style=para_style,
                    selectable=True,
                    expand=True,
                    no_wrap=False,
                )
                self._compare_virtual_left_widgets[ci] = left_diff
                self._compare_virtual_right_widgets[ci] = right_diff
                self._compare_left_diff_texts.append(left_diff)
                self._compare_right_diff_texts.append(right_diff)
                left_cell = ft.Container(
                    content=left_diff,
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                    opacity=_MOVED_OPACITY if row.is_moved else 1.0,
                )
                right_cell = ft.Container(
                    content=right_diff,
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                )
                eval_host = self._build_eval_cell(ci)
                self._compare_virtual_eval_hosts[ci] = eval_host
                self._compare_eval_hosts.append(eval_host)
                self._compare_row_pill_hosts.append(pill_host)
                cand_pi = getattr(row, "new_paragraph_index", None)
                row_key = (
                    compare_row_scroll_ft_key(int(cand_pi))
                    if cand_pi is not None and int(cand_pi) >= 0
                    else None
                )
                controls.append(
                    self._virtual_row_shell(
                        ft.Row(
                            [eval_host, left_cell, pill_host, right_cell],
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                        di,
                        "history",
                        scroll_key=row_key,
                    )
                )
                comp_idx += 1
        if tail_px > 0:
            controls.append(ft.Container(height=tail_px))
        lv.controls = controls
        if _ctrl_on_page(lv):
            lv.update()
            await safe_list_scroll(lv, saved_offset)

    def _refresh_compare_virtual_pills(self) -> None:
        if not getattr(self, "_compare_virtual_active", False):
            return
        for di, host in self._compare_virtual_pill_hosts.items():
            kind = self._compare_virtual_pill_kind[di]
            disp = self._compare_virtual_pill_disp[di]
            badge = self._compare_virtual_pill_badge[di]
            host.content = self._make_compare_pill_row(kind, disp, show_moved_badge=badge)
            if _ctrl_on_page(host):
                host.update()

    def _refresh_compare_virtual_spans(self) -> None:
        if not getattr(self, "_compare_virtual_active", False):
            return
        para_style = self._compare_para_text_style()
        for ci, left_t in self._compare_virtual_left_widgets.items():
            old_txt = self._compare_virtual_comp_left[ci]
            cur_txt = self._compare_virtual_comp_right[ci]
            left_t.spans = self._compare_old_side_spans(old_txt, cur_txt)
            if _ctrl_on_page(left_t):
                left_t.update()
            right_t = self._compare_virtual_right_widgets.get(ci)
            if right_t is not None:
                right_t.spans = self._compare_new_side_spans(old_txt, cur_txt)
                if _ctrl_on_page(right_t):
                    right_t.update()
            if ci < len(self._compare_right_fields):
                self._compare_right_fields[ci].value = cur_txt

    def _rebuild_compare_paragraph_ui_virtual(
        self,
        display_rows: list[HistoryRow],
        comparison_rows: list[HistoryRow],
    ) -> None:
        self._compare_pill_gen += 1
        n_comp = len(comparison_rows)
        self._hide_all_result_card_overlays()
        self._check_para_hashes = [
            compute_hash(f"{r.old_text}\x1e{r.new_text}") for r in comparison_rows
        ]
        for cid in list(self._check_results.keys()):
            results = self._check_results.get(cid) or []
            if len(results) != n_comp:
                self._check_results[cid] = (results + [None] * n_comp)[:n_comp]
        self._compare_prepare_virtual_history(display_rows, comparison_rows)
        self._compare_virtual_scroll_offset = 0.0
        self._compare_virtual_window = None
        self._compare_virtual_row_heights = compare_build_row_heights(
            display_rows,
            content_width=self._compare_virtual_text_column_width(
                self._compare_rows_listview,
                show_actions=False,
            ),
            text_single=False,
        )
        self.page.run_task(self._compare_mount_virtual_history_listview, scroll_offset=0.0)
        if self._active_check_id is not None:
            self._refresh_all_eval_cells()
        self._refresh_compare_bulk_buttons()
        self._compare_refine_gen += 1
        self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)

    def _refresh_future_virtual_pills(self) -> None:
        if not getattr(self, "_future_virtual_active", False):
            return
        text_single = hasattr(self, "_review_text_single_mode") and self._review_text_single_mode()
        pill_i = 0
        for di, row in enumerate(self._future_virtual_display_rows):
            if row.row_type == "ghost_moved" and text_single:
                continue
            if pill_i >= len(self._future_virtual_pill_kind):
                break
            kind = self._future_virtual_pill_kind[pill_i]
            disp = self._future_virtual_pill_disp[pill_i]
            badge = self._future_virtual_pill_badge[pill_i]
            host = self._future_virtual_pill_hosts.get(di)
            if host is not None:
                host.content = self._make_compare_pill_row(kind, disp, show_moved_badge=badge)
                if _ctrl_on_page(host):
                    host.update()
            pill_i += 1

    def _future_virtual_update_pill_kinds(self, kinds: list[str]) -> None:
        text_single = hasattr(self, "_review_text_single_mode") and self._review_text_single_mode()
        comp_i = 0
        pill_i = 0
        for row in self._future_virtual_display_rows:
            if row.row_type == "ghost_moved" and text_single:
                continue
            if row.row_type in ("ghost_moved", "removed"):
                pill_i += 1
                continue
            if comp_i < len(kinds) and pill_i < len(self._future_virtual_pill_kind):
                self._future_virtual_pill_kind[pill_i] = kinds[comp_i]
            comp_i += 1
            pill_i += 1
        self._refresh_future_virtual_pills()

    def _rebuild_future_paragraph_ui_virtual(
        self,
        *,
        display_rows: list[HistoryRow],
        comparison_rows: list[HistoryRow],
        current_text: str,
        ai_text: str,
        diffs: list[Any],
    ) -> None:
        self._compare_pill_gen += 1
        n_comp = len(comparison_rows)
        self._hide_all_result_card_overlays()
        self._check_para_hashes = [
            compute_hash(f"{r.old_text}\x1e{r.new_text}") for r in comparison_rows
        ]
        for cid in list(self._check_results.keys()):
            results = self._check_results.get(cid) or []
            if len(results) != n_comp:
                self._check_results[cid] = (results + [None] * n_comp)[:n_comp]

        self._future_virtual_active = True
        self._future_virtual_display_rows = list(display_rows)
        text_single = hasattr(self, "_review_text_single_mode") and self._review_text_single_mode()
        self._future_comp_display_index = build_future_comp_display_index(
            display_rows,
            text_single=text_single,
        )
        self._future_compare_scroll_offset = 0.0
        self._future_virtual_scroll_offset = 0.0
        self._future_virtual_field_meta = []
        self._future_row_kinds = []
        self._future_row_cand_idx = []
        self._future_row_stable_texts = []
        self._future_row_old_index = []
        self._future_row_insert_after_old = []
        self._future_eval_cand_indices = []
        self._future_virtual_pill_kind = []
        self._future_virtual_pill_disp = []
        self._future_virtual_pill_badge = []
        self._compare_right_fields = []
        self._compare_eval_hosts = []
        self._future_row_pill_hosts = []
        self._future_left_diff_texts = []
        self._future_comment_pick_cells = []

        show_actions = bool(self.current_path)
        future_user_comments: dict[int, str] = {}
        if show_actions:
            try:
                with session_scope() as s:
                    doc = content_repo.get_document_by_resolved_path(s, self.current_path.resolve())
                    if doc is not None:
                        snaps = content_repo.list_snapshots(s, self.current_path.resolve())
                        if snaps:
                            anchor_body = content_repo.load_version_body(
                                s, int(snaps[0].version_id)
                            )
                            future_user_comments = paragraph_user_comments.map_resolved_for_display(
                                s,
                                content_version_id=int(snaps[0].version_id),
                                anchor_body=anchor_body,
                                display_body=ai_text,
                            )
            except Exception:
                future_user_comments = {}
        self._future_virtual_user_comments = future_user_comments

        text_single = hasattr(self, "_review_text_single_mode") and self._review_text_single_mode()
        comp_idx = 0
        field_idx = 0

        for row in display_rows:
            if row.row_type == "ghost_moved" and text_single:
                continue
            if row.row_type in ("ghost_moved", "removed"):
                pill_disp = row.displacement
                pill_badge = True
            elif row.is_true_mover:
                pill_disp = None
                pill_badge = False
            else:
                pill_disp = row.displacement
                pill_badge = False
            self._future_virtual_pill_kind.append(row.slot_kind)
            self._future_virtual_pill_disp.append(pill_disp)
            self._future_virtual_pill_badge.append(pill_badge)

            if row.row_type == "ghost_moved":
                continue

            if row.row_type == "removed":
                meta = _FutureVirtualFieldMeta(
                    "delete", None, row.old_text, row.old_paragraph_index, -1, ""
                )
                self._future_virtual_field_meta.append(meta)
                self._future_row_kinds.append("delete")
                self._future_row_cand_idx.append(None)
                self._future_row_stable_texts.append(row.old_text)
                self._future_row_old_index.append(row.old_paragraph_index)
                self._future_row_insert_after_old.append(-1)
                carrier = ft.TextField(value="", visible=False, height=0, width=0)
                self._compare_right_fields.append(carrier)
                field_idx += 1
                continue

            old_txt = row.old_text
            cur_txt = row.new_text
            if row.slot_kind == "added":
                review_kind = "insert"
                oi = -1
                ia = paragraph_compare.insert_after_old_index_for_added(
                    diffs, row.new_paragraph_index
                )
            elif old_txt == "" and cur_txt:
                if row.old_paragraph_index >= 0:
                    review_kind = "replace"
                    oi = row.old_paragraph_index
                    ia = -1
                else:
                    review_kind = "insert"
                    oi = -1
                    ia = paragraph_compare.insert_after_old_index_for_added(
                        diffs, row.new_paragraph_index
                    )
            elif old_txt == cur_txt:
                review_kind = "equal"
                oi = row.old_paragraph_index
                ia = -1
            else:
                review_kind = "replace"
                oi = row.old_paragraph_index
                ia = -1

            self._future_row_kinds.append(review_kind)
            self._future_row_cand_idx.append(row.new_paragraph_index)
            self._future_row_stable_texts.append(old_txt)
            self._future_row_old_index.append(oi)
            self._future_row_insert_after_old.append(ia)
            self._future_virtual_field_meta.append(
                _FutureVirtualFieldMeta(
                    review_kind,
                    row.new_paragraph_index,
                    old_txt,
                    oi,
                    ia,
                    cur_txt,
                )
            )
            carrier = ft.TextField(value=cur_txt, visible=False, height=0, width=0)
            self._compare_right_fields.append(carrier)
            self._future_eval_cand_indices.append(row.new_paragraph_index)
            comp_idx += 1
            field_idx += 1

        self._future_virtual_scroll_offset = 0.0
        self._future_virtual_window = None
        show_actions = bool(self.current_path)
        self._future_virtual_row_heights = compare_build_row_heights(
            display_rows,
            content_width=self._compare_virtual_text_column_width(
                self._future_rows_listview,
                show_actions=show_actions,
            ),
            text_single=text_single,
            field_meta=self._future_virtual_field_meta,
        )
        self.page.run_task(self._compare_mount_virtual_future_listview, scroll_offset=0.0)

        if (
            self._main_tab_index == TAB_FUTURE
            and int(getattr(self, "_review_subtab_index", 0)) == 1
            and hasattr(self, "_sync_impact_paragraph_list_after_compare_rebuild")
        ):
            try:
                self._sync_impact_paragraph_list_after_compare_rebuild()
            except Exception:
                _log.exception("Impact paragraph list sync after Review rebuild failed")

        if self._active_check_id is not None:
            self._refresh_all_eval_cells()
        if hasattr(self, "_sync_ki_comment_pick_affordance"):
            self._sync_ki_comment_pick_affordance()
        self._refresh_compare_bulk_buttons()
        if self._compare_candidate_source != CompareCandidateSource.SPELL_PREVIEW:
            self._compare_refine_gen += 1
            self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)

    async def _compare_mount_virtual_future_listview(
        self,
        *,
        scroll_offset: float | None = None,
        force: bool = False,
    ) -> None:
        rows = self._future_virtual_display_rows
        if not rows:
            return
        lv = self._future_rows_listview
        if scroll_offset is not None:
            self._future_virtual_scroll_offset = scroll_offset
        saved_offset = self._future_virtual_scroll_offset
        viewport = self._compare_listview_viewport_height(lv)
        show_actions = bool(self.current_path)
        text_single = hasattr(self, "_review_text_single_mode") and self._review_text_single_mode()
        heights = getattr(self, "_future_virtual_row_heights", None) or []
        if len(heights) != len(rows):
            heights = compare_build_row_heights(
                rows,
                content_width=self._compare_virtual_text_column_width(
                    lv,
                    show_actions=show_actions,
                ),
                text_single=text_single,
                field_meta=self._future_virtual_field_meta,
            )
            self._future_virtual_row_heights = heights
        first, last = compare_visible_window_for_heights(
            scroll_offset=saved_offset,
            heights=heights,
            viewport_height=viewport,
        )
        window = (first, last)
        if not force and getattr(self, "_future_virtual_window", None) == window:
            return
        self._future_virtual_window = window
        top_px, tail_px = compare_spacer_heights(first, last, heights)
        para_style = self._compare_para_text_style()
        _MOVED_OPACITY = 0.55
        _ghost_fg = ui_theme.editor_text_color()
        ghost_text_style = ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=_ghost_fg,
            decoration=ft.TextDecoration.LINE_THROUGH,
            decoration_color=_ghost_fg,
        )
        right_tf_kwargs: dict[str, Any] = {
            "multiline": True,
            "max_lines": None,
            "min_lines": 1,
            "border": ft.InputBorder.NONE,
            "filled": False,
            "dense": True,
            "text_size": COMPARE_COL_FONT_SIZE,
            "text_style": para_style,
            "cursor_color": config.PRIMARY_COLOR,
            "selection_color": config.SELECTION_OVERLAY,
            "content_padding": ft.padding.all(0),
        }
        eval_spacer_w = COMPARE_EVAL_COL_W
        future_user_comments = getattr(self, "_future_virtual_user_comments", {}) or {}

        self._future_comp_row_hosts.clear()
        self._future_row_measured_heights.clear()
        self._future_virtual_left_widgets.clear()
        self._future_virtual_pill_hosts.clear()
        self._future_virtual_eval_hosts.clear()
        self._future_virtual_field_widgets.clear()
        self._future_row_pill_hosts = []
        self._future_left_diff_texts = []
        self._compare_eval_hosts = []
        self._future_comment_pick_cells = []

        controls: list[ft.Control] = []
        if top_px > 0:
            controls.append(ft.Container(height=top_px))

        pill_i = 0
        field_idx = 0
        comp_idx = 0

        for di, row in enumerate(rows):
            if row.row_type == "ghost_moved" and text_single:
                continue
            if di < first:
                pill_i += 1
                if row.row_type == "ghost_moved":
                    pass
                elif row.row_type == "removed":
                    field_idx += 1
                else:
                    field_idx += 1
                    comp_idx += 1
                continue
            if di >= last:
                break
            if pill_i >= len(self._future_virtual_pill_kind):
                break

            pill_host = ft.Container(
                content=self._make_compare_pill_row(
                    self._future_virtual_pill_kind[pill_i],
                    self._future_virtual_pill_disp[pill_i],
                    show_moved_badge=self._future_virtual_pill_badge[pill_i],
                ),
                width=COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_LEFT,
                padding=ft.padding.only(top=4),
            )
            self._future_virtual_pill_hosts[di] = pill_host
            pill_i += 1

            if row.row_type == "ghost_moved":
                ghost_left = ft.Text(
                    row.old_text,
                    style=ghost_text_style,
                    selectable=True,
                    expand=True,
                    no_wrap=False,
                )
                self._future_left_diff_texts.append(ghost_left)
                left_cell = ft.Container(
                    content=ghost_left,
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                    opacity=_MOVED_OPACITY,
                )
                eval_ctrl = ft.Container(width=eval_spacer_w)
                right_cell = ft.Container(expand=1, padding=_COMPARE_HISTORY_CELL_PAD)
                controls.append(
                    self._virtual_row_shell(
                        ft.Row(
                            self._future_review_visible_row_cells(
                                text_single=text_single,
                                eval_ctrl=eval_ctrl,
                                left_cell=left_cell,
                                pill_host=pill_host,
                                right_cell=right_cell,
                            ),
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                        di,
                        "future",
                    )
                )
                continue

            meta = self._future_virtual_field_meta[field_idx]
            if row.row_type == "removed":
                ghost_left = ft.Text(
                    row.old_text,
                    style=ghost_text_style,
                    selectable=True,
                    expand=True,
                    no_wrap=False,
                )
                self._future_left_diff_texts.append(ghost_left)
                left_cell = ft.Container(
                    content=ghost_left,
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                    opacity=_MOVED_OPACITY,
                )
                right_cell = ft.Container(expand=1, padding=_COMPARE_HISTORY_CELL_PAD)
                eval_ctrl = ft.Container(width=eval_spacer_w)
                row_cells = self._future_review_visible_row_cells(
                    text_single=text_single,
                    eval_ctrl=eval_ctrl,
                    left_cell=left_cell,
                    pill_host=pill_host,
                    right_cell=right_cell,
                )
                controls.append(
                    self._virtual_row_shell(
                        ft.Row(
                            row_cells,
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                        di,
                        "future",
                    )
                )
                field_idx += 1
                continue

            old_txt = row.old_text
            cur_txt = meta.text
            left_diff = ft.Text(
                spans=self._future_old_side_spans(old_txt, cur_txt),
                style=para_style,
                selectable=True,
                expand=True,
                no_wrap=False,
            )
            self._future_left_diff_texts.append(left_diff)
            left_cell = ft.Container(
                content=left_diff,
                expand=1,
                padding=_COMPARE_HISTORY_CELL_PAD,
                opacity=_MOVED_OPACITY if row.is_moved else 1.0,
            )
            cand_pi = row.new_paragraph_index
            if cand_pi is not None and int(cand_pi) >= 0:
                pick_pi = int(cand_pi)
                left_cell.on_click = lambda _e, p=pick_pi: self._on_review_compare_row_comment_pick(p)

            right_tf = ft.TextField(
                **right_tf_kwargs,
                value=cur_txt,
                read_only=False,
                enable_interactive_selection=True,
                hint_text="…",
                expand=True,
                on_change=lambda _e, ix=field_idx: self._on_compare_para_field_change(ix),
            )
            self._future_virtual_field_widgets[field_idx] = right_tf
            right_cell = ft.Container(
                content=right_tf,
                expand=1,
                padding=_COMPARE_HISTORY_CELL_PAD,
            )
            if cand_pi is not None and int(cand_pi) >= 0:
                pick_pi = int(cand_pi)
                right_cell.on_click = lambda _e, p=pick_pi: self._on_review_compare_row_comment_pick(p)
                self._future_comment_pick_cells.extend([left_cell, right_cell])

            eval_host = self._build_eval_cell(comp_idx)
            self._future_virtual_eval_hosts[comp_idx] = eval_host
            self._compare_eval_hosts.append(eval_host)
            self._future_row_pill_hosts.append(pill_host)

            row_cells = self._future_review_visible_row_cells(
                text_single=text_single,
                eval_ctrl=eval_host,
                left_cell=left_cell,
                pill_host=pill_host,
                right_cell=right_cell,
            )
            if show_actions:
                cand_pi = row.new_paragraph_index
                has_uc = cand_pi in future_user_comments
                actions_ctrl, hover_wrap_future, presence_host = self._build_actions_square(
                    field_idx,
                    draft_paragraph_index=int(cand_pi) if cand_pi is not None else None,
                    has_user_comment=has_uc,
                )
                row_cells.append(actions_ctrl)
                row_inner = ft.Row(
                    row_cells,
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
                row_control = ft.Container(
                    content=row_inner,
                    on_hover=lambda e, w=hover_wrap_future, ph=presence_host: self._on_compare_row_hover(
                        e, w, ph
                    ),
                )
            else:
                row_control = ft.Row(
                    row_cells,
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
            self._register_future_result_card_row(comp_idx, row_control)
            row_key = (
                compare_row_scroll_ft_key(int(cand_pi))
                if cand_pi is not None and int(cand_pi) >= 0
                else None
            )
            mounted = self._virtual_row_shell(
                self._future_comp_row_hosts[comp_idx],
                di,
                "future",
                scroll_key=row_key,
            )
            self._future_comp_row_hosts[comp_idx] = mounted
            controls.append(mounted)
            field_idx += 1
            comp_idx += 1

        if tail_px > 0:
            controls.append(ft.Container(height=tail_px))
        lv.controls = controls
        if _ctrl_on_page(lv):
            lv.update()
            await safe_list_scroll(lv, saved_offset)

    def _future_virtual_field_text(self, field_idx: int) -> str:
        if getattr(self, "_future_virtual_active", False):
            meta = self._future_virtual_field_meta
            if 0 <= field_idx < len(meta):
                tf = self._future_virtual_field_widgets.get(field_idx)
                if tf is not None:
                    return tf.value or ""
                return meta[field_idx].text
        fields = getattr(self, "_compare_right_fields", None) or []
        if 0 <= field_idx < len(fields):
            return fields[field_idx].value or ""
        return ""
