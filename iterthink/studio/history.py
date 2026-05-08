"""Compare tab: candidates, paragraph rows, bulk AI accept."""

from __future__ import annotations

import asyncio
from typing import Any, NamedTuple

import flet as ft
import httpx

from iterthink import config
from iterthink import prompts
from iterthink.compare import paragraph_compare
from iterthink.compare.diff_card import build_new_side_spans, build_old_side_spans, build_unified_spans
from iterthink.compare.layout import aligned_review_rows, pair_paragraphs_for_compare
from iterthink.compare.paragraph_align import compute_hash
from iterthink.db.session import session_scope
from iterthink.persistence import version_storage
from iterthink.persistence.version_storage import SnapshotInfo
from iterthink.compare.margin import (
    distribute_heights,
    estimate_total_editor_height,
    paragraph_compose_slot_weights,
    paragraph_index_at_offset,
    replace_paragraph_at_index,
    split_paragraphs,
)
from iterthink.ai.llm_router import remote_http_error_message
from iterthink.ai.ollama_util import chat_response_text, chat_stream_delta, ollama_error_message
from iterthink.prompts import TOPIC_CHANGE
from .action_chrome import wrap_workspace_action_chrome
from .components import (
    ACTION_RAIL_ICON_SIZE,
    action_rail_approve_icon_button,
    action_rail_icon_button_style,
    action_rail_reject_icon_button,
    build_action_square,
)
from .focus_area import (
    _ki_topic_index_for_prompt_topic,
    _strip_change_topic_preamble,
)
from . import ui_theme
from .constants import (
    COMPARE_ACTION_GRID_CELL,
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPARE_EVAL_COL_W_WIDE,
    COMPARE_KEY_CURRENT as _COMPARE_KEY_CURRENT,
    COMPARE_PILL_COL_W,
    DIFF_SPAN_CHAR_CAP as _DIFF_SPAN_CHAR_CAP,
    PROJECT_PAGE_URL as _PROJECT_PAGE_URL,
    TAB_HISTORY,
    TAB_PRESENT,
    TAB_FUTURE,
)
from .util import ctrl_on_page as _ctrl_on_page

# Outer padding only on both History columns so Text and TextField share the same inner width.
_COMPARE_HISTORY_CELL_PAD = ft.padding.all(8)


class CompareBuffers(NamedTuple):
    """The (baseline, candidate) text pair for the currently-active compare tab.

    baseline — the reference / older side.
    candidate — the target / newer side (draft, AI proposal, or snapshot).

    Use ``MarkdownStudioCompareText._active_compare_buffers()`` to obtain the
    correct pair for whatever tab the user is on, instead of repeating the
    ``if TAB_FUTURE / elif TAB_HISTORY / else`` pattern inline.
    """

    baseline: str
    candidate: str


