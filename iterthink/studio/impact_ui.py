"""Review → Impact subtab: prompts, context file pickers, parallel analysis, summary."""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path
from typing import Any

import flet as ft

from iterthink import config, impact_checks
from iterthink.impact_checks import (
    FINDINGS_CHECK_IDS,
    FINDINGS_PARAGRAPH_STATUSES,
    VALID_STATUSES,
)
from iterthink.compare.layout import aligned_compare_pairs
from iterthink.compare.margin import split_paragraphs
from iterthink.db.session import session_scope
from iterthink.persistence import impact_annotations as impact_ann
from iterthink.persistence import content_repo
from iterthink.contract.document_classification import classify_document
from iterthink.services.impact_context_scope import (
    default_context_paths,
    project_scoped_paths,
)
from iterthink.services.rag import impact_override
from iterthink.services.rag.project_scope import project_slug_for_path
from iterthink.studio.constants import (
    KI_PILL_TEXT_SIZE,
    KI_TOPIC_ANALYSE,
    RESULT_CARD_HIDE_DELAY_SEC,
    TAB_FUTURE,
)
from iterthink.studio.tree import build_md_tree
from iterthink.studio.util import ctrl_on_page as _ctrl_on_page


class MarkdownStudioImpactMixin:
    """Expects MarkdownStudio fields: page, _db, current_path, _main_tab_index, _review_subtab_index,
    _ki_topic_index, _compare_editor, _compare_snapshot_version_id, _make_llm_backend,
    chat_model_for_requests, _impact_status_text, _impact_results_list,
    _pill_row_impact, _impact_ki_context_panel, _impact_summary_right,
    _right_chat_section, _chat_input_row, _impact_run_dock, _ki_sidebar_well,
    _active_compare_buffers, _rebuild_compare_paragraph_ui (provided by ``iterthink.studio.history``).
    """

    def _init_impact_ui_fields(self) -> None:
        self._impact_tab_initialized = False
        self._active_impact_prompt_id: str | None = None
        self._impact_context_file_cbs: dict[Path, ft.Checkbox] = {}
        self._impact_context_manual_overrides: dict[Path, bool] = {}
        self._impact_folder_rows: list[tuple[ft.Checkbox, list[Path]]] = []
        self._impact_run_gen = 0
        self._impact_result_card_hide_gen = 0
        self._impact_result_card_visible_for: int | None = None
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

    def _impact_default_paths(self) -> set[Path]:
        cur = getattr(self, "current_path", None)
        check_id = self._active_impact_prompt_id
        if check_id:
            paths = default_context_paths(check_id=str(check_id), target_path=cur)
        else:
            paths = project_scoped_paths(target_path=cur)
        return {p.resolve() for p in paths}

    def _impact_visible_paths(self) -> set[Path]:
        cur = getattr(self, "current_path", None)
        check_id = self._active_impact_prompt_id
        if check_id:
            return {p.resolve() for p in default_context_paths(check_id=str(check_id), target_path=cur)}
        return {p.resolve() for p in project_scoped_paths(target_path=cur)}

    def _impact_path_checked(self, resolved: Path) -> bool:
        if resolved in self._impact_context_manual_overrides:
            return self._impact_context_manual_overrides[resolved]
        return resolved in self._impact_default_paths()

    def _impact_context_tooltip(self, fpath: Path) -> str:
        cl = classify_document(fpath.resolve())
        fn = ", ".join(cl.document_functions) if cl.document_functions else "—"
        bits = [str(fpath)]
        if cl.kbob_code:
            bits.append(f"KBOB {cl.kbob_code}")
        bits.append(fn)
        return "\n".join(bits)

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
        if exclude_res != getattr(self, "_impact_context_scope_path", None):
            self._impact_context_manual_overrides.clear()
            self._impact_context_scope_path = exclude_res
        visible = self._impact_visible_paths()

        title = getattr(self, "_impact_ki_context_title", None)
        if title is not None:
            slug = project_slug_for_path(cur) if cur else None
            check = self._active_impact_prompt_id
            label = f"Context — {slug or 'workspace'}"
            if check:
                label += f" ({check})"
            if title.value != label:
                title.value = label
                if _ctrl_on_page(title):
                    title.update()

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
                if r not in visible:
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
                    value=self._impact_path_checked(r),
                    label=fname,
                    label_style=ft.TextStyle(size=11, color=config.ON_SURFACE),
                    fill_color=config.PRIMARY_COLOR,
                    check_color=config.ON_PRIMARY,
                    scale=0.88,
                    tooltip=self._impact_context_tooltip(fpath),
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
                desc_paths = [p for p in self._files_under_node(sub, exclude_res) if p.resolve() in visible]
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

    def _on_impact_file_checkbox_change(self, path: Path) -> None:
        r = path.resolve()
        cb = self._impact_context_file_cbs.get(r)
        if cb is not None:
            self._impact_context_manual_overrides[r] = cb.value is True
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
        if getattr(self, "_impact_tab_initialized", False):
            self._rebuild_impact_context_tree()
        self._refresh_impact_annotations_ui(action_id)
        self._sync_impact_ki_context_visibility()
        if hasattr(self, "_impact_status_text") and self._impact_status_text:
            if not (getattr(self, "current_path", None)):
                self._impact_status_text.value = "Open a note first."
            else:
                self._impact_status_text.value = "Select context files, then Run analysis."
            if _ctrl_on_page(self._impact_status_text):
                self._impact_status_text.update()

    def _sync_impact_ki_context_visibility(self) -> None:
        impact_subtab = (
            getattr(self, "_main_tab_index", -1) == TAB_FUTURE
            and getattr(self, "_review_subtab_index", 0) == 1
        )
        ki_analyse = int(getattr(self, "_ki_topic_index", 0)) == KI_TOPIC_ANALYSE
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

    def _resolve_impact_version_id(self, session: Any) -> int | None:
        if getattr(self, "_compare_snapshot_version_id", None) is not None:
            return int(self._compare_snapshot_version_id)
        cur = getattr(self, "current_path", None)
        if not cur:
            return None
        resolved = cur.resolve()
        snaps = content_repo.list_snapshots(session, resolved)
        if snaps:
            return snaps[0].version_id
        body = ""
        if hasattr(self, "editor") and self.editor is not None:
            body = self.editor.value or ""
        if not (body or "").strip():
            try:
                buffers = self._active_compare_buffers()
                baseline = buffers.baseline or ""
                candidate = buffers.candidate or ""
                body = candidate if (candidate or "").strip() else baseline
            except Exception:  # noqa: BLE001
                body = ""
        if not (body or "").strip():
            return None
        vid = content_repo.persist_version_snapshot(
            session, resolved, body, "manual", skip_if_unchanged_sha=False
        )
        return int(vid) if vid is not None else None

    def _selected_impact_context_document_ids(self) -> list[int]:
        paths = [p for p, cb in self._impact_context_file_cbs.items() if cb.value is True]
        if not paths:
            return []
        with session_scope() as s:
            ids: list[int] = []
            for p in paths:
                doc = content_repo.get_or_create_document(s, p)
                ids.append(int(doc.id))
            s.commit()
            return ids

    @staticmethod
    def _impact_status_color(st: str) -> str:
        if st == "risk" or st == "error":
            return "#E57373"
        if st == "changed" or st == "warning":
            return "#FFB74D"
        if st == "stable" or st == "ok":
            return "#81C784"
        if st == "not_applicable":
            return "#9AA0A6"
        return "#9AA0A6"

    @staticmethod
    def _impact_findings_main_icon(st: str):
        if st == "ok":
            return ft.Icons.CHECK_CIRCLE
        if st == "warning":
            return ft.Icons.WARNING_AMBER_ROUNDED
        if st == "error":
            return ft.Icons.ERROR_OUTLINE
        if st == "not_applicable":
            return ft.Icons.REMOVE_CIRCLE_OUTLINE
        return ft.Icons.HELP_OUTLINE

    @staticmethod
    def _finding_detail_key_order() -> tuple[str, ...]:
        return (
            "type",
            "severity",
            "claim",
            "norm_ref",
            "expected",
            "found",
            "this_states",
            "context_states",
            "source_document",
            "source_excerpt",
            "action",
        )

    @staticmethod
    def _impact_progress_row_dict(
        *,
        paragraph_index: int,
        prompt_id: str,
        content_version_id: int,
        status: str,
        comment: str,
        details: dict | None,
        overridden: bool = False,
    ) -> dict[str, Any]:
        """Same keys as impact_ann.snapshot_row_ui for list rows during an in-flight run."""
        return {
            "status": str(status),
            "effective_comment": (comment or "").strip(),
            "details": details,
            "content_version_id": int(content_version_id),
            "paragraph_index": int(paragraph_index),
            "prompt_id": str(prompt_id),
            "overridden": bool(overridden),
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
            return base_only, False
        except Exception:  # noqa: BLE001
            return [], True

    def _get_candidate_paragraphs(self) -> list[str]:
        paras, _ = self._impact_paragraphs_for_display()
        return paras

    @staticmethod
    def _build_finding_type_tags(findings: list[dict]) -> ft.Row | None:
        """Small colored pills summarising finding types (skips 'ok'). Returns None if nothing to show."""
        from collections import Counter

        sev_color = {"error": "#E57373", "warning": "#FFB74D", "info": "#9AA0A6"}
        counts: Counter[str] = Counter()
        type_sev: dict[str, str] = {}
        for fd in findings:
            ftype = fd.get("type", "")
            if not ftype or ftype == "ok":
                continue
            counts[ftype] += 1
            type_sev[ftype] = fd.get("severity", "info")

        if not counts:
            return None

        pills: list[ft.Control] = []
        for ftype, count in counts.most_common():
            sev = type_sev.get(ftype, "info")
            color = sev_color.get(sev, "#9AA0A6")
            label = f"{ftype} ×{count}" if count > 1 else ftype
            pills.append(
                ft.Container(
                    content=ft.Text(
                        label,
                        size=9,
                        color=color,
                        no_wrap=True,
                        weight=ft.FontWeight.W_500,
                    ),
                    padding=ft.padding.symmetric(horizontal=5, vertical=1),
                    border_radius=3,
                    bgcolor=ft.Colors.with_opacity(0.13, color),
                    border=ft.border.all(1, ft.Colors.with_opacity(0.35, color)),
                )
            )

        return ft.Row(pills, spacing=4, tight=True, wrap=True)

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
            tip = str(ann_row.get("effective_comment", "") or "").strip()
            chip_tooltip = tip or None
            det_lc = ann_row.get("details")
            low_conf = (
                isinstance(det_lc, dict)
                and det_lc.get("low_confidence") is True
            )
            if st in FINDINGS_PARAGRAPH_STATUSES:
                main_ic = ft.Icon(self._impact_findings_main_icon(st), size=20, color=color)
                chip_children: list[ft.Control] = [main_ic]
                if low_conf:
                    chip_children.append(
                        ft.Icon(ft.Icons.HELP_OUTLINE, size=11, color="#FFB74D"),
                    )
                chip_content = ft.Row(
                    chip_children,
                    tight=True,
                    spacing=2,
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            else:
                chip_content = ft.Text(
                    st,
                    size=12,
                    weight=ft.FontWeight.W_700,
                    color=color,
                    no_wrap=True,
                )
            chip_bg = ft.Colors.with_opacity(0.14, color)
            if ann_row.get("overridden"):
                chip_border = ft.border.all(1.5, ft.Colors.with_opacity(0.75, color))
            else:
                chip_border = ft.border.all(1, ft.Colors.with_opacity(0.45, color))
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

        # Build inline finding-type tag pills from findings.
        tag_row: ft.Control | None = None
        if ann_row is not None:
            det = ann_row.get("details")
            if isinstance(det, dict):
                finds = det.get("findings")
                if isinstance(finds, list) and finds:
                    tag_row = self._build_finding_type_tags(finds)

        if tag_row is not None:
            right_col: ft.Control = ft.Column(
                [para_ctrl, tag_row],
                spacing=3,
                tight=True,
                expand=True,
            )
        else:
            right_col = para_ctrl

        def _on_hover(e: ft.HoverEvent, i: int = idx) -> None:
            if str(e.data).lower() == "true":
                self._show_impact_result_card(i)
            else:
                self._schedule_hide_impact_result_card()

        return ft.Container(
            content=ft.Row(
                [chip, right_col],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=ft.padding.symmetric(horizontal=6, vertical=4),
            on_hover=_on_hover if ann_row is not None else None,
        )

    def _impact_status_options(self, prompt_id: str) -> tuple[str, ...]:
        if prompt_id in FINDINGS_CHECK_IDS:
            return tuple(sorted(FINDINGS_PARAGRAPH_STATUSES))
        return tuple(sorted(VALID_STATUSES))

    def _load_impact_snap(self, idx: int) -> dict[str, Any] | None:
        prompt_id = self._active_impact_prompt_id
        cur = getattr(self, "current_path", None)
        if not prompt_id or not cur:
            return None
        try:
            with session_scope() as s:
                doc = content_repo.get_document_by_resolved_path(s, cur.resolve())
                if doc is None:
                    return None
                vid = self._resolve_impact_version_id(s)
                if vid is None:
                    return None
                m = impact_ann.list_for_version(
                    s,
                    content_version_id=int(vid),
                    prompt_id=prompt_id,
                )
                row = m.get(idx)
                if row is None:
                    return None
                return impact_ann.snapshot_row_ui(row)
        except Exception:  # noqa: BLE001
            return None

    def _refresh_impact_para_row(self, idx: int) -> None:
        para_lv = getattr(self, "_impact_para_listview", None)
        if para_lv is None or not (0 <= idx < len(para_lv.controls)):
            return
        paras, _ = self._impact_paragraphs_for_display()
        pt = paras[idx] if idx < len(paras) else ""
        snap = self._load_impact_snap(idx)
        para_lv.controls[idx] = self._build_impact_para_row(idx, pt, snap)
        if _ctrl_on_page(para_lv):
            para_lv.update()

    def _schedule_hide_impact_result_card(self) -> None:
        self._impact_result_card_hide_gen += 1
        gen = self._impact_result_card_hide_gen
        self.page.run_task(self._hide_impact_result_card_after_delay, gen)

    async def _hide_impact_result_card_after_delay(self, gen: int) -> None:
        await asyncio.sleep(RESULT_CARD_HIDE_DELAY_SEC)
        if gen != self._impact_result_card_hide_gen:
            return
        self._impact_result_card_visible_for = None
        self._hide_impact_result_card()

    def _on_impact_result_card_hover(self, e: ft.ControlEvent) -> None:
        if str(e.data).lower() == "true":
            self._impact_result_card_hide_gen += 1
        else:
            self._schedule_hide_impact_result_card()

    def _impact_status_badge_content(self, st: str, color: str) -> ft.Control:
        if st in FINDINGS_PARAGRAPH_STATUSES:
            return ft.Row(
                [
                    ft.Icon(self._impact_findings_main_icon(st), size=18, color=color),
                    ft.Text(st, size=12, weight=ft.FontWeight.W_700, color=color),
                ],
                tight=True,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        return ft.Text(st, size=14, weight=ft.FontWeight.W_700, color=color)

    def _build_impact_status_badge(
        self,
        idx: int,
        snap: dict[str, Any],
        *,
        st: str,
        color: str,
    ) -> ft.Control:
        badge_inner = self._impact_status_badge_content(st, color)
        menu_items: list[ft.PopupMenuItem] = []
        for opt in self._impact_status_options(str(snap.get("prompt_id", ""))):
            menu_items.append(
                ft.PopupMenuItem(
                    content=ft.Text(opt, size=13),
                    on_click=lambda _e, o=opt: self.page.run_task(
                        self._persist_impact_override_async,
                        snap,
                        o,
                        None,
                        idx,
                    ),
                )
            )
        if snap.get("overridden"):
            menu_items.append(ft.PopupMenuItem())  # divider
            menu_items.append(
                ft.PopupMenuItem(
                    content=ft.Text("Reset to model", size=13),
                    on_click=lambda _e: self.page.run_task(
                        self._clear_impact_override_async,
                        snap,
                        idx,
                    ),
                )
            )
        return ft.PopupMenuButton(
            content=ft.Container(
                content=badge_inner,
                padding=ft.padding.symmetric(horizontal=6, vertical=2),
                height=28,
                alignment=ft.Alignment.CENTER,
                border_radius=6,
                bgcolor=ft.Colors.with_opacity(0.16, color),
            ),
            items=menu_items,
            tooltip="Change status",
        )

    def _build_impact_result_card(self, idx: int, snap: dict[str, Any]) -> ft.Control:
        st = str(snap["status"])
        color = self._impact_status_color(st)
        eff = str(snap.get("effective_comment", "") or "").strip()

        rec_tf = ft.TextField(
            value=eff,
            dense=True,
            multiline=True,
            min_lines=2,
            max_lines=6,
            text_size=12,
            expand=True,
            on_blur=lambda e, s=snap, i=idx: self.page.run_task(
                self._persist_impact_override_async,
                s,
                None,
                e.control.value,
                i,
            ),
            on_submit=lambda e, s=snap, i=idx: self.page.run_task(
                self._persist_impact_override_async,
                s,
                None,
                e.control.value,
                i,
            ),
        )

        header = ft.Row(
            [
                self._build_impact_status_badge(idx, snap, st=st, color=color),
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
        if snap.get("overridden"):
            model_st = str(snap.get("model_status", "") or "").strip()
            model_c = str(snap.get("model_comment", "") or "").strip()
            model_color = self._impact_status_color(model_st) if model_st else config.ON_SURFACE_VARIANT
            model_lines: list[ft.Control] = [
                ft.Text(
                    "Model suggestion",
                    size=10,
                    weight=ft.FontWeight.W_600,
                    color=config.ON_SURFACE_VARIANT,
                ),
            ]
            if model_st:
                model_lines.append(
                    ft.Text(
                        model_st,
                        size=12,
                        weight=ft.FontWeight.W_700,
                        color=model_color,
                    )
                )
            if model_c:
                model_lines.append(
                    ft.Text(model_c, size=11, color=config.ON_SURFACE, selectable=True)
                )
            rows.append(
                ft.Container(
                    content=ft.Column(model_lines, spacing=3, tight=True),
                    padding=ft.padding.all(8),
                    bgcolor=ft.Colors.with_opacity(0.06, config.ON_SURFACE),
                    border_radius=6,
                    border=ft.border.all(1, ft.Colors.with_opacity(0.2, config.OUTLINE)),
                )
            )
        rows.append(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "Your override" if snap.get("overridden") else "Recommendation",
                            size=10,
                            color=config.ON_SURFACE_VARIANT,
                        ),
                        rec_tf,
                    ],
                    spacing=4,
                    tight=True,
                ),
                padding=ft.padding.only(top=6),
            )
        )
        det = snap.get("details")
        if isinstance(det, dict) and det:
            if det.get("low_confidence") is True:
                rows.append(
                    ft.Container(
                        content=ft.Text(
                            "Low confidence: retrieved context may not match this paragraph.",
                            size=11,
                            color=config.ON_SURFACE_VARIANT,
                            selectable=True,
                        ),
                        padding=ft.padding.only(top=8),
                    )
                )
            nar = det.get("not_applicable_reason")
            if isinstance(nar, str) and nar.strip():
                rows.append(
                    ft.Container(
                        content=ft.Text(
                            f"Not applicable: {nar.strip()}",
                            size=12,
                            color=config.ON_SURFACE,
                            selectable=True,
                        ),
                        padding=ft.padding.only(top=6),
                    )
                )
            rep = det.get("paragraph_status_reported")
            if isinstance(rep, str) and rep.strip():
                rows.append(
                    ft.Container(
                        content=ft.Text(
                            f"Model paragraph_status (differs from stored): {rep.strip()}",
                            size=10,
                            color=config.ON_SURFACE_VARIANT,
                            selectable=True,
                        ),
                        padding=ft.padding.only(top=2),
                    )
                )
            finds = det.get("findings")
            if isinstance(finds, list) and finds:
                rows.append(
                    ft.Container(
                        content=ft.Text(
                            "Findings",
                            size=11,
                            weight=ft.FontWeight.W_600,
                            color=config.ON_SURFACE_VARIANT,
                        ),
                        padding=ft.padding.only(top=10, bottom=2),
                    )
                )
                key_order = self._finding_detail_key_order()
                for fi, fd in enumerate(finds):
                    if not isinstance(fd, dict):
                        continue
                    rows.append(
                        ft.Container(
                            content=ft.Text(
                                f"— Finding {fi + 1}",
                                size=11,
                                weight=ft.FontWeight.W_500,
                                color=config.ON_SURFACE,
                            ),
                            padding=ft.padding.only(top=6, left=2),
                        )
                    )
                    seen_k: set[str] = set()
                    for key in key_order:
                        if key not in fd:
                            continue
                        val = fd[key]
                        if val is None or (isinstance(val, str) and not val.strip()):
                            continue
                        seen_k.add(key)
                        label = key.replace("_", " ")
                        rows.append(
                            ft.Container(
                                content=ft.Text(
                                    f"{label}: {val}",
                                    size=11,
                                    color=config.ON_SURFACE,
                                    selectable=True,
                                ),
                                padding=ft.padding.only(left=10, bottom=2),
                            )
                        )
                    for key, val in fd.items():
                        if key in seen_k or key in ("type", "severity"):
                            continue
                        if val is None or (isinstance(val, str) and not val.strip()):
                            continue
                        label = str(key).replace("_", " ")
                        rows.append(
                            ft.Container(
                                content=ft.Text(
                                    f"{label}: {val}",
                                    size=11,
                                    color=config.ON_SURFACE,
                                    selectable=True,
                                ),
                                padding=ft.padding.only(left=10, bottom=2),
                            )
                        )
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
        return ft.Column(rows, spacing=2, tight=True, scroll=ft.ScrollMode.AUTO)

    def _show_impact_result_card(self, idx: int) -> None:
        overlay = getattr(self, "_impact_result_card_overlay", None)
        if overlay is None:
            return
        if not self._active_impact_prompt_id:
            return
        snap = self._load_impact_snap(idx)
        if snap is None:
            return
        self._impact_result_card_hide_gen += 1
        row_pitch = 88.0
        overlay.top = max(4.0, idx * row_pitch + 4.0)
        overlay.content = self._build_impact_result_card(idx, snap)
        overlay.visible = True
        self._impact_result_card_visible_for = idx
        if _ctrl_on_page(overlay):
            overlay.update()

    async def _persist_impact_override_async(
        self,
        snap: dict[str, Any],
        status: str | None,
        comment: str | None,
        idx: int,
    ) -> None:
        new_status = status if status is not None else str(snap.get("status", ""))
        new_comment = (
            comment
            if comment is not None
            else str(snap.get("effective_comment", "") or "")
        )
        paras, _ = self._impact_paragraphs_for_display()
        para_text = paras[idx] if 0 <= idx < len(paras) else ""
        doc_title = "Untitled"
        cur = getattr(self, "current_path", None)
        if cur is not None:
            try:
                from iterthink.services.rag.chunking import document_title

                body = cur.read_text(encoding="utf-8", errors="replace")
                doc_title = document_title(body, cur.name)
            except OSError:
                pass
        with session_scope() as s:
            impact_ann.set_override(
                s,
                content_version_id=int(snap["content_version_id"]),
                paragraph_index=int(snap["paragraph_index"]),
                prompt_id=str(snap["prompt_id"]),
                status=new_status,
                override_comment=new_comment or "",
            )
            s.commit()
        conn = getattr(self, "_db", None)
        if conn is not None:
            try:
                await impact_override.upsert_override_embedding(
                    conn,
                    content_version_id=int(snap["content_version_id"]),
                    paragraph_index=int(snap["paragraph_index"]),
                    prompt_id=str(snap["prompt_id"]),
                    paragraph_text=para_text,
                    status=new_status,
                    override_comment=new_comment or "",
                    doc_title=doc_title,
                )
            except BaseException:  # noqa: BLE001
                pass
        self._refresh_impact_para_row(idx)
        if self._impact_result_card_visible_for == idx:
            fresh = self._load_impact_snap(idx)
            if fresh is not None:
                self._show_impact_result_card(idx)

    async def _clear_impact_override_async(self, snap: dict[str, Any], idx: int) -> None:
        with session_scope() as s:
            impact_ann.clear_override(
                s,
                content_version_id=int(snap["content_version_id"]),
                paragraph_index=int(snap["paragraph_index"]),
                prompt_id=str(snap["prompt_id"]),
            )
            s.commit()
        conn = getattr(self, "_db", None)
        if conn is not None:
            try:
                impact_override.delete_override_embedding(
                    conn,
                    content_version_id=int(snap["content_version_id"]),
                    paragraph_index=int(snap["paragraph_index"]),
                    prompt_id=str(snap["prompt_id"]),
                )
            except BaseException:  # noqa: BLE001
                pass
        self._refresh_impact_para_row(idx)
        if self._impact_result_card_visible_for == idx:
            fresh = self._load_impact_snap(idx)
            if fresh is not None:
                self._show_impact_result_card(idx)

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
            doc = content_repo.get_document_by_resolved_path(s, cur.resolve())
            if doc is not None:
                vid = self._resolve_impact_version_id(s)
                if vid is not None:
                    raw = impact_ann.list_for_version(
                        s,
                        content_version_id=int(vid),
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

        from iterthink.services import impact_analysis_runner

        if impact_analysis_runner._impact_debug_llm_enabled():
            n_work = sum(1 for p in paragraphs if (p or "").strip())
            print(
                f"\n[impact] run start check={act.id!r} context_doc_ids={ctx_ids!r} "
                f"paragraph_rows={len(paragraphs)} non_empty={n_work}",
                file=sys.stderr,
                flush=True,
            )

        try:
            with session_scope() as s:
                target_doc = content_repo.get_or_create_document(s, cur.resolve())
                target_did = int(target_doc.id)
                vid = self._resolve_impact_version_id(s)
                if vid is None:
                    self._snack("No text to analyse in this note.")
                    self._refresh_impact_annotations_ui(act.id)
                    return
                s.commit()

            sample_para = next((p for p in paragraphs if (p or "").strip()), "")
            try:
                context_ready = await impact_analysis_runner.prepare_impact_context(
                    self._db,
                    context_document_ids=ctx_ids,
                    sample_paragraph=sample_para,
                    check_id=act.id,
                    target_document_id=target_did,
                    target_path=cur.resolve(),
                )
            except impact_analysis_runner.ImpactPreflightError as exc:
                self._snack(str(exc))
                if self._impact_status_text:
                    self._impact_status_text.value = str(exc)
                    if _ctrl_on_page(self._impact_status_text):
                        self._impact_status_text.update()
                self._impact_run_spinner.visible = False
                if _ctrl_on_page(self._impact_run_spinner):
                    self._impact_run_spinner.update()
                return

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
                        content_version_id=int(vid),
                        status=str(payload.get("status", "")),
                        comment=str(payload.get("comment", "")),
                        details=det,
                        overridden=bool(payload.get("overridden")),
                    )
                    lv.controls[idx] = self._build_impact_para_row(idx, pt, ann)
                elif err:
                    er = self._impact_progress_row_dict(
                        paragraph_index=idx,
                        prompt_id=act.id,
                        content_version_id=int(vid),
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
                target_path=cur.resolve(),
                context_ready=context_ready,
            )

            ann_lines: list[tuple[int, str, str, int]] = []
            for i, r in enumerate(results):
                if isinstance(r, dict):
                    det = r.get("details")
                    nref = 0
                    if isinstance(det, dict):
                        fnd = det.get("findings")
                        if isinstance(fnd, list):
                            nref = len(fnd)
                        else:
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
