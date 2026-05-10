"""Review → Impact subtab: prompts, context file pickers, parallel analysis, summary."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

import flet as ft

from iterthink import config, impact_checks
from iterthink.compare.layout import aligned_compare_pairs
from iterthink.compare.margin import split_paragraphs
from iterthink.db.session import session_scope
from iterthink.persistence import impact_annotations as impact_ann
from iterthink.persistence import version_storage
from iterthink.services import impact_analysis_runner
from iterthink.services.impact_analysis_runner import _impact_debug_llm_enabled
from iterthink.studio.constants import KI_PILL_TEXT_SIZE, TAB_FUTURE
from iterthink.studio.tree import build_md_tree
from iterthink.studio.util import ctrl_on_page as _ctrl_on_page


class MarkdownStudioImpactMixin:
    """Expects MarkdownStudio fields: page, _db, current_path, _main_tab_index, _review_subtab_index,
    _ki_topic_index, _compare_editor, _compare_snapshot_version_id, _make_llm_backend,
    chat_model_for_requests, _impact_status_text, _impact_results_list,
    _pill_row_impact, _impact_ki_context_panel, _impact_summary_right,
    _right_chat_section, _chat_input_row, _impact_run_dock, _ki_sidebar_well,
    _active_compare_buffers, _rebuild_compare_paragraph_ui.
    """

    def _init_impact_ui_fields(self) -> None:
        self._impact_tab_initialized = False
        self._active_impact_prompt_id: str | None = None
        self._impact_context_file_cbs: dict[Path, ft.Checkbox] = {}
        self._impact_folder_rows: list[tuple[ft.Checkbox, list[Path]]] = []
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
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            elevation=0,
            expand=True,
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
        self._rebuild_impact_context_tree()
        self._impact_tab_initialized = True
        self._populate_impact_para_placeholders()

    def _populate_impact_para_placeholders(self) -> None:
        """Fill the paragraph listview with text-only rows (no chips) before any analysis runs."""
        para_lv = getattr(self, "_impact_para_listview", None)
        if para_lv is None:
            return
        paras, _ = self._impact_paragraphs_for_display()
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

    def _files_under_node(self, node: dict[str, Any], exclude_resolved: Path | None) -> list[Path]:
        """Resolved paths of selectable context files under *node* (excludes the open document)."""
        out: list[Path] = []
        for _name, fpath in node.get("_files", []):
            r = fpath.resolve()
            if exclude_resolved is not None and r == exclude_resolved:
                continue
            out.append(r)
        for key, sub in node.items():
            if key != "_files" and isinstance(sub, dict):
                out.extend(self._files_under_node(sub, exclude_resolved))
        return out

    def _rebuild_impact_context_tree(self) -> None:
        scroll = getattr(self, "_impact_ki_context_scroll", None)
        if scroll is None:
            return
        scroll.controls.clear()
        self._impact_context_file_cbs.clear()
        self._impact_folder_rows.clear()

        root = config.DOCUMENTS
        if not root.is_dir():
            if _ctrl_on_page(scroll):
                scroll.update()
            return

        cur = getattr(self, "current_path", None)
        exclude_res = cur.resolve() if cur else None

        try:
            tree = build_md_tree(root)
        except Exception:  # noqa: BLE001
            if _ctrl_on_page(scroll):
                scroll.update()
            return

        def build_node(node: dict[str, Any], depth: int, path_parts: tuple[str, ...]) -> list[ft.Control]:
            rows: list[ft.Control] = []
            subdirs = sorted(
                [k for k in node if k != "_files" and isinstance(node[k], dict)],
                key=str.casefold,
            )
            files_here = list(node.get("_files", []))
            files_here.sort(key=lambda x: x[0].casefold())
            pad = ft.padding.only(left=max(0, depth) * 12, top=0, bottom=0)
            for fname, fpath in files_here:
                r = fpath.resolve()
                if exclude_res is not None and r == exclude_res:
                    continue
                if r in self._impact_context_file_cbs:
                    rows.append(
                        ft.Container(
                            content=ft.Row(
                                [
                                    ft.Icon(ft.Icons.LINK, size=14, color=config.ON_SURFACE_VARIANT),
                                    ft.Text(
                                        fname,
                                        size=11,
                                        color=config.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=6,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            padding=pad,
                            height=28,
                            tooltip=f"Same file as elsewhere in this list ({fpath.name}); use the checkbox above.",
                        )
                    )
                    continue
                cb = ft.Checkbox(
                    value=True,
                    label=fname,
                    label_style=ft.TextStyle(size=11, color=config.ON_SURFACE),
                    fill_color=config.PRIMARY_COLOR,
                    check_color=config.ON_PRIMARY,
                    scale=0.88,
                    tooltip=str(fpath),
                    on_change=lambda _e, fp=fpath: self._on_impact_file_checkbox_change(fp),
                )
                self._impact_context_file_cbs[r] = cb
                rows.append(
                    ft.Container(
                        content=cb,
                        padding=pad,
                        height=28,
                    )
                )
            for key in subdirs:
                sub = node[key]
                desc_paths = self._files_under_node(sub, exclude_res)
                if not desc_paths:
                    continue
                folder_cb = ft.Checkbox(
                    tristate=True,
                    value=True,
                    label=key,
                    label_style=ft.TextStyle(size=11, color=config.ON_SURFACE),
                    fill_color=config.PRIMARY_COLOR,
                    check_color=config.ON_PRIMARY,
                    scale=0.88,
                )
                folder_cb.on_change = (
                    lambda e, desc=list(desc_paths), fc=folder_cb: self._on_impact_folder_checkbox_change(
                        e, desc, fc
                    )
                )
                self._impact_folder_rows.append((folder_cb, desc_paths))
                rows.append(
                    ft.Container(
                        content=folder_cb,
                        padding=pad,
                        height=28,
                    )
                )
                # Flatten subtree into this Column: nested Column inside the fixed-height
                # scroll Column often gets zero height in Flet, hiding folder contents (e.g. SIA Norms).
                rows.extend(build_node(sub, depth + 1, (*path_parts, key)))
            return rows

        scroll.controls.extend(build_node(tree, 0, ()))
        self._impact_refresh_folder_states()
        if _ctrl_on_page(scroll):
            scroll.update()

    def _impact_aggregate_paths(self, paths: list[Path]) -> bool | None:
        vals: list[bool] = []
        for p in paths:
            cb = self._impact_context_file_cbs.get(p)
            if cb is not None:
                vals.append(cb.value is True)
        if not vals:
            return False
        if all(vals):
            return True
        if not any(vals):
            return False
        return None

    def _impact_refresh_folder_states(self) -> None:
        for folder_cb, desc_paths in self._impact_folder_rows:
            st = self._impact_aggregate_paths(desc_paths)
            if folder_cb.value != st:
                folder_cb.value = st
            muted = st is None
            folder_cb.label_style = ft.TextStyle(
                size=11,
                color=config.ON_SURFACE_VARIANT if muted else config.ON_SURFACE,
            )
            if _ctrl_on_page(folder_cb):
                folder_cb.update()

    def _on_impact_file_checkbox_change(self, _path: Path) -> None:
        self._impact_refresh_folder_states()

    def _on_impact_folder_checkbox_change(
        self,
        e: ft.ControlEvent,
        desc_paths: list[Path],
        folder_cb: ft.Checkbox | None = None,
    ) -> None:
        cb = folder_cb if folder_cb is not None else e.control
        v = cb.value
        if v is True:
            for p in desc_paths:
                c = self._impact_context_file_cbs.get(p)
                if c is not None:
                    c.value = True
        elif v is False:
            for p in desc_paths:
                c = self._impact_context_file_cbs.get(p)
                if c is not None:
                    c.value = False
        else:
            for p in desc_paths:
                c = self._impact_context_file_cbs.get(p)
                if c is not None:
                    c.value = True
            cb.value = True
        for p in desc_paths:
            c = self._impact_context_file_cbs.get(p)
            if c is not None and _ctrl_on_page(c):
                c.update()
        self._impact_refresh_folder_states()

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
        ki_analyse = int(getattr(self, "_ki_topic_index", 0)) == 2
        # Context file tree: Review → Impact and KI Analyse only (same as run dock intent).
        context_on = impact_subtab and ki_analyse
        prompt_ready = impact_subtab and self._active_impact_prompt_id is not None
        # Run dock: any time Review → Impact and KI "Analyse" topic (index 2). Prompt is optional for
        # visibility; the button stays disabled until a check pill is selected.
        show_impact_run_dock = impact_subtab and ki_analyse

        chat_row = getattr(self, "_chat_input_row", None)
        run_dock = getattr(self, "_impact_run_dock", None)
        if chat_row is not None and chat_row.visible != (not show_impact_run_dock):
            chat_row.visible = not show_impact_run_dock
            if _ctrl_on_page(chat_row):
                chat_row.update()
        if run_dock is not None and run_dock.visible != show_impact_run_dock:
            run_dock.visible = show_impact_run_dock
            if _ctrl_on_page(run_dock):
                run_dock.update()

        run_btn = getattr(self, "_impact_run_btn", None)
        if run_btn is not None:
            dis = show_impact_run_dock and not self._active_impact_prompt_id
            if bool(getattr(run_btn, "disabled", False)) != dis:
                run_btn.disabled = dis
                if _ctrl_on_page(run_btn):
                    run_btn.update()

        analyse_pills = getattr(self, "_pill_row_analyse", None)
        if analyse_pills is not None:
            want_vis = not impact_subtab
            if analyse_pills.visible != want_vis:
                analyse_pills.visible = want_vis
                if _ctrl_on_page(analyse_pills):
                    analyse_pills.update()

        prompt_sec = getattr(self, "_impact_analyse_section", None)
        if prompt_sec is not None and prompt_sec.visible != impact_subtab:
            prompt_sec.visible = impact_subtab
            if _ctrl_on_page(prompt_sec):
                prompt_sec.update()

        panel = getattr(self, "_impact_ki_context_panel", None)
        title = getattr(self, "_impact_ki_context_title", None)
        if panel is not None and panel.visible != context_on:
            panel.visible = context_on
            if _ctrl_on_page(panel):
                panel.update()
        if title is not None and title.visible != context_on:
            title.visible = context_on
            if _ctrl_on_page(title):
                title.update()
        scroll = getattr(self, "_impact_ki_context_scroll", None)
        if scroll is not None and scroll.visible != context_on:
            scroll.visible = context_on
            if _ctrl_on_page(scroll):
                scroll.update()
        if (
            context_on
            and scroll is not None
            and not scroll.controls
            and getattr(self, "_impact_tab_initialized", False)
        ):
            self._rebuild_impact_context_tree()
        summary_r = getattr(self, "_impact_summary_right", None)
        if summary_r is not None:
            has_txt = bool(
                getattr(self, "_impact_summary_right_text", None) and self._impact_summary_right_text.value
            )
            want = impact_subtab and has_txt
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
        paths = [p for p, cb in self._impact_context_file_cbs.items() if cb.value is True]
        if not paths:
            return []
        with session_scope() as s:
            ids: list[int] = []
            for p in paths:
                doc = version_storage.get_or_create_document(s, p)
                ids.append(int(doc.id))
            s.commit()
            return ids

    @staticmethod
    def _impact_status_color(st: str) -> str:
        if st == "risk":
            return "#E57373"
        if st == "changed":
            return "#FFB74D"
        if st == "stable":
            return "#81C784"
        return "#9AA0A6"

    @staticmethod
    def _impact_progress_row_dict(
        *,
        paragraph_index: int,
        prompt_id: str,
        document_id: int,
        version_id: int,
        status: str,
        comment: str,
        details: dict | None,
    ) -> dict[str, Any]:
        """Same keys as impact_ann.snapshot_row_ui for list rows during an in-flight run."""
        return {
            "status": str(status),
            "effective_comment": (comment or "").strip(),
            "details": details,
            "document_id": int(document_id),
            "version_id": int(version_id),
            "paragraph_index": int(paragraph_index),
            "prompt_id": str(prompt_id),
        }

    def _impact_paragraphs_for_display(self) -> tuple[list[str], bool]:
        """Aligned new-side paragraphs for Review; baseline-only fallback when proposal is empty."""
        try:
            buffers = self._active_compare_buffers()
            baseline = buffers.baseline or ""
            candidate = buffers.candidate or ""
            pairs = aligned_compare_pairs(baseline, candidate)
            news = [new for _, new in pairs] if pairs else []
            if news and any((p or "").strip() for p in news):
                return news, False
            base_only = [p for p in split_paragraphs(baseline) if (p or "").strip()]
            return base_only, bool(base_only)
        except Exception:  # noqa: BLE001
            return [], True

    def _get_candidate_paragraphs(self) -> list[str]:
        paras, _ = self._impact_paragraphs_for_display()
        return paras

    def _build_impact_para_row_pending(self, idx: int, para_text: str) -> ft.Container:
        from iterthink.studio import ui_theme
        from iterthink.studio.constants import COMPARE_COL_FONT_SIZE

        chip = ft.Container(
            content=ft.ProgressRing(
                width=16,
                height=16,
                stroke_width=2,
                color=config.PRIMARY_COLOR,
            ),
            width=54,
            height=24,
            alignment=ft.Alignment.CENTER,
        )
        snip = (para_text or "").strip()
        para_ctrl = ft.Text(
            snip if snip else " ",
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

    def _build_impact_para_row(
        self,
        idx: int,
        para_text: str,
        ann_row: dict[str, Any] | None,
    ) -> ft.Container:
        """One row: [symbol chip | paragraph text (monospace)]. *ann_row* is snapshot_row_ui shape or None."""
        from iterthink.studio import ui_theme
        from iterthink.studio.constants import COMPARE_COL_FONT_SIZE

        if ann_row is not None:
            st = str(ann_row.get("status", "") or "")
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
            tip = str(ann_row.get("effective_comment", "") or "").strip()
            chip_tooltip = tip or None
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

        snip = (para_text or "").strip()
        para_ctrl = ft.Text(
            snip if snip else " ",
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
        overlay = getattr(self, "_impact_result_card_overlay", None)
        if overlay is None:
            return

        prompt_id = self._active_impact_prompt_id
        if not prompt_id:
            return

        cur = getattr(self, "current_path", None)
        if not cur:
            return

        snap: dict[str, Any] | None = None
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
                        row = m.get(idx)
                        if row is not None:
                            snap = impact_ann.snapshot_row_ui(row)
        except Exception:  # noqa: BLE001
            pass

        if snap is None:
            return

        st = str(snap["status"])
        color = self._impact_status_color(st)
        eff = str(snap.get("effective_comment", "") or "").strip()

        header = ft.Row(
            [
                ft.Container(
                    content=ft.Text(st, size=14, weight=ft.FontWeight.W_700, color=color),
                    width=60,
                    height=28,
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
                    spacing=0,
                    tight=True,
                    expand=True,
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
        det = snap.get("details")
        if isinstance(det, dict) and det:
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
            on_click=lambda _e, s=snap: self._on_impact_override_click(s),
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

        ann_map: dict[int, dict[str, Any]] = {}
        with session_scope() as s:
            doc = version_storage.get_document_by_resolved_path(s, cur.resolve())
            if doc is not None:
                vid = self._resolve_impact_version_id(s)
                if vid is not None:
                    raw = impact_ann.list_for_version(
                        s,
                        document_id=int(doc.id),
                        version_id=int(vid),
                        prompt_id=prompt_id,
                    )
                    ann_map = {i: impact_ann.snapshot_row_ui(r) for i, r in raw.items()}

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

    def _on_impact_override_click(self, snap: dict[str, Any]) -> None:
        tf = ft.TextField(
            value=str(snap.get("effective_comment", "") or ""),
            dense=True,
            multiline=True,
            min_lines=2,
            max_lines=5,
            expand=True,
        )

        def close_dlg() -> None:
            self.page.pop_dialog()

        def save(_e: ft.ControlEvent | None) -> None:
            with session_scope() as s:
                impact_ann.set_override(
                    s,
                    document_id=int(snap["document_id"]),
                    version_id=int(snap["version_id"]),
                    paragraph_index=int(snap["paragraph_index"]),
                    prompt_id=str(snap["prompt_id"]),
                    override_comment=tf.value or "",
                )
            close_dlg()
            self._refresh_impact_annotations_ui(str(snap["prompt_id"]))

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
            print("[impact] Run blocked: no Impact prompt selected (click a pill under Analyse).", file=sys.stderr, flush=True)
            self._snack("Select an Impact check first.")
            return
        cur = getattr(self, "current_path", None)
        if not cur:
            print("[impact] Run blocked: no file open.", file=sys.stderr, flush=True)
            self._snack("Open a note first.")
            return
        ctx_ids = self._selected_impact_context_document_ids()
        if not ctx_ids:
            n_files = len(getattr(self, "_impact_context_file_cbs", {}) or {})
            print(
                f"[impact] Run blocked: no context files selected (tree has {n_files} selectable .md rows).",
                file=sys.stderr,
                flush=True,
            )
            self._snack("Select at least one context file.")
            return
        if not getattr(self, "_compare_right_fields", None):
            self._rebuild_compare_paragraph_ui()
        paragraphs, _ann_stale = self._impact_paragraphs_for_display()
        if not any((x or "").strip() for x in paragraphs):
            print(
                "[impact] Run blocked: no non-empty paragraphs in the current note "
                "(empty document or compare buffers).",
                file=sys.stderr,
                flush=True,
            )
            self._snack("Nothing to analyse.")
            return

        print(
            "[impact] Run analysis starting (stderr always; set ITERTHINK_DEBUG_IMPACT=1 for full LLM payloads)",
            file=sys.stderr,
            flush=True,
        )

        self._impact_summary_cache = ""
        if getattr(self, "_impact_summary_right_text", None):
            self._impact_summary_right_text.value = ""
            if _ctrl_on_page(self._impact_summary_right_text):
                self._impact_summary_right_text.update()
        if getattr(self, "_impact_summary_right", None):
            self._impact_summary_right.visible = False
            if _ctrl_on_page(self._impact_summary_right):
                self._impact_summary_right.update()
        self._sync_impact_ki_context_visibility()

        para_lv = getattr(self, "_impact_para_listview", None)
        if para_lv is not None:
            para_lv.controls.clear()
            for i, pt in enumerate(paragraphs):
                if (pt or "").strip():
                    para_lv.controls.append(self._build_impact_para_row_pending(i, pt))
                else:
                    para_lv.controls.append(self._build_impact_para_row(i, pt, None))
            if _ctrl_on_page(para_lv):
                para_lv.update()

        self._impact_run_gen += 1
        gen = self._impact_run_gen
        self._impact_run_spinner.visible = True
        if _ctrl_on_page(self._impact_run_spinner):
            self._impact_run_spinner.update()

        if self._impact_status_text:
            self._impact_status_text.value = "Running analysis…"
            if _ctrl_on_page(self._impact_status_text):
                self._impact_status_text.update()

        if _impact_debug_llm_enabled():
            n_work = sum(1 for p in paragraphs if (p or "").strip())
            print(
                f"\n[impact] run start check={act.id!r} context_doc_ids={ctx_ids!r} "
                f"paragraph_rows={len(paragraphs)} non_empty={n_work}",
                file=sys.stderr,
                flush=True,
            )

        try:
            with session_scope() as s:
                target_doc = version_storage.get_or_create_document(s, cur.resolve())
                target_did = int(target_doc.id)
                vid = self._resolve_impact_version_id(s)
                if vid is None:
                    self._snack("No saved version for this note — save or switch version first.")
                    self._refresh_impact_annotations_ui(act.id)
                    return
                s.commit()

            async def on_progress(idx: int, payload: dict | None, err: str | None) -> None:
                if gen != self._impact_run_gen:
                    return
                if self._impact_status_text:
                    self._impact_status_text.value = f"Paragraph {idx + 1}…" + (f" ({err})" if err else "")
                    if _ctrl_on_page(self._impact_status_text):
                        self._impact_status_text.update()
                lv = getattr(self, "_impact_para_listview", None)
                if lv is None or idx >= len(lv.controls):
                    return
                pt = paragraphs[idx] if idx < len(paragraphs) else ""
                if not (pt or "").strip():
                    lv.controls[idx] = self._build_impact_para_row(idx, pt, None)
                    if _ctrl_on_page(lv):
                        lv.update()
                    return
                if payload is not None:
                    det = payload.get("details") if isinstance(payload.get("details"), dict) else None
                    ann = self._impact_progress_row_dict(
                        paragraph_index=idx,
                        prompt_id=act.id,
                        document_id=target_did,
                        version_id=int(vid),
                        status=str(payload.get("status", "")),
                        comment=str(payload.get("comment", "")),
                        details=det,
                    )
                    lv.controls[idx] = self._build_impact_para_row(idx, pt, ann)
                elif err:
                    er = self._impact_progress_row_dict(
                        paragraph_index=idx,
                        prompt_id=act.id,
                        document_id=target_did,
                        version_id=int(vid),
                        status="risk",
                        comment=str(err),
                        details=None,
                    )
                    lv.controls[idx] = self._build_impact_para_row(idx, pt, er)
                else:
                    lv.controls[idx] = self._build_impact_para_row(idx, pt, None)
                if _ctrl_on_page(lv):
                    lv.update()

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
            self._sync_impact_ki_context_visibility()

            if self._impact_status_text:
                self._impact_status_text.value = (
                    f"Done.{(' Summary error: ' + s_err) if s_err else ''}"
                )
                if _ctrl_on_page(self._impact_status_text):
                    self._impact_status_text.update()
            self._refresh_impact_annotations_ui(act.id)
        except Exception as exc:
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            self._snack(f"Impact analysis failed: {exc}")
            try:
                self._refresh_impact_annotations_ui(act.id)
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._impact_run_spinner.visible = False
            if _ctrl_on_page(self._impact_run_spinner):
                self._impact_run_spinner.update()
