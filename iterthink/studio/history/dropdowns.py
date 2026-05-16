"""Compare tab toolbars: History newer/older dropdowns and Review candidate dropdown."""

from __future__ import annotations

import flet as ft

from iterthink import prompts
from iterthink.db.session import session_scope
from iterthink.persistence import version_storage
from iterthink.persistence.version_storage import SnapshotInfo

from .. import ui_theme
from ..constants import (
    COMPARE_KEY_CURRENT as _COMPARE_KEY_CURRENT,
    REVIEW_KEY_DRAFT_MIRROR,
    REVIEW_KEY_SPELL_CHECK,
    REVIEW_MANUAL_CANDIDATE_ACTION_ID,
    TAB_FUTURE,
    TAB_HISTORY,
)
from ..util import ctrl_on_page as _ctrl_on_page
from .buffers import (
    build_history_snapshot_dropdown_options,
    history_compare_snapshots,
    review_action_apply_label,
    snapshots_strictly_older_than,
)
from .candidate_state import CompareCandidateSource


class _HistoryDropdownsMixin:
    def _refresh_compare_tab_candidate_ui(self) -> None:
        _st = ui_theme.compare_candidate_dropdown_option_style()

        # History / Review dropdowns live in a Stack under the tab bar; only the active tab's
        # toolbar is visible. Mutating + .update() on a hidden Dropdown can surface its menu
        # overlay (e.g. History flash on Focus Area). Gate each block to its tab.
        if self._main_tab_index == TAB_HISTORY:
            # ── History: newer (right) + older (left) dropdowns ─────────────────────
            snaps_all: list[SnapshotInfo] = []
            if self.current_path:
                with session_scope() as s:
                    snaps_all = version_storage.list_snapshots(s, self.current_path.resolve())
            filt = history_compare_snapshots(snaps_all)

            newer_opts: list[ft.dropdown.Option] = []
            # Only offer "Current draft" when the editor has unsaved changes; if it is
            # identical to the last-saved snapshot it would be a duplicate entry.
            if self._is_dirty():
                newer_opts.append(
                    ft.dropdown.Option(key=_COMPARE_KEY_CURRENT, text="Current draft", style=_st)
                )
            newer_opts.extend(build_history_snapshot_dropdown_options(filt, _st))
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

            older_slice = snapshots_strictly_older_than(filt, self._compare_newer_version_id)
            older_opts = build_history_snapshot_dropdown_options(older_slice, _st)
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
                self._compare_candidate_source = CompareCandidateSource.SNAPSHOT
                self._compare_editor.value = ""
                self._compare_candidate_dropdown.value = None

            if _ctrl_on_page(self._compare_newer_dropdown):
                self._compare_newer_dropdown.update()
            if _ctrl_on_page(self._compare_candidate_dropdown):
                self._compare_candidate_dropdown.update()

        if self._main_tab_index == TAB_FUTURE:
            # ── Review dropdown: ai_proposal / legacy ai_staged + manual imports ─
            snapshot_review_opts: list[ft.dropdown.Option] = []
            if self.current_path:
                with session_scope() as s:
                    snaps = version_storage.list_snapshots(s, self.current_path.resolve())
                for sn in snaps:
                    row_text = version_storage.snapshot_dropdown_text(sn)
                    if sn.reason == "ai_proposal":
                        snapshot_review_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"AI - {row_text}",
                                style=_st,
                            )
                        )
                    elif sn.reason == "review_edit":
                        snapshot_review_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"Edited - {row_text}",
                                style=_st,
                            )
                        )
                    elif sn.reason == "ai_staged":
                        snapshot_review_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"AI - {row_text} (legacy)",
                                style=_st,
                            )
                        )
                    elif version_storage.snapshot_bucket(sn) == "import":
                        snapshot_review_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"Import - {row_text}",
                                style=_st,
                            )
                        )
            review_opts: list[ft.dropdown.Option] = [
                ft.dropdown.Option(
                    key=REVIEW_KEY_DRAFT_MIRROR,
                    text="Draft vs draft (editable copy)",
                    style=_st,
                ),
                ft.dropdown.Option(
                    key=REVIEW_KEY_SPELL_CHECK,
                    text="Draft vs spelling (suggested)",
                    style=_st,
                ),
                *snapshot_review_opts,
            ]
            self._review_candidate_dropdown.options = review_opts
            r_keys = {o.key for o in review_opts}
            # Default selection: loaded proposal vid, else draft-mirror mode, else latest AI snapshot.
            preferred: str | None = None
            if (
                self._compare_candidate_source == CompareCandidateSource.AI_PREVIEW
                and self._compare_snapshot_version_id is not None
            ):
                preferred = str(self._compare_snapshot_version_id)
            elif self._compare_candidate_source == CompareCandidateSource.SPELL_PREVIEW:
                preferred = REVIEW_KEY_SPELL_CHECK
            elif (
                self._compare_candidate_source == CompareCandidateSource.AI_PREVIEW
                and self._compare_snapshot_version_id is None
                and self._pending_ai_accept_action_id == REVIEW_MANUAL_CANDIDATE_ACTION_ID
            ):
                preferred = REVIEW_KEY_DRAFT_MIRROR
            elif self._latest_ai_proposal_vid is not None:
                preferred = str(self._latest_ai_proposal_vid)
            if preferred and preferred in r_keys:
                self._review_candidate_dropdown.value = preferred
            elif snapshot_review_opts:
                self._review_candidate_dropdown.value = snapshot_review_opts[0].key
            else:
                self._review_candidate_dropdown.value = REVIEW_KEY_DRAFT_MIRROR
            self._review_candidate_dropdown.disabled = False
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
        if self._main_tab_index == TAB_HISTORY:
            self._refresh_tab_toolbar()

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
        self._reset_check_analysis_session()
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
            if v == REVIEW_KEY_SPELL_CHECK:
                self._enter_spell_review_mode()
                return
            if v == REVIEW_KEY_DRAFT_MIRROR:
                self._flush_review_edits_if_changed()
                self._reset_check_analysis_session()
                seeded = self.editor.value or ""
                self._compare_candidate_source = CompareCandidateSource.AI_PREVIEW
                self._compare_editor.value = seeded
                self._pending_ai_accept_action_id = REVIEW_MANUAL_CANDIDATE_ACTION_ID
                self._compare_snapshot_version_id = None
                self._loaded_proposal_sha = version_storage.content_sha256(seeded)
                if _ctrl_on_page(self._compare_editor):
                    self._compare_editor.update()
                self._refresh_compare_tab_candidate_ui()
                self._refresh_compare_diff_immediate()
                self._refresh_compare_bulk_buttons()
                self._refresh_title_bar()
                return
            try:
                vid = int(v)
            except (TypeError, ValueError):
                return
            # Persist edits to the currently-loaded proposal before swapping it out.
            self._flush_review_edits_if_changed()
            with session_scope() as s:
                row = version_storage.get_version_row(s, vid)
                row_reason = row.reason if row is not None else None
            if row_reason in ("ai_proposal", "ai_staged", "review_edit"):
                self._select_proposal_as_review_candidate(vid)
            else:
                # Imports: text-only snapshots use ai_preview for accept flow; PDF/Word keep asset sources.
                self._select_snapshot_as_candidate(vid)
                if self._compare_candidate_source == CompareCandidateSource.SNAPSHOT:
                    self._compare_candidate_source = CompareCandidateSource.AI_PREVIEW
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
        prev_vid = self._compare_snapshot_version_id
        try:
            with session_scope() as s:
                body = version_storage.load_version_body(s, vid)
                row = version_storage.get_version_row(s, vid)
                # Read columns inside the session; ORM rows detach on scope exit.
                fallback_label = (row.display_label or "").strip() if row is not None else ""
        except BaseException:
            self._snack("Could not load that proposal.")
            return
        if prev_vid != vid:
            self._reset_check_analysis_session()
        self._compare_snapshot_version_id = vid
        self._compare_pdf_peer_snapshot_id = None
        self._compare_editor.value = body
        self._compare_candidate_source = CompareCandidateSource.AI_PREVIEW
        self._pending_ai_accept_action_id = (
            self._ai_proposal_action_ids.get(vid)
            or (fallback_label or None)
            or "ai_proposal"
        )
        self._loaded_proposal_sha = version_storage.content_sha256(body)
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()

    def _flush_review_edits_if_changed(self, *, refresh_compare_ui: bool = True) -> None:
        """Persist edits to the loaded ai_proposal as a new snapshot when the user leaves it.

        SHA-dedup against the snapshot we loaded; no row is written for unchanged sessions.
        Updates _latest_ai_proposal_vid + _ai_proposal_action_ids + _compare_snapshot_version_id
        + _loaded_proposal_sha so dropdown / accept paths point at the new row.
        """
        if not self.current_path:
            return
        if self._compare_candidate_source != CompareCandidateSource.AI_PREVIEW:
            return
        if self._loaded_proposal_sha is None:
            return
        body = self._compare_editor.value or ""
        new_sha = version_storage.content_sha256(body)
        if new_sha == self._loaded_proposal_sha:
            return
        aid = self._pending_ai_accept_action_id or ""
        act = prompts.get_margin_action(aid) if aid else None
        base_label = act.label if act else review_action_apply_label(aid)
        label = f"{base_label} - edited"
        try:
            with session_scope() as s:
                new_vid = version_storage.persist_version_snapshot(
                    s,
                    self.current_path.resolve(),
                    body,
                    "review_edit",
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
        if refresh_compare_ui:
            self._refresh_compare_tab_candidate_ui()

    def _select_snapshot_as_candidate(self, vid: int) -> None:
        """Pick a snapshot row (History or Import). Auto-route to the correct format renderer.

        Sets ``_compare_candidate_source`` based on which asset (PDF, DOCX, …)
        is attached to the snapshot row, then delegates rendering to
        ``_rebuild_compare_view()``.
        """
        prev_vid = self._compare_snapshot_version_id
        try:
            with session_scope() as s:
                body = version_storage.load_version_body(s, vid)
                pdf_rel = version_storage.get_version_pdf_relpath(s, vid)
                docx_rel = version_storage.get_version_docx_relpath(s, vid)
        except BaseException:
            self._snack("Could not load that version.")
            return
        if prev_vid != vid:
            self._reset_check_analysis_session()
        self._compare_snapshot_version_id = vid
        self._pending_ai_accept_action_id = None
        self._compare_editor.value = body
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        if docx_rel:
            self._compare_candidate_source = CompareCandidateSource.DOCX_ORIGINAL
            self._compare_pdf_peer_snapshot_id = vid
        elif pdf_rel:
            self._compare_candidate_source = CompareCandidateSource.PDF_ORIGINAL
            self._compare_pdf_peer_snapshot_id = vid
        else:
            self._compare_candidate_source = CompareCandidateSource.SNAPSHOT
            self._compare_pdf_peer_snapshot_id = None
        self._rebuild_compare_view()

    def _set_compare_version_dd_focused(self, focused: bool) -> None:
        self._compare_version_dd_focused = focused
        self._apply_compare_candidate_dropdown_tab_chrome()

    def _apply_compare_candidate_dropdown_tab_chrome(self) -> None:
        """Outline around the version dropdown (tree search style): grey at rest, blue on hover/focus."""
        from iterthink import config

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
        self._apply_plan_compare_dropdown_chrome()

    def _on_compare_dropdown_container_hover(self, e: ft.ControlEvent) -> None:
        self._compare_dropdown_hover = str(e.data).lower() == "true"
        self._apply_compare_candidate_dropdown_tab_chrome()

    def _on_compare_newer_dropdown_container_hover(self, e: ft.ControlEvent) -> None:
        self._compare_newer_dropdown_hover = str(e.data).lower() == "true"
        self._apply_compare_candidate_dropdown_tab_chrome()
