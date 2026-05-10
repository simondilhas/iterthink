"""Review → Impact subtab: prompts, context file pickers, parallel analysis, summary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import flet as ft

from iterthink import config, impact_checks
from iterthink.compare.layout import aligned_compare_pairs
from iterthink.db.session import session_scope
from iterthink.persistence import impact_annotations as impact_ann
from iterthink.persistence import version_storage
from iterthink.services import impact_analysis_runner
from iterthink.studio.constants import KI_PILL_TEXT_SIZE, TAB_FUTURE
from iterthink.studio.tree import build_md_tree
from iterthink.studio.util import ctrl_on_page as _ctrl_on_page


class MarkdownStudioImpactMixin:
    """Expects MarkdownStudio fields: page, _db, current_path, _main_tab_index, _review_subtab_index,
    _compare_editor, _compare_snapshot_version_id, _make_llm_backend, chat_model_for_requests,
    _impact_status_text, _impact_results_list,
    _pill_row_impact (created in markdown_studio), _impact_ki_context_panel, _impact_summary_right,
    _right_chat_section, _ki_sidebar_well, _active_compare_buffers, _rebuild_compare_paragraph_ui.
    """

    def _init_impact_ui_fields(self) -> None:
        self._impact_tab_initialized = False
        self._active_impact_prompt_id: str | None = None
        self._impact_context_entries: list[tuple[Path, ft.Checkbox]] = []
        self._impact_run_gen = 0
        self._impact_run_spinner = ft.ProgressRing(
            width=12,
            height=12,
            stroke_width=2,
            color=config.ON_PRIMARY,
            visible=False,
        )
        self._impact_run_btn = ft.FilledButton(
            content=ft.Row(
                [
                    self._impact_run_spinner,
                    ft.Text("Run analysis", size=KI_PILL_TEXT_SIZE, color=config.ON_PRIMARY),
                ],
                tight=True,
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            elevation=0,
            style=ft.ButtonStyle(
                bgcolor=config.PRIMARY_COLOR,
                color=config.ON_PRIMARY,
                overlay_color=ft.Colors.with_opacity(0.14, config.ON_PRIMARY),
                visual_density=ft.VisualDensity.COMPACT,
                padding=ft.padding.symmetric(horizontal=12, vertical=6),
            ),
            tooltip="Run Impact analysis on each paragraph (uses context files in the right panel).",
            on_click=lambda _e: self.page.run_task(self._run_impact_analysis_async),
        )

    def _ensure_impact_tab_initialized(self) -> None:
        if self._impact_tab_initialized:
            return
        impact_checks.reload()
        self._rebuild_impact_prompt_pills()
        self._rebuild_impact_context_checkboxes()
        self._impact_tab_initialized = True
        # Show current document paragraphs as placeholder rows immediately.
        self._populate_impact_para_placeholders()

    def _populate_impact_para_placeholders(self) -> None:
        """Fill the paragraph listview with text-only rows (no chips) before any analysis runs."""
        para_lv = getattr(self, "_impact_para_listview", None)
        if para_lv is None:
            return
        paras = self._get_candidate_paragraphs()
        if not paras:
            return
        para_lv.controls.clear()
        for i, text in enumerate(paras):
            para_lv.controls.append(self._build_impact_para_row(i, text, None))
        if _ctrl_on_page(para_lv):
            para_lv.update()

    def _sync_impact_paragraph_list_after_compare_rebuild(self) -> None:
        """Keep Impact tab paragraphs in sync when Review rows / proposal buffer are rebuilt."""
        if int(getattr(self, "_main_tab_index", -1)) != TAB_FUTURE:
            return
        if int(getattr(self, "_review_subtab_index", 0)) != 1:
            return
        pid = getattr(self, "_active_impact_prompt_id", None)
        if pid:
            self._refresh_impact_annotations_ui(str(pid))
        else:
            self._populate_impact_para_placeholders()

    def _rebuild_impact_prompt_pills(self) -> None:
        row = getattr(self, "_pill_row_impact", None)
        if row is None:
            return
        row.controls.clear()
        for act in impact_checks.IMPACT_CHECKS:
            row.controls.append(
                ft.OutlinedButton(
                    act.label,
                    style=ft.ButtonStyle(
                        text_style=ft.TextStyle(size=KI_PILL_TEXT_SIZE),
                        visual_density=ft.VisualDensity.COMPACT,
                        padding=ft.padding.symmetric(horizontal=8, vertical=4),
                    ),
                    on_click=lambda _e, aid=act.id: self._on_impact_prompt_click(aid),
                )
            )
        if _ctrl_on_page(row):
            row.update()
        self._sync_impact_ki_context_visibility()

    def _collect_all_project_md_paths(self) -> list[Path]:
        from iterthink import config as _cfg

        root = _cfg.DOCUMENTS
        if not root.is_dir():
            return []
        result: list[Path] = []

        def _walk(node: dict, _parent: Path) -> None:
            for fname, fpath in node.get("_files", []):
                result.append(fpath)
            for key, sub in node.items():
                if key != "_files" and isinstance(sub, dict):
                    _walk(sub, root / key)

        try:
            tree = build_md_tree(root)
            _walk(tree, root)
        except Exception:  # noqa: BLE001
            pass
        result.sort(key=lambda p: str(p).lower())
        return result

    def _rebuild_impact_context_checkboxes(self) -> None:
        scroll = getattr(self, "_impact_ki_context_scroll", None)
        if scroll is None:
            return
        scroll.controls.clear()
        self._impact_context_entries.clear()
        paths = self._collect_all_project_md_paths()
        for p in paths:
            cb = ft.Checkbox(
                value=True,
                label=p.name,
                label_style=ft.TextStyle(size=11, color=config.ON_SURFACE),
                fill_color=config.PRIMARY_COLOR,
                check_color=config.ON_PRIMARY,
                scale=0.88,
                tooltip=str(p),
            )
            self._impact_context_entries.append((p, cb))
            scroll.controls.append(
                ft.Container(
                    content=cb,
                    padding=ft.padding.only(left=2, top=0, bottom=0),
                    height=26,
                )
            )
        if _ctrl_on_page(scroll):
            scroll.update()

    def _on_impact_prompt_click(self, action_id: str) -> None:
        self._active_impact_prompt_id = action_id
        self._sync_impact_ki_context_visibility()
        if hasattr(self, "_impact_status_text") and self._impact_status_text:
            self._impact_status_text.value = "Select context files, then Run analysis."
            if _ctrl_on_page(self._impact_status_text):
                self._impact_status_text.update()

    def _sync_impact_ki_context_visibility(self) -> None:
        impact_subtab = (
            getattr(self, "_main_tab_index", -1) == TAB_FUTURE
            and getattr(self, "_review_subtab_index", 0) == 1
        )
        on = impact_subtab and self._active_impact_prompt_id is not None

        # Compare-tab checks (checks.yaml) vs Impact checks (impact_checks.yaml) on KI Analyse.
        analyse_pills = getattr(self, "_pill_row_analyse", None)
        if analyse_pills is not None:
            want_vis = not impact_subtab
            if analyse_pills.visible != want_vis:
                analyse_pills.visible = want_vis
                if _ctrl_on_page(analyse_pills):
                    analyse_pills.update()

        # Impact check pills + run — visible on Review → Impact subtab only.
        prompt_sec = getattr(self, "_impact_analyse_section", None)
        if prompt_sec is not None and prompt_sec.visible != impact_subtab:
            prompt_sec.visible = impact_subtab
            if _ctrl_on_page(prompt_sec):
                prompt_sec.update()

        panel = getattr(self, "_impact_ki_context_panel", None)
        title = getattr(self, "_impact_ki_context_title", None)
        if panel is not None and panel.visible != on:
            panel.visible = on
            if _ctrl_on_page(panel):
                panel.update()
        if title is not None and title.visible != on:
            title.visible = on
            if _ctrl_on_page(title):
                title.update()
        scroll = getattr(self, "_impact_ki_context_scroll", None)
        if scroll is not None and scroll.visible != on:
            scroll.visible = on
            if _ctrl_on_page(scroll):
                scroll.update()
        summary_r = getattr(self, "_impact_summary_right", None)
        if summary_r is not None:
            has_txt = bool(getattr(self, "_impact_summary_right_text", None) and self._impact_summary_right_text.value)
            want = on and has_txt
            if summary_r.visible != want:
                summary_r.visible = want
                if _ctrl_on_page(summary_r):
                    summary_r.update()

        if hasattr(self, "page") and hasattr(self, "_defer_sync_ki_tab_height"):
            self.page.run_task(self._defer_sync_ki_tab_height)

    def _resolve_impact_version_id(self, session: Any) -> int | None:
        if getattr(self, "_compare_snapshot_version_id", None) is not None:
            return int(self._compare_snapshot_version_id)
        cur = getattr(self, "current_path", None)
        if not cur:
            return None
        snaps = version_storage.list_snapshots(session, cur.resolve())
        return snaps[0].version_id if snaps else None

    def _selected_impact_context_document_ids(self) -> list[int]:
        paths = [p for p, cb in self._impact_context_entries if cb.value]
        if not paths:
            return []
        with session_scope() as s:
            ids: list[int] = []
            for p in paths:
                doc = version_storage.get_or_create_document(s, p.resolve())
                ids.append(int(doc.id))
            s.commit()
            return ids

    # ------------------------------------------------------------------
    # Impact para view helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _impact_status_color(st: str) -> str:
        if st == "risk":
            return "#E57373"
        if st == "changed":
            return "#FFB74D"
        if st == "stable":
            return "#81C784"
        return "#9AA0A6"

    def _impact_paragraphs_for_display(self) -> tuple[list[str], bool]:
        """Paragraphs to show in the Impact tab: candidate (proposal) if any, else baseline.

        Returns ``(paragraphs, annotations_may_be_stale)``. When the second value is
        True, stored annotations were keyed to the candidate buffer and must not be
        merged by index (we are showing baseline text as a readability fallback).
        """
        try:
            buffers = self._active_compare_buffers()
            cand = [p for p in (buffers.candidate or "").split("\n\n") if p.strip()]
            if cand:
                return cand, False
            base = [p for p in (buffers.baseline or "").split("\n\n") if p.strip()]
            return base, bool(base)
        except Exception:  # noqa: BLE001
            return [], True

    def _get_candidate_paragraphs(self) -> list[str]:
        """Non-empty proposal paragraphs, or baseline paragraphs if the proposal is still empty."""
        paras, _ = self._impact_paragraphs_for_display()
        return paras

    def _build_impact_para_row(
        self,
        idx: int,
        para_text: str,
        ann_row: Any | None,
    ) -> ft.Container:
        """One row: [symbol chip | paragraph text (monospace)].

        *ann_row* is an ImpactAnnotation ORM object or None (not yet analysed).
        Clicking the chip opens the detail overlay.
        """
        from iterthink.studio import ui_theme
        from iterthink.studio.constants import COMPARE_COL_FONT_SIZE, COMPARE_COL_LINE_HEIGHT

        if ann_row is not None:
            st = str(ann_row.status)
            color = self._impact_status_color(st)
            chip_content: ft.Control = ft.Text(
                st,
                size=12,
                weight=ft.FontWeight.W_700,
                color=color,
                no_wrap=True,
            )
            chip_bg = ft.Colors.with_opacity(0.14, color)
            chip_border = ft.border.all(1, ft.Colors.with_opacity(0.45, color))
            chip_tooltip = impact_ann.effective_comment(ann_row)
        else:
            color = config.OUTLINE
            chip_content = ft.Text("·", size=14, color=color, no_wrap=True)
            chip_bg = ft.Colors.TRANSPARENT
            chip_border = None
            chip_tooltip = None

        chip = ft.Container(
            content=chip_content,
            width=54,
            height=24,
            alignment=ft.Alignment.CENTER,
            border_radius=5,
            bgcolor=chip_bg,
            border=chip_border,
            tooltip=chip_tooltip,
            on_click=(
                (lambda _e, i=idx: self._show_impact_result_card(i))
                if ann_row is not None
                else None
            ),
        )

        snip = para_text.strip()
        para_ctrl = ft.Text(
            snip,
            size=COMPARE_COL_FONT_SIZE,
            font_family="monospace",
            color=ui_theme.editor_text_color(),
            selectable=True,
            expand=True,
        )

        return ft.Container(
            content=ft.Row(
                [chip, para_ctrl],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=ft.padding.symmetric(horizontal=6, vertical=4),
        )

    def _show_impact_result_card(self, idx: int) -> None:
        """Populate and show the impact detail overlay for paragraph *idx*."""
        overlay = getattr(self, "_impact_result_card_overlay", None)
        if overlay is None:
            return

        prompt_id = self._active_impact_prompt_id
        if not prompt_id:
            return

        cur = getattr(self, "current_path", None)
        if not cur:
            return

        ann_row = None
        try:
            with session_scope() as s:
                doc = version_storage.get_document_by_resolved_path(s, cur.resolve())
                if doc is not None:
                    vid = self._resolve_impact_version_id(s)
                    if vid is not None:
                        m = impact_ann.list_for_version(
                            s,
                            document_id=int(doc.id),
                            version_id=int(vid),
                            prompt_id=prompt_id,
                        )
                        ann_row = m.get(idx)
        except Exception:  # noqa: BLE001
            pass

        if ann_row is None:
            return

        st = str(ann_row.status)
        color = self._impact_status_color(st)
        eff = impact_ann.effective_comment(ann_row)

        header = ft.Row(
            [
                ft.Container(
                    content=ft.Text(st, size=14, weight=ft.FontWeight.W_700, color=color),
                    width=60, height=28,
                    alignment=ft.Alignment.CENTER,
                    border_radius=6,
                    bgcolor=ft.Colors.with_opacity(0.16, color),
                ),
                ft.Column(
                    [
                        ft.Text(
                            f"Paragraph {idx + 1}",
                            size=12,
                            weight=ft.FontWeight.W_600,
                            color=config.ON_SURFACE,
                        ),
                    ],
                    spacing=0, tight=True, expand=True,
                ),
                ft.IconButton(
                    ft.Icons.CLOSE,
                    icon_size=14,
                    padding=ft.padding.all(0),
                    on_click=lambda _e: self._hide_impact_result_card(),
                    icon_color=config.ON_SURFACE_VARIANT,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        rows: list[ft.Control] = [header]
        if eff:
            rows.append(
                ft.Container(
                    content=ft.Text(eff, size=12, color=config.ON_SURFACE, selectable=True),
                    padding=ft.padding.only(top=6),
                )
            )
        det = impact_ann.parse_details_dict(ann_row)
        if det:
            ex = det.get("explanation")
            if isinstance(ex, str) and ex.strip():
                rows.append(
                    ft.Container(
                        content=ft.Text(
                            "Explanation",
                            size=11,
                            weight=ft.FontWeight.W_600,
                            color=config.ON_SURFACE_VARIANT,
                        ),
                        padding=ft.padding.only(top=10, bottom=2),
                    )
                )
                rows.append(
                    ft.Container(
                        content=ft.Text(ex.strip(), size=12, color=config.ON_SURFACE, selectable=True),
                        padding=ft.padding.only(bottom=4),
                    )
                )
            refs = det.get("references")
            if isinstance(refs, list) and refs:
                rows.append(
                    ft.Container(
                        content=ft.Text(
                            "References",
                            size=11,
                            weight=ft.FontWeight.W_600,
                            color=config.ON_SURFACE_VARIANT,
                        ),
                        padding=ft.padding.only(top=6, bottom=2),
                    )
                )
                for rf in refs:
                    if not isinstance(rf, dict):
                        continue
                    doc = rf.get("document", "")
                    para = rf.get("paragraph")
                    note = rf.get("note", "")
                    line = str(doc)
                    if isinstance(para, int):
                        line += f" — paragraph {para}"
                    if isinstance(note, str) and note.strip():
                        line += f" ({note.strip()})"
                    rows.append(
                        ft.Container(
                            content=ft.Text(line, size=12, color=config.ON_SURFACE, selectable=True),
                            padding=ft.padding.only(left=8, bottom=2),
                        )
                    )
        override_btn = ft.TextButton(
            "Override comment",
            style=ft.ButtonStyle(
                text_style=ft.TextStyle(size=11),
                padding=ft.padding.symmetric(horizontal=0, vertical=0),
            ),
            on_click=lambda _e, r=ann_row: self._on_impact_override_click(r),
        )
        rows.append(
            ft.Container(
                content=ft.Row([override_btn], tight=True),
                padding=ft.padding.only(top=4),
            )
        )

        overlay.content = ft.Column(rows, spacing=2, tight=True, scroll=ft.ScrollMode.AUTO)
        overlay.visible = True
        if _ctrl_on_page(overlay):
            overlay.update()

    def _hide_impact_result_card(self) -> None:
        overlay = getattr(self, "_impact_result_card_overlay", None)
        if overlay and overlay.visible:
            overlay.visible = False
            if _ctrl_on_page(overlay):
                overlay.update()

    def _refresh_impact_annotations_ui(self, prompt_id: str) -> None:
        para_lv = getattr(self, "_impact_para_listview", None)
        summary_right = getattr(self, "_impact_summary_right_text", None)
        status_text = getattr(self, "_impact_status_text", None)

        cur = getattr(self, "current_path", None)
        if not cur or para_lv is None:
            return

        ann_map: dict[int, Any] = {}
        with session_scope() as s:
            doc = version_storage.get_document_by_resolved_path(s, cur.resolve())
            if doc is not None:
                vid = self._resolve_impact_version_id(s)
                if vid is not None:
                    ann_map = impact_ann.list_for_version(
                        s,
                        document_id=int(doc.id),
                        version_id=int(vid),
                        prompt_id=prompt_id,
                    )

        # Rebuild paragraph rows using current candidate text.
        para_lv.controls.clear()
        self._hide_impact_result_card()
        candidate_paras, ann_stale = self._impact_paragraphs_for_display()
        if ann_stale:
            ann_map = {}

        for i, para_text in enumerate(candidate_paras):
            ann_row = ann_map.get(i)
            para_lv.controls.append(self._build_impact_para_row(i, para_text, ann_row))

        if _ctrl_on_page(para_lv):
            para_lv.update()

        if summary_right is not None:
            summary_right.value = getattr(self, "_impact_summary_cache", "") or ""
            if _ctrl_on_page(summary_right):
                summary_right.update()
        if status_text is not None:
            status_text.value = "Ready." if ann_map else "Select a prompt and run analysis."
            if _ctrl_on_page(status_text):
                status_text.update()
        self._sync_impact_ki_context_visibility()

    def _on_impact_override_click(self, row: Any) -> None:
        tf = ft.TextField(
            value=impact_ann.effective_comment(row),
            dense=True,
            multiline=True,
            min_lines=2,
            max_lines=5,
            expand=True,
        )

        def close_dlg() -> None:
            self.page.pop_dialog()

        def save(_e: ft.ControlEvent | None) -> None:
            cur = getattr(self, "current_path", None)
            if not cur:
                close_dlg()
                return
            with session_scope() as s:
                doc = version_storage.get_or_create_document(s, cur.resolve())
                vid = self._resolve_impact_version_id(s)
                if vid is None:
                    close_dlg()
                    return
                impact_ann.set_override(
                    s,
                    document_id=int(doc.id),
                    version_id=int(vid),
                    paragraph_index=int(row.paragraph_index),
                    prompt_id=str(row.prompt_id),
                    override_comment=tf.value or "",
                )
            close_dlg()
            self._refresh_impact_annotations_ui(str(row.prompt_id))

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Override comment"),
                content=tf,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _e: close_dlg()),
                    ft.FilledButton("Save", on_click=save),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    async def _run_impact_analysis_async(self) -> None:
        act = impact_checks.get_impact_check(self._active_impact_prompt_id or "")
        if act is None:
            self._snack("Select an Impact check first.")
            return
        cur = getattr(self, "current_path", None)
        if not cur:
            self._snack("Open a note first.")
            return
        ctx_ids = self._selected_impact_context_document_ids()
        if not ctx_ids:
            self._snack("Select at least one context file.")
            return
        if not getattr(self, "_compare_right_fields", None):
            self._rebuild_compare_paragraph_ui()
        buffers = self._active_compare_buffers()
        pairs = aligned_compare_pairs(buffers.baseline, buffers.candidate)
        paragraphs = [new for _, new in pairs]
        if not any((x or "").strip() for x in paragraphs):
            self._snack("Nothing to analyse.")
            return

        self._impact_run_gen += 1
        gen = self._impact_run_gen
        self._impact_run_spinner.visible = True
        if _ctrl_on_page(self._impact_run_spinner):
            self._impact_run_spinner.update()

        if self._impact_status_text:
            self._impact_status_text.value = "Running analysis…"
            if _ctrl_on_page(self._impact_status_text):
                self._impact_status_text.update()

        try:
            with session_scope() as s:
                target_doc = version_storage.get_or_create_document(s, cur.resolve())
                target_did = int(target_doc.id)
                vid = self._resolve_impact_version_id(s)
                if vid is None:
                    self._snack("No saved version for this note — save or switch version first.")
                    return
                s.commit()

            candidate_paras = self._get_candidate_paragraphs()

            async def on_progress(idx: int, payload: dict | None, err: str | None) -> None:
                if gen != self._impact_run_gen:
                    return
                if self._impact_status_text:
                    self._impact_status_text.value = f"Paragraph {idx + 1}…" + (f" ({err})" if err else "")
                    if _ctrl_on_page(self._impact_status_text):
                        self._impact_status_text.update()
                # Refresh just the row that arrived for snappy progressive feedback.
                para_lv = getattr(self, "_impact_para_listview", None)
                if para_lv is not None and payload is not None and idx < len(para_lv.controls):
                    # Build an ad-hoc ann-like object from the payload dict.
                    class _FakeRow:
                        def __init__(self, st: str, co: str, details: dict | None) -> None:
                            self.status = st
                            self.comment = co
                            self.details_json = (
                                json.dumps(details, ensure_ascii=False) if isinstance(details, dict) else None
                            )
                            self.override_comment = None
                            self.overridden = False

                    ann = _FakeRow(
                        str(payload.get("status", "")),
                        str(payload.get("comment", "")),
                        payload.get("details") if isinstance(payload.get("details"), dict) else None,
                    )
                    ann.paragraph_index = idx
                    ann.prompt_id = act.id
                    para_text = candidate_paras[idx] if idx < len(candidate_paras) else ""
                    para_lv.controls[idx] = self._build_impact_para_row(idx, para_text, ann)
                    if _ctrl_on_page(para_lv):
                        para_lv.update()

            results = await impact_analysis_runner.run_impact_analysis(
                self._make_llm_backend(),
                model=self.chat_model_for_requests(),
                check=act,
                conn=self._db,
                target_document_id=target_did,
                target_version_id=int(vid),
                context_document_ids=ctx_ids,
                paragraphs=paragraphs,
                on_progress=on_progress,
            )

            ann_lines: list[tuple[int, str, str, int]] = []
            for i, r in enumerate(results):
                if isinstance(r, dict):
                    det = r.get("details")
                    nref = 0
                    if isinstance(det, dict):
                        refs = det.get("references")
                        if isinstance(refs, list):
                            nref = len(refs)
                    ann_lines.append((i, str(r.get("status", "")), str(r.get("comment", "")), nref))

            summary, s_err = await impact_analysis_runner.run_impact_summary(
                self._make_llm_backend(),
                model=self.chat_model_for_requests(),
                annotations=ann_lines,
            )
            self._impact_summary_cache = summary or ""
            if self._impact_summary_text:
                self._impact_summary_text.value = self._impact_summary_cache
            if getattr(self, "_impact_summary_right_text", None):
                self._impact_summary_right_text.value = self._impact_summary_cache
                if _ctrl_on_page(self._impact_summary_right_text):
                    self._impact_summary_right_text.update()
            if getattr(self, "_impact_summary_right", None):
                self._impact_summary_right.visible = bool(self._impact_summary_cache)
                if _ctrl_on_page(self._impact_summary_right):
                    self._impact_summary_right.update()

            if self._impact_status_text:
                self._impact_status_text.value = (
                    f"Done.{(' Summary error: ' + s_err) if s_err else ''}"
                )
                if _ctrl_on_page(self._impact_status_text):
                    self._impact_status_text.update()
            self._refresh_impact_annotations_ui(act.id)
        finally:
            self._impact_run_spinner.visible = False
            if _ctrl_on_page(self._impact_run_spinner):
                self._impact_run_spinner.update()