class MarkdownStudioCompareText:
    def _working_document_text(self) -> str:
        """Text compared to on-disk `last_saved_text` for dirty + save."""
        return self.editor.value or ""

    def _is_dirty(self) -> bool:
        return self._working_document_text() != self.last_saved_text

    def _editor_buffer(self) -> str:
        if self._main_tab_index == TAB_HISTORY:
            return self._history_newer_side_text() or ""
        if self._main_tab_index == TAB_FUTURE:
            return self._compare_editor.value or ""
        return self.editor.value or ""

    @staticmethod
    def _history_compare_snapshots(snaps: list[SnapshotInfo]) -> list[SnapshotInfo]:
        return [s for s in snaps if s.reason != "ai_proposal"]

    @staticmethod
    def _snapshots_strictly_older_than(
        newest_first: list[SnapshotInfo], newer_version_id: int | None
    ) -> list[SnapshotInfo]:
        if newer_version_id is None:
            return list(newest_first)
        for i, s in enumerate(newest_first):
            if s.version_id == newer_version_id:
                return newest_first[i + 1 :]
        return list(newest_first)

    def _history_newer_side_text(self) -> str:
        if self._compare_newer_version_id is None:
            return self.editor.value or ""
        return self._compare_newer_cached_body or ""

    def _history_default_older_version_id(
        self, snaps_all: list[SnapshotInfo], older_slice: list[SnapshotInfo]
    ) -> int | None:
        if not older_slice:
            return None
        allowed = {s.version_id for s in older_slice}
        pick = version_storage.second_newest_history_autosave_version_id(snaps_all)
        if pick is not None and pick in allowed:
            return pick
        return older_slice[0].version_id

    def _reset_compare_state(self) -> None:
        """Clear all compare-side state before loading a new document.

        Centralises the field assignments that open_file used to make inline.
        Keeps the full set of compare fields in one documented place so that
        adding a new format (e.g. IFC) only requires touching this method.
        """
        self._compare_candidate_source = "draft"
        self._compare_snapshot_version_id = None
        self._compare_newer_version_id = None
        self._compare_newer_cached_body = ""
        self._pending_ai_accept_action_id = None
        self._compare_pdf_peer_snapshot_id = None
        self._latest_ai_proposal_vid = None
        self._ai_proposal_action_ids.clear()
        self._loaded_proposal_sha = None

    # ------------------------------------------------------------------
    # Compare buffer accessor
    # ------------------------------------------------------------------

    def _active_compare_buffers(self) -> CompareBuffers:
        """Return the (baseline, candidate) text pair for the active tab.

        Centralises the ``if TAB_FUTURE / elif TAB_HISTORY / else`` lookup
        that was duplicated across checks, pill refresh, and slot refinement.

        - History:  baseline = snapshot (left column)
                    candidate = newer draft or "Current draft" (right column)
        - Future:   baseline = current editor draft
                    candidate = AI proposal
        - Present:  baseline = latest saved baseline snapshot
                    candidate = compare editor buffer
        """
        if self._main_tab_index == TAB_FUTURE:
            return CompareBuffers(
                baseline=self.editor.value or "",
                candidate=self._compare_editor.value or "",
            )
        if self._main_tab_index == TAB_HISTORY:
            return CompareBuffers(
                baseline=self._compare_editor.value or "",
                candidate=self._history_newer_side_text() or "",
            )
        # TAB_PRESENT
        return CompareBuffers(
            baseline=self._compare_latest_baseline_text(),
            candidate=self._compare_editor.value or "",
        )

    # ------------------------------------------------------------------
    # Format dispatch
    # ------------------------------------------------------------------

    def _rebuild_compare_view(self) -> None:
        """Rebuild the History compare pane for the current candidate source.

        This is the single dispatch point for format-specific compare renderers.
        Adding a new format requires three steps:
          1. Add its literal to ``CompareCandidateSource`` in ``studio/__init__.py``.
          2. Implement ``_rebuild_compare_<fmt>_panes()`` in its own mixin.
          3. Add one ``elif`` branch here.
        """
        source = self._compare_candidate_source
        if source == "pdf_original":
            self._rebuild_compare_pdf_panes()
            self._sync_compare_pdf_layers_visibility()
        elif source == "docx_original":
            self._rebuild_compare_docx_panes()
            self._sync_compare_pdf_layers_visibility()
        elif source == "ifc_original":
            # Dispatches to MarkdownStudioIfcFormat (formats.ifc).
            self._rebuild_compare_ifc_panes()
            self._sync_compare_pdf_layers_visibility()
        else:
            self._rebuild_compare_paragraph_ui()

    def _refresh_compare_tab_candidate_ui(self) -> None:
        _st = ui_theme.compare_candidate_dropdown_option_style()

        # History / Review dropdowns live in a Stack under the tab bar; only the active tab's
        # toolbar is visible. Mutating + .update() on a hidden Dropdown can surface its menu
        # overlay (e.g. History flash on Focus Area). Gate each block to its tab.
        if self._main_tab_index == TAB_HISTORY:
            # ── History: newer (right) + older (left) dropdowns ─────────────────────

            def snapshot_option_rows(slist: list[SnapshotInfo]) -> list[ft.dropdown.Option]:
                snap_opts: list[ft.dropdown.Option] = []
                import_opts: list[ft.dropdown.Option] = []
                for sn in slist:
                    row_text = version_storage.snapshot_dropdown_text(sn)
                    bucket = version_storage.snapshot_bucket(sn)
                    if bucket == "import":
                        import_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"Import - {row_text}",
                                style=_st,
                            )
                        )
                    else:
                        snap_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=row_text,
                                style=_st,
                            )
                        )
                snap_opts.extend(import_opts)
                return snap_opts

            snaps_all: list[SnapshotInfo] = []
            if self.current_path:
                with session_scope() as s:
                    snaps_all = version_storage.list_snapshots(s, self.current_path.resolve())
            filt = self._history_compare_snapshots(snaps_all)

            newer_opts: list[ft.dropdown.Option] = []
            # Only offer "Current draft" when the editor has unsaved changes; if it is
            # identical to the last-saved snapshot it would be a duplicate entry.
            if self._is_dirty():
                newer_opts.append(
                    ft.dropdown.Option(key=_COMPARE_KEY_CURRENT, text="Current draft", style=_st)
                )
            newer_opts.extend(snapshot_option_rows(filt))
            self._compare_newer_dropdown.options = newer_opts
            newer_keys = {o.key for o in newer_opts}
            if self._compare_newer_version_id is not None:
                nk = str(self._compare_newer_version_id)
                if nk in newer_keys:
                    self._compare_newer_dropdown.value = nk
                else:
                    self._compare_newer_version_id = None
                    self._compare_newer_cached_body = ""
                    # Fall through to default selection below.
            if self._compare_newer_version_id is None:
                if _COMPARE_KEY_CURRENT in newer_keys:
                    self._compare_newer_dropdown.value = _COMPARE_KEY_CURRENT
                elif newer_opts:
                    # Draft is clean — default to the most recent snapshot.
                    first_snap = newer_opts[0]
                    self._compare_newer_dropdown.value = first_snap.key
                    try:
                        vid = int(first_snap.key)
                        self._compare_newer_version_id = vid
                        with session_scope() as s:
                            self._compare_newer_cached_body = version_storage.load_version_body(
                                s, vid
                            )
                    except (TypeError, ValueError, BaseException):
                        self._compare_newer_version_id = None
                        self._compare_newer_cached_body = ""
                else:
                    self._compare_newer_dropdown.value = None

            if self._compare_newer_version_id is not None:
                try:
                    with session_scope() as s:
                        self._compare_newer_cached_body = version_storage.load_version_body(
                            s, self._compare_newer_version_id
                        )
                except BaseException:
                    self._compare_newer_cached_body = ""

            older_slice = self._snapshots_strictly_older_than(filt, self._compare_newer_version_id)
            older_opts = snapshot_option_rows(older_slice)
            self._compare_candidate_dropdown.options = older_opts
            o_keys = {o.key for o in older_opts}

            cur_old = self._compare_snapshot_version_id
            sk = str(cur_old) if cur_old is not None else None
            if sk in o_keys:
                self._compare_candidate_dropdown.value = sk
            elif older_slice:
                dv = self._history_default_older_version_id(snaps_all, older_slice)
                if dv is not None:
                    if cur_old != dv:
                        self._select_snapshot_as_candidate(dv)
                    self._compare_candidate_dropdown.value = str(dv)
                else:
                    self._compare_candidate_dropdown.value = None
            else:
                self._compare_snapshot_version_id = None
                self._compare_pdf_peer_snapshot_id = None
                self._pending_ai_accept_action_id = None
                self._compare_candidate_source = "snapshot"
                self._compare_editor.value = ""
                self._compare_candidate_dropdown.value = None

            if _ctrl_on_page(self._compare_newer_dropdown):
                self._compare_newer_dropdown.update()
            if _ctrl_on_page(self._compare_candidate_dropdown):
                self._compare_candidate_dropdown.update()

        if self._main_tab_index == TAB_FUTURE:
            # ── Review dropdown: ai_proposal / legacy ai_staged + manual imports ─
            review_opts: list[ft.dropdown.Option] = []
            if self.current_path:
                with session_scope() as s:
                    snaps = version_storage.list_snapshots(s, self.current_path.resolve())
                for sn in snaps:
                    row_text = version_storage.snapshot_dropdown_text(sn)
                    if sn.reason == "ai_proposal":
                        review_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"AI - {row_text}",
                                style=_st,
                            )
                        )
                    elif sn.reason == "ai_staged":
                        review_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"AI - {row_text} (legacy)",
                                style=_st,
                            )
                        )
                    elif version_storage.snapshot_bucket(sn) == "import":
                        review_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"Import - {row_text}",
                                style=_st,
                            )
                        )
            self._review_candidate_dropdown.options = review_opts
            r_keys = {o.key for o in review_opts}
            # Default selection: currently-loaded ai_preview vid, else most recent ai_proposal, else first option.
            preferred: str | None = None
            if (
                self._compare_candidate_source == "ai_preview"
                and self._compare_snapshot_version_id is not None
            ):
                preferred = str(self._compare_snapshot_version_id)
            elif self._latest_ai_proposal_vid is not None:
                preferred = str(self._latest_ai_proposal_vid)
            if preferred and preferred in r_keys:
                self._review_candidate_dropdown.value = preferred
            elif review_opts:
                self._review_candidate_dropdown.value = review_opts[0].key
            else:
                self._review_candidate_dropdown.value = None
            self._review_candidate_dropdown.disabled = not bool(review_opts)
            if _ctrl_on_page(self._review_candidate_dropdown):
                self._review_candidate_dropdown.update()

        self._refresh_plan_compare_bar()

    def _sync_version_toolbar_state(self) -> None:
        has_doc = self.current_path is not None
        self._compare_newer_dropdown.disabled = not has_doc
        self._compare_newer_dropdown.tooltip = (
            "Pick the newer version (right column, insertions). Default is the current draft."
            if has_doc
            else "Open a markdown file from the tree to list versions."
        )
        self._compare_candidate_dropdown.tooltip = (
            "Pick an older saved version (left column, deletions). Only versions older than the "
            "newer side are listed."
            if has_doc
            else "Open a markdown file from the tree to list versions."
        )
        if self._main_tab_index == TAB_HISTORY and _ctrl_on_page(self._compare_newer_dropdown):
            self._compare_newer_dropdown.update()
        if self._main_tab_index == TAB_HISTORY and _ctrl_on_page(self._compare_candidate_dropdown):
            self._compare_candidate_dropdown.update()
        self._refresh_compare_bulk_buttons()

    def _compare_has_pending_bulk_apply(self) -> bool:
        if not self.current_path or not self._compare_right_fields:
            return False
        merged = "\n\n".join(tf.value or "" for tf in self._compare_right_fields)
        return merged != (self.editor.value or "")

    def _refresh_compare_bulk_buttons(self) -> None:
        n = len(self._compare_right_fields)
        # Approve/decline bulk buttons only shown on Future tab (ai_preview).
        on_future = self._main_tab_index == TAB_FUTURE
        pending_apply = self._compare_has_pending_bulk_apply() if on_future else False
        self._compare_approve_all_btn.visible = pending_apply and on_future
        self._compare_decline_all_btn.disabled = n == 0 or not on_future
        if _ctrl_on_page(self._compare_approve_all_btn):
            self._compare_approve_all_btn.update()
        if _ctrl_on_page(self._compare_decline_all_btn):
            self._compare_decline_all_btn.update()

    async def _on_compare_newer_change_async(self, e: ft.ControlEvent) -> None:
        if e.control is not self._compare_newer_dropdown or self._compare_newer_dropdown.disabled:
            return
        if not self.current_path:
            return
        v = e.control.value
        if v == _COMPARE_KEY_CURRENT:
            self._compare_newer_version_id = None
            self._compare_newer_cached_body = ""
        else:
            try:
                vid = int(v)
            except (TypeError, ValueError):
                return
            self._compare_newer_version_id = vid
            try:
                with session_scope() as s:
                    self._compare_newer_cached_body = version_storage.load_version_body(s, vid)
            except BaseException:
                self._compare_newer_version_id = None
                self._compare_newer_cached_body = ""
                self._snack("Could not load that version.")
                self._refresh_compare_tab_candidate_ui()
                return
        self._refresh_compare_tab_candidate_ui()
        self._capture_compare_baseline_snapshot()
        self._sync_compare_pdf_layers_visibility()
        self._refresh_compare_diff_immediate()
        self._refresh_compare_bulk_buttons()
        self._refresh_title_bar()

    async def _on_compare_candidate_change_async(self, e: ft.ControlEvent) -> None:
        from_review = e.control is self._review_candidate_dropdown
        if from_review:
            # Review dropdown: ai_proposal / legacy ai_staged snapshot or import — stays on Review tab.
            v = e.control.value
            if v is None:
                return
            try:
                vid = int(v)
            except (TypeError, ValueError):
                return
            # Persist edits to the currently-loaded proposal before swapping it out.
            self._flush_review_edits_if_changed()
            with session_scope() as s:
                row = version_storage.get_version_row(s, vid)
            if row is not None and row.reason in ("ai_proposal", "ai_staged"):
                self._select_proposal_as_review_candidate(vid)
            else:
                # Imports: load as candidate, then treat as ai_preview so accept goes through AI flow.
                self._select_snapshot_as_candidate(vid)
                self._compare_candidate_source = "ai_preview"
                self._pending_ai_accept_action_id = (
                    self._pending_ai_accept_action_id or "ai_proposal"
                )
                self._loaded_proposal_sha = version_storage.content_sha256(
                    self._compare_editor.value or ""
                )
            self._rebuild_future_paragraph_ui()
            self._refresh_compare_tab_candidate_ui()
            self._refresh_compare_diff_immediate()
            self._refresh_compare_bulk_buttons()
            self._refresh_title_bar()
            return

        if e.control is self._compare_newer_dropdown:
            await self._on_compare_newer_change_async(e)
            return

        if self._compare_candidate_dropdown.disabled or not self.current_path:
            return
        v = e.control.value

        # History: older-side dropdown (snapshots strictly older than the newer pick)
        if v is None:
            return
        try:
            vid = int(v)
        except (TypeError, ValueError):
            return
        self._select_snapshot_as_candidate(vid)
        if self._main_tab_index != TAB_HISTORY:
            await self._request_tab_switch_async(TAB_HISTORY)
        self._refresh_compare_diff_immediate()
        self._refresh_compare_bulk_buttons()
        self._refresh_title_bar()

    def _select_proposal_as_review_candidate(self, vid: int) -> None:
        """Load a persisted ai_proposal snapshot into the Review right column.

        Sets ai_preview state so per-row / bulk Accept go through the AI flow. The action_id
        used by Accept comes from the in-memory map (recorded on persist) or the snapshot's
        display_label as a fallback after restart; ``ai_proposal`` is the last-resort sentinel.
        """
        try:
            with session_scope() as s:
                body = version_storage.load_version_body(s, vid)
                row = version_storage.get_version_row(s, vid)
        except BaseException:
            self._snack("Could not load that proposal.")
            return
        self._compare_snapshot_version_id = vid
        self._compare_pdf_peer_snapshot_id = None
        self._compare_editor.value = body
        self._compare_candidate_source = "ai_preview"
        self._pending_ai_accept_action_id = (
            self._ai_proposal_action_ids.get(vid)
            or (row.display_label if row else None)
            or "ai_proposal"
        )
        self._loaded_proposal_sha = version_storage.content_sha256(body)
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()

    def _flush_review_edits_if_changed(self) -> None:
        """Persist edits to the loaded ai_proposal as a new snapshot when the user leaves it.

        SHA-dedup against the snapshot we loaded; no row is written for unchanged sessions.
        Updates _latest_ai_proposal_vid + _ai_proposal_action_ids + _compare_snapshot_version_id
        + _loaded_proposal_sha so dropdown / accept paths point at the new row.
        """
        if not self.current_path:
            return
        if self._compare_candidate_source != "ai_preview":
            return
        if self._loaded_proposal_sha is None:
            return
        body = self._compare_editor.value or ""
        new_sha = version_storage.content_sha256(body)
        if new_sha == self._loaded_proposal_sha:
            return
        aid = self._pending_ai_accept_action_id or ""
        act = prompts.get_margin_action(aid) if aid else None
        base_label = act.label if act else (aid or "AI proposal")
        label = f"{base_label} - edited"
        try:
            with session_scope() as s:
                new_vid = version_storage.persist_version_snapshot(
                    s,
                    self.current_path.resolve(),
                    body,
                    "ai_proposal",
                    display_label=label,
                )
        except BaseException:
            return
        if new_vid is None:
            # Most-recent doc snapshot already had this sha; treat as already saved.
            self._loaded_proposal_sha = new_sha
            return
        if aid:
            self._ai_proposal_action_ids[new_vid] = aid
        self._latest_ai_proposal_vid = new_vid
        self._compare_snapshot_version_id = new_vid
        self._loaded_proposal_sha = new_sha
        self._refresh_compare_tab_candidate_ui()

    def _select_snapshot_as_candidate(self, vid: int) -> None:
        """Pick a snapshot row (History or Import). Auto-route to the correct format renderer.

        Sets ``_compare_candidate_source`` based on which asset (PDF, DOCX, …)
        is attached to the snapshot row, then delegates rendering to
        ``_rebuild_compare_view()``.
        """
        try:
            with session_scope() as s:
                body = version_storage.load_version_body(s, vid)
                pdf_rel = version_storage.get_version_pdf_relpath(s, vid)
                docx_rel = version_storage.get_version_docx_relpath(s, vid)
        except BaseException:
            self._snack("Could not load that version.")
            return
        self._compare_snapshot_version_id = vid
        self._pending_ai_accept_action_id = None
        self._compare_editor.value = body
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        if pdf_rel:
            self._compare_candidate_source = "pdf_original"
            self._compare_pdf_peer_snapshot_id = vid
        elif docx_rel:
            self._compare_candidate_source = "docx_original"
            self._compare_pdf_peer_snapshot_id = vid
        else:
            self._compare_candidate_source = "snapshot"
            self._compare_pdf_peer_snapshot_id = None
        self._rebuild_compare_view()

    def _set_compare_version_dd_focused(self, focused: bool) -> None:
        self._compare_version_dd_focused = focused
        self._apply_compare_candidate_dropdown_tab_chrome()

    def _apply_compare_candidate_dropdown_tab_chrome(self) -> None:
        """Outline around the version dropdown (tree search style): grey at rest, blue on hover/focus."""
        selected = self._main_tab_index == TAB_HISTORY
        for wrap, own_hover in (
            (self._compare_dropdown_hover_wrap, self._compare_dropdown_hover),
            (self._compare_newer_dropdown_hover_wrap, self._compare_newer_dropdown_hover),
        ):
            accent = selected and (own_hover or self._compare_version_dd_focused)
            rim = config.PRIMARY_COLOR if accent else ui_theme.outline_muted()
            wrap.border = ft.Border.all(1, rim)
        if self._main_tab_index == TAB_HISTORY:
            if _ctrl_on_page(self._compare_dropdown_hover_wrap):
                self._compare_dropdown_hover_wrap.update()
            if _ctrl_on_page(self._compare_newer_dropdown_hover_wrap):
                self._compare_newer_dropdown_hover_wrap.update()
            if _ctrl_on_page(self._compare_candidate_dropdown):
                self._compare_candidate_dropdown.update()
            if _ctrl_on_page(self._compare_newer_dropdown):
                self._compare_newer_dropdown.update()

    def _on_compare_dropdown_container_hover(self, e: ft.ControlEvent) -> None:
        self._compare_dropdown_hover = str(e.data).lower() == "true"
        self._apply_compare_candidate_dropdown_tab_chrome()

    def _on_compare_newer_dropdown_container_hover(self, e: ft.ControlEvent) -> None:
        self._compare_newer_dropdown_hover = str(e.data).lower() == "true"
        self._apply_compare_candidate_dropdown_tab_chrome()

    def _compare_para_text_style(self) -> ft.TextStyle:
        return ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=config.ON_SURFACE,
        )

    def _compare_insertion_diff_colors(self) -> tuple[str, str | None]:
        """Insertion spans: foreground always ``on_surface``; light uses ``success`` tint as bg."""
        if config.IS_LIGHT:
            return config.ON_SURFACE, ft.Colors.with_opacity(0.5, config.SUCCESS)
        return config.ON_SURFACE, None

    def _compare_pill_colors(self, kind: paragraph_compare.SlotKind) -> tuple[str, str]:
        new_pill: tuple[str, str] = (
            (ft.Colors.with_opacity(0.45, config.SUCCESS), config.ON_SURFACE)
            if config.IS_LIGHT
            else (ft.Colors.with_opacity(0.28, ft.Colors.GREEN_400), ft.Colors.GREEN_100)
        )
        m: dict[str, tuple[str, str]] = {
            "stable": (
                ft.Colors.with_opacity(0.18, config.OUTLINE),
                config.ON_SURFACE_VARIANT,
            ),
            "refined":   (ft.Colors.with_opacity(0.28, ft.Colors.BLUE_400), ft.Colors.BLUE_100),
            "modified":  (ft.Colors.with_opacity(0.28, ft.Colors.ORANGE_400), ft.Colors.ORANGE_100),
            "rephrased": (ft.Colors.with_opacity(0.28, ft.Colors.PURPLE_400), ft.Colors.PURPLE_100),
            "added":     new_pill,
            "removed":   (ft.Colors.with_opacity(0.28, ft.Colors.RED_400), ft.Colors.RED_100),
        }
        return m.get(
            kind,
            (ft.Colors.with_opacity(0.22, config.OUTLINE), config.ON_SURFACE_VARIANT),
        )

    @staticmethod
    def _compare_displacement_arrow_text(displacement: int | None) -> str:
        if displacement is None or displacement == 0:
            return ""
        n = abs(displacement)
        return f"↑{n}" if displacement > 0 else f"↓{n}"

    def _make_compare_pill(self, kind: paragraph_compare.SlotKind) -> ft.Container:
        label = paragraph_compare.slot_kind_label(kind)
        bg, fg = self._compare_pill_colors(kind)
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
        bg = ft.Colors.with_opacity(0.28, ft.Colors.TEAL_400)
        fg = ft.Colors.TEAL_100
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

    def _sync_compare_buffer_from_fields(self) -> None:
        parts = [tf.value or "" for tf in self._compare_right_fields]
        if self._main_tab_index == TAB_FUTURE:
            # Drop empty entries: a delete row that's accepted (field stays "") removes
            # the paragraph from the buffer; an insert row that's declined ("" set on
            # decline) does the same — naturally yielding the post-accept document.
            merged = "\n\n".join(p for p in parts if p)
        else:
            merged = "\n\n".join(parts) if parts else ""
        if self._main_tab_index == TAB_HISTORY:
            if self._compare_newer_version_id is None:
                self.editor.value = merged
            else:
                self._compare_newer_cached_body = merged
        else:
            self._compare_editor.value = merged

    def _capture_compare_baseline_snapshot(self) -> None:
        """Store Compose buffer as the Compare left baseline (draft mode only uses this via _compare_latest_baseline_text)."""
        self._compare_baseline_snapshot = self.editor.value or ""

    def _compare_latest_baseline_text(self) -> str:
        """On History, the newer (right) side; otherwise the compose document."""
        if self._main_tab_index == TAB_HISTORY:
            return self._history_newer_side_text()
        return self.editor.value or ""

    def _persist_ai_accept_snapshots(
        self,
        pre_buf: str,
        post_buf: str,
        *,
        para_index: int,
        action_id: str,
        bulk: bool = False,
    ) -> None:
        """Two DB snapshots for AI Compare accept: document before merge, then after (parent chain)."""
        if not self.current_path:
            return
        act = prompts.get_margin_action(action_id)
        apply_label = act.label if act else action_id
        before_label = "Before accept · all paragraphs" if bulk else f"Before accept · paragraph {para_index + 1}"
        try:
            with session_scope() as s:
                rp = self.current_path.resolve()
                version_storage.persist_version_snapshot(
                    s,
                    rp,
                    pre_buf,
                    "before_apply",
                    display_label=before_label,
                )
                version_storage.persist_version_snapshot(
                    s,
                    rp,
                    post_buf,
                    "ai_apply",
                    display_label=apply_label,
                )
        except BaseException:
            pass

    def _compare_paragraph_diff_spans(self, left_para: str, right_para: str) -> list[ft.TextSpan]:
        """Unified inline diff (Review right column): both deletions and insertions in one stream."""
        old_t, new_t = self._compare_diff_clip(left_para, right_para)
        ins_fg, ins_bg = self._compare_insertion_diff_colors()
        return build_unified_spans(
            old_t,
            new_t,
            base_size=COMPARE_COL_FONT_SIZE,
            base_color=config.ON_SURFACE,
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
            base_color=config.ON_SURFACE,
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
            base_color=config.ON_SURFACE,
            font_family="monospace",
            insert_color=ins_fg,
            insert_bgcolor=ins_bg,
            line_height=COMPARE_COL_LINE_HEIGHT,
        )

    def _refresh_future_left_diff_spans(self) -> None:
        """Update Review left column diff spans for the gap-aligned row list."""
        if self._main_tab_index != TAB_FUTURE:
            return
        if len(self._future_left_diff_texts) != len(self._compare_right_fields):
            return
        current = self.editor.value or ""
        candidate = self._compare_editor.value or ""
        if len(current) + len(candidate) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            current = current[:half] + "\n…"
            candidate = candidate[:half] + "\n…"
        rows = aligned_review_rows(current, candidate)
        for i, row in enumerate(rows):
            if i >= len(self._future_left_diff_texts):
                break
            t = self._future_left_diff_texts[i]
            if row.kind == "insert":
                t.spans = []
                t.value = ""
            else:
                t.value = None
                t.spans = self._future_old_side_spans(row.old_text, row.new_text)
            if _ctrl_on_page(t):
                t.update()

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
        pairs = pair_paragraphs_for_compare(older, newer)
        for i, (left_txt, right_txt) in enumerate(pairs):
            if i >= len(self._compare_left_diff_texts):
                break
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

    def _build_actions_square(
        self,
        i: int,
        *,
        persistent: bool = False,
    ) -> tuple[ft.Container, ft.Container | None]:
        """Slim Review action grid: check + x only. Returns (actions_ctrl, hover_wrap_or_None)."""
        actions_inner = build_action_square(
            left=action_rail_approve_icon_button(
                on_click=lambda _e, ix=i: self.page.run_task(self._compare_accept_paragraph_async, ix),
            ),
            right=action_rail_reject_icon_button(
                on_click=lambda _e, ix=i: self.page.run_task(self._compare_decline_paragraph_async, ix),
            ),
            row_h=COMPARE_ACTION_GRID_CELL,
        )
        return wrap_workspace_action_chrome(actions_inner, persistent=persistent)

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
        self._check_para_hashes = [compute_hash(r.new_text) for r in comparison_rows]
        for cid in list(self._check_results.keys()):
            results = self._check_results.get(cid) or []
            if len(results) != n_comp:
                self._check_results[cid] = (results + [None] * n_comp)[:n_comp]

        _MOVED_OPACITY = 0.55
        para_style = self._compare_para_text_style()
        # Ghost rows use the same ON_SURFACE colour + opacity as moved comparison rows
        # so both sides of a moved paragraph read at the same grey level.
        ghost_text_style = ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=config.ON_SURFACE,
            decoration=ft.TextDecoration.LINE_THROUGH,
            decoration_color=config.ON_SURFACE,
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
                pill_disp = row.displacement  # passive: ↑n/↓n arrow, no badge
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

    def _rebuild_future_paragraph_ui(self) -> None:
        """Future tab: eval | current(read-only diff with deletions) | pill | AI proposal(editable) | actions.

        Rows are gap-aligned: pure deletions render with an empty right cell, pure
        insertions with an empty left cell. ``_compare_right_fields`` keeps one entry per
        UI row (a hidden empty TextField for delete rows) so accept/decline indices align
        with the visible row order. ``_future_row_kinds`` / ``_future_row_cand_idx`` /
        ``_future_row_stable_texts`` track per-row metadata for buffer sync and decline.
        """
        self._compare_pill_gen += 1
        current_text = self.editor.value or ""
        ai_text = self._compare_editor.value or ""
        if len(current_text) + len(ai_text) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            current_text = current_text[:half] + "\n…"
            ai_text = ai_text[:half] + "\n…"
        rows = aligned_review_rows(current_text, ai_text)
        kinds_h, disps_h = paragraph_compare.compare_slots_heuristic(current_text, ai_text)

        self._future_rows_listview.controls.clear()
        self._future_left_diff_texts.clear()
        self._future_row_pill_hosts.clear()
        self._compare_right_fields.clear()
        self._compare_eval_hosts.clear()
        self._future_row_kinds = []
        self._future_row_cand_idx = []
        self._future_row_stable_texts = []
        self._hide_all_result_card_overlays()

        para_style = self._compare_para_text_style()
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

        for i, row_spec in enumerate(rows):
            self._future_row_kinds.append(row_spec.kind)
            self._future_row_cand_idx.append(row_spec.cand_idx)
            self._future_row_stable_texts.append(row_spec.old_text)

            if row_spec.kind == "delete":
                pill_kind: paragraph_compare.SlotKind = "removed"
                disp: int | None = None
            elif row_spec.cand_idx is not None and row_spec.cand_idx < len(kinds_h):
                pill_kind = kinds_h[row_spec.cand_idx]
                disp = (
                    disps_h[row_spec.cand_idx]
                    if row_spec.cand_idx < len(disps_h)
                    else None
                )
            else:
                pill_kind = "added" if row_spec.kind == "insert" else "stable"
                disp = None
            pill_host = ft.Container(
                content=self._make_compare_pill_row(pill_kind, disp),
                width=COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_LEFT,
                padding=ft.padding.only(top=4),
            )

            # Left cell: empty placeholder for inserts; struck-through old paragraph
            # (full strikethrough on delete since new_text="") otherwise.
            if row_spec.kind == "insert":
                left_diff = ft.Text(
                    "",
                    style=para_style,
                    selectable=True,
                    expand=True,
                    no_wrap=False,
                )
            else:
                left_diff = ft.Text(
                    spans=self._future_old_side_spans(row_spec.old_text, row_spec.new_text),
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
                opacity=0.55 if (disp is not None and disp != 0) else 1.0,
            )

            # Right cell: editable AI proposal for replace/insert/equal; empty placeholder
            # for delete rows. The hidden TextField keeps accept/decline index parity.
            if row_spec.kind == "delete":
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
            else:
                right_tf = ft.TextField(
                    **right_tf_kwargs,
                    value=row_spec.new_text,
                    read_only=False,
                    enable_interactive_selection=True,
                    hint_text="…",
                    expand=True,
                    on_change=lambda _e, ix=i: self._on_compare_para_field_change(ix),
                )
                right_cell = ft.Container(
                    content=right_tf,
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                )

            if show_actions:
                actions_ctrl, hover_wrap_future = self._build_actions_square(i, persistent=False)
            else:
                actions_ctrl, hover_wrap_future = None, None

            eval_host = self._build_eval_cell(i)

            row_cells: list[ft.Control] = [
                eval_host,
                left_cell,
                pill_host,
                right_cell,
            ]
            if actions_ctrl is not None:
                row_cells.append(actions_ctrl)
            row_inner = ft.Row(
                row_cells,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
            if hover_wrap_future is not None:
                row = ft.Container(
                    content=row_inner,
                    on_hover=lambda e, w=hover_wrap_future: self._on_compare_row_hover(e, w),
                )
            else:
                row = row_inner
            self._future_rows_listview.controls.append(row)
            self._future_row_pill_hosts.append(pill_host)
            self._compare_eval_hosts.append(eval_host)
            self._compare_right_fields.append(right_tf)

        if _ctrl_on_page(self._future_rows_listview):
            self._future_rows_listview.update()

        if self._active_check_id is not None:
            self._refresh_all_eval_cells()

        self._refresh_compare_bulk_buttons()
        self._compare_refine_gen += 1
        self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)

    def _refresh_compare_diff_immediate(self) -> None:
        if self._main_tab_index == TAB_FUTURE:
            self._rebuild_future_paragraph_ui()
            return
        self._rebuild_compare_view()

    async def _debounced_compare_diff(self, gen: int) -> None:
        await asyncio.sleep(0.12)
        if gen != self._compare_diff_gen:
            return
        if self._compare_candidate_source not in ("snapshot", "draft", "ai_preview"):
            return  # non-text format (PDF, DOCX, IFC, …); no debounced diff needed
        if self._main_tab_index == TAB_HISTORY:
            cand = self._history_newer_side_text() or ""
            n_rows = len(split_paragraphs(cand))
        else:
            cand = self._compare_editor.value or ""
            current = self.editor.value or ""
            n_rows = len(aligned_review_rows(current, cand))
        if n_rows != len(self._compare_right_fields):
            if self._main_tab_index == TAB_FUTURE:
                self._rebuild_future_paragraph_ui()
            else:
                self._rebuild_compare_paragraph_ui()
            return
        if self._main_tab_index == TAB_FUTURE:
            self._refresh_future_left_diff_spans()
        else:
            self._refresh_compare_left_diff_spans()
        # Drop in-memory analysis results for paragraphs whose new-text hash changed.
        self._invalidate_check_results_for_changes()
        if self._active_check_id is not None:
            self._refresh_all_eval_cells()
        self._compare_pill_gen += 1
        pg = self._compare_pill_gen
        self.page.run_task(self._debounced_compare_pill_refresh, pg)

    def _active_pill_hosts(self) -> list:
        """Return the pill host list for the currently active compare tab."""
        if self._main_tab_index == TAB_FUTURE:
            return self._future_row_pill_hosts
        return self._compare_row_pill_hosts

    async def _debounced_compare_pill_refresh(self, gen: int) -> None:
        await asyncio.sleep(0.18)
        if gen != self._compare_pill_gen:
            return
        if self._main_tab_index not in (TAB_HISTORY, TAB_FUTURE):
            return
        buffers = self._active_compare_buffers()
        kinds, disps = paragraph_compare.compare_slots_heuristic(buffers.baseline, buffers.candidate)
        on_history = self._main_tab_index == TAB_HISTORY
        for i, host in enumerate(self._active_pill_hosts()):
            k = kinds[i] if i < len(kinds) else "stable"
            d = disps[i] if i < len(disps) else None
            host.content = self._make_compare_pill_row(k, d, show_moved_badge=not on_history)
            if _ctrl_on_page(host):
                host.update()

    async def _debounced_refine_compare_slots(self, gen: int) -> None:
        await asyncio.sleep(0.05)
        if gen != self._compare_refine_gen:
            return
        if self._main_tab_index not in (TAB_HISTORY, TAB_FUTURE):
            return
        buffers = self._active_compare_buffers()
        if not buffers.baseline.strip() and not buffers.candidate.strip():
            return
        try:
            refined, aligned_lefts, disps_ref = await paragraph_compare.classify_slots_async(
                self._db,
                self._make_llm_backend(),
                chat_model=self.chat_model_for_requests(),
                doc_path=str(self.current_path.resolve()) if self.current_path else None,
                baseline_text=buffers.baseline,
                new_text=buffers.candidate,
            )
        except BaseException:
            return
        if gen != self._compare_refine_gen:
            return
        on_history = self._main_tab_index == TAB_HISTORY
        for i, host in enumerate(self._active_pill_hosts()):
            k = refined[i] if i < len(refined) else "stable"
            d = disps_ref[i] if i < len(disps_ref) else None
            host.content = self._make_compare_pill_row(k, d, show_moved_badge=not on_history)
            if _ctrl_on_page(host):
                host.update()
        # Update diff spans in both History columns after AI refinement.
        if self._main_tab_index == TAB_HISTORY and (
            len(aligned_lefts) == len(self._compare_left_diff_texts)
            and len(aligned_lefts) == len(self._compare_right_fields)
        ):
            for i, left_txt in enumerate(aligned_lefts):
                right_txt = self._compare_right_fields[i].value or ""
                left_t = self._compare_left_diff_texts[i]
                left_t.spans = self._compare_old_side_spans(left_txt, right_txt)
                if _ctrl_on_page(left_t):
                    left_t.update()
                if i < len(self._compare_right_diff_texts):
                    right_t = self._compare_right_diff_texts[i]
                    right_t.spans = self._compare_new_side_spans(left_txt, right_txt)
                    if _ctrl_on_page(right_t):
                        right_t.update()

    def _on_compare_para_field_change(self, index: int) -> None:
        self._sync_compare_buffer_from_fields()
        # History: right fields track the current draft (editor) → autosave to disk.
        # Future:  right fields are the AI proposal candidate → in-memory only, no autosave.
        on_future = self._main_tab_index == TAB_FUTURE
        cand = (self.editor.value if not on_future else self._compare_editor.value) or ""
        if on_future:
            n_rows = len(aligned_review_rows(self.editor.value or "", cand))
        else:
            n_rows = len(split_paragraphs(cand))
        if n_rows != len(self._compare_right_fields):
            if on_future:
                self._rebuild_future_paragraph_ui()
            else:
                self._rebuild_compare_paragraph_ui()
            self._refresh_title_bar()
            if not on_future:
                self._kick_debounced_autosave()
            return
        self._refresh_title_bar()
        self._compare_diff_gen += 1
        dgen = self._compare_diff_gen
        self.page.run_task(self._debounced_compare_diff, dgen)
        if not on_future:
            self._kick_debounced_autosave()

    async def _compare_accept_paragraph_async(self, index: int) -> None:
        if not self.current_path:
            self._snack("Open a note first.")
            return
        if index < 0 or index >= len(self._compare_right_fields):
            return
        cand_para = self._compare_right_fields[index].value or ""
        pre_buf = self.editor.value or ""
        new_buf = replace_paragraph_at_index(pre_buf, index, cand_para)
        ai_flow = (
            self._compare_candidate_source == "ai_preview"
            and self._pending_ai_accept_action_id
        )
        if ai_flow:
            self._persist_ai_accept_snapshots(
                pre_buf,
                new_buf,
                para_index=index,
                action_id=self._pending_ai_accept_action_id,
            )
            try:
                self.current_path.write_text(new_buf, encoding="utf-8")
            except OSError as ex:
                self._snack(f"Save failed: {ex}")
                return
            self.last_saved_text = new_buf
            self._refresh_compare_tab_candidate_ui()
        self.editor.value = new_buf
        self._capture_compare_baseline_snapshot()
        if _ctrl_on_page(self.editor):
            self.editor.update()
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()
        self._snack(f"Paragraph {index + 1} applied to the document.")

    async def _compare_decline_paragraph_async(self, index: int) -> None:
        if index < 0 or index >= len(self._compare_right_fields):
            return
        on_future = self._main_tab_index == TAB_FUTURE
        stable_list = (
            self._future_row_stable_texts if on_future else self._compare_row_stable_texts
        )
        if index < len(stable_list):
            revert = stable_list[index]
        else:
            paras = split_paragraphs(self._compare_latest_baseline_text() or "")
            revert = paras[index] if 0 <= index < len(paras) else ""
        self._compare_right_fields[index].value = revert
        if _ctrl_on_page(self._compare_right_fields[index]):
            self._compare_right_fields[index].update()
        self._sync_compare_buffer_from_fields()
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()

    async def _compare_approve_all_async(self) -> None:
        if not self.current_path:
            self._snack("Open a note first.")
            return
        if not self._compare_right_fields:
            return
        parts = [tf.value or "" for tf in self._compare_right_fields]
        new_buf = "\n\n".join(parts)
        pre_buf = self.editor.value or ""
        ai_flow = (
            self._compare_candidate_source == "ai_preview"
            and self._pending_ai_accept_action_id
        )
        if ai_flow:
            self._persist_ai_accept_snapshots(
                pre_buf,
                new_buf,
                para_index=0,
                action_id=self._pending_ai_accept_action_id,
                bulk=True,
            )
            try:
                self.current_path.write_text(new_buf, encoding="utf-8")
            except OSError as ex:
                self._snack(f"Save failed: {ex}")
                return
            self.last_saved_text = new_buf
            self._refresh_compare_tab_candidate_ui()
        self.editor.value = new_buf
        self._capture_compare_baseline_snapshot()
        if _ctrl_on_page(self.editor):
            self.editor.update()
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()
        self._snack("All paragraphs applied to the document.")

    async def _compare_decline_all_async(self) -> None:
        if not self._compare_right_fields:
            return
        on_future = self._main_tab_index == TAB_FUTURE
        stable_list = (
            self._future_row_stable_texts if on_future else self._compare_row_stable_texts
        )
        baseline = self._compare_latest_baseline_text()
        paras = split_paragraphs(baseline or "")
        for i, tf in enumerate(self._compare_right_fields):
            if i < len(stable_list):
                revert = stable_list[i]
            else:
                revert = paras[i] if i < len(paras) else ""
            tf.value = revert
            if _ctrl_on_page(tf):
                tf.update()
        self._sync_compare_buffer_from_fields()
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()
        self._snack("All paragraphs reset to match latest.")

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

    def _compare_paragraph_for_index(self, idx: int) -> str:
        if 0 <= idx < len(self._compare_right_fields):
            return self._compare_right_fields[idx].value or ""
        paras = split_paragraphs(self._compare_editor.value or "")
        return paras[idx] if 0 <= idx < len(paras) else ""

    def _resolve_vid_after_proposal_persist(
        self, new_vid: int | None, cand_body: str
    ) -> int | None:
        """If persist returned None (SHA dedup), use newest snapshot row matching ``cand_body``."""
        if new_vid is not None or not self.current_path:
            return new_vid
        want_sha = version_storage.content_sha256(cand_body)
        try:
            with session_scope() as s:
                snaps = version_storage.list_snapshots(s, self.current_path.resolve())
            for sn in snaps:
                if sn.content_sha256 == want_sha:
                    return sn.version_id
        except BaseException:
            pass
        return None

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
        self._compare_candidate_source = "ai_preview"
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
        self._compare_candidate_source = "ai_preview"
        self._compare_snapshot_version_id = new_vid
        self._pending_ai_accept_action_id = action_id
        if new_vid is not None:
            self._ai_proposal_action_ids[new_vid] = action_id
            self._latest_ai_proposal_vid = new_vid
        self._loaded_proposal_sha = version_storage.content_sha256(self._compare_editor.value or "")
        self._hide_prompt_footer(footer)
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        await self._request_tab_switch_async(TAB_FUTURE)
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()

