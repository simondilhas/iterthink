
"""Right KI sidebar: topic tabs, pills, chat stream, layout helpers."""

from __future__ import annotations

import flet as ft
import httpx

from iterthink import config
from iterthink import prompts
from iterthink.persistence import store_db
from iterthink.ai.ollama_models import classify_installed_models
from iterthink.ai.llm_router import remote_http_error_message
from iterthink.ai.ollama_util import chat_response_text, chat_stream_delta, ollama_error_message
from iterthink.prompts import TOPIC_CHANGE, TOPIC_DISCUSS
from .constants import (
    KI_PILL_TEXT_SIZE,
    SIDEBAR_EXPANDED_WIDTH_PX,
    COLLAPSED_RAIL_WIDTH_PX,
    TAB_HISTORY,
    TAB_FUTURE,
    TAB_PRESENT,
    KI_TOPIC_ACT,
    KI_TOPIC_ANALYSE,
    KI_TOPIC_CHANGE,
    KI_TOPIC_COMMENTS,
    KI_TOPIC_DISCUSS,
)
from .llm_backend import sync_llm_tier_tab_icons
from .util import KI_TIERS, ctrl_on_page as _ctrl_on_page, normalize_ki_tier

# KI strip icon tooltips (must match markdown_studio.py topic mode row order).
_KI_TOPIC_STRIP_LABELS = ("Comments", "Discuss", "Change", "Analyse", "Act")

# Discuss tab: double speech bubbles (used by markdown_studio topic mode strip).
KI_TOPIC_STRIP_DISCUSS_ICON = ft.Icons.QUESTION_ANSWER


