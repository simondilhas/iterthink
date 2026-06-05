"""History and Review paragraph row ListViews (ghost rows, eval column, actions)."""

from __future__ import annotations

import logging
from typing import Any

import flet as ft

from iterthink import config
from iterthink.compare import paragraph_compare
from iterthink.compare.paragraph_align import compute_alignment, compute_hash
from iterthink.db.session import session_scope
from iterthink.persistence import content_repo, paragraph_user_comments

from ..action_chrome import wrap_workspace_action_chrome
from ..components import (
    ACTION_RAIL_ICON_SIZE,
    action_rail_approve_icon_button,
    action_rail_icon_button_style,
    action_rail_reject_icon_button,
    build_action_rectangle,
)
from .. import ui_theme
from ..constants import (
    COMPARE_ACTION_COL_W,
    COMPARE_ACTION_COMMENT_ICON_CX,
    COMPARE_ACTION_COMMENT_ICON_CY,
    COMPARE_ACTION_GRID_CELL,
    COMPARE_ACTION_RAIL_HOVER_WRAP_MIN_H,
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPARE_EVAL_COL_W,
    COMPARE_PILL_COL_W,
    DIFF_SPAN_CHAR_CAP as _DIFF_SPAN_CHAR_CAP,
    TAB_FUTURE,
)
from ..util import ctrl_on_page as _ctrl_on_page
from .candidate_state import CompareCandidateSource

_log = logging.getLogger(__name__)

_COMPARE_HISTORY_CELL_PAD = ft.padding.all(8)


