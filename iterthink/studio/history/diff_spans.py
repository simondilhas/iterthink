"""Pills and paragraph-level diff spans for History and Review columns."""

from __future__ import annotations

import flet as ft

from iterthink import config
from iterthink.compare import paragraph_compare
from iterthink.compare.diff_card import build_new_side_spans, build_old_side_spans, build_unified_spans

from .. import ui_theme
from ..constants import (
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    DIFF_SPAN_CHAR_CAP as _DIFF_SPAN_CHAR_CAP,
    TAB_FUTURE,
    TAB_HISTORY,
)
from ..util import ctrl_on_page as _ctrl_on_page


class _HistoryDiffSpansMixin:
    def _compare_para_text_style(self) -> ft.TextStyle:
        return ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=ui_theme.editor_text_color(),
        )

    def _compare_insertion_diff_colors(self) -> tuple[str, str]:
        """Insertion spans: same foreground as editor; green bg distinguishes them."""
        bg_alpha = 0.5 if config.IS_LIGHT else 0.24
        return ui_theme.editor_text_color(), ft.Colors.with_opacity(bg_alpha, config.SUCCESS)

    @staticmethod
    def _compare_displacement_arrow_text(displacement: int | None) -> str:
        if displacement is None or displacement == 0:
            return ""
        n = abs(displacement)
        return f"↑{n}" if displacement > 0 else f"↓{n}"

    def _make_compare_pill(self, kind: paragraph_compare.SlotKind) -> ft.Container:
        label = paragraph_compare.slot_kind_label(kind)
        bg, fg = ui_theme.compare_slot_pill_colors(kind)
        return ft.Container(
            content=ft.Text(
                label,
                size=10,
                weight=ft.FontWeight.W_600,
                color=fg,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                text_align=ft.TextAlign.CENTER,
            ),
            bgcolor=bg,
            padding=ft.padding.symmetric(horizontal=5, vertical=3),
            border_radius=10,
            alignment=ft.Alignment.CENTER,
        )

    def _make_moved_pill(self) -> ft.Container:
        bg, fg = ui_theme.compare_moved_pill_colors()
        return ft.Container(
            content=ft.Text(
                "Moved",
                size=10,
                weight=ft.FontWeight.W_600,
                color=fg,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                text_align=ft.TextAlign.CENTER,
            ),
            bgcolor=bg,
            padding=ft.padding.symmetric(horizontal=5, vertical=3),
            border_radius=10,
            alignment=ft.Alignment.CENTER,
        )

    def _make_compare_pill_row(
        self,
        kind: paragraph_compare.SlotKind,
        displacement: int | None,
        *,
        show_moved_badge: bool = True,
    ) -> ft.Row:
        arrow = self._compare_displacement_arrow_text(displacement)
        arrow_ctrl = ft.Container(
            content=ft.Text(
                arrow,
                size=11,
                weight=ft.FontWeight.W_500,
                color=config.ON_SURFACE_VARIANT,
                font_family="monospace",
                text_align=ft.TextAlign.CENTER,
            ),
            width=34,
            alignment=ft.Alignment.CENTER,
            padding=ft.padding.only(top=2),
        )
        is_moved = displacement is not None and displacement != 0
        change_pill = self._make_compare_pill(kind) if kind != "stable" else ft.Container()
        if is_moved and show_moved_badge:
            pills = ft.Column(
                [change_pill, self._make_moved_pill()],
                spacing=3,
                tight=True,
            )
        else:
            pills = change_pill
        return ft.Row(
            [arrow_ctrl, pills],
            spacing=2,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _compare_paragraph_diff_spans(self, left_para: str, right_para: str) -> list[ft.TextSpan]:
        """Unified inline diff (Review right column): both deletions and insertions in one stream."""
        old_t, new_t = self._compare_diff_clip(left_para, right_para)
        ins_fg, ins_bg = self._compare_insertion_diff_colors()
        return build_unified_spans(
            old_t,
            new_t,
            base_size=COMPARE_COL_FONT_SIZE,
            base_color=ui_theme.editor_text_color(),
            font_family="monospace",
            insert_color=ins_fg,
            insert_bgcolor=ins_bg,
            line_height=COMPARE_COL_LINE_HEIGHT,
        )

    @staticmethod
    def _compare_diff_clip(left_para: str, right_para: str) -> tuple[str, str]:
        cap = 80_000
        if len(left_para) + len(right_para) > cap:
            half = cap // 2
            return left_para[:half] + "\n…", right_para[:half] + "\n…"
        return left_para, right_para

    def _compare_old_side_spans(self, old_para: str, new_para: str) -> list[ft.TextSpan]:
        """History left column: only words from the snapshot, deletions struck through."""
        old_t, new_t = self._compare_diff_clip(old_para, new_para)
        return build_old_side_spans(
            old_t,
            new_t,
            base_size=COMPARE_COL_FONT_SIZE,
            base_color=ui_theme.editor_text_color(),
            font_family="monospace",
            line_height=COMPARE_COL_LINE_HEIGHT,
        )

    def _future_old_side_spans(self, cur_para: str, ai_para: str) -> list[ft.TextSpan]:
        """Future left column: words from current draft, deletions struck red.

        Caller convention for build_old_side_spans is (old, new). On Future, current text is
        the 'old' side and the AI proposal is the 'new' side, so removed-by-AI tokens get
        the strikethrough.
        """
        return self._compare_old_side_spans(cur_para, ai_para)

    def _compare_new_side_spans(self, old_para: str, new_para: str) -> list[ft.TextSpan]:
        """History right column: only words from the draft, insertions tinted green."""
        old_t, new_t = self._compare_diff_clip(old_para, new_para)
        ins_fg, ins_bg = self._compare_insertion_diff_colors()
        return build_new_side_spans(
            old_t,
            new_t,
            base_size=COMPARE_COL_FONT_SIZE,
            base_color=ui_theme.editor_text_color(),
            font_family="monospace",
            insert_color=ins_fg,
            insert_bgcolor=ins_bg,
            line_height=COMPARE_COL_LINE_HEIGHT,
        )

    def _refresh_future_left_diff_spans(self) -> None:
        """Update Review left column diff spans for the gap-aligned row list."""
        if self._main_tab_index != TAB_FUTURE:
            return
        if not self._future_left_diff_texts:
            return
        current = self._review_baseline_text()
        candidate = self._compare_editor.value or ""
        if len(current) + len(candidate) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            current = current[:half] + "\n…"
            candidate = candidate[:half] + "\n…"
        display_rows = paragraph_compare.build_history_display_rows(current, candidate)
        _ghost_fg = ui_theme.editor_text_color()
        ghost_text_style = ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=_ghost_fg,
            decoration=ft.TextDecoration.LINE_THROUGH,
            decoration_color=_ghost_fg,
        )
        para_style = self._compare_para_text_style()
        di = 0
        for row in display_rows:
            if di >= len(self._future_left_diff_texts):
                break
            t = self._future_left_diff_texts[di]
            if row.row_type in ("ghost_moved", "removed"):
                t.value = row.old_text
                t.style = ghost_text_style
                t.spans = []
            elif row.row_type == "comparison" and not row.old_text:
                t.value = ""
                t.style = para_style
                t.spans = []
            else:
                t.value = None
                t.style = para_style
                t.spans = self._future_old_side_spans(row.old_text, row.new_text)
            if _ctrl_on_page(t):
                t.update()
            di += 1

    def _refresh_compare_left_diff_spans(self) -> None:
        """Update History inline diff for both columns (snapshot vs draft; same paragraph count)."""
        if self._main_tab_index != TAB_HISTORY:
            return
        if len(self._compare_left_diff_texts) != len(self._compare_right_fields):
            return
        older = self._compare_editor.value or ""
        newer = self._history_newer_side_text() or ""
        if len(older) + len(newer) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            older = older[:half] + "\n…"
            newer = newer[:half] + "\n…"
        display_rows = paragraph_compare.build_history_display_rows(older, newer)
        comparison_rows = [r for r in display_rows if r.row_type == "comparison"]
        for i, r in enumerate(comparison_rows):
            if i >= len(self._compare_left_diff_texts):
                break
            left_txt, right_txt = r.old_text, r.new_text
            left_t = self._compare_left_diff_texts[i]
            left_t.spans = self._compare_old_side_spans(left_txt, right_txt)
            if _ctrl_on_page(left_t):
                left_t.update()
            if i < len(self._compare_right_diff_texts):
                right_t = self._compare_right_diff_texts[i]
                right_t.spans = self._compare_new_side_spans(left_txt, right_txt)
                if _ctrl_on_page(right_t):
                    right_t.update()
            # Keep hidden carrier value in sync with the displayed draft paragraph.
            if i < len(self._compare_right_fields):
                self._compare_right_fields[i].value = right_txt
