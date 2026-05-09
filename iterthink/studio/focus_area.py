"""Focus Area (Present) tab: editor, margin LLM actions, inline rename."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import flet as ft
import httpx

from iterthink import config
from iterthink import prompts
from iterthink.db.session import session_scope
from iterthink.prompts import TOPIC_CHANGE, TOPIC_DISCUSS, TOPIC_EVALUATE
from iterthink.persistence import version_storage
from .components import (
    ACTION_RAIL_ICON_SIZE,
    action_rail_icon_button_style,
    sparkle_margin_popup_menu,
)
from iterthink.compare.margin import paragraph_index_at_offset, replace_paragraph_at_index, split_paragraphs
from iterthink.ai.llm_router import remote_http_error_message
from iterthink.ai.ollama_util import chat_response_text, chat_stream_delta, ollama_error_message
from .constants import (
    AUTOSAVE_DISK_IDLE_SEC,
    AUTOSAVE_SNAPSHOT_IDLE_SEC,
    COMPOSE_READING_WIDTH_FRAC,
    PROJECT_PAGE_URL as _PROJECT_PAGE_URL,
    READING_MAX_PX,
    TAB_FUTURE,
    TAB_HISTORY,
    TAB_PRESENT,
)
from .util import ctrl_on_page as _ctrl_on_page


_TOPIC_MENU_LABEL: dict[str, str] = {
    TOPIC_DISCUSS: "Discuss",
    TOPIC_CHANGE: "Change",
    TOPIC_EVALUATE: "Evaluate",
}

_CHANGE_REPLY_PREAMBLE = re.compile(
    r"(?is)^\s*(?:"
    r"(?:here(?:'s|\s+is)\s+)?(?:the\s+)?(?:corrected|rewritten|revised|fixed|updated)\s+text\s*[.:：]?\s*"
    r"|here(?:'s|\s+is)\s+(?:your\s+)?(?:the\s+)?(?:corrected|rewritten)\s+(?:text|paragraph|version)\s*[.:：]?\s*"
    r"|\*\*(?:here(?:'s|\s+is)\s+)?(?:the\s+)?(?:corrected|rewritten)\s+text\*\*\s*[.:：]?\s*"
    r")+"
)


def _strip_change_topic_preamble(text: str) -> str:
    t = text.strip()
    for _ in range(4):
        nxt = _CHANGE_REPLY_PREAMBLE.sub("", t).strip()
        if nxt == t:
            return t
        t = nxt
    return t


def _ki_topic_index_for_prompt_topic(topic: str) -> int:
    """Map prompts.yaml margin action topic to KI tab strip index (Discuss / Change only)."""
    t = (topic or "").strip()
    if t == TOPIC_CHANGE:
        return 1
    return 0


class MarkdownStudioCompose:
    def _paragraph_for_index(self, idx: int) -> str:
        parts = split_paragraphs(self.editor.value or "")
        if not parts:
            return ""
        if idx < 0 or idx >= len(parts):
            return ""
        return parts[idx]

    async def _open_project_page(self) -> None:
        u = (_PROJECT_PAGE_URL or "").strip()
        if u:
            self.page.launch_url(u)

    # --- Editor right-click context menu ---

    def _ctx_item_header(self, label: str) -> ft.PopupMenuItem:
        return ft.PopupMenuItem(
            content=ft.Text(
                label,
                size=12,
                weight=ft.FontWeight.W_600,
                color=config.ON_SURFACE_VARIANT,
            ),
            height=28,
            disabled=True,
        )

    def _ctx_item_action(
        self, label: str, *, on_click
    ) -> ft.PopupMenuItem:
        return ft.PopupMenuItem(
            content=ft.Text(label, size=13, color=config.ON_SURFACE),
            on_click=on_click,
        )

    def _ctx_item_prompt(self, action: prompts.MarginAction) -> ft.PopupMenuItem:
        return ft.PopupMenuItem(
            content=ft.Container(
                content=ft.Text(action.label, size=13, color=config.ON_SURFACE),
                padding=ft.padding.only(left=12),
            ),
            on_click=lambda _e, aid=action.id: self._ctx_run_prompt(aid),
        )

    def _build_editor_ctx_menu_items(self) -> list[ft.PopupMenuItem]:
        items: list[ft.PopupMenuItem] = []
        items.append(
            self._ctx_item_action(
                "Start {yourcompany}os process",
                on_click=lambda _e: self._ctx_open_company_workflow(),
            )
        )
        items.append(ft.PopupMenuItem())
        items.append(self._ctx_item_header("Discuss"))
        for a in sorted(
            prompts.actions_for_topic(TOPIC_DISCUSS),
            key=lambda x: x.label.casefold(),
        ):
            items.append(self._ctx_item_prompt(a))
        items.append(ft.PopupMenuItem())
        items.append(self._ctx_item_header("Edit"))
        for a in sorted(
            prompts.actions_for_topic(TOPIC_CHANGE),
            key=lambda x: x.label.casefold(),
        ):
            items.append(self._ctx_item_prompt(a))
        items.append(ft.PopupMenuItem())
        items.append(
            self._ctx_item_action(
                "Cut",
                on_click=lambda _e: self.page.run_task(self._ctx_clipboard_cut),
            )
        )
        items.append(
            self._ctx_item_action(
                "Copy",
                on_click=lambda _e: self.page.run_task(self._ctx_clipboard_copy),
            )
        )
        items.append(
            self._ctx_item_action(
                "Paste",
                on_click=lambda _e: self.page.run_task(self._ctx_clipboard_paste),
            )
        )
        items.append(
            self._ctx_item_action(
                "Select All",
                on_click=lambda _e: self._ctx_select_all(),
            )
        )
        return items

    def _ctx_open_company_workflow(self) -> None:
        self.page.run_task(self._open_project_page)

    def _ctx_run_prompt(self, action_id: str) -> None:
        if not self.right_open:
            self.toggle_right()
        self.page.run_task(self._quick_margin_action, action_id)

    def _ctx_selection_range(self) -> tuple[int, int] | None:
        sel = self.editor.selection
        if sel is not None and not sel.is_collapsed:
            return int(sel.start), int(sel.end)
        span = getattr(self, "_compose_sel_span", None)
        if span is not None:
            return int(span[0]), int(span[1])
        return None

    async def _ctx_clipboard_copy(self) -> None:
        rng = self._ctx_selection_range()
        if rng is None:
            return
        a, b = rng
        buf = self.editor.value or ""
        if a < 0 or b > len(buf) or a >= b:
            return
        text = buf[a:b]
        if not text:
            return
        await self.page.clipboard.set(text)

    async def _ctx_clipboard_cut(self) -> None:
        rng = self._ctx_selection_range()
        if rng is None:
            return
        a, b = rng
        buf = self.editor.value or ""
        if a < 0 or b > len(buf) or a >= b:
            return
        text = buf[a:b]
        if not text:
            return
        await self.page.clipboard.set(text)
        self.editor.value = buf[:a] + buf[b:]
        self.editor.selection = ft.TextSelection(a, a)
        self._compose_sel_span = None
        if _ctrl_on_page(self.editor):
            self.editor.update()
        self._after_editor_programmatic_change()

    async def _ctx_clipboard_paste(self) -> None:
        try:
            text = await self.page.clipboard.get()
        except BaseException:
            text = None
        if not text:
            return
        buf = self.editor.value or ""
        rng = self._ctx_selection_range()
        if rng is not None:
            a, b = rng
            if 0 <= a <= b <= len(buf):
                self.editor.value = buf[:a] + text + buf[b:]
                caret = a + len(text)
            else:
                self.editor.value = buf + text
                caret = len(self.editor.value or "")
        else:
            sel = self.editor.selection
            caret = int(sel.start) if sel is not None else len(buf)
            caret = max(0, min(caret, len(buf)))
            self.editor.value = buf[:caret] + text + buf[caret:]
            caret = caret + len(text)
        self.editor.selection = ft.TextSelection(caret, caret)
        self._compose_sel_span = None
        if _ctrl_on_page(self.editor):
            self.editor.update()
        self._after_editor_programmatic_change()

    def _ctx_select_all(self) -> None:
        buf = self.editor.value or ""
        if not buf:
            return
        self.editor.focus()
        self.editor.selection = ft.TextSelection(0, len(buf))
        self._compose_sel_span = (0, len(buf))
        if _ctrl_on_page(self.editor):
            self.editor.update()

    def _refresh_editor_ctx_menu_items(self) -> None:
        ctx = getattr(self, "_editor_ctx_menu", None)
        if ctx is None:
            return
        ctx.items = self._build_editor_ctx_menu_items()
        if _ctrl_on_page(ctx):
            ctx.update()

    def _on_compose_editor_area_secondary_down(self, e: ft.TapEvent) -> None:
        """Open editor context menu at pointer; ContextMenu.open() uses items=."""
        gp = e.global_position
        if gp is None:
            return
        self.page.run_task(
            self._open_editor_ctx_menu_at_global_async, float(gp.x), float(gp.y)
        )

    async def _open_editor_ctx_menu_at_global_async(self, gx: float, gy: float) -> None:
        ctx = getattr(self, "_editor_ctx_menu", None)
        if ctx is None:
            return
        await ctx.open(global_position=ft.Offset(gx, gy))

    def _after_editor_programmatic_change(self) -> None:
        """Mirror the bookkeeping _on_editor_change does after a programmatic mutation (cut/paste)."""
        self._refresh_title_bar()
        if self._main_tab_index == TAB_PRESENT:
            self._margin_gen += 1
            gen = self._margin_gen
            self.page.run_task(self._debounced_compose_rebuild, gen)
        if not self.current_path:
            return
        self._kick_debounced_autosave()

    def _cancel_autosave_timers(self) -> None:
        """Invalidate pending disk/snapshot idle saves (e.g. tab switch, boundary save)."""
        self._disk_autosave_gen += 1
        self._snapshot_autosave_gen += 1

    def _kick_debounced_autosave(self) -> None:
        """After edits: debounced disk flush + long-idle snapshot."""
        if not self.current_path:
            return
        self._disk_autosave_gen += 1
        self._snapshot_autosave_gen += 1
        dgen = self._disk_autosave_gen
        sgen = self._snapshot_autosave_gen
        self.page.run_task(self._disk_autosave_after_idle, dgen)
        self.page.run_task(self._snapshot_autosave_after_idle, sgen)

    def _compose_snapshot_margin_selection_for_menu(self) -> None:
        """Capture selection before PopupMenuButton focus clears it (tap-down / menu open)."""
        buf = self.editor.value or ""
        sel = self.editor.selection
        snap_start: int | None = None
        snap_end: int | None = None
        if sel is not None and not sel.is_collapsed:
            raw = sel.get_selected_text(buf)
            if raw.strip():
                self._compose_margin_menu_snap = (raw, int(sel.start))
                snap_start, snap_end = int(sel.start), int(sel.end)
        if snap_start is None:
            span = getattr(self, "_compose_sel_span", None)
            if span is not None:
                a, b = span
                if 0 <= a <= b <= len(buf):
                    raw = buf[a:b]
                    if raw.strip():
                        self._compose_margin_menu_snap = (raw, int(a))
                        snap_start, snap_end = int(a), int(b)
        if snap_start is None:
            self._compose_margin_menu_snap = None
            return
        # Best-effort: re-focus the editor and re-apply the selection so the highlight
        # is repainted right before the popup overlay grabs focus. Flet may still steal
        # focus once the menu opens; the chat bubble quote remains the durable marker.
        try:
            self.editor.focus()
            self.editor.selection = ft.TextSelection(snap_start, snap_end)
            if _ctrl_on_page(self.editor):
                self.editor.update()
        except BaseException:
            pass

    def _compose_clear_margin_menu_snap(self) -> None:
        self._compose_margin_menu_snap = None

    async def _quick_margin_action(self, action_id: str, *, use_menu_snap: bool = True) -> None:
        if not use_menu_snap:
            self._compose_clear_margin_menu_snap()
        buf = self.editor.value or ""
        tf = self.editor
        sel = tf.selection
        selected = ""
        off = int(sel.start) if sel is not None else 0
        replace_span: tuple[int, int] | None = None

        if sel is not None and not sel.is_collapsed:
            t = sel.get_selected_text(buf).strip()
            if t:
                selected = t
                off = int(sel.start)
                replace_span = (int(sel.start), int(sel.end))

        if not selected and use_menu_snap:
            snap = self._compose_margin_menu_snap
            if snap is not None:
                raw, snap_off = snap
                if raw.strip():
                    selected = raw.strip()
                    off = snap_off
                    replace_span = (snap_off, snap_off + len(raw))

        if not selected:
            span = getattr(self, "_compose_sel_span", None)
            if span is not None:
                a, b = span
                if 0 <= a <= b <= len(buf):
                    chunk = buf[a:b]
                    if chunk.strip():
                        selected = chunk.strip()
                        off = a
                        replace_span = (a, b)

        idx = paragraph_index_at_offset(buf, off)
        try:
            await self.run_margin_action(
                action_id,
                idx,
                text_override=selected if selected else None,
                replace_span=replace_span,
            )
        finally:
            if use_menu_snap:
                self._compose_clear_margin_menu_snap()

    def _sparkle_margin_popup_menu_wrap(
        self,
        *,
        tooltip: str,
        for_compare: bool,
        compact: bool,
        items: list[ft.PopupMenuItem],
    ) -> ft.Control:
        if for_compare:
            return sparkle_margin_popup_menu(
                tooltip=tooltip,
                for_compare=for_compare,
                compact=compact,
                items=items,
            )
        return sparkle_margin_popup_menu(
            tooltip=tooltip,
            for_compare=for_compare,
            compact=compact,
            items=items,
            on_menu_open=self._compose_snapshot_margin_selection_for_menu,
            on_menu_cancel=self._compose_clear_margin_menu_snap,
        )

    def _refresh_compose_tab_label(self) -> None:
        if self._compose_tab_inline_rename_active:
            return
        if not self.current_path:
            self._compose_tab_filename_text.value = "—"
            self._compose_tab_filename_text.color = config.ON_SURFACE_VARIANT
            self._compose_tab_filename_text.style = None
            self._compose_tab_filename_hit.mouse_cursor = ft.MouseCursor.BASIC
            self._compose_tab_filename_hit.tooltip = "Open a note first"
        else:
            self._compose_tab_filename_text.value = self.current_path.stem
            self._compose_tab_filename_text.color = config.ON_SURFACE
            self._compose_tab_filename_text.style = None
            self._compose_tab_filename_hit.mouse_cursor = ft.MouseCursor.CLICK
            self._compose_tab_filename_hit.tooltip = "Click to rename"
        if _ctrl_on_page(self._compose_tab_filename_text):
            self._compose_tab_filename_text.update()
        if _ctrl_on_page(self._compose_tab_filename_hit):
            self._compose_tab_filename_hit.update()

    def _compose_tab_exit_rename_mode(self) -> None:
        self._compose_tab_inline_rename_active = False
        self._compose_tab_filename_hit.visible = True
        self._compose_tab_filename_field.visible = False
        self._compose_tab_filename_suffix_text.visible = False
        self._refresh_compose_tab_label()
        if _ctrl_on_page(self._compose_tab_filename_hit):
            self._compose_tab_filename_hit.update()
        if _ctrl_on_page(self._compose_tab_filename_field):
            self._compose_tab_filename_field.update()
        if _ctrl_on_page(self._compose_tab_filename_suffix_text):
            self._compose_tab_filename_suffix_text.update()
        if _ctrl_on_page(self._compose_tab_filename_row):
            self._compose_tab_filename_row.update()

    def _on_compose_tab_filename_tap(self, _e: ft.ControlEvent) -> None:
        if not self.current_path:
            self._snack("Open or create a note first.")
            return
        self.page.run_task(self._compose_tab_begin_inline_rename_async)

    async def _compose_tab_begin_inline_rename_async(self) -> None:
        async with self._compose_tab_rename_lock:
            if not self.current_path:
                return
            if self._compose_tab_inline_rename_active:
                return
            self._compose_tab_inline_rename_active = True
            self._compose_tab_filename_field.value = self.current_path.stem
            self._compose_tab_filename_suffix_text.value = self.current_path.suffix
            self._compose_tab_filename_suffix_text.visible = bool(self.current_path.suffix)
            self._compose_tab_filename_hit.visible = False
            self._compose_tab_filename_field.visible = True
            if _ctrl_on_page(self._compose_tab_filename_field):
                self._compose_tab_filename_field.update()
            if _ctrl_on_page(self._compose_tab_filename_suffix_text):
                self._compose_tab_filename_suffix_text.update()
            if _ctrl_on_page(self._compose_tab_filename_hit):
                self._compose_tab_filename_hit.update()
            if _ctrl_on_page(self._compose_tab_filename_row):
                self._compose_tab_filename_row.update()
        await asyncio.sleep(0.05)
        await self._compose_tab_filename_field.focus()

    def _on_compose_tab_rename_field_submit(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._compose_tab_commit_inline_rename)

    def _on_compose_tab_rename_field_blur(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._compose_tab_commit_inline_rename)

    async def _compose_tab_cancel_inline_rename(self) -> None:
        async with self._compose_tab_rename_lock:
            if not self._compose_tab_inline_rename_active:
                return
            self._compose_tab_exit_rename_mode()

    async def _compose_tab_commit_inline_rename(self) -> None:
        async with self._compose_tab_rename_lock:
            if not self._compose_tab_inline_rename_active:
                return
            old = self.current_path
            if not old:
                self._compose_tab_exit_rename_mode()
                return
            stem = (self._compose_tab_filename_field.value or "").strip()
            if not stem or stem in (".", ".."):
                self._snack("Invalid filename.")
                self._compose_tab_exit_rename_mode()
                return
            if "/" in stem or "\\" in stem or "\x00" in stem:
                self._snack("Use a single filename only.")
                self._compose_tab_exit_rename_mode()
                return
            name = stem + old.suffix
            new_path = old.parent / name
            if new_path.resolve() == old.resolve():
                self._compose_tab_exit_rename_mode()
                return
            if new_path.exists():
                self._snack("A file or folder with that name already exists.")
                self._compose_tab_exit_rename_mode()
                return
            old_r = old.resolve()
            new_r = new_path.resolve()
            try:
                old.rename(new_path)
            except OSError as ex:
                self._snack(f"Could not rename: {ex}")
                self._compose_tab_exit_rename_mode()
                return
            try:
                with session_scope() as s:
                    st = version_storage.update_document_path_after_rename(s, old_r, new_r)
                if st == "collision":
                    try:
                        new_path.rename(old)
                    except OSError:
                        pass
                    self._snack("Rename blocked: another document already uses that path in the library.")
                    self._compose_tab_exit_rename_mode()
                    return
            except BaseException:
                try:
                    new_path.rename(old)
                except OSError:
                    pass
                self._snack("Could not update document history after rename.")
                self._compose_tab_exit_rename_mode()
                return
            self.current_path = new_path
            self._compose_tab_exit_rename_mode()
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            self._refresh_compare_tab_candidate_ui()
            self._sync_version_toolbar_state()
            self._refresh_title_bar()
            self._snack(f'Renamed to "{name}".')

    def _on_compose_reading_wrap_size(self, e: ft.LayoutSizeChangeEvent) -> None:
        """Size reading card width from available compose column width."""
        avail = max(200.0, float(e.width))
        content_w = int(
            min(float(READING_MAX_PX), max(240.0, avail * COMPOSE_READING_WIDTH_FRAC))
        )
        reading_w = content_w
        cur = int(self._compose_reading_card.width or 0)
        if cur == reading_w:
            return
        self._compose_reading_card.width = reading_w
        if _ctrl_on_page(self._compose_reading_card):
            self._compose_reading_card.update()
        self._margin_gen += 1
        self.page.run_task(self._debounced_compose_rebuild, self._margin_gen)

    def _on_editor_change(self, _e: ft.ControlEvent) -> None:
        self._compose_sel_span = None
        self._refresh_title_bar()
        if self._main_tab_index == TAB_PRESENT:
            self._margin_gen += 1
            gen = self._margin_gen
            self.page.run_task(self._debounced_compose_rebuild, gen)
        if (
            self._main_tab_index == TAB_HISTORY
            and self._compare_candidate_source == "docx_original"
            and self._compare_newer_version_id is None
        ):
            self._refresh_compare_diff_immediate()
        if not self.current_path:
            return
        self._kick_debounced_autosave()

    async def _debounced_compose_rebuild(self, gen: int) -> None:
        await asyncio.sleep(0.05)
        if gen != self._margin_gen:
            return
        if self._main_tab_index != TAB_PRESENT:
            return
        self._refresh_compose_plan_surface()

    async def _disk_autosave_after_idle(self, gen: int) -> None:
        await asyncio.sleep(AUTOSAVE_DISK_IDLE_SEC)
        if gen != self._disk_autosave_gen:
            return
        if not self._is_dirty():
            return
        await self.save_file(silent=True, persist_snapshot=False)

    async def _snapshot_autosave_after_idle(self, gen: int) -> None:
        await asyncio.sleep(AUTOSAVE_SNAPSHOT_IDLE_SEC)
        if gen != self._snapshot_autosave_gen:
            return
        if not self._is_dirty():
            return
        await self.save_file(silent=True, snapshot_reason="autosave")

    def _on_selection_change(self, e: ft.TextSelectionChangeEvent) -> None:
        sel = e.selection
        buf = self.editor.value or ""
        if sel is not None and not sel.is_collapsed:
            t = (e.selected_text or "").strip()
            if t:
                self.last_selection = e.selected_text or ""
                self._compose_sel_span = (int(sel.start), int(sel.end))
            else:
                self._compose_sel_span = None
            return
        if self._compose_sel_span is not None and sel is not None:
            a, b = self._compose_sel_span
            cur = int(sel.start)
            if cur < a or cur > b:
                self._compose_sel_span = None

    def _paragraph_sparkle_menu_control(self, para_index: int, *, for_compare: bool, compact: bool = False) -> ft.Control:
        """Compose: one popup with topic sections + prompts. Compare: Change-topic flat popup."""
        tooltip = (
            "Change prompts for this paragraph"
            if for_compare
            else "LLM prompts for this paragraph"
        )
        _text_sz = 12 if compact else 13

        if not prompts.MARGIN_ACTIONS:
            return self._sparkle_margin_popup_menu_wrap(
                tooltip=tooltip,
                for_compare=for_compare,
                compact=compact,
                items=[
                    ft.PopupMenuItem(
                        content=ft.Text("Add prompts in Settings → Prompts", size=13),
                    ),
                ],
            )

        # Compare action card: only Change-topic actions (no Discuss / Evaluate presets).
        if for_compare:
            change_acts = tuple(sorted(prompts.actions_for_topic(TOPIC_CHANGE), key=lambda a: a.label.casefold()))
            if not change_acts:
                return self._sparkle_margin_popup_menu_wrap(
                    tooltip=tooltip,
                    for_compare=for_compare,
                    compact=compact,
                    items=[
                        ft.PopupMenuItem(
                            content=ft.Text("No Change prompts. Add them in Settings → Prompts", size=13),
                        ),
                    ],
                )
            compare_items = [
                ft.PopupMenuItem(
                    content=ft.Text(a.label, size=_text_sz),
                    on_click=lambda e, aid=a.id, ix=para_index: self.page.run_task(
                        self._run_compare_margin_action, aid, ix
                    ),
                )
                for a in change_acts
            ]
            return self._sparkle_margin_popup_menu_wrap(
                tooltip=tooltip,
                for_compare=for_compare,
                compact=compact,
                items=compare_items,
            )

        rows_for_menu: list[tuple[str, tuple[prompts.MarginAction, ...]]] = []
        for topic in (TOPIC_DISCUSS, TOPIC_CHANGE, TOPIC_EVALUATE):
            acts = prompts.actions_for_topic(topic)
            if not acts:
                continue
            cat_label = _TOPIC_MENU_LABEL.get(topic, topic)
            sorted_acts = tuple(sorted(acts, key=lambda a: a.label.casefold()))
            rows_for_menu.append((cat_label, sorted_acts))

        if not rows_for_menu:
            return self._sparkle_margin_popup_menu_wrap(
                tooltip=tooltip,
                for_compare=for_compare,
                compact=compact,
                items=[
                    ft.PopupMenuItem(content=ft.Text("No prompts for any topic.", size=13)),
                ],
            )

        # Single popup (no MenuBar): avoids horizontal scroll when the sparkle sits in a
        # narrow action cell. Flet has no PopupMenuItem children; topics are section rows.
        compose_items: list[ft.PopupMenuItem] = []
        _hdr_h = 30.0 if compact else 34.0
        _leaf_h = 40.0 if compact else 44.0
        _hdr_color = ft.Colors.with_opacity(0.65, config.ON_SURFACE_VARIANT)
        for cat_label, sorted_acts in rows_for_menu:
            compose_items.append(
                ft.PopupMenuItem(
                    content=ft.Text(cat_label, size=_text_sz, weight=ft.FontWeight.W_600, color=_hdr_color),
                    height=_hdr_h,
                    disabled=True,
                )
            )
            for a in sorted_acts:
                compose_items.append(
                    ft.PopupMenuItem(
                        content=ft.Container(
                            content=ft.Text(a.label, size=_text_sz),
                            padding=ft.padding.only(left=12),
                        ),
                        height=_leaf_h,
                        on_click=lambda e, action_id=a.id: self.page.run_task(
                            self._quick_margin_action, action_id
                        ),
                    )
                )

        return self._sparkle_margin_popup_menu_wrap(
            tooltip=tooltip,
            for_compare=for_compare,
            compact=compact,
            items=compose_items,
        )

    def _on_compare_row_hover(self, e: ft.ControlEvent, actions_wrap: ft.Container) -> None:
        actions_wrap.opacity = 1.0 if e.data else 0.0
        if _ctrl_on_page(actions_wrap):
            actions_wrap.update()

    async def _compose_restore_editor_selection(self, a: int, b: int) -> None:
        await asyncio.sleep(0.06)
        buf = self.editor.value or ""
        if a < 0 or b > len(buf) or a > b:
            return
        await self.editor.focus()
        self.editor.selection = ft.TextSelection(a, b)
        if _ctrl_on_page(self.editor):
            self.editor.update()

    async def run_margin_action(
        self,
        action_id: str,
        idx: int,
        *,
        text_override: str | None = None,
        replace_span: tuple[int, int] | None = None,
    ) -> None:
        act = prompts.get_margin_action(action_id)
        if act is None:
            return
        src = text_override if text_override is not None else self._paragraph_for_index(idx)
        para = src.strip()
        if not para:
            self._snack("This paragraph is empty.")
            return

        self._set_ki_topic(_ki_topic_index_for_prompt_topic(act.topic))

        if replace_span is not None:
            self._append_chat_line("user", f"Selection · {act.label}", quote=para)
        else:
            self._append_chat_line(
                "user", f"Paragraph {idx + 1}: {act.label}", quote=para
            )

        reply = ft.Text("", size=12, selectable=True, color=config.ON_SURFACE)
        footer = ft.Row(spacing=8, visible=False)
        bubble = ft.Column([reply, footer], tight=True, spacing=8)
        wrap = ft.Container(
            content=bubble,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            bgcolor=ft.Colors.with_opacity(0.14, config.OUTLINE),
            border_radius=10,
            alignment=ft.Alignment.CENTER_LEFT,
        )
        self._chat_history.controls.append(wrap)
        if _ctrl_on_page(self._chat_history):
            self._chat_history.update()

        messages: list[dict[str, str]] = [
            {"role": "system", "content": act.system_prompt},
            {"role": "user", "content": act.user_template.format(text=para)},
        ]
        acc = ""
        backend = self._make_llm_backend()
        cm = self.chat_model_for_requests()
        try:
            stream = await backend.chat(model=cm, messages=messages, stream=True)
            async for part in stream:
                acc += chat_stream_delta(part)
                live = _strip_change_topic_preamble(acc) if act.topic == TOPIC_CHANGE else acc
                reply.value = live.strip() or "…"
                if _ctrl_on_page(reply):
                    reply.update()
        except BaseException:
            try:
                resp = await backend.chat(model=cm, messages=messages, stream=False)
                acc = chat_response_text(resp) or ""
            except BaseException as ex_final:
                if isinstance(ex_final, httpx.HTTPStatusError):
                    detail = remote_http_error_message(ex_final)
                elif isinstance(ex_final, ValueError):
                    detail = str(ex_final)
                else:
                    detail = ollama_error_message(ex_final)
                reply.value = f"(Error) {detail}"
                if _ctrl_on_page(reply):
                    reply.update()
                if _ctrl_on_page(self._chat_history):
                    self._chat_history.update()
                return

        acc = (acc or "").strip()
        if act.topic == TOPIC_CHANGE:
            acc = _strip_change_topic_preamble(acc)
        if not acc:
            reply.value = "(Empty reply from model.)"
            if _ctrl_on_page(reply):
                reply.update()
            footer.controls = [
                ft.IconButton(
                    ft.Icons.CLOSE_ROUNDED,
                    icon_size=ACTION_RAIL_ICON_SIZE,
                    icon_color=config.ON_SURFACE_VARIANT,
                    tooltip="Dismiss",
                    style=action_rail_icon_button_style(),
                    on_click=lambda _e, f=footer: self._hide_prompt_footer(f),
                ),
            ]
            footer.visible = True
            if _ctrl_on_page(footer):
                footer.update()
            return

        reply.value = acc
        if _ctrl_on_page(reply):
            reply.update()

        if act.topic == TOPIC_CHANGE and self.current_path:
            buf = self.editor.value or ""
            if replace_span is not None:
                a, b = replace_span
                cand_buf = buf[:a] + acc + buf[b:]
                loc_label = "selection"
            else:
                cand_buf = replace_paragraph_at_index(buf, idx, acc)
                loc_label = f"paragraph {idx + 1}"
            try:
                with session_scope() as s:
                    new_vid = version_storage.persist_version_snapshot(
                        s,
                        self.current_path.resolve(),
                        cand_buf,
                        "ai_proposal",
                        display_label=f"{act.label} - {loc_label}",
                    )
                if new_vid is not None:
                    self._ai_proposal_action_ids[new_vid] = action_id
                    self._latest_ai_proposal_vid = new_vid
                    self._refresh_compare_tab_candidate_ui()
                    if self._main_tab_index == TAB_FUTURE:
                        self._select_proposal_as_review_candidate(new_vid)
                        self._rebuild_future_paragraph_ui()
                        self._refresh_compare_diff_immediate()
            except BaseException:
                pass

        if act.topic == TOPIC_CHANGE:
            apply_snippet = (
                (self.editor.value or "")[replace_span[0] : replace_span[1]]
                if replace_span is not None
                else ""
            )
            apply_btn: ft.IconButton | None = None
            if replace_span is not None:
                apply_btn = ft.IconButton(
                    ft.Icons.CHECK_ROUNDED,
                    icon_size=ACTION_RAIL_ICON_SIZE,
                    icon_color=config.PRIMARY_COLOR,
                    tooltip="Apply: replace the selected range with this reply",
                    style=action_rail_icon_button_style(),
                    on_click=lambda _e, r=reply, f=footer, aid=action_id, sp=replace_span, sn=apply_snippet: self.page.run_task(
                        self._apply_margin_reply_to_selection_async, r, f, aid, sp, sn
                    ),
                )
            review_btn = ft.IconButton(
                ft.Icons.VISIBILITY_OUTLINED,
                icon_size=ACTION_RAIL_ICON_SIZE,
                icon_color=config.ON_SURFACE_VARIANT,
                tooltip="Review: open Compare with this text as candidate",
                style=action_rail_icon_button_style(),
                on_click=lambda _e, i=idx, r=reply, f=footer, aid=action_id: self.page.run_task(
                    self._stage_ai_candidate_async, i, r, f, aid
                ),
            )
            dismiss_btn = ft.IconButton(
                ft.Icons.CLOSE_ROUNDED,
                icon_size=ACTION_RAIL_ICON_SIZE,
                icon_color=config.ON_SURFACE_VARIANT,
                tooltip="Dismiss",
                style=action_rail_icon_button_style(),
                on_click=lambda _e, f=footer: self._hide_prompt_footer(f),
            )
            footer.controls = (
                [apply_btn, review_btn, dismiss_btn] if apply_btn is not None else [review_btn, dismiss_btn]
            )
        else:
            footer.controls = [
                ft.IconButton(
                    ft.Icons.CLOSE_ROUNDED,
                    icon_size=ACTION_RAIL_ICON_SIZE,
                    icon_color=config.ON_SURFACE_VARIANT,
                    tooltip="Dismiss",
                    style=action_rail_icon_button_style(),
                    on_click=lambda _e, f=footer: self._hide_prompt_footer(f),
                ),
            ]
        footer.visible = True
        if _ctrl_on_page(footer):
            footer.update()
