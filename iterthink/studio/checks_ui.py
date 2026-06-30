"""Analyse checks, eval cells, and result detail for KI Comments sidebar."""

from __future__ import annotations

import sys
from collections import Counter
from typing import Any

import flet as ft

from iterthink import checks as checks_mod
from iterthink.services import checks_runner
from iterthink import config
from iterthink.compare.layout import aligned_compare_pairs
from iterthink.ai.ollama_util import ollama_error_message
from iterthink.compare.paragraph_align import compute_hash
from iterthink.db.session import session_scope
from iterthink.persistence import paragraph_user_comments
from iterthink.persistence.content_repo import path_key_for
from .constants import (
    COMPARE_EVAL_COL_W,
    KI_PILL_TEXT_SIZE,
    KI_TOPIC_COMMENTS,
    TAB_FUTURE,
    TAB_HISTORY,
)
from .util import safe_ctrl_mutate as _safe_ctrl_mutate


class MarkdownStudioChecksUi:
    def _analysis_document_path_key(self) -> str:
        """SHA-256 hex of the open note path; empty when no file (matches ``documents.path_key``)."""
        p = getattr(self, "current_path", None)
        if not p:
            return ""
        try:
            return path_key_for(p.resolve())
        except OSError:
            return ""

    def _reset_check_analysis_session(self) -> None:
        """Clear in-memory check UI and cancel in-flight runs (History/Review version or candidate change)."""
        for cid in list(self._check_run_gen.keys()):
            self._check_run_gen[cid] = self._check_run_gen.get(cid, 0) + 1
        for cid in list(self._check_running.keys()):
            self._check_running[cid] = False
        self._check_results.clear()
        self._check_para_hashes.clear()
        self._active_check_id = None
        pairs = getattr(self, "_analyse_compare_version_pairs", None)
        if pairs is not None:
            pairs.clear()
        self._refresh_analyse_button_state()
        if getattr(self, "_compare_eval_hosts", None):
            self._refresh_all_eval_cells()
        if hasattr(self, "_rebuild_ki_comments_list"):
            self._rebuild_ki_comments_list()

    def _compare_pairs_for_checks(self) -> list[tuple[str, str]]:
        buffers = self._active_compare_buffers()
        return aligned_compare_pairs(buffers.baseline, buffers.candidate)

    def _analyse_display_bodies(self) -> tuple[str, str]:
        buffers = self._active_compare_buffers()
        display = buffers.candidate or ""
        return display, display

    def _analyse_compare_version_pair_for_ui(self) -> tuple[int | None, int | None]:
        return (
            getattr(self, "_review_baseline_version_id", None),
            getattr(self, "_compare_snapshot_version_id", None),
        )

    def _load_analyse_compare_pair_from_db(
        self, check_id: str
    ) -> tuple[int | None, int | None] | None:
        with session_scope() as s:
            vid = self._resolve_impact_version_id(s)
            if vid is None:
                return None
            return paragraph_user_comments.analyse_compare_version_pair_from_stored(
                s,
                content_version_id=int(vid),
                check_id=str(check_id),
            )

    def _stored_analyse_compare_version_pair(
        self, check_id: str
    ) -> tuple[int | None, int | None] | None:
        pairs = getattr(self, "_analyse_compare_version_pairs", None)
        if pairs is None:
            self._analyse_compare_version_pairs = {}
            pairs = self._analyse_compare_version_pairs
        if check_id in pairs:
            return pairs[check_id]
        loaded = self._load_analyse_compare_pair_from_db(check_id)
        if loaded is not None:
            pairs[check_id] = loaded
        return loaded

    def _analyse_eval_symbols_allowed(self, check_id: str | None) -> bool:
        if not check_id or self._main_tab_index != TAB_FUTURE:
            return False
        if (
            hasattr(self, "_review_text_single_layout_active")
            and self._review_text_single_layout_active()
        ):
            return False
        stored = self._stored_analyse_compare_version_pair(str(check_id))
        if stored is None:
            return False
        return stored == self._analyse_compare_version_pair_for_ui()

    def _hydrate_check_results_from_db(self, check_id: str, n: int) -> None:
        anchor, display = self._analyse_display_bodies()
        with session_scope() as s:
            vid = self._resolve_impact_version_id(s)
            if vid is None:
                return
            resolved = paragraph_user_comments.map_analyse_resolved_for_display(
                s,
                content_version_id=int(vid),
                anchor_body=anchor,
                display_body=display,
                check_id=check_id,
            )
        results = self._check_results.setdefault(check_id, [None] * n)
        while len(results) < n:
            results.append(None)
        for row in resolved:
            pi = int(row.display_paragraph_index)
            if 0 <= pi < n and row.payload is not None and results[pi] is None:
                results[pi] = row.payload

    def _persist_analyse_row(
        self,
        check_id: str,
        cand_idx: int,
        payload: dict[str, Any],
    ) -> None:
        check = checks_mod.get_check(check_id)
        if check is None:
            return
        pairs = self._compare_pairs_for_checks()
        if not (0 <= cand_idx < len(pairs)):
            return
        _old, new = pairs[cand_idx]
        summary = checks_mod.extract_summary(check, payload) or ""
        symbol = checks_mod.effective_symbol(check, payload)
        baseline_id, candidate_id = self._analyse_compare_version_pair_for_ui()
        stored_payload = {
            **payload,
            "_iterthink_compare_version_pair": {
                "baseline_version_id": baseline_id,
                "candidate_version_id": candidate_id,
            },
        }
        with session_scope() as s:
            vid = self._resolve_impact_version_id(s)
            if vid is None:
                return
            paragraph_user_comments.upsert_analyse(
                s,
                content_version_id=int(vid),
                paragraph_index=int(cand_idx),
                check_id=str(check_id),
                body=summary,
                symbol=symbol,
                payload=stored_payload,
                candidate_paragraph=new or None,
                overridden=checks_mod.is_overridden(payload),
            )

    def _analyse_payload_for_cand_idx(
        self, check_id: str, cand_idx: int
    ) -> dict[str, Any] | None:
        if bool(self._check_running.get(check_id)):
            results = self._check_results.get(check_id) or []
            if 0 <= cand_idx < len(results) and isinstance(results[cand_idx], dict):
                return results[cand_idx]
        anchor, display = self._analyse_display_bodies()
        with session_scope() as s:
            vid = self._resolve_impact_version_id(s)
            if vid is None:
                return None
            return paragraph_user_comments.get_analyse_payload_resolved(
                s,
                content_version_id=int(vid),
                anchor_body=anchor,
                display_body=display,
                check_id=str(check_id),
                paragraph_index=int(cand_idx),
            )

    def _restore_active_check_from_db(self) -> None:
        """Pick an analyse check with persisted rows and hydrate eval cells (explicit callers only)."""
        if getattr(self, "_active_check_id", None) is not None:
            return
        if not getattr(self, "current_path", None):
            return
        pairs = self._compare_pairs_for_checks()
        if not pairs:
            return
        n = len(pairs)
        with session_scope() as s:
            vid = self._resolve_impact_version_id(s)
            if vid is None:
                return
            check_ids = paragraph_user_comments.list_analyse_check_ids_for_version(
                s, content_version_id=int(vid)
            )
        if not check_ids:
            return
        id_set = set(check_ids)
        chosen: str | None = None
        for c in checks_mod.CHECKS:
            if c.id in id_set:
                chosen = c.id
                break
        if chosen is None:
            chosen = str(check_ids[0])
        self._activate_analyse_check_for_display(chosen)

    def _dismiss_analyse_review_display(self) -> None:
        """Hide eval-column analyse symbols until pill or comment selection."""
        self._active_check_id = None
        self._clear_ki_analyse_focus()
        self._refresh_analyse_button_state()
        if getattr(self, "_compare_eval_hosts", None):
            self._refresh_all_eval_cells()

    def _activate_analyse_check_for_display(self, check_id: str) -> None:
        """Load persisted analyse rows for the current version and show eval symbols."""
        pairs = self._compare_pairs_for_checks()
        if not pairs:
            return
        n = len(pairs)
        self._check_para_hashes = [
            compute_hash(f"{old}\x1e{new}") for old, new in pairs
        ]
        if (cid_results := self._check_results.get(check_id)) is None or len(cid_results) != n:
            self._check_results[check_id] = [None] * n
        self._hydrate_check_results_from_db(check_id, n)
        if check_id not in getattr(self, "_analyse_compare_version_pairs", {}):
            loaded = self._load_analyse_compare_pair_from_db(check_id)
            if loaded is not None:
                self._analyse_compare_version_pairs[check_id] = loaded
        self._active_check_id = check_id
        self._refresh_analyse_button_state()
        if getattr(self, "_compare_eval_hosts", None):
            self._refresh_all_eval_cells()

    def _rebuild_analyse_pills(self) -> None:
        """Build a button per check; click runs/loads results, hover shows nothing (use card)."""
        self._pill_row_analyse.controls.clear()
        self._analyse_buttons.clear()
        self._analyse_button_progress.clear()
        self._analyse_button_count.clear()

        for c in checks_mod.CHECKS:
            spinner = ft.ProgressRing(
                width=10,
                height=10,
                stroke_width=2,
                color=config.ON_PRIMARY,
                visible=False,
            )
            counter = ft.Text(
                "",
                size=KI_PILL_TEXT_SIZE,
                color=ft.Colors.with_opacity(0.92, config.ON_PRIMARY),
                visible=False,
            )
            label_row = ft.Row(
                [
                    spinner,
                    ft.Text(c.label, size=KI_PILL_TEXT_SIZE, color=config.ON_PRIMARY),
                    counter,
                ],
                tight=True,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            btn = ft.FilledButton(
                content=label_row,
                elevation=0,
                style=ft.ButtonStyle(
                    text_style=ft.TextStyle(size=KI_PILL_TEXT_SIZE, color=config.ON_PRIMARY),
                    bgcolor=config.PRIMARY_COLOR,
                    color=config.ON_PRIMARY,
                    overlay_color=ft.Colors.with_opacity(0.14, config.ON_PRIMARY),
                    visual_density=ft.VisualDensity.COMPACT,
                    padding=ft.padding.symmetric(horizontal=8, vertical=4),
                ),
                tooltip=f"Run {c.label} on every paragraph (cached results reused).",
                on_click=lambda _e, cid=c.id: self._on_analyse_pill_click(cid),
            )
            self._analyse_buttons[c.id] = btn
            self._analyse_button_progress[c.id] = spinner
            self._analyse_button_count[c.id] = counter
            self._pill_row_analyse.controls.append(btn)

        self._refresh_analyse_button_state()

    def _on_analyse_pill_click(self, check_id: str) -> None:
        self.page.run_task(self._run_check_async, check_id)

    def _refresh_analyse_button_state(self) -> None:
        """Highlight the active check's button; show spinner+counter while running."""
        for cid, btn in self._analyse_buttons.items():
            check = checks_mod.get_check(cid)
            if check is None:
                continue
            is_active = cid == self._active_check_id
            running = bool(self._check_running.get(cid))
            spinner = self._analyse_button_progress.get(cid)
            counter = self._analyse_button_count.get(cid)
            if spinner is not None:
                _safe_ctrl_mutate(
                    spinner,
                    lambda c, vis=running: setattr(c, "visible", vis),
                )
            if counter is not None:
                results = self._check_results.get(cid) or []
                done = sum(1 for r in results if r is not None)
                total = max(len(results), len(self._check_para_hashes))

                def _apply_counter(c: ft.Control, *, running=running, done=done, total=total) -> None:
                    c.value = f"{done}/{total}" if running else ""
                    c.visible = running and total > 0

                _safe_ctrl_mutate(counter, _apply_counter)
            # Keep full filled style on every refresh (partial ButtonStyle → dark M3 fallbacks).
            btn_style = ft.ButtonStyle(
                text_style=ft.TextStyle(size=KI_PILL_TEXT_SIZE, color=config.ON_PRIMARY),
                bgcolor=config.PRIMARY_COLOR,
                color=config.ON_PRIMARY,
                overlay_color=ft.Colors.with_opacity(0.14, config.ON_PRIMARY),
                visual_density=ft.VisualDensity.COMPACT,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                side=(
                    ft.BorderSide(1.5, config.HIGHLIGHT)
                    if is_active
                    else ft.BorderSide(0, ft.Colors.TRANSPARENT)
                ),
            )
            _safe_ctrl_mutate(btn, lambda c, style=btn_style: setattr(c, "style", style))

    async def _run_check_async(self, check_id: str) -> None:
        """Activate a check; load cached results, run remaining paragraphs in background."""
        check = checks_mod.get_check(check_id)
        if check is None:
            self._snack(f"Check '{check_id}' is not configured.")
            return
        # Analysis symbols and result cards only render on Review (Future).
        if self._main_tab_index != TAB_FUTURE:
            await self._request_tab_switch_async(TAB_FUTURE)
        # Need a candidate to analyse against the baseline.
        if not self._compare_comp_slot_count():
            self._rebuild_future_paragraph_ui()
        buffers = self._active_compare_buffers()
        if not buffers.candidate.strip():
            self._snack(
                "Analyse needs text on the candidate side (right column). "
                "On Review → Difference, type or paste candidate text, or load a proposal from the dropdown."
            )
            print(
                "[analyse] skipped: candidate (right) text is empty",
                file=sys.stderr,
                flush=True,
            )
            return
        pairs = aligned_compare_pairs(buffers.baseline, buffers.candidate)
        n = len(pairs)
        # Per-row fingerprints: baseline + candidate so older-version changes invalidate.
        self._check_para_hashes = [
            compute_hash(f"{old}\x1e{new}") for old, new in pairs
        ]
        if (cid_results := self._check_results.get(check_id)) is None or len(cid_results) != n:
            self._check_results[check_id] = [None] * n
        self._hydrate_check_results_from_db(check_id, n)
        pair = self._analyse_compare_version_pair_for_ui()
        self._analyse_compare_version_pairs[check_id] = pair
        self._active_check_id = check_id
        self._refresh_all_eval_cells()
        hydrated = self._check_results.get(check_id) or []
        if n > 0 and all(isinstance(r, dict) for r in hydrated):
            self._refresh_analyse_button_state()
            return
        # Bump generation so any prior in-flight run for this check gets cancelled.
        self._check_run_gen[check_id] = self._check_run_gen.get(check_id, 0) + 1
        my_gen = self._check_run_gen[check_id]
        self._check_running[check_id] = True
        self._refresh_analyse_button_state()
        self._refresh_all_eval_cells()

        print(
            f"[analyse] starting check={check_id!r} paragraph_rows={n}",
            file=sys.stderr,
            flush=True,
        )

        async def on_progress(idx: int, payload: dict | None, err: str | None) -> None:
            if my_gen != self._check_run_gen.get(check_id):
                return
            if 0 <= idx < len(self._check_results.get(check_id, [])):
                self._check_results[check_id][idx] = payload
            if isinstance(payload, dict):
                self._persist_analyse_row(check_id, idx, payload)
            if self._main_tab_index != TAB_FUTURE:
                return
            self._refresh_eval_cell(idx)
            self._refresh_analyse_button_state()

        run_ok = False
        try:
            await checks_runner.run_check_for_document(
                self._make_llm_backend(),
                model=self.chat_model_for_requests(),
                check=check,
                pairs=pairs,
                on_progress=on_progress,
                use_cache=True,
                document_path_key=self._analysis_document_path_key(),
            )
            run_ok = True
        except BaseException as exc:  # noqa: BLE001
            self._snack(f"Analyse failed: {ollama_error_message(exc)}")
        finally:
            if my_gen == self._check_run_gen.get(check_id):
                self._check_running[check_id] = False
                self._refresh_analyse_button_state()
                if self._main_tab_index == TAB_FUTURE:
                    self._refresh_all_eval_cells()
                self._sync_ki_sidebar_after_analyse_change()
                if run_ok:
                    results = self._check_results.get(check_id) or []
                    summary = self._build_document_check_summary_text(check, results)
                    self._append_chat_line("assistant", summary)

    # ------------------------------------------------------------------
    # Eval cell (leftmost cell in compare rows)
    # ------------------------------------------------------------------

    def _build_eval_cell(self, idx: int) -> ft.Container:
        cid = self._active_check_id
        col_w = 36 if self._main_tab_index == TAB_HISTORY else COMPARE_EVAL_COL_W
        host = ft.Container(
            width=col_w,
            alignment=ft.Alignment.TOP_CENTER,
            padding=ft.padding.only(top=4, right=2),
            content=self._build_eval_cell_inner(idx, cid),
        )
        return host

    def _eval_cand_idx(self, ui_idx: int) -> int | None:
        """UI row index → candidate-paragraph index used to key into ``_check_results``.

        On Review (Future) the visible row list may include gap rows (pure deletions)
        with no candidate paragraph; those map to ``None`` so the eval cell stays empty.
        """
        if self._main_tab_index != TAB_FUTURE:
            return ui_idx
        ev_cands = getattr(self, "_future_eval_cand_indices", None)
        if ev_cands and 0 <= ui_idx < len(ev_cands):
            return ev_cands[ui_idx]
        arr = getattr(self, "_future_row_cand_idx", None)
        if not arr or not (0 <= ui_idx < len(arr)):
            return ui_idx
        return arr[ui_idx]

    def _build_eval_cell_inner(self, idx: int, check_id: str | None) -> ft.Control:
        if self._main_tab_index != TAB_FUTURE:
            return ft.Container(width=0, height=0)
        if check_id is None:
            return ft.Container(width=18, height=18)
        if not self._analyse_eval_symbols_allowed(check_id):
            return ft.Container(width=18, height=18)
        cand_idx = self._eval_cand_idx(idx)
        if cand_idx is None:
            return ft.Container(width=18, height=18)
        check = checks_mod.get_check(check_id)
        results = self._check_results.get(check_id) or []
        payload = self._analyse_payload_for_cand_idx(check_id, cand_idx)
        if payload is None and 0 <= cand_idx < len(results):
            payload = results[cand_idx] if isinstance(results[cand_idx], dict) else None
        running = bool(self._check_running.get(check_id))
        if payload is None:
            if running:
                return ft.Container(
                    content=ft.ProgressRing(
                        width=14,
                        height=14,
                        stroke_width=2,
                        color=(check.accent if check else config.PRIMARY_COLOR),
                    ),
                    alignment=ft.Alignment.TOP_CENTER,
                )
            return ft.Container(
                content=ft.Text("·", size=14, color=config.OUTLINE),
                alignment=ft.Alignment.TOP_CENTER,
            )
        symbol = checks_mod.effective_symbol(check, payload) if check else "?"
        color = check.color_for_symbol(symbol) if check else config.ON_SURFACE_VARIANT
        summary_raw = checks_mod.extract_summary(check, payload) if check else ""
        tip: str | None = None
        if summary_raw:
            tip = summary_raw if len(summary_raw) <= 220 else summary_raw[:217] + "…"
        border = (
            ft.border.all(1.5, ft.Colors.with_opacity(0.75, color))
            if checks_mod.is_overridden(payload)
            else None
        )
        symbol_ctrl = ft.Container(
            content=ft.Text(
                symbol,
                size=18,
                weight=ft.FontWeight.W_700,
                color=color,
                no_wrap=True,
            ),
            alignment=ft.Alignment.TOP_CENTER,
            padding=ft.padding.only(top=2),
            border=border,
            border_radius=4,
            on_click=lambda _e, i=idx: self._on_eval_symbol_click(i),
            tooltip=tip,
        )
        col_children: list[ft.Control] = [symbol_ctrl]
        return ft.Column(
            col_children,
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )

    def _ui_idx_for_eval_cand_idx(self, cand_idx: int) -> int:
        ev_cands = getattr(self, "_future_eval_cand_indices", None)
        if ev_cands:
            for ui_idx, ci in enumerate(ev_cands):
                if ci == cand_idx:
                    return ui_idx
        arr = getattr(self, "_future_row_cand_idx", None)
        if arr:
            for ui_idx, ci in enumerate(arr):
                if ci == cand_idx:
                    return ui_idx
        return int(cand_idx)

    def _sync_ki_sidebar_after_analyse_change(self) -> None:
        if hasattr(self, "_ki_comment_thread_items"):
            items = self._ki_comment_thread_items()
            if hasattr(self, "_ki_reindex_thread_keys"):
                self._ki_reindex_thread_keys(items)
            if hasattr(self, "_ki_thread_items_cache"):
                self._ki_thread_items_cache = {it.list_key: it for it in items}
        if int(getattr(self, "_ki_topic_index", -1)) != KI_TOPIC_COMMENTS:
            if hasattr(self, "_refresh_all_eval_cells"):
                self._refresh_all_eval_cells()
            return
        if hasattr(self, "_rebuild_ki_comments_list"):
            self._rebuild_ki_comments_list()
        elif hasattr(self, "_refresh_all_eval_cells"):
            self._refresh_all_eval_cells()
        if hasattr(self, "_sync_ki_detail_for_focus"):
            self._sync_ki_detail_for_focus()

    def _clear_ki_analyse_focus(self) -> None:
        focus = getattr(self, "_ki_comment_focus", None)
        if focus and focus[0] == "analyse":
            self._ki_comment_focus = None
            if hasattr(self, "_sync_ki_detail_for_focus"):
                self._sync_ki_detail_for_focus()
            if hasattr(self, "_sync_ki_comments_detail_visibility"):
                self._sync_ki_comments_detail_visibility()
            if hasattr(self, "_rebuild_ki_comments_list"):
                self._rebuild_ki_comments_list()

    def _hide_all_result_card_overlays(self) -> None:
        """Legacy no-op; Analyse detail lives in the KI Comments sidebar."""

    def _on_eval_symbol_click(self, ui_idx: int) -> None:
        cand_idx = self._eval_cand_idx(ui_idx)
        if cand_idx is None:
            return
        cid = self._active_check_id
        if not cid or not self._analyse_eval_symbols_allowed(cid):
            return
        self.page.run_task(self._open_ki_analyse_card_async, int(cand_idx), str(cid))

    def _eval_cell_host(self, comp_idx: int) -> ft.Container | None:
        if getattr(self, "_future_virtual_active", False):
            return getattr(self, "_future_virtual_eval_hosts", {}).get(comp_idx)
        hosts = getattr(self, "_compare_eval_hosts", None) or []
        if 0 <= comp_idx < len(hosts):
            return hosts[comp_idx]
        return None

    def _refresh_eval_cell(self, idx: int) -> None:
        host = self._eval_cell_host(idx)
        if host is None:
            return
        inner = self._build_eval_cell_inner(idx, self._active_check_id)
        _safe_ctrl_mutate(host, lambda c, content=inner: setattr(c, "content", content))

    def _refresh_all_eval_cells(self) -> None:
        if getattr(self, "_future_virtual_active", False):
            indices = sorted(getattr(self, "_future_virtual_eval_hosts", {}).keys())
        else:
            indices = range(len(getattr(self, "_compare_eval_hosts", []) or []))
        for i in indices:
            self._refresh_eval_cell(i)

    def _check_pair_for_ui_idx(self, ui_idx: int) -> tuple[int, str, str] | None:
        cand_idx = self._eval_cand_idx(ui_idx)
        if cand_idx is None:
            return None
        buffers = self._active_compare_buffers()
        pairs = aligned_compare_pairs(buffers.baseline, buffers.candidate)
        if not (0 <= cand_idx < len(pairs)):
            return None
        old, new = pairs[cand_idx]
        return cand_idx, old, new

    def _check_payload_for_ui_idx(self, ui_idx: int) -> tuple[str, dict[str, Any], int] | None:
        cid = self._active_check_id
        if not cid:
            return None
        pair = self._check_pair_for_ui_idx(ui_idx)
        if pair is None:
            return None
        cand_idx, _old, _new = pair
        payload = self._analyse_payload_for_cand_idx(cid, cand_idx)
        if not isinstance(payload, dict):
            return None
        return cid, payload, cand_idx

    async def _persist_check_override_async(
        self,
        ui_idx: int,
        symbol: str | None,
        recommendation: str | None,
    ) -> None:
        got = self._check_payload_for_ui_idx(ui_idx)
        if got is None:
            return
        cid, payload, cand_idx = got
        check = checks_mod.get_check(cid)
        if check is None:
            return
        pair = self._check_pair_for_ui_idx(ui_idx)
        if pair is None:
            return
        _ci, old, new = pair
        new_sym = symbol if symbol is not None else checks_mod.effective_symbol(check, payload)
        new_rec = (
            recommendation
            if recommendation is not None
            else checks_mod.effective_primary_recommendation(payload)
        )
        patched = checks_mod.apply_check_override(
            payload,
            check,
            symbol=new_sym,
            recommendation=new_rec or "",
        )
        checks_runner.save_result(
            cid,
            old,
            new,
            self.chat_model_for_requests(),
            patched,
            document_path_key=self._analysis_document_path_key(),
        )
        results = self._check_results.setdefault(cid, [])
        if cand_idx < len(results):
            results[cand_idx] = patched
        self._persist_analyse_row(cid, cand_idx, patched)
        self._refresh_eval_cell(ui_idx)
        self._sync_ki_sidebar_after_analyse_change()

    async def _clear_check_override_async(self, ui_idx: int) -> None:
        got = self._check_payload_for_ui_idx(ui_idx)
        if got is None:
            return
        cid, payload, cand_idx = got
        check = checks_mod.get_check(cid)
        if check is None:
            return
        pair = self._check_pair_for_ui_idx(ui_idx)
        if pair is None:
            return
        _ci, old, new = pair
        cleared = checks_mod.clear_check_override(payload, check)
        checks_runner.save_result(
            cid,
            old,
            new,
            self.chat_model_for_requests(),
            cleared,
            document_path_key=self._analysis_document_path_key(),
        )
        results = self._check_results.setdefault(cid, [])
        if cand_idx < len(results):
            results[cand_idx] = cleared
        self._persist_analyse_row(cid, cand_idx, cleared)
        self._refresh_eval_cell(ui_idx)
        self._sync_ki_sidebar_after_analyse_change()

    def _build_check_symbol_badge(
        self,
        check: checks_mod.Check,
        payload: dict[str, Any],
        *,
        symbol: str,
        color: str,
        ui_idx: int,
    ) -> ft.Control:
        badge_inner = ft.Text(symbol, size=22, weight=ft.FontWeight.W_700, color=color)
        menu_items: list[ft.PopupMenuItem] = [
            ft.PopupMenuItem(
                content=ft.Text(s.symbol, size=13),
                on_click=lambda _e, sym=s.symbol: self.page.run_task(
                    self._persist_check_override_async,
                    ui_idx,
                    sym,
                    None,
                ),
            )
            for s in check.symbol_set
        ]
        if checks_mod.is_overridden(payload):
            menu_items.append(ft.PopupMenuItem())
            menu_items.append(
                ft.PopupMenuItem(
                    content=ft.Text("Reset to model", size=13),
                    on_click=lambda _e: self.page.run_task(
                        self._clear_check_override_async,
                        ui_idx,
                    ),
                )
            )
        return ft.PopupMenuButton(
            content=ft.Container(
                content=badge_inner,
                width=34,
                height=34,
                alignment=ft.Alignment.CENTER,
                border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.18, color),
            ),
            items=menu_items,
            tooltip="Change symbol",
        )

    def _metric_chip(self, label: str, value: Any, value_set: tuple[str, ...]) -> ft.Container:
        """Coloured chip for a project/sustainability metric (None/Low/Medium/High) or numeric score."""
        text_val: str
        chip_color: str
        if isinstance(value, (int, float)):
            text_val = f"{value:.0f}" if isinstance(value, float) and not value.is_integer() else str(int(value)) if isinstance(value, float) else str(value)
            # Numeric scale assumed 0-100 for readability/virality scores.
            v = float(value)
            if v >= 70:
                chip_color = "#3FBE6B"
            elif v >= 50:
                chip_color = "#7ED9A0"
            elif v >= 30:
                chip_color = "#F0A455"
            else:
                chip_color = "#E5484D"
        else:
            text_val = str(value or "—")
            mapping = {
                "none": "#5A6068",
                "low": "#5AB0FF",
                "medium": "#F0A455",
                "high": "#E5484D",
            }
            chip_color = mapping.get(text_val.lower(), "#5A6068")
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(label, size=10, color=config.ON_SURFACE_VARIANT, no_wrap=True),
                    ft.Text(
                        text_val,
                        size=12,
                        weight=ft.FontWeight.W_700,
                        color=config.ON_SURFACE,
                        no_wrap=True,
                    ),
                ],
                spacing=0,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=6, vertical=3),
            border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.18, chip_color),
            border=ft.border.all(1, ft.Colors.with_opacity(0.55, chip_color)),
        )

    def _build_result_card(
        self,
        check: checks_mod.Check,
        payload: dict,
        idx: int,
        *,
        ui_idx: int,
    ) -> ft.Control:
        symbol = checks_mod.effective_symbol(check, payload)
        color = check.color_for_symbol(symbol)
        summary = checks_mod.extract_summary(check, payload)
        metrics = checks_mod.extract_metrics(check, payload)
        recs = checks_mod.extract_recommendations(payload, limit=3)
        confidence = checks_mod.extract_confidence(payload)
        label = checks_mod.extract_label(payload)
        primary_rec = checks_mod.effective_primary_recommendation(payload)

        def _close_card(_e: ft.ControlEvent) -> None:
            self._clear_ki_analyse_focus()

        header = ft.Row(
            [
                self._build_check_symbol_badge(
                    check, payload, symbol=symbol, color=color, ui_idx=ui_idx
                ),
                ft.Column(
                    [
                        ft.Text(
                            check.label,
                            size=13,
                            weight=ft.FontWeight.W_600,
                            color=config.ON_SURFACE,
                        ),
                        ft.Text(
                            f"Paragraph {idx + 1}" + (f" · {label}" if label else ""),
                            size=10,
                            color=config.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=0,
                    tight=True,
                    expand=True,
                ),
                ft.Container(
                    content=ft.Text(
                        f"{int(round(confidence * 100))}%" if confidence is not None else "",
                        size=10,
                        color=config.ON_SURFACE_VARIANT,
                    ),
                    tooltip="Model confidence" if confidence is not None else None,
                ),
                ft.IconButton(
                    ft.Icons.CLOSE,
                    icon_size=14,
                    padding=ft.padding.all(0),
                    on_click=_close_card,
                    icon_color=config.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        rows: list[ft.Control] = [header]

        overridden = checks_mod.is_overridden(payload)

        if summary and not overridden:
            rows.append(
                ft.Container(
                    content=ft.Text(
                        summary,
                        size=12,
                        color=config.ON_SURFACE,
                        selectable=True,
                    ),
                    padding=ft.padding.only(top=4),
                )
            )

        if metrics:
            chips: list[ft.Control] = []
            for _key, m_label, m_val in metrics:
                if m_val in (None, "", "none", "None") and not isinstance(m_val, (int, float)):
                    continue
                chips.append(self._metric_chip(m_label, m_val, check.metric_value_set))
            if chips:
                rows.append(
                    ft.Container(
                        content=ft.Row(chips, spacing=4, wrap=True, run_spacing=4),
                        padding=ft.padding.only(top=6),
                    )
                )

        if overridden:
            model_sym = checks_mod.model_symbol(check, payload)
            model_sym_color = check.color_for_symbol(model_sym)
            model_summary_text = checks_mod.model_summary(check, payload)
            model_recs = checks_mod.model_recommendations(payload, limit=3)
            model_lines: list[ft.Control] = [
                ft.Text("Model suggestion", size=10, weight=ft.FontWeight.W_600, color=config.ON_SURFACE_VARIANT),
                ft.Row(
                    [
                        ft.Text(
                            model_sym,
                            size=16,
                            weight=ft.FontWeight.W_700,
                            color=model_sym_color,
                        ),
                    ],
                    tight=True,
                ),
            ]
            if model_summary_text:
                model_lines.append(
                    ft.Text(
                        model_summary_text,
                        size=11,
                        color=config.ON_SURFACE,
                        selectable=True,
                    )
                )
            for r in model_recs:
                action = str(r.get("action") or r.get("recommendation") or "").strip()
                if action:
                    model_lines.append(
                        ft.Text(f"• {action}", size=11, color=config.ON_SURFACE, selectable=True)
                    )
            rows.append(
                ft.Container(
                    content=ft.Column(model_lines, spacing=3, tight=True),
                    padding=ft.padding.only(top=6),
                    bgcolor=ft.Colors.with_opacity(0.06, config.ON_SURFACE),
                    border_radius=6,
                    border=ft.border.all(1, ft.Colors.with_opacity(0.2, config.OUTLINE)),
                )
            )

        rec_label = "Your override" if overridden else "Recommendation"
        rec_tf = ft.TextField(
            value=primary_rec,
            dense=True,
            multiline=True,
            min_lines=2,
            max_lines=6,
            text_size=12,
            expand=True,
            on_blur=lambda e, i=ui_idx: self.page.run_task(
                self._persist_check_override_async,
                i,
                None,
                e.control.value,
            ),
            on_submit=lambda e, i=ui_idx: self.page.run_task(
                self._persist_check_override_async,
                i,
                None,
                e.control.value,
            ),
        )
        rows.append(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(rec_label, size=10, color=config.ON_SURFACE_VARIANT),
                        rec_tf,
                    ],
                    spacing=4,
                    tight=True,
                ),
                padding=ft.padding.only(top=6),
            )
        )

        if len(recs) > 1 and not overridden:
            extra_controls: list[ft.Control] = []
            for r in recs[1:]:
                action = str(r.get("action") or r.get("recommendation") or "").strip()
                if not action:
                    continue
                priority = str(r.get("priority") or r.get("uncertainty") or "").strip().lower()
                pcolor = {
                    "high": "#E5484D",
                    "medium": "#F0A455",
                    "low": "#7ED9A0",
                }.get(priority, config.ON_SURFACE_VARIANT)
                extra_controls.append(
                    ft.Row(
                        [
                            ft.Container(
                                width=6,
                                height=6,
                                border_radius=3,
                                bgcolor=pcolor,
                                margin=ft.margin.only(top=6),
                            ),
                            ft.Text(
                                action,
                                size=12,
                                color=config.ON_SURFACE,
                                expand=True,
                                selectable=True,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    )
                )
            if extra_controls:
                rows.append(
                    ft.Container(
                        content=ft.Column(extra_controls, spacing=2, tight=True),
                        padding=ft.padding.only(top=4),
                    )
                )

        return ft.Column(
            rows,
            spacing=2,
            tight=True,
            scroll=ft.ScrollMode.AUTO,
        )

    def _build_document_check_summary_text(
        self, check: checks_mod.Check, results: list[dict | None]
    ) -> str:
        """Deterministic rollup for chat after a full document check run."""
        n = len(results)
        nonempty = [(i, p) for i, p in enumerate(results) if isinstance(p, dict)]
        lines: list[str] = [f"{check.label} — {n} paragraph(s)."]
        if not nonempty:
            lines.append("No paragraph-level results returned.")
            return "\n".join(lines)
        syms = [checks_mod.effective_symbol(check, p) for _, p in nonempty]
        ctr = Counter(syms)
        hist = ", ".join(f"{s}: {c}" for s, c in sorted(ctr.items(), key=lambda x: (-x[1], x[0]))[:12])
        lines.append(f"Symbols: {hist}")
        scored: list[tuple[float, int, str, str]] = []
        neutral_syms = frozenset({"~", "●", "?"})
        for i, p in nonempty:
            recs = checks_mod.extract_recommendations(p)
            conf = checks_mod.extract_confidence(p)
            sym = checks_mod.extract_symbol(check, p)
            summary = checks_mod.extract_summary(check, p)
            low = (
                "unchanged" in summary.lower()
                or "skipped" in summary.lower()
                or sym in neutral_syms
            )
            score = float(len(recs)) * 10.0
            if conf is not None:
                score += (1.0 - max(0.0, min(1.0, conf))) * 4.0
            if not low:
                score += 3.0
            scored.append((score, i, sym, summary))
        scored.sort(key=lambda t: t[0], reverse=True)
        lines.append("")
        lines.append("Highlights:")
        for _sc, i, sym, summary in scored[:5]:
            snip = summary.replace("\n", " ").strip()
            if len(snip) > 120:
                snip = snip[:117] + "…"
            lines.append(f"- Para {i + 1} ({sym}): {snip or '—'}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Invalidation: drop per-paragraph cache when candidate text changes.
    # ------------------------------------------------------------------

    def _invalidate_check_results_for_changes(self) -> None:
        """Detect changed aligned pairs (baseline and/or candidate) and drop stale memory rows.

        Disk cache is keyed by document path + paragraph content hashes.
        """
        buffers = self._active_compare_buffers()
        pairs = aligned_compare_pairs(buffers.baseline, buffers.candidate)
        new_hashes = [compute_hash(f"{old}\x1e{new}") for old, new in pairs]
        if not self._check_results:
            self._check_para_hashes = new_hashes
            return
        prev_hashes = self._check_para_hashes
        n = len(new_hashes)
        # If row count changed, blow away results (rebuild will repopulate).
        if len(prev_hashes) != n:
            for cid in list(self._check_results.keys()):
                self._check_results[cid] = [None] * n
            self._check_para_hashes = new_hashes
            return
        # Same count: invalidate only changed indices.
        changed = [i for i, (a, b) in enumerate(zip(prev_hashes, new_hashes, strict=True)) if a != b]
        if changed:
            for cid, results in self._check_results.items():
                if len(results) != n:
                    self._check_results[cid] = [None] * n
                    continue
                for i in changed:
                    results[i] = None
        self._check_para_hashes = new_hashes