class MarkdownStudioKiSidebar:
    def _ki_topic_allowed_indices(self) -> frozenset[int]:
        tab = int(getattr(self, "_main_tab_index", TAB_PRESENT))
        if tab == TAB_HISTORY:
            return frozenset({KI_TOPIC_COMMENTS, KI_TOPIC_DISCUSS})
        if tab == TAB_PRESENT:
            return frozenset({KI_TOPIC_COMMENTS, KI_TOPIC_DISCUSS, KI_TOPIC_CHANGE})
        return frozenset(
            {KI_TOPIC_COMMENTS, KI_TOPIC_DISCUSS, KI_TOPIC_CHANGE, KI_TOPIC_ANALYSE, KI_TOPIC_ACT}
        )

    def _ki_topic_strip_disabled_tooltip(self, index: int) -> str:
        if index == KI_TOPIC_COMMENTS:
            return "Open a markdown note first."
        if index == KI_TOPIC_CHANGE:
            return "Switch to Focus Area to use Change."
        if index == KI_TOPIC_ANALYSE:
            return "Switch to Review to use Analyse."
        return "Switch to Review to use Act."

    def _sync_ki_topic_mode_buttons(self) -> None:
        ix = int(getattr(self, "_ki_topic_index", 0))
        allowed = self._ki_topic_allowed_indices()
        if ix not in allowed:
            ix = min(allowed)
            self._ki_topic_index = ix
        u_w = 1.5
        grey = ft.Colors.with_opacity(0.42, config.ON_SURFACE_VARIANT)
        for i, b in enumerate(self._ki_topic_mode_buttons):
            want = i == ix
            dis = i not in allowed
            base_tip = _KI_TOPIC_STRIP_LABELS[i] if i < len(_KI_TOPIC_STRIP_LABELS) else ""
            tip = self._ki_topic_strip_disabled_tooltip(i) if dis else base_tip
            if getattr(b, "tooltip", None) != tip:
                b.tooltip = tip
                if _ctrl_on_page(b):
                    b.update()
            if bool(getattr(b, "disabled", False)) != dis:
                b.disabled = dis
                if _ctrl_on_page(b):
                    b.update()
            if dis:
                col = grey
            elif want:
                col = config.HIGHLIGHT
            else:
                col = config.ON_SURFACE_VARIANT
            if getattr(b, "icon_color", None) != col:
                b.icon_color = col
                if _ctrl_on_page(b):
                    b.update()
        for i, c in enumerate(getattr(self, "_ki_topic_mode_cells", [])):
            want = i == ix
            c.border = ft.border.only(
                bottom=ft.BorderSide(
                    u_w if want else 0.0,
                    config.HIGHLIGHT if want else ft.Colors.TRANSPARENT,
                )
            )
            if _ctrl_on_page(c):
                c.update()

    def _set_ki_topic(self, index: int) -> None:
        pages = getattr(self, "_ki_tab_pages", None) or []
        max_index = max(0, len(pages) - 1)
        ix = max(0, min(max_index, int(index)))
        allowed = self._ki_topic_allowed_indices()
        if ix not in allowed:
            ix = min(allowed)
        self._ki_topic_index = ix
        if pages:
            new_content = pages[ix]
            if self._ki_tab_bar_view.content is not new_content:
                self._ki_tab_bar_view.content = new_content
                if _ctrl_on_page(self._ki_tab_bar_view):
                    self._ki_tab_bar_view.update()
        self._sync_ki_topic_mode_buttons()
        if hasattr(self, "_sync_impact_ki_context_visibility"):
            self._sync_impact_ki_context_visibility()
        if hasattr(self, "_on_ki_topic_index_changed"):
            self._on_ki_topic_index_changed(ix)

    def _sync_ki_topic_strip_after_workspace_tab_change(self) -> None:
        """After ``_main_tab_index`` updates: reclamp topic, pill page, strip, impact dock.

        Kept on the KI mixin so workspace tab code does not call ``_set_ki_topic`` / impact
        helpers directly (avoids coupling tab switching to sidebar implementation details).
        """
        if not getattr(self, "_ki_topic_mode_buttons", None):
            return
        self._set_ki_topic(int(getattr(self, "_ki_topic_index", 0)))

    def _rebuild_topic_pills(self) -> None:
        def fill_row(row: ft.Row, topic: str) -> None:
            row.controls.clear()
            actions = sorted(
                prompts.actions_for_topic(topic),
                key=lambda a: (a.label or "").casefold(),
            )
            for a in actions:
                label = a.label
                aid = a.id
                row.controls.append(
                    ft.FilledButton(
                        content=label,
                        elevation=0,
                        style=ft.ButtonStyle(
                            text_style=ft.TextStyle(size=KI_PILL_TEXT_SIZE),
                            visual_density=ft.VisualDensity.COMPACT,
                            padding=ft.padding.symmetric(horizontal=6, vertical=3),
                        ),
                        on_click=lambda e, action_id=aid: self.page.run_task(
                            self._quick_margin_action, action_id
                        ),
                    )
                )

        fill_row(self._pill_row_discuss, TOPIC_DISCUSS)
        fill_row(self._pill_row_change, TOPIC_CHANGE)
        self._rebuild_analyse_pills()

        for row in (self._pill_row_discuss, self._pill_row_change, self._pill_row_analyse):
            if _ctrl_on_page(row):
                row.update()

        if hasattr(self, "_refresh_editor_ctx_menu_items"):
            self._refresh_editor_ctx_menu_items()

    def _on_page_keyboard(self, e: ft.KeyboardEvent) -> None:
        key = (e.key or "").lower()
        if key == "escape" and self._compose_tab_inline_rename_active:
            self.page.run_task(self._compose_tab_cancel_inline_rename)
            return
        if key != "j":
            return
        if not (e.ctrl or e.meta):
            return
        self.toggle_right()

    def _append_chat_line(self, role: str, text: str, *, quote: str | None = None) -> None:
        bg = (
            ft.Colors.with_opacity(0.22, config.OUTLINE)
            if role == "user"
            else ft.Colors.with_opacity(0.22, config.PRIMARY_COLOR)
        )
        align = ft.Alignment.CENTER_RIGHT if role == "user" else ft.Alignment.CENTER_LEFT
        header = ft.Text(
            text,
            size=12,
            selectable=True,
            color=config.ON_SURFACE,
            weight=ft.FontWeight.W_600 if quote else ft.FontWeight.NORMAL,
        )
        body: ft.Control
        if quote:
            quote_text = ft.Text(
                quote,
                size=12,
                selectable=True,
                italic=True,
                color=config.ON_SURFACE_VARIANT,
            )
            quote_box = ft.Container(
                content=quote_text,
                padding=ft.padding.only(left=8, top=2, bottom=2),
                border=ft.border.only(left=ft.BorderSide(2, config.PRIMARY_COLOR)),
            )
            body = ft.Column([header, quote_box], tight=True, spacing=4)
        else:
            body = header
        bubble = ft.Container(
            content=body,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            bgcolor=bg,
            border_radius=10,
            alignment=align,
        )
        self._chat_history.controls.append(bubble)
        if _ctrl_on_page(self._chat_history):
            self._chat_history.update()

    def _on_ki_tier_tabs_change(self, e: ft.ControlEvent) -> None:
        try:
            idx = int(e.data)
        except (TypeError, ValueError):
            idx = int(getattr(e.control, "selected_index", 0))
        if not (0 <= idx < len(KI_TIERS)):
            return
        self.ki_tier = KI_TIERS[idx]
        self._persist_ki_tier()
        self._sync_chat_model_ui()
        self._sync_ki_tier_tab_icons()

    def _sync_ki_tier_tab_icons(self) -> None:
        sync_llm_tier_tab_icons(getattr(self, "_ki_tier_tabs", None))

    def _sync_ki_tier_tabs_ui(self) -> None:
        tabs_ctrl = getattr(self, "_ki_tier_tabs", None)
        if tabs_ctrl is None:
            return
        want = KI_TIERS.index(normalize_ki_tier(self.ki_tier))
        if int(tabs_ctrl.selected_index) != want:
            tabs_ctrl.selected_index = want
            if _ctrl_on_page(tabs_ctrl):
                tabs_ctrl.update()
        self._sync_ki_tier_tab_icons()

    async def _send_chat_message(self, _e: ft.ControlEvent | None = None) -> None:
        raw = (self._chat_input.value or "").strip()
        if not raw:
            return
        self._chat_input.value = ""
        if _ctrl_on_page(self._chat_input):
            self._chat_input.update()

        self._append_chat_line("user", raw)

        doc = (self._editor_buffer() or "")[:8000]
        messages: list[dict[str, str]] = [{"role": "system", "content": config.CHAT_SYSTEM}]
        if doc.strip() and not self._chat_api_messages:
            messages.append(
                {
                    "role": "user",
                    "content": "Aktuelles Markdown-Dokument (Auszug):\n```markdown\n" + doc + "\n```",
                }
            )
        messages.extend(self._chat_api_messages)
        messages.append({"role": "user", "content": raw})

        acc = ""
        reply = ft.Text("", size=12, selectable=True, color=config.ON_SURFACE)
        wrap = ft.Container(
            content=reply,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            bgcolor=ft.Colors.with_opacity(0.14, config.OUTLINE),
            border_radius=10,
            alignment=ft.Alignment.CENTER_LEFT,
        )
        self._chat_history.controls.append(wrap)
        if _ctrl_on_page(self._chat_history):
            self._chat_history.update()

        backend = self._make_llm_backend()
        cm = self.chat_model_for_requests()
        try:
            stream = await backend.chat(model=cm, messages=messages, stream=True)
            async for part in stream:
                acc += chat_stream_delta(part)
                reply.value = acc.strip() or "…"
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
                reply.value = f"(Fehler) {detail}"
                if _ctrl_on_page(reply):
                    reply.update()
                return

        acc = (acc or "").strip()
        reply.value = acc or "(Leere Antwort)"
        if _ctrl_on_page(reply):
            reply.update()
        self._chat_api_messages.append({"role": "user", "content": raw})
        self._chat_api_messages.append({"role": "assistant", "content": acc})

    def reflow_columns(self, _e: ft.ControlEvent | None = None) -> None:
        left_w = SIDEBAR_EXPANDED_WIDTH_PX if self.left_open else COLLAPSED_RAIL_WIDTH_PX
        right_w = SIDEBAR_EXPANDED_WIDTH_PX if self.right_open else COLLAPSED_RAIL_WIDTH_PX
        self.left_panel.width = left_w
        self.right_panel.width = right_w
        self._sync_side_panel_chrome()
        self._margin_gen += 1
        self.page.run_task(self._debounced_compose_rebuild, self._margin_gen)
        if self._main_tab_index in (TAB_HISTORY, TAB_FUTURE):
            self._compare_diff_gen += 1
            self.page.run_task(self._debounced_compare_diff, self._compare_diff_gen)
        # build() calls reflow_columns() before page.add(); page.update() would flush
        # child controls that are not mounted yet ("Control must be added to the page first").
        if _ctrl_on_page(self.left_panel):
            self.page.update()

    async def _refresh_ki_chat_model_dropdown(self) -> None:
        try:
            chat_opts, _ = await classify_installed_models(self.ollama)
        except BaseException:
            return
        m = (self.ollama_model or "").strip()
        if m and m not in chat_opts:
            chat_opts = [m, *chat_opts]
        if not chat_opts and m:
            chat_opts = [m]
        self._chat_model_options = chat_opts
        if chat_opts:
            picked = m if m in chat_opts else chat_opts[0]
            if picked != m:
                self.ollama_model = picked
                store_db.settings_set(self._db, store_db.SETTINGS_CHAT, self.ollama_model)
        self._sync_chat_model_ui()

    def _sync_chat_model_ui(self) -> None:
        m = (self.ollama_model or "").strip()
        if m and m not in self._chat_model_options:
            self._chat_model_options = [m, *self._chat_model_options]

    def _refresh_chat_model_button(self) -> None:
        self._sync_chat_model_ui()
