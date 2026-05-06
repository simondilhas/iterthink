"""Analyse checks, eval cells, and result card overlay for MarkdownStudio."""

from __future__ import annotations

import asyncio
from typing import Any

import flet as ft

from iterthink import checks as checks_mod
from iterthink import checks_runner
from iterthink import config
from iterthink.compare_layout import aligned_compare_pairs
from iterthink.ollama_util import ollama_error_message
from iterthink.paragraph_align import compute_hash
from iterthink.studio_constants import (
    COMPARE_EVAL_COL_W,
    KI_PILL_TEXT_SIZE,
    RESULT_CARD_HIDE_DELAY_SEC,
)
from iterthink.studio_util import ctrl_on_page as _ctrl_on_page


class MarkdownStudioChecksUi:
    def _rebuild_analyse_pills(self) -> None:
        """Build a button per check; click runs/loads results, hover shows nothing (use card)."""
        self._pill_row_analyse.controls.clear()
        self._analyse_buttons.clear()
        self._analyse_button_progress.clear()
        self._analyse_button_count.clear()
        for c in checks_mod.CHECKS:
            spinner = ft.ProgressRing(
                width=10, height=10, stroke_width=2, color=config.FEDORA_BLUE, visible=False
            )
            counter = ft.Text("", size=KI_PILL_TEXT_SIZE, color=ft.Colors.GREY_300, visible=False)
            label_row = ft.Row(
                [
                    spinner,
                    ft.Text(c.label, size=KI_PILL_TEXT_SIZE),
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
                    text_style=ft.TextStyle(size=KI_PILL_TEXT_SIZE),
                    visual_density=ft.VisualDensity.COMPACT,
                    padding=ft.padding.symmetric(horizontal=8, vertical=4),
                    bgcolor={
                        ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.18, config.FEDORA_BLUE),
                        ft.ControlState.HOVERED: ft.Colors.with_opacity(0.32, config.FEDORA_BLUE),
                    },
                    color={
                        ft.ControlState.DEFAULT: ft.Colors.GREY_100,
                    },
                ),
                tooltip=f"Run {c.label} on every paragraph (cached results reused).",
                on_click=lambda _e, cid=c.id: self.page.run_task(self._run_check_async, cid),
            )
            self._analyse_buttons[c.id] = btn
            self._analyse_button_progress[c.id] = spinner
            self._analyse_button_count[c.id] = counter
            self._pill_row_analyse.controls.append(btn)
        self._refresh_analyse_button_state()

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
            # Active check: stronger background (same accent as other KI pills).
            base_color = config.FEDORA_BLUE
            btn.style = ft.ButtonStyle(
                text_style=ft.TextStyle(size=KI_PILL_TEXT_SIZE),
                visual_density=ft.VisualDensity.COMPACT,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                bgcolor={
                    ft.ControlState.DEFAULT: ft.Colors.with_opacity(
                        0.42 if is_active else 0.18, base_color
                    ),
                    ft.ControlState.HOVERED: ft.Colors.with_opacity(
                        0.55 if is_active else 0.32, base_color
                    ),
                },
                color={ft.ControlState.DEFAULT: ft.Colors.GREY_100},
            )
            if _ctrl_on_page(btn):
                btn.update()

    async def _run_check_async(self, check_id: str) -> None:
        """Activate a check; load cached results, run remaining paragraphs in background."""
        check = checks_mod.get_check(check_id)
        if check is None:
            self._snack(f"Check '{check_id}' is not configured.")
            return
        # Make sure Compare tab is selected so user sees results.
        if self._main_tab_index != 1:
            self._main_tabs.selected_index = 1
            if _ctrl_on_page(self._main_tabs):
                self._main_tabs.update()
            self._main_tab_index = 1
        # Need a candidate to analyse against the baseline.
        if not self._compare_right_fields:
            self._rebuild_compare_paragraph_ui()
        baseline = self._compare_latest_baseline_text()
        candidate = self._compare_editor.value or self.editor.value or ""
        if not candidate.strip():
            self._snack("Open a note first to analyse it.")
            return
        pairs = aligned_compare_pairs(baseline, candidate)
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
            self._refresh_eval_cell(idx)
            self._refresh_analyse_button_state()

        try:
            await checks_runner.run_check_for_document(
                self._make_llm_backend(),
                model=self.chat_model_for_requests(),
                check=check,
                pairs=pairs,
                on_progress=on_progress,
                use_cache=True,
            )
        except BaseException as exc:  # noqa: BLE001
            self._snack(f"Analyse failed: {ollama_error_message(exc)}")
        finally:
            if my_gen == self._check_run_gen.get(check_id):
                self._check_running[check_id] = False
                self._refresh_analyse_button_state()
                self._refresh_all_eval_cells()

    # ------------------------------------------------------------------
    # Eval cell (leftmost cell in compare rows)
    # ------------------------------------------------------------------

    def _build_eval_cell(self, idx: int) -> ft.Container:
        cid = self._active_check_id
        host = ft.Container(
            width=COMPARE_EVAL_COL_W,
            alignment=ft.Alignment.TOP_CENTER,
            padding=ft.padding.only(top=4, right=2),
            content=self._build_eval_cell_inner(idx, cid),
        )
        return host

    def _build_eval_cell_inner(self, idx: int, check_id: str | None) -> ft.Control:
        if check_id is None:
            return ft.Container(width=18, height=18)
        check = checks_mod.get_check(check_id)
        results = self._check_results.get(check_id) or []
        payload = results[idx] if 0 <= idx < len(results) else None
        running = bool(self._check_running.get(check_id))
        if payload is None:
            if running:
                return ft.Container(
                    content=ft.ProgressRing(
                        width=14, height=14, stroke_width=2,
                        color=(check.accent if check else config.FEDORA_BLUE),
                    ),
                    alignment=ft.Alignment.TOP_CENTER,
                )
            return ft.Container(
                content=ft.Text("·", size=14, color=ft.Colors.GREY_700),
                alignment=ft.Alignment.TOP_CENTER,
            )
        symbol = checks_mod.extract_symbol(check, payload) if check else "?"
        color = check.color_for_symbol(symbol) if check else ft.Colors.GREY_400
        return ft.Container(
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
            tooltip=None,
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
        results = self._check_results.get(cid) or []
        if not (0 <= idx < len(results)):
            return
        payload = results[idx]
        if payload is None:
            return
        self._result_card_hide_gen += 1  # cancel pending hide
        # Position vertically: estimate row position by index * row pitch.
        row_pitch = 88.0  # pragmatic estimate; ListView spacing=0 + padding=2.
        top = max(4.0, idx * row_pitch + 4.0)
        # If there are many rows, keep card visible.
        self._result_card_overlay.top = top
        self._result_card_overlay.content = self._build_result_card(check, payload, idx)
        self._result_card_overlay.visible = True
        self._result_card_visible_for = (cid, idx)
        if _ctrl_on_page(self._result_card_overlay):
            self._result_card_overlay.update()

    def _schedule_hide_result_card(self) -> None:
        self._result_card_hide_gen += 1
        gen = self._result_card_hide_gen
        self.page.run_task(self._hide_result_card_after_delay, gen)

    async def _hide_result_card_after_delay(self, gen: int) -> None:
        await asyncio.sleep(RESULT_CARD_HIDE_DELAY_SEC)
        if gen != self._result_card_hide_gen:
            return
        self._result_card_overlay.visible = False
        self._result_card_visible_for = None
        if _ctrl_on_page(self._result_card_overlay):
            self._result_card_overlay.update()

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
                    ft.Text(label, size=10, color=ft.Colors.GREY_400, no_wrap=True),
                    ft.Text(text_val, size=12, weight=ft.FontWeight.W_700, color=ft.Colors.GREY_100, no_wrap=True),
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
                        ft.Text(check.label, size=13, weight=ft.FontWeight.W_600, color=ft.Colors.GREY_100),
                        ft.Text(
                            f"Paragraph {idx + 1}" + (f" · {label}" if label else ""),
                            size=10,
                            color=ft.Colors.GREY_400,
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
                        color=ft.Colors.GREY_400,
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
                        color=ft.Colors.GREY_200,
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
                ft.Text("Recommendations", size=10, color=ft.Colors.GREY_400)
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
                }.get(priority, ft.Colors.GREY_500)
                rec_controls.append(
                    ft.Row(
                        [
                            ft.Container(
                                width=6, height=6, border_radius=3, bgcolor=pcolor,
                                margin=ft.margin.only(top=6),
                            ),
                            ft.Text(action, size=12, color=ft.Colors.GREY_100, expand=True, selectable=True),
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
        baseline = self._compare_latest_baseline_text()
        candidate = self._compare_editor.value or ""
        pairs = aligned_compare_pairs(baseline, candidate)
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
