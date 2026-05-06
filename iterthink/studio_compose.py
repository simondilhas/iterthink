"""Compose tab: editor, sparkle menus, margin LLM actions, inline rename."""

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
from iterthink import version_storage
from iterthink.margin import (
    distribute_heights,
    estimate_total_editor_height,
    paragraph_compose_slot_weights,
    paragraph_index_at_offset,
    replace_paragraph_at_index,
    split_paragraphs,
)
from iterthink.llm_router import remote_http_error_message
from iterthink.ollama_util import chat_response_text, chat_stream_delta, ollama_error_message
from iterthink.studio_constants import (
    AUTOSAVE_IDLE_SEC,
    COMPOSE_MARGIN_COL_W,
    COMPOSE_READING_WIDTH_FRAC,
    KI_TAB_ICON_PX,
    PROJECT_PAGE_URL as _PROJECT_PAGE_URL,
    READING_MAX_PX,
)
from iterthink.studio_util import ctrl_on_page as _ctrl_on_page


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


def _compare_grid_slot(content: ft.Control, *, row_h: float, expand: bool) -> ft.Container:
    """One quadrant: equal flex width when expand=True; icon centered (fixes right-edge skew)."""
    return ft.Container(
        expand=expand,
        height=row_h,
        alignment=ft.Alignment.CENTER,
        content=content,
    )


def _ki_topic_index_for_prompt_topic(topic: str) -> int:
    """Map prompts.yaml margin action topic to KI tab strip index (Discuss / Change only)."""
    t = (topic or "").strip()
    if t == TOPIC_CHANGE:
        return 1
    return 0


