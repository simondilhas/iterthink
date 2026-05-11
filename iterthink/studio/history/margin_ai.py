"""Compare-tab margin (sparkle) LLM actions and staging into Review / Compare."""

from __future__ import annotations

import flet as ft
import httpx

from iterthink import config
from iterthink import prompts
from iterthink.ai.llm_router import remote_http_error_message
from iterthink.ai.ollama_util import chat_response_text, chat_stream_delta, ollama_error_message
from iterthink.compare.margin import replace_paragraph_at_index, split_paragraphs
from iterthink.db.session import session_scope
from iterthink.persistence import version_storage
from iterthink.prompts import TOPIC_CHANGE

from ..components import (
    ACTION_RAIL_ICON_SIZE,
    action_rail_icon_button_style,
)
from ..focus_area import (
    _ki_topic_index_for_prompt_topic,
    _strip_change_topic_preamble,
)
from ..util import ctrl_on_page as _ctrl_on_page
from ..constants import TAB_FUTURE
from .candidate_state import CompareCandidateSource


class _HistoryMarginAiMixin:
    def _hide_prompt_footer(self, footer: ft.Row) -> None:
        footer.controls.clear()
        footer.visible = False
        if _ctrl_on_page(footer):
            footer.update()

    async def _apply_margin_reply_to_selection_async(
        self,
        reply: ft.Text,
        footer: ft.Row,
        action_id: str,
        span: tuple[int, int],
        original_snippet: str,
    ) -> None:
        text = _strip_change_topic_preamble(reply.value or "")
        if not text:
            self._snack("Reply is empty.")
            return
        a, b = int(span[0]), int(span[1])
        buf = self.editor.value or ""
        if a < 0 or b > len(buf) or a > b:
            self._snack("Selection range is no longer valid.")
            self._hide_prompt_footer(footer)
            return
        if buf[a:b] != original_snippet:
            self._snack("Document changed; cannot apply to original selection.")
            return
        act = prompts.get_margin_action(action_id)
        if self.current_path:
            try:
                with session_scope() as s:
                    version_storage.persist_version_snapshot(
                        s,
                        self.current_path.resolve(),
                        buf,
                        "ai_apply",
                        display_label=f"{act.label} · apply" if act else "AI · apply",
                    )
            except BaseException:
                pass
        self.editor.value = buf[:a] + text + buf[b:]
        self._compose_sel_span = None
        self._hide_prompt_footer(footer)
        if _ctrl_on_page(self.editor):
            self.editor.update()
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._refresh_title_bar()
        self._snack("Selection replaced.")

    async def _apply_margin_reply_to_paragraph_async(
        self,
        idx: int,
        reply: ft.Text,
        footer: ft.Row,
        action_id: str,
    ) -> None:
        text = _strip_change_topic_preamble(reply.value or "")
        if not text:
            self._snack("Reply is empty.")
            return
        buf = self.editor.value or ""
        paras = split_paragraphs(buf)
        if idx < 0 or idx >= len(paras):
            self._snack("Paragraph is no longer valid.")
            self._hide_prompt_footer(footer)
            return
        act = prompts.get_margin_action(action_id)
        if self.current_path:
            try:
                with session_scope() as s:
                    version_storage.persist_version_snapshot(
                        s,
                        self.current_path.resolve(),
                        buf,
                        "ai_apply",
                        display_label=f"{act.label} · apply" if act else "AI · apply",
                    )
            except BaseException:
                pass
        self.editor.value = replace_paragraph_at_index(buf, idx, text)
        self._compose_sel_span = None
        self._hide_prompt_footer(footer)
        if _ctrl_on_page(self.editor):
            self.editor.update()
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._refresh_title_bar()
        self._snack(f"Paragraph {idx + 1} replaced.")

    def _compare_paragraph_for_index(self, idx: int) -> str:
        if 0 <= idx < len(self._compare_right_fields):
            return self._compare_right_fields[idx].value or ""
        paras = split_paragraphs(self._compare_editor.value or "")
        return paras[idx] if 0 <= idx < len(paras) else ""

    async def _stage_compare_margin_review_async(
        self, idx: int, reply: ft.Text, footer: ft.Row, action_id: str
    ) -> None:
        """Like _stage_ai_candidate_async but replaces within the Compare candidate buffer (already on Compare)."""
        text = _strip_change_topic_preamble(reply.value or "")
        if not text:
            self._snack("Reply is empty.")
            return
        act = prompts.get_margin_action(action_id)
        base = self._compare_editor.value or ""
        cand_body = replace_paragraph_at_index(base, idx, text)
        loc_label = f"paragraph {idx + 1}"
        new_vid: int | None = None
        if self.current_path:
            try:
                with session_scope() as s:
                    new_vid = version_storage.persist_version_snapshot(
                        s,
                        self.current_path.resolve(),
                        cand_body,
                        "ai_proposal",
                        display_label=f"{act.label} - {loc_label}" if act else f"AI - {loc_label}",
                    )
            except BaseException:
                pass
        new_vid = self._resolve_vid_after_proposal_persist(new_vid, cand_body)
        self._compare_editor.value = cand_body
        self._compare_candidate_source = CompareCandidateSource.AI_PREVIEW
        self._compare_snapshot_version_id = new_vid
        self._pending_ai_accept_action_id = action_id
        if new_vid is not None:
            self._ai_proposal_action_ids[new_vid] = action_id
            self._latest_ai_proposal_vid = new_vid
        self._loaded_proposal_sha = version_storage.content_sha256(self._compare_editor.value or "")
        self._hide_prompt_footer(footer)
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._refresh_tab_toolbar()
        self._refresh_compare_tab_candidate_ui()
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()

    async def _run_compare_margin_action(self, action_id: str, idx: int) -> None:
        """Compare-tab sparkle: run margin prompt on the candidate paragraph; stage result into Compare."""
        act = prompts.get_margin_action(action_id)
        if act is None:
            return
        para = self._compare_paragraph_for_index(idx).strip()
        if not para:
            self._snack("This paragraph is empty.")
            return

        self._set_ki_topic(_ki_topic_index_for_prompt_topic(act.topic))

        self._append_chat_line(
            "user", f"Compare · paragraph {idx + 1}: {act.label}", quote=para
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

        if act.topic == TOPIC_CHANGE:
            # Future/review + KI "Change" tab: candidate is already in context — stage immediately, no eye/dismiss row.
            if self._main_tab_index == TAB_FUTURE and int(getattr(self, "_ki_topic_index", 0)) == 1:
                await self._stage_compare_margin_review_async(idx, reply, footer, action_id)
                return

            footer.controls = [
                ft.IconButton(
                    ft.Icons.VISIBILITY_OUTLINED,
                    icon_size=ACTION_RAIL_ICON_SIZE,
                    icon_color=config.ON_SURFACE_VARIANT,
                    tooltip="Review: stage this text as the Compare candidate for this paragraph",
                    style=action_rail_icon_button_style(),
                    on_click=lambda _e, i=idx, r=reply, f=footer, aid=action_id: self.page.run_task(
                        self._stage_compare_margin_review_async, i, r, f, aid
                    ),
                ),
                ft.IconButton(
                    ft.Icons.CLOSE_ROUNDED,
                    icon_size=ACTION_RAIL_ICON_SIZE,
                    icon_color=config.ON_SURFACE_VARIANT,
                    tooltip="Dismiss",
                    style=action_rail_icon_button_style(),
                    on_click=lambda _e, f=footer: self._hide_prompt_footer(f),
                ),
            ]
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

    async def _stage_ai_candidate_async(self, idx: int, reply: ft.Text, footer: ft.Row, action_id: str) -> None:
        text = _strip_change_topic_preamble(reply.value or "")
        if not text:
            self._snack("Reply is empty.")
            return
        self._compose_sel_span = None
        act = prompts.get_margin_action(action_id)
        base = self.editor.value or ""
        cand_body = replace_paragraph_at_index(base, idx, text)
        loc_label = f"paragraph {idx + 1}"
        new_vid: int | None = None
        if self.current_path:
            try:
                with session_scope() as s:
                    new_vid = version_storage.persist_version_snapshot(
                        s,
                        self.current_path.resolve(),
                        cand_body,
                        "ai_proposal",
                        display_label=f"{act.label} - {loc_label}" if act else f"AI - {loc_label}",
                    )
            except BaseException:
                pass
        new_vid = self._resolve_vid_after_proposal_persist(new_vid, cand_body)
        self._compare_editor.value = cand_body
        self._compare_candidate_source = CompareCandidateSource.AI_PREVIEW
        self._compare_snapshot_version_id = new_vid
        self._pending_ai_accept_action_id = action_id
        if new_vid is not None:
            self._ai_proposal_action_ids[new_vid] = action_id
            self._latest_ai_proposal_vid = new_vid
        self._loaded_proposal_sha = version_storage.content_sha256(self._compare_editor.value or "")
        self._hide_prompt_footer(footer)
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._review_subtab_index = 0
        await self._request_tab_switch_async(TAB_FUTURE)
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()
