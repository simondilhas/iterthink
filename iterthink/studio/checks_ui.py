"""Analyse checks, eval cells, and result card overlay for MarkdownStudio."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

import flet as ft

from iterthink import checks as checks_mod
from iterthink.services import checks_runner
from iterthink import config
from iterthink.compare.layout import aligned_compare_pairs
from iterthink.ai.ollama_util import ollama_error_message
from iterthink.compare.paragraph_align import compute_hash
from .constants import (
    COMPARE_EVAL_COL_W,
    KI_PILL_TEXT_SIZE,
    RESULT_CARD_HIDE_DELAY_SEC,
    TAB_FUTURE,
    TAB_HISTORY,
)
from .util import ctrl_on_page as _ctrl_on_page


class MarkdownStudioChecksUi:
    def _rebuild_analyse_pills(self) -> None:
        """Build a button per check; click runs/loads results, hover shows nothing (use card)."""
        self._pill_row_analyse.controls.clear()
        self._analyse_buttons.clear()
        self._analyse_button_progress.clear()
        self._analyse_button_count.clear()

        # Clear expansion host and per-check state.
        expansion_host = getattr(self, "_analyse_expansion_host", None)
        if expansion_host is not None:
            expansion_host.controls.clear()
        impact_expansions: dict[str, ft.Container] = {}
        impact_checkboxes: dict[str, list[tuple[Path, ft.Checkbox]]] = {}

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

            # Build the per-check impact file selector expansion (hidden by default).
            expansion, checkboxes = self._build_impact_file_selector(c.id)
            impact_expansions[c.id] = expansion
            impact_checkboxes[c.id] = checkboxes
            if expansion_host is not None:
                expansion_host.controls.append(expansion)

        if hasattr(self, "_impact_file_expansions"):
            self._impact_file_expansions = impact_expansions
        if hasattr(self, "_impact_file_checkboxes"):
            self._impact_file_checkboxes = impact_checkboxes

        self._refresh_analyse_button_state()

    # ------------------------------------------------------------------
    # Impact: file selector expansion helpers
    # ------------------------------------------------------------------

    def _collect_project_md_files(self) -> list[Path]:
        """Return all .md files under config.DOCUMENTS, sorted by relative path."""
        from iterthink import config as _cfg
        from iterthink.studio.tree import build_md_tree

        root = _cfg.DOCUMENTS
        if not root.is_dir():
            return []

        result: list[Path] = []

        def _walk(node: dict, parent: Path) -> None:
            for fname, fpath in node.get("_files", []):
                result.append(fpath)
            for key, sub in node.items():
                if key != "_files" and isinstance(sub, dict):
                    _walk(sub, parent / key)

        try:
            tree = build_md_tree(root)
            _walk(tree, root)
        except Exception:  # noqa: BLE001
            pass

        # Exclude the currently open file so users don't compare a file against itself.
        current = getattr(self, "current_path", None)
        if current is not None:
            result = [p for p in result if p != current]

        result.sort(key=lambda p: str(p).lower())
        return result

    def _build_impact_file_selector(
        self, check_id: str
    ) -> tuple[ft.Container, list[tuple[Path, ft.Checkbox]]]:
        """Build the hidden expansion panel (file list + Run button) for *check_id*."""
        md_files = self._collect_project_md_files()
        checkboxes: list[tuple[Path, ft.Checkbox]] = []

        file_controls: list[ft.Control] = []
        for fpath in md_files:
            cb = ft.Checkbox(
                value=False,
                label=fpath.name,
                label_style=ft.TextStyle(
                    size=11,
                    color=config.ON_SURFACE,
                ),
                fill_color=config.PRIMARY_COLOR,
                check_color=config.ON_PRIMARY,
                scale=0.85,
                tooltip=str(fpath),
            )
            checkboxes.append((fpath, cb))
            file_controls.append(
                ft.Container(
                    content=cb,
                    padding=ft.padding.only(left=4, top=0, bottom=0),
                    height=28,
                )
            )

        no_files_label = ft.Text(
            "No .md files found in project.",
            size=11,
            color=config.ON_SURFACE_VARIANT,
            italic=True,
        )

        run_spinner = ft.ProgressRing(
            width=12, height=12, stroke_width=2,
            color=config.ON_PRIMARY,
            visible=False,
        )
        run_btn = ft.FilledButton(
            content=ft.Row(
                [run_spinner, ft.Text("Run", size=KI_PILL_TEXT_SIZE, color=config.ON_PRIMARY)],
                tight=True,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            elevation=0,
            style=ft.ButtonStyle(
                bgcolor=config.PRIMARY_COLOR,
                color=config.ON_PRIMARY,
                overlay_color=ft.Colors.with_opacity(0.14, config.ON_PRIMARY),
                visual_density=ft.VisualDensity.COMPACT,
                padding=ft.padding.symmetric(horizontal=10, vertical=4),
            ),
            tooltip="Run impact analysis using selected files as context.",
            on_click=lambda _e, cid=check_id: self.page.run_task(
                self._run_impact_check_async, cid
            ),
        )

        inner_col: list[ft.Control] = []
        if file_controls:
            file_list = ft.Column(
                file_controls,
                spacing=0,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
                height=min(len(file_controls) * 28, 180),
            )
            inner_col.append(file_list)
        else:
            inner_col.append(no_files_label)

        inner_col.append(
            ft.Container(
                content=run_btn,
                padding=ft.padding.only(top=6, bottom=4),
            )
        )

        panel = ft.Container(
            visible=False,
            padding=ft.padding.only(left=4, right=4, top=6, bottom=4),
            border=ft.border.only(
                left=ft.BorderSide(2, ft.Colors.with_opacity(0.35, config.PRIMARY_COLOR))
            ),
            margin=ft.margin.only(top=6),
            content=ft.Column(inner_col, spacing=2, tight=True),
        )

        return panel, checkboxes

    def _on_analyse_pill_click(self, check_id: str) -> None:
        """Route pill click: normal check run OR impact file-selector toggle."""
        impact_active = (
            getattr(self, "_main_tab_index", -1) == TAB_FUTURE
            and getattr(self, "_review_subtab_index", 0) == 1
        )
        if impact_active:
            self._toggle_impact_expansion(check_id)
        else:
            self.page.run_task(self._run_check_async, check_id)

    def _toggle_impact_expansion(self, check_id: str) -> None:
        """Show the expansion for *check_id*; hide all others."""
        expansions: dict[str, ft.Container] = getattr(self, "_impact_file_expansions", {})
        for cid, panel in expansions.items():
            want = cid == check_id and not panel.visible
            if panel.visible != want:
                panel.visible = want
                if _ctrl_on_page(panel):
                    panel.update()

    def _refresh_impact_expansion_visibility(self) -> None:
        """Hide all expansion panels when not in Impact subtab."""
        impact_active = (
            getattr(self, "_main_tab_index", -1) == TAB_FUTURE
            and getattr(self, "_review_subtab_index", 0) == 1
        )
        if not impact_active:
            for panel in getattr(self, "_impact_file_expansions", {}).values():
                if panel.visible:
                    panel.visible = False
                    if _ctrl_on_page(panel):
                        panel.update()

    async def _run_impact_check_async(self, check_id: str) -> None:
        """Run a check with selected project files as per-paragraph RAG context."""
        from iterthink.services.impact_rag import ingest_project_file, retrieve_context_for_paragraph
        from iterthink.compare.paragraph_semantics import embed_texts_cached

        check = checks_mod.get_check(check_id)
        if check is None:
            self._snack(f"Check '{check_id}' is not configured.")
            return

        # Collect selected file paths.
        checkboxes: list[tuple[Path, ft.Checkbox]] = getattr(
            self, "_impact_file_checkboxes", {}
        ).get(check_id, [])
        selected_files = [p for p, cb in checkboxes if cb.value]
        if not selected_files:
            self._snack("Select at least one file in the Analyse sidebar.")
            return

        # Ensure we have a candidate to analyse.
        if not self._compare_right_fields:
            self._rebuild_compare_paragraph_ui()
        buffers = self._active_compare_buffers()
        if not buffers.candidate.strip():
            self._snack("Open a note first to analyse it.")
            return

        pairs = aligned_compare_pairs(buffers.baseline, buffers.candidate)
        n = len(pairs)
        self._check_para_hashes = [compute_hash(new) for _, new in pairs]
        self._check_results[check_id] = [None] * n
        self._active_check_id = check_id
        if hasattr(self, "_impact_results_check_id"):
            self._impact_results_check_id = check_id

        self._check_run_gen[check_id] = self._check_run_gen.get(check_id, 0) + 1
        my_gen = self._check_run_gen[check_id]
        self._check_running[check_id] = True
        self._refresh_analyse_button_state()
        self._refresh_all_eval_cells()

        # Update impact tab status.
        if hasattr(self, "_impact_status_text") and self._impact_status_text is not None:
            self._impact_status_text.value = (
                f"Ingesting {len(selected_files)} file(s)…"
            )
            if _ctrl_on_page(self._impact_status_text):
                self._impact_status_text.update()

        # Ingest selected files into the embedding cache.
        conn = getattr(self, "_db", None)
        file_chunks: dict[Path, list[tuple[str, int]]] = {}
        if conn is not None:
            for fpath in selected_files:
                try:
                    chunks = await ingest_project_file(fpath, conn)
                    file_chunks[fpath] = chunks
                except Exception as exc:  # noqa: BLE001
                    self._snack(f"Ingest failed for {fpath.name}: {exc}")

        # Build per-paragraph context strings.
        context_per_pair: list[str | None] = []
        if file_chunks and conn is not None:
            if hasattr(self, "_impact_status_text") and self._impact_status_text is not None:
                self._impact_status_text.value = "Building context…"
                if _ctrl_on_page(self._impact_status_text):
                    self._impact_status_text.update()

            candidate_texts = [new for _, new in pairs]
            try:
                cand_vecs = await embed_texts_cached(conn, None, candidate_texts)
            except Exception:  # noqa: BLE001
                cand_vecs = [[] for _ in candidate_texts]

            for i, vec in enumerate(cand_vecs):
                if vec:
                    ctx = retrieve_context_for_paragraph(vec, file_chunks, conn)
                else:
                    ctx = ""
                context_per_pair.append(ctx if ctx else None)
        else:
            context_per_pair = [None] * n

        # Update status.
        if hasattr(self, "_impact_status_text") and self._impact_status_text is not None:
            self._impact_status_text.value = f"Running {check.label} with context…"
            if _ctrl_on_page(self._impact_status_text):
                self._impact_status_text.update()

        async def on_progress(idx: int, payload: dict | None, err: str | None) -> None:
            if my_gen != self._check_run_gen.get(check_id):
                return
            if 0 <= idx < len(self._check_results.get(check_id, [])):
                self._check_results[check_id][idx] = payload
            if self._main_tab_index in (TAB_HISTORY, TAB_FUTURE):
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
                use_cache=False,
                context_per_pair=context_per_pair,
            )
            run_ok = True
        except BaseException as exc:  # noqa: BLE001
            self._snack(f"Impact analysis failed: {ollama_error_message(exc)}")
        finally:
            if my_gen == self._check_run_gen.get(check_id):
                self._check_running[check_id] = False
                self._refresh_analyse_button_state()
                if self._main_tab_index in (TAB_HISTORY, TAB_FUTURE):
                    self._refresh_all_eval_cells()
                if run_ok:
                    results = self._check_results.get(check_id) or []
                    summary = self._build_document_check_summary_text(check, results)
                    self._append_chat_line("assistant", summary)
                    self._refresh_impact_tab_results(check_id)
                else:
                    if hasattr(self, "_impact_status_text") and self._impact_status_text is not None:
                        self._impact_status_text.value = "Analysis failed — check the chat log."
                        if _ctrl_on_page(self._impact_status_text):
                            self._impact_status_text.update()

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
                spinner.visible = running
            if counter is not None:
                results = self._check_results.get(cid) or []
                done = sum(1 for r in results if r is not None)
                total = max(len(results), len(self._check_para_hashes))
                counter.value = f"{done}/{total}" if running else ""
                counter.visible = running and total > 0
            # Keep full filled style on every refresh (partial ButtonStyle → dark M3 fallbacks).
            btn.style = ft.ButtonStyle(
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
            if _ctrl_on_page(btn):
                btn.update()

    async def _run_check_async(self, check_id: str) -> None:
        """Activate a check; load cached results, run remaining paragraphs in background."""
        check = checks_mod.get_check(check_id)
        if check is None:
            self._snack(f"Check '{check_id}' is not configured.")
            return
        # Stay put if user is already on a tab with eval cells (History or Review);
        # otherwise jump to History so the symbols are visible.
        if self._main_tab_index not in (TAB_HISTORY, TAB_FUTURE):
            await self._request_tab_switch_async(TAB_HISTORY)
        # Need a candidate to analyse against the baseline.
        if not self._compare_right_fields:
            self._rebuild_compare_paragraph_ui()
        buffers = self._active_compare_buffers()
        if not buffers.candidate.strip():
            self._snack("Open a note first to analyse it.")
            return
        pairs = aligned_compare_pairs(buffers.baseline, buffers.candidate)
        n = len(pairs)
        # Refresh hashes; reset results sized to the current document.
        self._check_para_hashes = [compute_hash(new) for _, new in pairs]
        if (cid_results := self._check_results.get(check_id)) is None or len(cid_results) != n:
            self._check_results[check_id] = [None] * n
        self._active_check_id = check_id
        # Bump generation so any prior in-flight run for this check gets cancelled.
        self._check_run_gen[check_id] = self._check_run_gen.get(check_id, 0) + 1
        my_gen = self._check_run_gen[check_id]
        self._check_running[check_id] = True
        self._refresh_analyse_button_state()
        self._refresh_all_eval_cells()

        async def on_progress(idx: int, payload: dict | None, err: str | None) -> None:
            if my_gen != self._check_run_gen.get(check_id):
                return
            if 0 <= idx < len(self._check_results.get(check_id, [])):
                self._check_results[check_id][idx] = payload
            if self._main_tab_index not in (TAB_HISTORY, TAB_FUTURE):
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
            )
            run_ok = True
        except BaseException as exc:  # noqa: BLE001
            self._snack(f"Analyse failed: {ollama_error_message(exc)}")
        finally:
            if my_gen == self._check_run_gen.get(check_id):
                self._check_running[check_id] = False
                self._refresh_analyse_button_state()
                if self._main_tab_index in (TAB_HISTORY, TAB_FUTURE):
                    self._refresh_all_eval_cells()
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
        arr = getattr(self, "_future_row_cand_idx", None)
        if not arr or not (0 <= ui_idx < len(arr)):
            return ui_idx
        return arr[ui_idx]

    def _build_eval_cell_inner(self, idx: int, check_id: str | None) -> ft.Control:
        on_history = self._main_tab_index == TAB_HISTORY
        if check_id is None:
            # History: show nothing when no check is active.
            return ft.Container(width=0, height=0) if on_history else ft.Container(width=18, height=18)
        cand_idx = self._eval_cand_idx(idx)
        if cand_idx is None:
            return ft.Container(width=18, height=18)
        check = checks_mod.get_check(check_id)
        results = self._check_results.get(check_id) or []
        payload = results[cand_idx] if 0 <= cand_idx < len(results) else None
        running = bool(self._check_running.get(check_id))
        if payload is None:
            if running:
                return ft.Container(
                    content=ft.ProgressRing(
                        width=14, height=14, stroke_width=2,
                        color=(check.accent if check else config.PRIMARY_COLOR),
                    ),
                    alignment=ft.Alignment.TOP_CENTER,
                )
            # History: no placeholder dot — show nothing until results arrive.
            if on_history:
                return ft.Container(width=0, height=0)
            return ft.Container(
                content=ft.Text("·", size=14, color=config.OUTLINE),
                alignment=ft.Alignment.TOP_CENTER,
            )
        symbol = checks_mod.extract_symbol(check, payload) if check else "?"
        color = check.color_for_symbol(symbol) if check else config.ON_SURFACE_VARIANT
        summary_raw = checks_mod.extract_summary(check, payload) if check else ""
        tip: str | None = None
        if summary_raw:
            tip = summary_raw if len(summary_raw) <= 220 else summary_raw[:217] + "…"
        # History + Review (Future): bare symbol only — no pill chrome, no summary subtext.
        if self._main_tab_index in (TAB_FUTURE, TAB_HISTORY):
            return ft.Container(
                content=ft.Text(
                    symbol,
                    size=18,
                    weight=ft.FontWeight.W_700,
                    color=color,
                    no_wrap=True,
                ),
                alignment=ft.Alignment.TOP_CENTER,
                padding=ft.padding.only(top=2),
                on_hover=lambda e, i=idx: self._on_eval_symbol_hover(e, i),
                tooltip=tip,
            )
        symbol_box = ft.Container(
            content=ft.Text(
                symbol,
                size=18,
                weight=ft.FontWeight.W_700,
                color=color,
                no_wrap=True,
            ),
            alignment=ft.Alignment.TOP_CENTER,
            padding=ft.padding.symmetric(horizontal=4, vertical=2),
            border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.10, color),
            on_hover=lambda e, i=idx: self._on_eval_symbol_hover(e, i),
            tooltip=tip,
        )
        if not summary_raw:
            return symbol_box
        summary_txt = ft.Text(
            summary_raw,
            size=10,
            color=config.ON_SURFACE_VARIANT,
            max_lines=3,
            overflow=ft.TextOverflow.ELLIPSIS,
            selectable=True,
            text_align=ft.TextAlign.CENTER,
        )
        return ft.Column(
            [symbol_box, summary_txt],
            tight=True,
            spacing=4,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _refresh_eval_cell(self, idx: int) -> None:
        if not (0 <= idx < len(self._compare_eval_hosts)):
            return
        host = self._compare_eval_hosts[idx]
        host.content = self._build_eval_cell_inner(idx, self._active_check_id)
        if _ctrl_on_page(host):
            host.update()

    def _refresh_all_eval_cells(self) -> None:
        for i in range(len(self._compare_eval_hosts)):
            self._refresh_eval_cell(i)

    # ------------------------------------------------------------------
    # Floating result card
    # ------------------------------------------------------------------

    def _active_result_card_overlay(self) -> ft.Container:
        if self._main_tab_index == TAB_FUTURE:
            return self._future_result_card_overlay
        return self._result_card_overlay

    def _hide_all_result_card_overlays(self) -> None:
        self._result_card_visible_for = None
        self._result_card_hide_gen += 1
        for ov in (self._result_card_overlay, self._future_result_card_overlay):
            ov.visible = False
            if _ctrl_on_page(ov):
                ov.update()

    def _on_eval_symbol_hover(self, e: ft.ControlEvent, idx: int) -> None:
        if str(e.data).lower() == "true":
            self._show_result_card(idx)
        else:
            self._schedule_hide_result_card()

    def _on_result_card_hover(self, e: ft.ControlEvent) -> None:
        if str(e.data).lower() == "true":
            self._result_card_hide_gen += 1  # cancel pending hide
        else:
            self._schedule_hide_result_card()

    def _show_result_card(self, idx: int) -> None:
        cid = self._active_check_id
        if cid is None:
            return
        check = checks_mod.get_check(cid)
        if check is None:
            return
        cand_idx = self._eval_cand_idx(idx)
        if cand_idx is None:
            return
        results = self._check_results.get(cid) or []
        if not (0 <= cand_idx < len(results)):
            return
        payload = results[cand_idx]
        if payload is None:
            return
        self._result_card_hide_gen += 1  # cancel pending hide
        active = self._active_result_card_overlay()
        other = (
            self._future_result_card_overlay
            if active is self._result_card_overlay
            else self._result_card_overlay
        )
        if other.visible:
            other.visible = False
            if _ctrl_on_page(other):
                other.update()
        # Position vertically: estimate row position by index * row pitch.
        row_pitch = 88.0  # pragmatic estimate; ListView spacing=0 + padding=2.
        top = max(4.0, idx * row_pitch + 4.0)
        active.top = top
        active.content = self._build_result_card(check, payload, cand_idx)
        active.visible = True
        self._result_card_visible_for = (cid, idx)
        if _ctrl_on_page(active):
            active.update()

    def _schedule_hide_result_card(self) -> None:
        self._result_card_hide_gen += 1
        gen = self._result_card_hide_gen
        self.page.run_task(self._hide_result_card_after_delay, gen)

    async def _hide_result_card_after_delay(self, gen: int) -> None:
        await asyncio.sleep(RESULT_CARD_HIDE_DELAY_SEC)
        if gen != self._result_card_hide_gen:
            return
        self._result_card_visible_for = None
        for ov in (self._result_card_overlay, self._future_result_card_overlay):
            if ov.visible:
                ov.visible = False
                if _ctrl_on_page(ov):
                    ov.update()

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

    def _build_result_card(self, check: checks_mod.Check, payload: dict, idx: int) -> ft.Control:
        symbol = checks_mod.extract_symbol(check, payload)
        color = check.color_for_symbol(symbol)
        summary = checks_mod.extract_summary(check, payload)
        metrics = checks_mod.extract_metrics(check, payload)
        recs = checks_mod.extract_recommendations(payload, limit=3)
        confidence = checks_mod.extract_confidence(payload)
        label = checks_mod.extract_label(payload)

        header = ft.Row(
            [
                ft.Container(
                    content=ft.Text(symbol, size=22, weight=ft.FontWeight.W_700, color=color),
                    width=34, height=34,
                    alignment=ft.Alignment.CENTER,
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.18, color),
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
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        rows: list[ft.Control] = [header]

        if summary:
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

        if recs:
            rec_controls: list[ft.Control] = [
                ft.Text("Recommendations", size=10, color=config.ON_SURFACE_VARIANT)
            ]
            for r in recs:
                action = str(r.get("action") or r.get("recommendation") or "").strip()
                if not action:
                    continue
                priority = str(r.get("priority") or r.get("uncertainty") or "").strip().lower()
                pcolor = {
                    "high": "#E5484D",
                    "medium": "#F0A455",
                    "low": "#7ED9A0",
                }.get(priority, config.ON_SURFACE_VARIANT)
                rec_controls.append(
                    ft.Row(
                        [
                            ft.Container(
                                width=6, height=6, border_radius=3, bgcolor=pcolor,
                                margin=ft.margin.only(top=6),
                            ),
                            ft.Text(action, size=12, color=config.ON_SURFACE, expand=True, selectable=True),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    )
                )
            rows.append(
                ft.Container(
                    content=ft.Column(rec_controls, spacing=2, tight=True),
                    padding=ft.padding.only(top=6),
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
        syms = [checks_mod.extract_symbol(check, p) for _, p in nonempty]
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
    # Impact tab results rendering
    # ------------------------------------------------------------------

    def _refresh_impact_tab_results(self, check_id: str) -> None:
        """Re-render the Impact tab's per-paragraph list and summary from check results."""
        results_list = getattr(self, "_impact_results_list", None)
        summary_container = getattr(self, "_impact_summary_container", None)
        summary_text_ctrl = getattr(self, "_impact_summary_text", None)
        status_text = getattr(self, "_impact_status_text", None)

        check = checks_mod.get_check(check_id)
        results: list[dict | None] = self._check_results.get(check_id) or []
        pairs = []
        try:
            buffers = self._active_compare_buffers()
            from iterthink.compare.layout import aligned_compare_pairs
            pairs = aligned_compare_pairs(buffers.baseline, buffers.candidate)
        except Exception:  # noqa: BLE001
            pass

        # Per-paragraph rows.
        if results_list is not None:
            results_list.controls.clear()
            for i, payload in enumerate(results):
                if payload is None:
                    continue
                sym = checks_mod.extract_symbol(check, payload) if check else "?"
                color = check.color_for_symbol(sym) if check else config.ON_SURFACE_VARIANT
                summary_raw = checks_mod.extract_summary(check, payload) if check else ""

                # Paragraph snippet from the candidate (new) text.
                snip = ""
                if i < len(pairs):
                    _, new_text = pairs[i]
                    snip = new_text.strip().replace("\n", " ")
                    if len(snip) > 120:
                        snip = snip[:117] + "…"

                sym_chip = ft.Container(
                    content=ft.Text(
                        sym,
                        size=16,
                        weight=ft.FontWeight.W_700,
                        color=color,
                        no_wrap=True,
                    ),
                    width=32,
                    height=32,
                    alignment=ft.Alignment.CENTER,
                    border_radius=6,
                    bgcolor=ft.Colors.with_opacity(0.15, color),
                )

                row = ft.Container(
                    content=ft.Row(
                        [
                            sym_chip,
                            ft.Column(
                                [
                                    ft.Text(
                                        f"¶ {i + 1}"
                                        + (f"  {snip}" if snip else ""),
                                        size=11,
                                        color=config.ON_SURFACE_VARIANT,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Text(
                                        summary_raw,
                                        size=12,
                                        color=config.ON_SURFACE,
                                        max_lines=2,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                        selectable=True,
                                    ),
                                ],
                                spacing=2,
                                tight=True,
                                expand=True,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    padding=ft.padding.symmetric(horizontal=8, vertical=6),
                    border_radius=6,
                    bgcolor=ft.Colors.with_opacity(0.06, color),
                    margin=ft.margin.only(bottom=2),
                )
                results_list.controls.append(row)

            results_list.visible = bool(results_list.controls)
            if _ctrl_on_page(results_list):
                results_list.update()

        # Document-level summary.
        if check is not None and summary_text_ctrl is not None and summary_container is not None:
            summary_body = self._build_document_check_summary_text(check, results)
            summary_text_ctrl.value = summary_body
            summary_container.visible = bool(summary_body)
            if _ctrl_on_page(summary_container):
                summary_container.update()

        # Update status text.
        if status_text is not None:
            label = check.label if check else check_id
            n_done = sum(1 for r in results if r is not None)
            status_text.value = f"{label} — {n_done}/{len(results)} paragraph(s) analysed."
            if _ctrl_on_page(status_text):
                status_text.update()

    # ------------------------------------------------------------------
    # Invalidation: drop per-paragraph cache when candidate text changes.
    # ------------------------------------------------------------------

    def _invalidate_check_results_for_changes(self) -> None:
        """Detect changed candidate paragraphs and drop their cached results from memory.

        Called after candidate edits. Disk cache (paragraph_analysis) is keyed by
        content hashes so re-runs reuse it.
        """
        if not self._check_results:
            self._check_para_hashes = []
            return
        buffers = self._active_compare_buffers()
        pairs = aligned_compare_pairs(buffers.baseline, buffers.candidate)
        new_hashes = [compute_hash(new) for _, new in pairs]
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