class MarkdownStudioCompose:
    async def _quick_margin_action(self, action_id: str) -> None:
        buf = self.editor.value or ""
        tf = self.editor
        sel = tf.selection
        selected = ""
        if sel is not None and not sel.is_collapsed:
            selected = sel.get_selected_text(buf).strip()
        off = sel.start if sel is not None else 0
        idx = paragraph_index_at_offset(buf, off)
        await self.run_margin_action(action_id, idx, text_override=selected if selected else None)
    def _refresh_compose_tab_label(self) -> None:
        if self._compose_tab_inline_rename_active:
            return
        if not self.current_path:
            self._compose_tab_filename_text.value = "—"
            self._compose_tab_filename_text.color = ft.Colors.GREY_500
            self._compose_tab_filename_text.style = None
            self._compose_tab_filename_hit.mouse_cursor = ft.MouseCursor.BASIC
            self._compose_tab_filename_hit.tooltip = "Open a note first"
        else:
            self._compose_tab_filename_text.value = self.current_path.name
            self._compose_tab_filename_text.color = ft.Colors.GREY_200
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
        self._refresh_compose_tab_label()
        if _ctrl_on_page(self._compose_tab_filename_hit):
            self._compose_tab_filename_hit.update()
        if _ctrl_on_page(self._compose_tab_filename_field):
            self._compose_tab_filename_field.update()
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
            self._compose_tab_filename_field.value = self.current_path.name
            self._compose_tab_filename_hit.visible = False
            self._compose_tab_filename_field.visible = True
            if _ctrl_on_page(self._compose_tab_filename_field):
                self._compose_tab_filename_field.update()
            if _ctrl_on_page(self._compose_tab_filename_hit):
                self._compose_tab_filename_hit.update()
            if _ctrl_on_page(self._compose_tab_filename_row):
                self._compose_tab_filename_row.update()
        await asyncio.sleep(0.05)
        self._compose_tab_filename_field.focus()

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
            name = (self._compose_tab_filename_field.value or "").strip()
            if not name or name in (".", ".."):
                self._snack("Invalid filename.")
                self._compose_tab_exit_rename_mode()
                return
            if "/" in name or "\\" in name or "\x00" in name:
                self._snack("Use a single filename only.")
                self._compose_tab_exit_rename_mode()
                return
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
        """Size reading column from full-width compose row (not the inner wrap) so width matches the editor strip."""
        avail = max(200.0, float(e.width))
        reading_w = int(min(float(READING_MAX_PX), max(240, avail * COMPOSE_READING_WIDTH_FRAC)))
        cur = int(self._compose_reading_card.width or 0)
        self._last_editor_content_w = max(120.0, float(reading_w - COMPOSE_MARGIN_COL_W - 8))
        if cur == reading_w:
            return
        self._compose_reading_card.width = reading_w
        if _ctrl_on_page(self._compose_reading_card):
            self._compose_reading_card.update()
        self._margin_gen += 1
        self.page.run_task(self._debounced_compose_rebuild, self._margin_gen)

    def _on_editor_size_change(self, e: ft.LayoutSizeChangeEvent) -> None:
        cw = max(120.0, float(e.width))
        self._last_editor_content_w = cw
        reported = float(e.height)
        paras = split_paragraphs(self.editor.value or "")
        est = estimate_total_editor_height(paras, cw)
        self._last_editor_h = max(reported, est * 0.98)
        self._apply_compose_sparkle_heights()

    def _on_editor_change(self, _e: ft.ControlEvent) -> None:
        self._refresh_title_bar()
        if self._main_tab_index == 0:
            self._margin_gen += 1
            gen = self._margin_gen
            self.page.run_task(self._debounced_compose_rebuild, gen)
        if not self.current_path:
            return
        self._autosave_gen += 1
        agen = self._autosave_gen
        self.page.run_task(self._autosave_after_idle, agen)

    async def _debounced_compose_rebuild(self, gen: int) -> None:
        await asyncio.sleep(0.05)
        if gen != self._margin_gen:
            return
        if self._main_tab_index != 0:
            return
        self._rebuild_compose_sparkle_slots()
        self._apply_compose_sparkle_heights()
        self._refresh_compose_plan_surface()

    async def _autosave_after_idle(self, gen: int) -> None:
        await asyncio.sleep(AUTOSAVE_IDLE_SEC)
        if gen != self._autosave_gen:
            return
        await self.save_file(silent=True, snapshot_reason="autosave")

    def _on_selection_change(self, e: ft.TextSelectionChangeEvent) -> None:
        t = (e.selected_text or "").strip()
        if t:
            self.last_selection = e.selected_text or ""
    def _paragraph_sparkle_menu_control(self, para_index: int, *, for_compare: bool, compact: bool = False) -> ft.Control:
        """Compose: cascade Discuss / Change / Evaluate. Compare cube: Change-topic prompts only (flat popup)."""
        tooltip = (
            "Change prompts for this paragraph"
            if for_compare
            else "LLM prompts for this paragraph"
        )
        task = self._run_compare_margin_action if for_compare else self.run_margin_action
        _spark_icon = 14 if for_compare else (15 if compact else 18)
        _spark_pad: int | ft.Padding = (
            ft.padding.symmetric(horizontal=3, vertical=2)
            if for_compare
            else (2 if compact else 4)
        )
        _text_sz = 12 if compact else 13

        _sparkle_btn_style = ft.ButtonStyle(
            color=ft.Colors.with_opacity(0.45, ft.Colors.WHITE),
            padding=(
                ft.padding.symmetric(horizontal=2, vertical=1)
                if for_compare
                else ft.padding.all(1 if compact else 2)
            ),
            visual_density=ft.VisualDensity.COMPACT,
        )

        if not prompts.MARGIN_ACTIONS:
            return ft.PopupMenuButton(
                icon=ft.Icons.AUTO_AWESOME,
                icon_size=_spark_icon,
                icon_color=ft.Colors.with_opacity(0.85, config.FEDORA_BLUE),
                tooltip=tooltip,
                padding=_spark_pad,
                style=_sparkle_btn_style,
                menu_position=ft.PopupMenuPosition.UNDER,
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
                return ft.PopupMenuButton(
                    icon=ft.Icons.AUTO_AWESOME,
                    icon_size=_spark_icon,
                    icon_color=ft.Colors.with_opacity(0.85, config.FEDORA_BLUE),
                    tooltip=tooltip,
                    padding=_spark_pad,
                    style=_sparkle_btn_style,
                    menu_position=ft.PopupMenuPosition.UNDER,
                    items=[
                        ft.PopupMenuItem(
                            content=ft.Text("No Change prompts. Add them in Settings → Prompts", size=13),
                        ),
                    ],
                )
            compare_items = [
                ft.PopupMenuItem(
                    content=ft.Text(a.label, size=_text_sz),
                    on_click=lambda e, aid=a.id, ix=para_index: self.page.run_task(task, aid, ix),
                )
                for a in change_acts
            ]
            return ft.PopupMenuButton(
                icon=ft.Icons.AUTO_AWESOME,
                icon_size=_spark_icon,
                icon_color=ft.Colors.with_opacity(0.85, config.FEDORA_BLUE),
                tooltip=tooltip,
                padding=_spark_pad,
                style=_sparkle_btn_style,
                menu_position=ft.PopupMenuPosition.UNDER,
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
            return ft.PopupMenuButton(
                icon=ft.Icons.AUTO_AWESOME,
                icon_size=_spark_icon,
                icon_color=ft.Colors.with_opacity(0.85, config.FEDORA_BLUE),
                tooltip=tooltip,
                padding=_spark_pad,
                style=_sparkle_btn_style,
                menu_position=ft.PopupMenuPosition.UNDER,
                items=[
                    ft.PopupMenuItem(content=ft.Text("No prompts for any topic.", size=13)),
                ],
            )

        category_controls: list[ft.Control] = []
        for cat_label, sorted_acts in rows_for_menu:
            leaves: list[ft.Control] = []
            for a in sorted_acts:
                item_label = f"{cat_label} → {a.label}"
                leaves.append(
                    ft.MenuItemButton(
                        content=ft.Text(item_label, size=_text_sz),
                        on_click=lambda e, action_id=a.id, ix=para_index: self.page.run_task(
                            task, action_id, ix
                        ),
                    )
                )
            category_controls.append(
                ft.SubmenuButton(
                    content=ft.Text(cat_label, size=_text_sz),
                    controls=leaves,
                )
            )

        root_menu = ft.SubmenuButton(
            content=ft.Icon(
                ft.Icons.AUTO_AWESOME,
                size=_spark_icon,
                color=ft.Colors.with_opacity(0.85, config.FEDORA_BLUE),
            ),
            tooltip=tooltip,
            style=_sparkle_btn_style,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            controls=category_controls,
            menu_style=ft.MenuStyle(
                alignment=ft.Alignment.TOP_RIGHT,
                visual_density=ft.VisualDensity.COMPACT,
            ),
        )

        return ft.MenuBar(
            controls=[root_menu],
            style=ft.MenuStyle(
                bgcolor={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
                shadow_color={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
                elevation={ft.ControlState.DEFAULT: 0},
                visual_density=ft.VisualDensity.COMPACT,
            ),
        )

    def _compose_sparkle_menu_control(self, para_index: int) -> ft.Control:
        return self._paragraph_sparkle_menu_control(para_index, for_compare=False)

    def _on_compare_row_hover(self, e: ft.ControlEvent, actions_wrap: ft.Container) -> None:
        actions_wrap.opacity = 1.0 if e.data else 0.0
        if _ctrl_on_page(actions_wrap):
            actions_wrap.update()

    def _rebuild_compose_sparkle_slots(self) -> None:
        buf = self.editor.value or ""
        cur = split_paragraphs(buf)
        if not cur:
            cur = [""]
        self._compose_sparkle_column.controls.clear()
        self._compose_sparkle_roots.clear()
        for i in range(len(cur)):
            menu = self._compose_sparkle_menu_control(i)
            slot = ft.Container(
                content=menu,
                alignment=ft.Alignment.TOP_CENTER,
                padding=ft.Padding.only(top=2, bottom=2),
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            )
            self._compose_sparkle_roots.append(slot)
            self._compose_sparkle_column.controls.append(slot)
        if _ctrl_on_page(self._compose_sparkle_column):
            self._compose_sparkle_column.update()

    def _apply_compose_sparkle_heights(self) -> None:
        if not self._compose_sparkle_roots:
            return
        paras = split_paragraphs(self.editor.value or "")
        if not paras:
            paras = [""]
        inner_w = max(120.0, self._last_editor_content_w)
        wts = paragraph_compose_slot_weights(paras, inner_w)
        heights = distribute_heights(wts, self._last_editor_h)
        n = min(len(self._compose_sparkle_roots), len(heights))
        for i in range(n):
            self._compose_sparkle_roots[i].height = heights[i]
        if _ctrl_on_page(self._compose_sparkle_column):
            self._compose_sparkle_column.update()

    async def run_margin_action(
        self, action_id: str, idx: int, *, text_override: str | None = None
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

        self._append_chat_line("user", f"Paragraph {idx + 1}: {act.label}")

        reply = ft.Text("", size=12, selectable=True, color=ft.Colors.GREY_100)
        footer = ft.Row(spacing=8, visible=False)
        bubble = ft.Column([reply, footer], tight=True, spacing=8)
        wrap = ft.Container(
            content=bubble,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.GREY_800),
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
                ft.TextButton("Dismiss", on_click=lambda _e, f=footer: self._hide_prompt_footer(f)),
            ]
            footer.visible = True
            if _ctrl_on_page(footer):
                footer.update()
            return

        reply.value = acc
        if _ctrl_on_page(reply):
            reply.update()

        if act.topic == TOPIC_CHANGE:
            footer.controls = [
                ft.FilledButton(
                    "Review",
                    tooltip="Open Compare with this text as candidate",
                    on_click=lambda _e, i=idx, r=reply, f=footer, aid=action_id: self.page.run_task(
                        self._stage_ai_candidate_async, i, r, f, aid
                    ),
                ),
                ft.TextButton("Dismiss", on_click=lambda _e, f=footer: self._hide_prompt_footer(f)),
            ]
        else:
            footer.controls = [
                ft.TextButton("Dismiss", on_click=lambda _e, f=footer: self._hide_prompt_footer(f)),
            ]
        footer.visible = True
        if _ctrl_on_page(footer):
            footer.update()