class _HistoryParagraphUIMixin:
    def _review_comment_presence_icon(
        self, *, has_comment: bool, rail_h: float = float(COMPARE_ACTION_RAIL_HOVER_WRAP_MIN_H)
    ) -> ft.Container | None:
        """Non-interactive outline bubble exactly over the comment ``IconButton`` (same size / center)."""
        if not has_comment:
            return None
        iz = float(ACTION_RAIL_ICON_SIZE)
        cx = COMPARE_ACTION_COMMENT_ICON_CX
        cy = COMPARE_ACTION_COMMENT_ICON_CY
        ic = ft.Icon(
            ft.Icons.CHAT_BUBBLE_OUTLINE,
            size=int(iz),
            color=ft.Colors.with_opacity(0.42, config.ON_SURFACE_VARIANT),
        )
        return ft.Container(
            width=float(COMPARE_ACTION_COL_W),
            height=rail_h,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            opacity=1.0,
            content=ft.Stack(
                controls=[
                    ft.Container(
                        left=cx - iz * 0.5,
                        top=cy - iz * 0.5,
                        width=iz,
                        height=iz,
                        alignment=ft.Alignment.CENTER,
                        content=ic,
                    ),
                ],
            ),
        )

    def _build_actions_square(
        self,
        i: int,
        *,
        persistent: bool = False,
        draft_paragraph_index: int | None = None,
        has_user_comment: bool = False,
    ) -> tuple[ft.Container, ft.Container | None, ft.Container | None]:
        """Review: action grid (hover); optional comment glyph shares the same rail footprint."""
        dis_comment = draft_paragraph_index is None

        comment_btn = ft.IconButton(
            ft.Icons.CHAT_BUBBLE_OUTLINE,
            icon_size=ACTION_RAIL_ICON_SIZE,
            icon_color=config.HIGHLIGHT if has_user_comment else config.ON_SURFACE_VARIANT,
            tooltip="Paragraph comment" if not dis_comment else "No paragraph slot for a comment here",
            style=action_rail_icon_button_style(),
            disabled=dis_comment,
            on_click=lambda _e, p=draft_paragraph_index, se=(not has_user_comment): self.page.run_task(
                self._open_ki_comments_for_paragraph_async, p, se
            ),
        )
        act_btn = ft.IconButton(
            ft.Icons.PRECISION_MANUFACTURING,
            icon_size=ACTION_RAIL_ICON_SIZE,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Act",
            style=action_rail_icon_button_style(),
            on_click=lambda _e: self.page.run_task(self._open_ki_act_tab_async),
        )
        actions_inner = build_action_rectangle(
            top_left=action_rail_approve_icon_button(
                on_click=lambda _e, ix=i: self.page.run_task(self._compare_accept_paragraph_async, ix),
            ),
            top_right=action_rail_reject_icon_button(
                on_click=lambda _e, ix=i: self.page.run_task(self._compare_decline_paragraph_async, ix),
            ),
            bottom_left=comment_btn,
            bottom_right=act_btn,
            row_h=COMPARE_ACTION_GRID_CELL,
        )
        inner_chrome, hover = wrap_workspace_action_chrome(actions_inner, persistent=persistent)
        _rail_h = float(COMPARE_ACTION_RAIL_HOVER_WRAP_MIN_H)
        presence = self._review_comment_presence_icon(has_comment=has_user_comment, rail_h=_rail_h)
        if presence is not None:
            rail_content = ft.Stack(
                fit=ft.StackFit.EXPAND,
                width=float(COMPARE_ACTION_COL_W),
                height=_rail_h,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                controls=[
                    ft.Container(
                        width=float(COMPARE_ACTION_COL_W),
                        height=_rail_h,
                        alignment=ft.Alignment.TOP_CENTER,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        content=inner_chrome,
                    ),
                    presence,
                ],
            )
        else:
            rail_content = inner_chrome
        rail = ft.Container(
            width=float(COMPARE_ACTION_COL_W),
            height=_rail_h,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=rail_content,
        )
        return rail, hover, presence

    def _rebuild_compare_paragraph_ui(self) -> None:
        """History tab: eval | old | pill | new — with ghost rows at old positions of moved content.

        Ghost rows ("ghost_moved", "removed") appear at the original old-document position:
          left = struck-through old text, right = empty gap, pill = Moved / Removed.
        Comparison rows appear at the new-document position:
          left = old-aligned text (greyed if moved), right = new text, pill = change kind.

        Only comparison rows populate the tracking lists (_compare_right_fields, etc.) so
        all index-based logic (pill refresh, span refresh, hash checks) stays unchanged.
        """
        self._compare_pill_gen += 1
        older_text = self._compare_editor.value or ""
        newer_text = self._history_newer_side_text()
        if len(older_text) + len(newer_text) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            older_text = older_text[:half] + "\n…"
            newer_text = newer_text[:half] + "\n…"

        display_rows = paragraph_compare.build_history_display_rows(older_text, newer_text)
        comparison_rows = [r for r in display_rows if r.row_type == "comparison"]
        n_comp = len(comparison_rows)

        self._compare_rows_listview.controls.clear()
        self._compare_right_fields.clear()
        self._compare_row_pill_hosts.clear()
        self._compare_left_diff_texts.clear()
        self._compare_right_diff_texts.clear()
        self._compare_eval_hosts.clear()
        self._hide_all_result_card_overlays()

        self._compare_row_stable_texts = [r.new_text for r in comparison_rows]
        self._check_para_hashes = [
            compute_hash(f"{r.old_text}\x1e{r.new_text}") for r in comparison_rows
        ]
        for cid in list(self._check_results.keys()):
            results = self._check_results.get(cid) or []
            if len(results) != n_comp:
                self._check_results[cid] = (results + [None] * n_comp)[:n_comp]

        _MOVED_OPACITY = 0.55
        para_style = self._compare_para_text_style()
        _ghost_fg = ui_theme.editor_text_color()
        # Ghost rows use the same editor foreground + opacity as moved comparison rows
        # so both sides of a moved paragraph read at the same grey level.
        ghost_text_style = ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=_ghost_fg,
            decoration=ft.TextDecoration.LINE_THROUGH,
            decoration_color=_ghost_fg,
        )
        comp_idx = 0

        for row in display_rows:
            is_ghost = row.row_type in ("ghost_moved", "removed")
            # Ghost rows (true movers at old position): show Moved badge + arrow.
            # True-mover comparison rows: ghost already communicates, suppress displacement.
            # Passive-shift comparison rows: show arrow only (no Moved badge).
            if is_ghost:
                pill_disp = row.displacement
                pill_badge = True
            elif row.is_true_mover:
                pill_disp = None
                pill_badge = False
            else:
                pill_disp = row.displacement  # passive: ↑n/↓n arrow, no Moved badge
                pill_badge = False
            pill_host = ft.Container(
                content=self._make_compare_pill_row(
                    row.slot_kind, pill_disp, show_moved_badge=pill_badge
                ),
                width=COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_LEFT,
                padding=ft.padding.only(top=4),
            )

            if is_ghost:
                # Ghost row: struck-through old text on left, empty gap on right.
                # eval column is a fixed-width spacer to keep column alignment.
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
                row_ctrl = ft.Row(
                    [eval_spacer, left_cell, pill_host, right_cell],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
                self._compare_rows_listview.controls.append(row_ctrl)

            else:
                # Comparison row: old-aligned left, new right.
                old_txt = row.old_text
                cur_txt = row.new_text
                left_diff = ft.Text(
                    spans=self._compare_old_side_spans(old_txt, cur_txt),
                    style=para_style,
                    selectable=True,
                    expand=True,
                    no_wrap=False,
                )
                self._compare_left_diff_texts.append(left_diff)
                left_cell = ft.Container(
                    content=left_diff,
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                    opacity=_MOVED_OPACITY if row.is_moved else 1.0,
                )
                right_diff = ft.Text(
                    spans=self._compare_new_side_spans(old_txt, cur_txt),
                    style=para_style,
                    selectable=True,
                    expand=True,
                    no_wrap=False,
                )
                self._compare_right_diff_texts.append(right_diff)
                right_cell = ft.Container(
                    content=right_diff,
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                )
                right_carrier = ft.TextField(
                    value=cur_txt,
                    visible=False,
                    height=0,
                    width=0,
                )
                eval_host = self._build_eval_cell(comp_idx)
                self._compare_eval_hosts.append(eval_host)
                self._compare_row_pill_hosts.append(pill_host)
                self._compare_right_fields.append(right_carrier)
                row_ctrl = ft.Row(
                    [eval_host, left_cell, pill_host, right_cell],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
                self._compare_rows_listview.controls.append(row_ctrl)
                comp_idx += 1

        if _ctrl_on_page(self._compare_rows_listview):
            self._compare_rows_listview.update()

        if self._active_check_id is not None:
            self._refresh_all_eval_cells()

        self._refresh_compare_bulk_buttons()
        self._compare_refine_gen += 1
        self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)

    def _future_review_load_failed_ui(self, exc: BaseException | None = None) -> None:
        """Replace the Review row list with a safe message; reset tracking lists on hard failure."""
        self._future_rows_listview.controls.clear()
        self._future_left_diff_texts.clear()
        self._future_row_pill_hosts.clear()
        self._compare_right_fields.clear()
        self._compare_eval_hosts.clear()
        self._future_row_kinds = []
        self._future_row_cand_idx = []
        self._future_row_stable_texts = []
        self._future_row_old_index = []
        self._future_row_insert_after_old = []
        self._future_eval_cand_indices = []
        msg = (
            "Review could not build the comparison for this file. "
            "Try adding blank lines between sections, or reopen the note."
        )
        if exc is not None:
            msg = f"{msg}\n\n({type(exc).__name__})"
        self._future_rows_listview.controls.append(
            ft.Container(
                content=ft.Text(msg, size=13, color=config.ON_SURFACE_VARIANT, selectable=True),
                alignment=ft.Alignment.TOP_LEFT,
                padding=ft.padding.all(20),
                expand=True,
            )
        )
        if _ctrl_on_page(self._future_rows_listview):
            self._future_rows_listview.update()
        else:
            pg = getattr(self, "page", None)
            if pg is not None:
                try:
                    pg.update()
                except Exception:
                    pass
        if hasattr(self, "_snack"):
            self._snack("Review failed to load.")

    def _rebuild_future_paragraph_ui(self) -> None:
        """Future tab: eval | current | pill | AI proposal | actions.

        Uses ``build_history_display_rows`` (same as History): move ghosts at vacated old
        positions, removed baseline paragraphs with an empty right cell, and comparison
        rows at candidate positions. ``_compare_right_fields`` and parallel ``_future_*``
        lists cover only rows that carry a hidden/editable right TextField (removed +
        comparison), not ``ghost_moved`` rows. ``_future_eval_cand_indices`` maps eval cell
        order to candidate paragraph indices when delete rows precede comparisons.
        """
        if self._compare_candidate_source == CompareCandidateSource.PDF_ORIGINAL:
            self._compare_pill_gen += 1
            self._future_rows_listview.controls.clear()
            self._future_left_diff_texts.clear()
            self._future_row_pill_hosts.clear()
            self._compare_right_fields.clear()
            self._compare_eval_hosts.clear()
            self._future_row_kinds = []
            self._future_row_cand_idx = []
            self._future_row_stable_texts = []
            self._future_row_old_index = []
            self._future_row_insert_after_old = []
            self._future_eval_cand_indices = []
            self._hide_all_result_card_overlays()
            self._rebuild_future_pdf_import_panes()
            self._sync_future_pdf_layers_visibility()
            self._refresh_compare_bulk_buttons()
            return

        try:
            self._rebuild_future_paragraph_md_core()
        except BaseException as ex:
            _log.exception("Review (Future) paragraph UI rebuild failed")
            self._future_review_load_failed_ui(ex)

    def _rebuild_future_paragraph_md_core(self) -> None:
        # Guarantee the change panel is visible before rows reach the client.
        # _apply_active_tab_ui_state() sets visible=True later, but the listview
        # update below runs first; if the panel is still False from a prior
        # non-Review tab visit the rows would be invisible until the state call fires.
        if self._main_tab_index == TAB_FUTURE and not self._review_change_panel.visible:
            self._review_change_panel.visible = True
            self._review_change_panel.expand = True
            if _ctrl_on_page(self._review_change_panel):
                self._review_change_panel.update()

        self._sync_future_pdf_layers_visibility()
        self._compare_pill_gen += 1
        current_text = self.editor.value or ""
        ai_text = self._compare_editor.value or ""
        if len(current_text) + len(ai_text) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            current_text = current_text[:half] + "\n…"
            ai_text = ai_text[:half] + "\n…"

        diffs = compute_alignment(current_text, ai_text)
        display_rows = paragraph_compare.build_history_display_rows(current_text, ai_text)
        comparison_rows = [r for r in display_rows if r.row_type == "comparison"]
        n_comp = len(comparison_rows)

        self._future_rows_listview.controls.clear()
        self._future_left_diff_texts.clear()
        self._future_row_pill_hosts.clear()
        self._compare_right_fields.clear()
        self._compare_eval_hosts.clear()
        self._future_row_kinds = []
        self._future_row_cand_idx = []
        self._future_row_stable_texts = []
        self._future_row_old_index = []
        self._future_row_insert_after_old = []
        self._future_eval_cand_indices = []
        self._hide_all_result_card_overlays()

        self._check_para_hashes = [
            compute_hash(f"{r.old_text}\x1e{r.new_text}") for r in comparison_rows
        ]
        for cid in list(self._check_results.keys()):
            results = self._check_results.get(cid) or []
            if len(results) != n_comp:
                self._check_results[cid] = (results + [None] * n_comp)[:n_comp]

        _MOVED_OPACITY = 0.55
        para_style = self._compare_para_text_style()
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
        eval_spacer_w = COMPARE_EVAL_COL_W
        comp_idx = 0
        field_idx = 0

        for row in display_rows:
            is_ghost = row.row_type in ("ghost_moved", "removed")
            if is_ghost:
                pill_disp = row.displacement
                pill_badge = True
            elif row.is_true_mover:
                pill_disp = None
                pill_badge = False
            else:
                pill_disp = row.displacement
                pill_badge = False

            pill_host = ft.Container(
                content=self._make_compare_pill_row(
                    row.slot_kind, pill_disp, show_moved_badge=pill_badge
                ),
                width=COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_LEFT,
                padding=ft.padding.only(top=4),
            )

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
                ghost_row_cells: list[ft.Control] = [
                    eval_ctrl,
                    left_cell,
                    pill_host,
                    right_cell,
                ]
                # Match comparison/removed rows: reserve action rail width so expand columns
                # split the same way and the pill column stays aligned.
                if show_actions:
                    ghost_row_cells.append(
                        ft.Container(
                            width=COMPARE_ACTION_COL_W,
                            height=float(COMPARE_ACTION_RAIL_HOVER_WRAP_MIN_H),
                        )
                    )
                row_inner = ft.Row(
                    ghost_row_cells,
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
                self._future_rows_listview.controls.append(row_inner)
                continue

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
                right_tf = ft.TextField(
                    **right_tf_kwargs,
                    value="",
                    read_only=True,
                    visible=False,
                )
                right_cell = ft.Container(
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                )
                self._future_row_kinds.append("delete")
                self._future_row_cand_idx.append(None)
                self._future_row_stable_texts.append(row.old_text)
                self._future_row_old_index.append(row.old_paragraph_index)
                self._future_row_insert_after_old.append(-1)
                self._compare_right_fields.append(right_tf)
                eval_ctrl = ft.Container(width=eval_spacer_w)
                row_cells: list[ft.Control] = [
                    eval_ctrl,
                    left_cell,
                    pill_host,
                    right_cell,
                ]
                if show_actions:
                    actions_ctrl, hover_wrap_future, presence_host = self._build_actions_square(
                        field_idx,
                        draft_paragraph_index=None,
                        has_user_comment=False,
                    )
                    row_cells.append(actions_ctrl)
                    row_inner = ft.Row(
                        row_cells,
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    )
                    row_wrap = ft.Container(
                        content=row_inner,
                        on_hover=lambda e, w=hover_wrap_future, ph=presence_host: self._on_compare_row_hover(
                            e, w, ph
                        ),
                    )
                    self._future_rows_listview.controls.append(row_wrap)
                else:
                    self._future_rows_listview.controls.append(
                        ft.Row(
                            row_cells,
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        )
                    )
                field_idx += 1
                continue

            # comparison row (new-side slot)
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
            right_tf = ft.TextField(
                **right_tf_kwargs,
                value=cur_txt,
                read_only=False,
                enable_interactive_selection=True,
                hint_text="…",
                expand=True,
                on_change=lambda _e, ix=field_idx: self._on_compare_para_field_change(ix),
            )
            right_cell = ft.Container(
                content=right_tf,
                expand=1,
                padding=_COMPARE_HISTORY_CELL_PAD,
            )
            self._compare_right_fields.append(right_tf)
            eval_host = self._build_eval_cell(comp_idx)
            self._compare_eval_hosts.append(eval_host)
            self._future_row_pill_hosts.append(pill_host)
            self._future_eval_cand_indices.append(row.new_paragraph_index)
            comp_idx += 1

            row_cells = [
                eval_host,
                left_cell,
                pill_host,
                right_cell,
            ]
            if show_actions:
                cand_pi = row.new_paragraph_index
                has_uc = cand_pi in future_user_comments
                actions_ctrl, hover_wrap_future, presence_host = self._build_actions_square(
                    field_idx,
                    draft_paragraph_index=int(cand_pi),
                    has_user_comment=has_uc,
                )
                row_cells.append(actions_ctrl)
                row_inner = ft.Row(
                    row_cells,
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
                row_wrap = ft.Container(
                    content=row_inner,
                    on_hover=lambda e, w=hover_wrap_future, ph=presence_host: self._on_compare_row_hover(
                        e, w, ph
                    ),
                )
                self._future_rows_listview.controls.append(row_wrap)
            else:
                self._future_rows_listview.controls.append(
                    ft.Row(
                        row_cells,
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    )
                )
            field_idx += 1

        if _ctrl_on_page(self._future_rows_listview):
            self._future_rows_listview.update()
        else:
            pg = getattr(self, "page", None)
            if pg is not None:
                try:
                    pg.update()
                except Exception:
                    pass

        if (
            self._main_tab_index == TAB_FUTURE
            and int(getattr(self, "_review_subtab_index", 0)) == 1
            and hasattr(self, "_sync_impact_paragraph_list_after_compare_rebuild")
        ):
            try:
                self._sync_impact_paragraph_list_after_compare_rebuild()
            except Exception:
                _log.exception("Impact paragraph list sync after Review rebuild failed")

        _log.debug(
            "review_future_rebuild row_specs=%s list_controls=%s change_panel_visible=%s",
            len(display_rows),
            len(self._future_rows_listview.controls),
            self._review_change_panel.visible,
        )

        if self._active_check_id is not None:
            self._refresh_all_eval_cells()

        self._refresh_compare_bulk_buttons()
        if self._compare_candidate_source != CompareCandidateSource.SPELL_PREVIEW:
            self._compare_refine_gen += 1
            self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)
