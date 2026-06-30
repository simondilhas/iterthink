"""KI Comments sidebar: grouped thread cards and workspace navigation."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import flet as ft

from iterthink import checks as checks_mod
from iterthink import config
from iterthink import impact_checks
from iterthink.db.session import session_scope
from iterthink.impact_checks import FINDINGS_PARAGRAPH_STATUSES
from iterthink.persistence import impact_annotations, paragraph_user_comments
from iterthink.studio import ui_theme
from iterthink.studio.constants import KI_TOPIC_COMMENTS, TAB_FUTURE, TAB_HISTORY
from iterthink.studio.history.compare_virtual import compare_row_scroll_key
from iterthink.studio.ki_comments import (
    KI_COMMENTS_GROUP_USER,
    CommentThreadItem,
    analyse_group_key,
    analyse_list_key,
    analysis_group_key_for_item,
    impact_list_key,
    paragraph_comment_label,
    partition_thread_items,
    plan_comment_list_label,
    sort_thread_items,
    sorted_comment_rows,
    truncate_preview,
    user_comment_list_key,
    version_line_from_run_context,
)
from iterthink.studio.util import (
    ctrl_on_page as _ctrl_on_page,
    safe_ctrl_mutate as _safe_ctrl_mutate,
    safe_list_scroll_to_key,
)


class KiCommentsSidebarMixin:
    """Grouped KI Comments list, card chrome, and paragraph navigation."""

    def _init_ki_comments_sidebar_fields(self) -> None:
        self._ki_comments_group_expanded: dict[str, bool] = {
            KI_COMMENTS_GROUP_USER: True,
        }
        self._ki_thread_keys_by_cand_idx: dict[int, list[str]] = {}
        self._ki_thread_items_cache: dict[str, CommentThreadItem] = {}
        self._ki_compare_row_highlight_ui_idx: int | None = None
        self._ki_compare_row_highlight_cand_idx: int | None = None

    def _ki_impact_thread_symbol_from_row(self, snap: dict[str, Any]) -> tuple[str, str, bool]:
        st = str(snap.get("status", "") or "")
        color = self._impact_status_color(st)
        if st in FINDINGS_PARAGRAPH_STATUSES:
            return st[:1].upper(), color, True
        label = st.upper()[:3] if st else "·"
        return label, color, False

    def _ki_comment_thread_items(self) -> list[CommentThreadItem]:
        items: list[CommentThreadItem] = []
        for pi, body in sorted_comment_rows(self._ki_comments_for_current_version()):
            items.append(
                CommentThreadItem(
                    kind="user",
                    paragraph_index=int(pi),
                    title=f"{paragraph_comment_label(pi)} · Note",
                    preview=body,
                    list_key=user_comment_list_key(int(pi)),
                    symbol_is_icon=True,
                )
            )
        if not self.current_path:
            return sort_thread_items(items)
        version_fallback = self._ki_analyse_version_line_for_current_ui()
        prompt_id = getattr(self, "_active_impact_prompt_id", None)
        if prompt_id:
            check = impact_checks.get_impact_check(str(prompt_id))
            impact_version_fallback = version_fallback
            try:
                with session_scope() as s:
                    vid = self._resolve_impact_version_id(s)
                    if vid is not None:
                        ann_map = impact_annotations.list_for_version(
                            s,
                            content_version_id=int(vid),
                            prompt_id=str(prompt_id),
                        )
                        for pi, row in sorted(ann_map.items(), key=lambda x: int(x[0])):
                            snap = impact_annotations.snapshot_row_ui(row)
                            preview = str(snap.get("effective_comment") or "").strip()
                            if not preview:
                                continue
                            det = snap.get("details")
                            ctx = impact_annotations.run_context_from_details(
                                det if isinstance(det, dict) else None
                            )
                            version_line = (
                                version_line_from_run_context(ctx)
                                if ctx
                                else impact_version_fallback
                            )
                            sym, sym_color, _ = self._ki_impact_thread_symbol_from_row(snap)
                            items.append(
                                CommentThreadItem(
                                    kind="impact",
                                    paragraph_index=int(pi),
                                    title=paragraph_comment_label(int(pi)),
                                    preview=preview,
                                    list_key=impact_list_key(int(pi), str(prompt_id)),
                                    version_line=version_line,
                                    prompt_id=str(prompt_id),
                                    symbol=sym,
                                    symbol_color=sym_color,
                                )
                            )
            except BaseException:
                pass
        try:
            with session_scope() as s:
                vid = self._resolve_impact_version_id(s)
                if vid is not None:
                    if hasattr(self, "_analyse_display_bodies"):
                        anchor, display = self._analyse_display_bodies()
                    else:
                        buffers = self._active_compare_buffers()
                        display = buffers.candidate or ""
                        anchor = display
                    resolved = paragraph_user_comments.map_analyse_resolved_for_display(
                        s,
                        content_version_id=int(vid),
                        anchor_body=anchor,
                        display_body=display,
                    )
                    for row in resolved:
                        check = checks_mod.get_check(str(row.check_id))
                        if check is None:
                            continue
                        payload = row.payload if isinstance(row.payload, dict) else {}
                        if payload and checks_mod.is_unchanged_paragraph_payload(
                            check, payload
                        ):
                            continue
                        symbol = (
                            checks_mod.effective_symbol(check, payload)
                            if payload
                            else (row.symbol or "?")
                        )
                        sym_color = check.color_for_symbol(symbol)
                        summary = truncate_preview(
                            checks_mod.extract_summary(check, payload)
                            if payload
                            else (row.body or "")
                        )
                        preview = summary or row.body or "(analysis result)"
                        items.append(
                            CommentThreadItem(
                                kind="analyse",
                                paragraph_index=int(row.display_paragraph_index),
                                title=paragraph_comment_label(int(row.display_paragraph_index)),
                                preview=preview,
                                list_key=analyse_list_key(
                                    int(row.display_paragraph_index), str(row.check_id)
                                ),
                                version_line=version_fallback,
                                check_id=str(row.check_id),
                                symbol=symbol,
                                symbol_color=sym_color,
                            )
                        )
        except BaseException:
            pass
        return sort_thread_items(items)

    def _ki_thread_item_for_list_key(self, list_key: str) -> CommentThreadItem | None:
        return getattr(self, "_ki_thread_items_cache", {}).get(str(list_key))

    async def _focus_ki_sidebar_link_for_cand_idx_async(self, cand_idx: int) -> None:
        list_key = self._ki_sidebar_link_key_for_cand_idx(int(cand_idx))
        if not list_key:
            return
        item = self._ki_thread_item_for_list_key(list_key)
        if item is None:
            return
        await self._focus_ki_comment_thread_key_async(list_key, item)

    def _ki_reindex_thread_keys(self, items: list[CommentThreadItem]) -> None:
        by_pi: dict[int, list[str]] = {}
        for item in items:
            by_pi.setdefault(int(item.paragraph_index), []).append(item.list_key)
        self._ki_thread_keys_by_cand_idx = by_pi

    def _ki_sidebar_link_key_for_cand_idx(self, cand_idx: int) -> str | None:
        keys = self._ki_thread_keys_by_cand_idx.get(int(cand_idx)) or []
        if not keys:
            return None
        cid = getattr(self, "_active_check_id", None)
        if cid:
            want = analyse_list_key(int(cand_idx), str(cid))
            if want in keys:
                return want
        want_user = user_comment_list_key(int(cand_idx))
        if want_user in keys:
            return want_user
        return keys[0]

    def _ki_group_key_for_item(self, item: CommentThreadItem) -> str:
        return analysis_group_key_for_item(item)

    def _ki_analysis_group_label(self, item: CommentThreadItem) -> str:
        if item.kind == "analyse":
            check = checks_mod.get_check(str(item.check_id))
            return check.label if check else str(item.check_id)
        if item.kind == "impact":
            check = impact_checks.get_impact_check(str(item.prompt_id))
            return check.label if check else str(item.prompt_id)
        return "Analysis"

    def _ki_grouped_analysis_items(
        self, analysis_items: list[CommentThreadItem]
    ) -> list[tuple[str, str, list[CommentThreadItem]]]:
        """Return (group_key, label, items) preserving paragraph order within each group."""
        order: list[str] = []
        buckets: dict[str, list[CommentThreadItem]] = {}
        labels: dict[str, str] = {}
        for item in analysis_items:
            gk = analysis_group_key_for_item(item)
            if gk not in buckets:
                buckets[gk] = []
                order.append(gk)
                labels[gk] = self._ki_analysis_group_label(item)
            buckets[gk].append(item)
        return [(gk, labels[gk], buckets[gk]) for gk in order]

    def _toggle_ki_comments_group(self, group_key: str) -> None:
        expanded = getattr(self, "_ki_comments_group_expanded", None) or {}
        expanded[group_key] = not bool(expanded.get(group_key, True))
        self._ki_comments_group_expanded = expanded
        self._rebuild_ki_comments_list()

    def _ki_build_thread_symbol_chip(self, item: CommentThreadItem) -> ft.Control:
        if item.symbol_is_icon:
            return ft.Container(
                width=34,
                height=34,
                alignment=ft.Alignment.CENTER,
                border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.12, config.HIGHLIGHT),
                content=ft.Icon(
                    ft.Icons.CHAT_BUBBLE_OUTLINE,
                    size=18,
                    color=config.HIGHLIGHT,
                ),
            )
        sym = item.symbol or "·"
        color = item.symbol_color or config.ON_SURFACE_VARIANT
        return ft.Container(
            width=34,
            height=34,
            alignment=ft.Alignment.CENTER,
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.18, color),
            content=ft.Text(
                sym,
                size=16,
                weight=ft.FontWeight.W_700,
                color=color,
                no_wrap=True,
            ),
        )

    def _ki_build_comments_group_header(
        self, *, group_key: str, label: str, count: int
    ) -> ft.Container:
        expanded = bool(
            getattr(self, "_ki_comments_group_expanded", {}).get(group_key, True)
        )
        chevron = ft.Icons.EXPAND_LESS if expanded else ft.Icons.EXPAND_MORE
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(chevron, size=18, color=config.ON_SURFACE_VARIANT),
                    ft.Text(
                        label,
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=config.ON_SURFACE,
                        expand=True,
                    ),
                    ft.Text(
                        str(count),
                        size=11,
                        color=config.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=4, vertical=6),
            border_radius=6,
            ink=True,
            on_click=lambda _e, g=group_key: self._toggle_ki_comments_group(g),
        )

    async def _on_ki_thread_card_body_async(self, item: CommentThreadItem) -> None:
        if item.kind in ("analyse", "impact") and self._main_tab_index != TAB_FUTURE:
            await self._request_tab_switch_async(TAB_FUTURE)
        lv = getattr(self, "_future_rows_listview", None)
        rebuilt = False
        if self._main_tab_index == TAB_FUTURE and hasattr(self, "_rebuild_future_paragraph_ui"):
            if lv is None or not lv.controls or getattr(self, "_compare_rebuild_pending", False):
                self._rebuild_future_paragraph_ui()
                rebuilt = True
        if rebuilt:
            await asyncio.sleep(0.05)
        await self._scroll_workspace_to_paragraph_async(int(item.paragraph_index))
        await self._focus_ki_comment_thread_key_async(item.list_key, item)

    async def _focus_ki_comment_thread_key_async(
        self, list_key: str, item: CommentThreadItem
    ) -> None:
        if not self.right_open:
            self.toggle_right()
        self._set_ki_topic(KI_TOPIC_COMMENTS)
        group_key = self._ki_group_key_for_item(item)
        expanded = getattr(self, "_ki_comments_group_expanded", None) or {}
        if not expanded.get(group_key, True):
            expanded[group_key] = True
            self._ki_comments_group_expanded = expanded
        if item.kind == "user":
            self._ki_comment_focus = ("user", int(item.paragraph_index))
        elif item.kind == "impact":
            self._ki_comment_focus = ("impact", int(item.paragraph_index), str(item.prompt_id))
        else:
            self._ki_comment_focus = ("analyse", int(item.paragraph_index), str(item.check_id))
            if hasattr(self, "_activate_analyse_check_for_display"):
                self._activate_analyse_check_for_display(str(item.check_id))
        self._comment_para_index = int(item.paragraph_index)
        self._comment_edit_mode = False
        self._ki_comments_detail.visible = True
        self._sync_ki_detail_for_focus()
        self._rebuild_ki_comments_list()
        await self._scroll_ki_comments_to_key(list_key)
        self._sync_ki_comments_detail_visibility()
        if _ctrl_on_page(self._ki_comments_detail):
            self._ki_comments_detail.update()

    def _highlight_compare_row_cand_idx(self, cand_idx: int | None) -> None:
        self._ki_compare_row_highlight_cand_idx = cand_idx
        hosts = getattr(self, "_future_comp_row_hosts", {}) or {}
        for comp_i, host in hosts.items():
            ev = getattr(self, "_future_eval_cand_indices", None)
            row_cand = ev[comp_i] if ev and comp_i < len(ev) else comp_i
            self._apply_compare_row_highlight(host, row_cand == cand_idx)

    def _highlight_compare_row_ui_idx(self, ui_idx: int | None) -> None:
        self._ki_compare_row_highlight_ui_idx = ui_idx
        cand = self._eval_cand_idx(ui_idx) if ui_idx is not None else None
        self._highlight_compare_row_cand_idx(cand)

    @staticmethod
    def _apply_compare_row_highlight(host: ft.Control | None, active: bool) -> None:
        if host is None or not isinstance(host, ft.Container):
            return
        border = (
            ft.border.all(2, config.HIGHLIGHT)
            if active
            else ft.border.all(1, ui_theme.outline_muted(alpha=0.25))
        )
        bgcolor = ft.Colors.with_opacity(0.08, config.HIGHLIGHT) if active else None

        def _apply(c: ft.Control) -> None:
            c.border = border
            c.bgcolor = bgcolor

        _safe_ctrl_mutate(host, _apply)

    async def _scroll_workspace_to_paragraph_async(self, paragraph_index: int) -> None:
        cand_idx = int(paragraph_index)
        scroll_key = compare_row_scroll_key(cand_idx)
        if self._main_tab_index == TAB_FUTURE:
            lv = getattr(self, "_future_rows_listview", None)
            if await safe_list_scroll_to_key(lv, scroll_key, duration=150):
                ui_idx = self._ui_idx_for_eval_cand_idx(cand_idx)
                self._highlight_compare_row_ui_idx(ui_idx)
            return
        if self._main_tab_index == TAB_HISTORY:
            lv = getattr(self, "_compare_rows_listview", None)
            if await safe_list_scroll_to_key(lv, scroll_key, duration=150):
                ui_idx = self._ui_idx_for_eval_cand_idx(cand_idx)
                self._highlight_compare_row_ui_idx(ui_idx)
            return
        if hasattr(self, "editor") and self.editor is not None:
            paras = (self.editor.value or "").split("\n\n")
            off = 0
            for i, p in enumerate(paras):
                if i >= cand_idx:
                    break
                off += len(p) + 2
            self.editor.selection = ft.TextSelection(base_offset=off, extent_offset=off)
            if _ctrl_on_page(self.editor):
                self.editor.update()

    def _ki_comment_thread_card_for_item(
        self, item: CommentThreadItem, *, focus_key: str | None
    ) -> ft.Container:
        highlight = focus_key == item.list_key
        return self._ki_comment_thread_card(
            item=item,
            highlight=highlight,
            on_click=lambda it=item: self.page.run_task(self._on_ki_thread_card_body_async, it),
        )

    def _ki_comment_thread_card(
        self,
        *,
        item: CommentThreadItem,
        highlight: bool,
        on_click: Callable[[], Any],
        italic_preview: bool = False,
    ) -> ft.Container:
        text_col_children: list[ft.Control] = [
            ft.Text(
                item.title,
                size=13,
                weight=ft.FontWeight.W_600,
                color=config.ON_SURFACE,
            ),
        ]
        if item.version_line:
            text_col_children.append(
                ft.Text(
                    item.version_line,
                    size=10,
                    color=config.ON_SURFACE_VARIANT,
                )
            )
        text_col_children.append(
            ft.Text(
                item.preview,
                size=12,
                selectable=True,
                color=config.ON_SURFACE,
                italic=italic_preview,
            )
        )
        body = ft.Container(
            content=ft.Row(
                [
                    self._ki_build_thread_symbol_chip(item),
                    ft.Column(text_col_children, tight=True, spacing=4, expand=True),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            ink=True,
            on_click=lambda _e: on_click(),
        )
        return ft.Container(
            key=ft.ScrollKey(item.list_key),
            content=body,
            padding=ft.padding.symmetric(horizontal=4, vertical=4),
            border_radius=8,
            bgcolor=(
                ft.Colors.with_opacity(0.12, config.HIGHLIGHT) if highlight else None
            ),
            border=(
                ft.border.all(1, config.HIGHLIGHT)
                if highlight
                else ft.border.all(1, ui_theme.outline_muted(alpha=0.25))
            ),
        )

    def _rebuild_ki_comments_list(self) -> None:
        lv = getattr(self, "_ki_comments_list", None)
        if lv is None:
            return
        focus_key = self._ki_comment_focus_key()
        if self._ki_comments_use_plan_labels():
            rows = self._ki_plan_comment_rows_for_list()
            controls: list[ft.Control] = []
            if not rows:
                controls.append(
                    ft.Text(
                        "No comments in this note yet.",
                        size=12,
                        color=config.ON_SURFACE_VARIANT,
                        italic=True,
                    )
                )
            else:
                for pi, body in rows:
                    highlight = focus_key == user_comment_list_key(int(pi))
                    meta = self._ki_plan_comment_meta(int(pi))
                    title = (
                        plan_comment_list_label(meta[0], meta[1])
                        if meta is not None
                        else paragraph_comment_label(pi)
                    )
                    item = CommentThreadItem(
                        kind="user",
                        paragraph_index=int(pi),
                        title=title,
                        preview=body or "(no comment text)",
                        list_key=user_comment_list_key(int(pi)),
                        symbol_is_icon=True,
                    )
                    controls.append(
                        self._ki_comment_thread_card(
                            item=item,
                            highlight=highlight,
                            on_click=lambda p=int(pi): self.page.run_task(
                                self._open_ki_comments_for_paragraph_async, p, False
                            ),
                            italic_preview=not bool(body),
                        )
                    )
            lv.controls = controls
            if _ctrl_on_page(lv):
                lv.update()
            return

        items = self._ki_comment_thread_items()
        self._ki_reindex_thread_keys(items)
        self._ki_thread_items_cache = {it.list_key: it for it in items}
        user_items, analysis_items = partition_thread_items(items)
        controls: list[ft.Control] = []
        if not items:
            controls.append(
                ft.Text(
                    "No comments or impact results in this note yet.",
                    size=12,
                    color=config.ON_SURFACE_VARIANT,
                    italic=True,
                )
            )
        else:
            expanded = getattr(self, "_ki_comments_group_expanded", {}) or {}
            if user_items:
                controls.append(
                    self._ki_build_comments_group_header(
                        group_key=KI_COMMENTS_GROUP_USER,
                        label="User comments",
                        count=len(user_items),
                    )
                )
                if expanded.get(KI_COMMENTS_GROUP_USER, True):
                    for item in user_items:
                        controls.append(
                            self._ki_comment_thread_card_for_item(item, focus_key=focus_key)
                        )
            for group_key, label, group_items in self._ki_grouped_analysis_items(analysis_items):
                if group_key not in expanded:
                    expanded[group_key] = True
                controls.append(
                    self._ki_build_comments_group_header(
                        group_key=group_key,
                        label=label,
                        count=len(group_items),
                    )
                )
                if expanded.get(group_key, True):
                    for item in group_items:
                        controls.append(
                            self._ki_comment_thread_card_for_item(item, focus_key=focus_key)
                        )
            self._ki_comments_group_expanded = expanded
        lv.controls = controls
        if _ctrl_on_page(lv):
            lv.update()
        if hasattr(self, "_refresh_all_eval_cells"):
            self._refresh_all_eval_cells()
