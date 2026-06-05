"""Semantic search results in Focus view and search-mode sidebar."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import flet as ft

from iterthink import config
from iterthink.compare.margin import paragraph_offset_at_index
from iterthink.db.session import session_scope
from iterthink.persistence import store_db
from iterthink.services.rag.workspace_search import (
    SearchHit,
    parse_search_query,
    search_workspace,
    unique_files_from_hits,
)
from iterthink.services.rag.project_scope import project_slug_for_path
from iterthink.studio.tree import build_search_md_tree

from .explorer import MarkdownStudioExplorer
from .util import KI_TIER_LOCAL, ctrl_on_page as _ctrl_on_page, normalize_ki_tier


class MarkdownStudioSearchResults:
    _search_gen: int
    _search_hits: list[SearchHit]
    _search_results_host: ft.Container
    _search_results_list: ft.ListView
    _semantic_search_active: bool

    def _rag_search_enabled(self) -> bool:
        return config.RAG_SEARCH_ENABLED

    def _sync_rag_search_ui(self) -> None:
        """Show or hide sidebar search; clear state when disabled."""
        enabled = self._rag_search_enabled()
        bar = getattr(self, "_tree_search_bar", None)
        if bar is not None and getattr(bar, "visible", None) != enabled:
            bar.visible = enabled
        field = getattr(self, "tree_search_field", None)
        if not enabled:
            self._search_gen = getattr(self, "_search_gen", 0) + 1
            self._search_hits = []
            if field is not None and getattr(field, "value", ""):
                field.value = ""
            self._show_search_results_panel(False)
            MarkdownStudioExplorer._rebuild_tree_ui(self)
            if _ctrl_on_page(getattr(self, "tree_column", None)):
                self.tree_column.update()
        if bar is not None and _ctrl_on_page(bar):
            bar.update()
        if field is not None and _ctrl_on_page(field):
            field.update()

    def _rag_latest_version_only(self) -> bool:
        raw = store_db.settings_get(self._db, store_db.SETTINGS_RAG_LATEST_VERSION_ONLY)
        if raw is None:
            return True
        return str(raw).strip().lower() != "false"

    def _init_search_results_ui(self) -> None:
        self._search_gen = 0
        self._search_hits = []
        self._semantic_search_active = False
        self._rag_index_running = False
        self._rag_status_line_value = "Idle"
        self._rag_index_progress_visible = False
        self._rag_index_progress_current = 0
        self._rag_index_progress_total = 0
        self._rag_index_progress_name = ""
        self._rag_background_index_count = 0
        self._rag_settings_status_line_text: ft.Text | None = None
        self._rag_settings_documents_text: ft.Text | None = None
        self._rag_settings_index_size_text: ft.Text | None = None
        self._rag_settings_last_indexed_text: ft.Text | None = None
        self._rag_settings_active_chunks_text: ft.Text | None = None
        self._rag_settings_historical_chunks_text: ft.Text | None = None
        self._rag_settings_status_text: ft.Text | None = None
        self._rag_settings_chunks_text: ft.Text | None = None
        self._rag_settings_progress_bar: ft.ProgressBar | None = None
        self._rag_settings_progress_label: ft.Text | None = None
        self._rag_settings_reindex_btn: ft.OutlinedButton | None = None
        self._rag_settings_tier_dd: ft.Dropdown | None = None
        self._rag_settings_latest_only_switch: ft.Switch | None = None
        self._rag_settings_enrichment_dd: ft.Dropdown | None = None
        self._rag_settings_reranker_switch: ft.Switch | None = None
        self._focus_rag_settings_panel: Any = None
        self._search_results_list = ft.ListView(expand=True, spacing=8, padding=8)
        self._search_results_host = ft.Container(
            expand=True,
            visible=False,
            content=self._search_results_list,
        )

    def _rag_enrichment_mode(self) -> str:
        mode = store_db.settings_get(self._db, store_db.SETTINGS_RAG_ENRICHMENT_MODE)
        return (mode or "local").strip().lower()

    def _rag_enrichment_tier(self) -> str:
        raw = store_db.settings_get(self._db, store_db.SETTINGS_RAG_ENRICHMENT_TIER)
        return normalize_ki_tier(raw) if raw else KI_TIER_LOCAL

    def _rag_llm_bundle(self) -> tuple[Any | None, str | None]:
        from iterthink.services.rag.enrichment import enrichment_allowed_for_tier

        enrichment = self._rag_enrichment_mode()
        tier = self._rag_enrichment_tier()
        if not enrichment_allowed_for_tier(tier, enrichment):
            return None, None
        backend = self._make_llm_backend_for_tier(tier)
        return backend, backend.effective_model(None)

    def _rag_reranker_enabled(self) -> bool:
        raw = store_db.settings_get(self._db, store_db.SETTINGS_RAG_RERANKER_ENABLED)
        if raw is None:
            return config.RAG_RERANKER_ENABLED
        return str(raw).strip().lower() != "false"

    def _show_search_results_panel(self, visible: bool) -> None:
        if visible and not self._rag_search_enabled():
            visible = False
        self._semantic_search_active = visible
        self._search_results_host.visible = visible
        writing = getattr(self, "_compose_writing_slot", None)
        if writing is not None:
            writing.visible = not visible
        host = getattr(self, "_compose_tab_body_stack", None)
        if host is not None and _ctrl_on_page(host):
            host.update()

    def _build_search_hit_card(self, hit: SearchHit) -> ft.Control:
        snippet_source = hit.parent_text.strip() or hit.raw_text.strip()
        snippet = snippet_source
        if len(snippet) > 320:
            snippet = snippet[:319] + "…"

        async def on_tap(_e: ft.ControlEvent | None = None) -> None:
            await self._open_search_hit(hit)

        return ft.Container(
            bgcolor=config.SURFACE_VARIANT,
            border_radius=8,
            padding=12,
            on_click=lambda e: self.page.run_task(on_tap),
            content=ft.Column(
                [
                    ft.Text(hit.doc_title, size=13, weight=ft.FontWeight.W_600, color=config.ON_SURFACE),
                    ft.Text(hit.section_header, size=11, color=config.ON_SURFACE_SOFT),
                    ft.Text(snippet, size=12, color=config.ON_SURFACE),
                ],
                tight=True,
                spacing=4,
            ),
        )

    def _render_search_results(self, hits: list[SearchHit]) -> None:
        self._search_hits = hits
        self._search_results_list.controls.clear()
        if not hits:
            self._search_results_list.controls.append(
                ft.Text("No matching paragraphs.", size=12, color=config.ON_SURFACE_VARIANT)
            )
        else:
            for hit in hits:
                self._search_results_list.controls.append(self._build_search_hit_card(hit))
        if _ctrl_on_page(self._search_results_list):
            self._search_results_list.update()

    async def _open_search_hit(self, hit: SearchHit) -> None:
        path = hit.resolved_path
        if not path.is_file():
            return
        await self.open_file(path)
        buf = self.editor.value or ""
        off = paragraph_offset_at_index(buf, hit.slot_index)
        self.editor.selection = ft.TextSelection(off, off)
        if _ctrl_on_page(self.editor):
            self.editor.update()
        self._show_search_results_panel(False)
        self.tree_search_field.value = ""
        self._rebuild_tree_ui()
        if _ctrl_on_page(self.tree_column):
            self.tree_column.update()

    async def _run_semantic_search_async(self, query: str, gen: int) -> None:
        enrichment = self._rag_enrichment_mode()
        tier = self._rag_enrichment_tier()
        llm, llm_model = self._rag_llm_bundle()
        project_slug: str | None = None
        current = getattr(self, "current_path", None)
        if current is not None:
            try:
                project_slug = project_slug_for_path(Path(current))
            except (TypeError, ValueError, OSError):
                project_slug = None
        try:
            with session_scope() as session:
                hits = await search_workspace(
                    query,
                    self._db,
                    session,
                    llm=llm,
                    llm_model=llm_model,
                    enrichment_mode=enrichment,
                    ki_tier=tier,
                    rerank=self._rag_reranker_enabled(),
                    latest_version_only=self._rag_latest_version_only(),
                    project_slug=project_slug,
                )
        except BaseException:
            hits = []
        if gen != self._search_gen:
            return
        self._render_search_results(hits)
        self._rebuild_tree_ui_for_search(hits)
        self._show_search_results_panel(True)

    def _rebuild_tree_ui_for_search(self, hits: list[SearchHit]) -> None:
        self.tree_column.controls.clear()
        files = unique_files_from_hits(hits)
        if not files:
            self.tree_column.controls.append(
                ft.Text("No matching files.", size=12, color=config.ON_SURFACE_VARIANT)
            )
            return
        for path, _score in files:
            self.tree_column.controls.append(self._make_tree_file_row(path.name, path))

    def _on_tree_search_change(self, _e: ft.ControlEvent | None = None) -> None:
        if not self._rag_search_enabled():
            return
        raw = (self.tree_search_field.value or "").strip()
        query, filename_mode = parse_search_query(raw)

        if not raw:
            self._search_gen += 1
            self._search_hits = []
            self._show_search_results_panel(False)
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            return

        if filename_mode:
            self._search_gen += 1
            self._show_search_results_panel(False)
            self._rebuild_tree_ui_filename(query)
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            return

        self._search_gen += 1
        gen = self._search_gen
        self.page.run_task(self._debounced_semantic_search, query, gen)

    async def _debounced_semantic_search(self, query: str, gen: int) -> None:
        await asyncio.sleep(0.3)
        if gen != self._search_gen:
            return
        await self._run_semantic_search_async(query, gen)

    def _rebuild_tree_ui_filename(self, query: str) -> None:
        self.tree_column.controls.clear()
        root = config.DOCUMENTS
        if not root.is_dir():
            self.tree_column.controls.append(
                ft.Text(f"Missing folder: {root}", size=12, color=ft.Colors.ORANGE_200)
            )
            return
        tree = build_search_md_tree(root.resolve(), query)
        if not tree:
            self.tree_column.controls.append(
                ft.Text("No matching files.", size=12, color=config.ON_SURFACE_VARIANT)
            )
            return

        from iterthink.studio.explorer import _sorted_dirnames, _sorted_file_entries

        sort_mode = getattr(self, "_tree_sort_mode", "name_az")
        root_res = root.resolve()

        def render_level(node: dict[str, Any], parent_path: Path, depth: int = 0) -> list[ft.Control]:
            ctrls: list[ft.Control] = []
            for dirname in _sorted_dirnames(node, parent_path, sort_mode):
                sub = node[dirname]
                folder_path = parent_path / dirname
                inner = render_level(sub, folder_path, depth + 1)
                ctrls.append(
                    ft.ExpansionTile(
                        title=self._make_tree_folder_title_row(dirname, folder_path),
                        controls=[
                            ft.Container(
                                content=ft.Column(inner, tight=True, spacing=0),
                                padding=ft.Padding.only(left=8),
                            )
                        ],
                        expanded=False,
                        maintain_state=True,
                        dense=True,
                        affinity=ft.TileAffinity.LEADING,
                        show_trailing_icon=True,
                        leading=None,
                        icon_color=config.ON_SURFACE_VARIANT,
                        collapsed_icon_color=config.ON_SURFACE_VARIANT,
                    )
                )
            files = node.get("_files", [])
            for fname, fpath in _sorted_file_entries(list(files), sort_mode):
                ctrls.append(self._make_tree_file_row(fname, fpath))
            return ctrls

        self.tree_column.controls.extend(render_level(tree, root_res))

    def _rebuild_tree_ui(self) -> None:
        if not self._rag_search_enabled():
            MarkdownStudioExplorer._rebuild_tree_ui(self)
            return
        raw = (self.tree_search_field.value or "").strip()
        if not raw:
            MarkdownStudioExplorer._rebuild_tree_ui(self)
            return
        query, filename_mode = parse_search_query(raw)
        if filename_mode:
            self._rebuild_tree_ui_filename(query)
            return
        if self._search_hits:
            self._rebuild_tree_ui_for_search(self._search_hits)
        else:
            self.tree_column.controls.clear()
            self.tree_column.controls.append(
                ft.Text("Searching…", size=12, color=config.ON_SURFACE_VARIANT)
            )

    def schedule_rag_reindex(self, path: Path | None = None) -> None:
        self.page.run_task(self._rag_reindex_path_async, path)

    def schedule_rag_index_all(self) -> None:
        """Start full workspace indexing (Settings RAG tab or programmatic)."""
        self.page.run_task(self._rag_reindex_all_from_settings)

    def _rag_settings_stat_controls(self) -> tuple[tuple[str, ft.Text | None], ...]:
        return (
            ("documents", self._rag_settings_documents_text),
            ("index_size", self._rag_settings_index_size_text),
            ("last_indexed", self._rag_settings_last_indexed_text),
            ("active_chunks", self._rag_settings_active_chunks_text),
            ("historical_chunks", self._rag_settings_historical_chunks_text),
        )

    def _set_rag_settings_stats_loading(self) -> None:
        for _, ctrl in self._rag_settings_stat_controls():
            if ctrl is not None:
                ctrl.value = "…"
                if _ctrl_on_page(ctrl):
                    ctrl.update()

    def _apply_rag_settings_stats(self, stats: dict[str, str]) -> None:
        for key, ctrl in self._rag_settings_stat_controls():
            if ctrl is not None:
                ctrl.value = stats[key]
                if _ctrl_on_page(ctrl):
                    ctrl.update()

    def _refresh_rag_settings_status_sync(self) -> None:
        from iterthink.services.rag.index_status import compute_rag_index_status, rag_stat_values

        try:
            with session_scope() as session:
                status = compute_rag_index_status(self._db, session)
            self._apply_rag_settings_stats(rag_stat_values(status))
        except BaseException:
            pass
        self._apply_rag_job_ui()

    def _refresh_rag_settings_status(self) -> None:
        page = getattr(self, "page", None)
        if page is not None:
            page.run_task(self._refresh_rag_settings_status_async)
            return
        self._refresh_rag_settings_status_sync()

    async def _refresh_rag_settings_status_async(self) -> None:
        from iterthink.services.rag.index_status import compute_rag_index_status, rag_stat_values

        self._set_rag_settings_stats_loading()

        def _compute() -> dict[str, str]:
            with session_scope() as session:
                status = compute_rag_index_status(self._db, session)
            return rag_stat_values(status)

        try:
            stats = await asyncio.to_thread(_compute)
        except BaseException:
            return
        self._apply_rag_settings_stats(stats)
        self._apply_rag_job_ui()

    @staticmethod
    def _rag_progress_status_text(*, current: int, total: int, name: str) -> str:
        if total > 0:
            return f"Indexing {current} / {total} — {name}"
        if name:
            return f"Indexing — {name}"
        return "Indexing…"

    def _set_rag_status_line(self, text: str) -> None:
        self._rag_status_line_value = text
        ctrl = self._rag_settings_status_line_text
        if ctrl is not None:
            ctrl.value = text
            if _ctrl_on_page(ctrl):
                ctrl.update()

    def _apply_rag_job_ui(self) -> None:
        visible = self._rag_index_progress_visible
        current = self._rag_index_progress_current
        total = self._rag_index_progress_total
        name = self._rag_index_progress_name

        ctrl = self._rag_settings_status_line_text
        if ctrl is not None:
            ctrl.value = self._rag_status_line_value
            if _ctrl_on_page(ctrl):
                ctrl.update()

        bar = self._rag_settings_progress_bar
        if bar is not None:
            bar.visible = visible
            bar.value = (current / total) if visible and total > 0 else None
            if _ctrl_on_page(bar):
                bar.update()

        label = self._rag_settings_progress_label
        if label is not None:
            label.visible = visible
            label.value = (
                f"{current} / {total} — {name}"
                if visible and total > 0
                else ("Starting…" if visible else "")
            )
            if _ctrl_on_page(label):
                label.update()

        disabled = visible
        for ctrl in (
            self._rag_settings_reindex_btn,
            self._rag_settings_tier_dd,
            self._rag_settings_latest_only_switch,
            self._rag_settings_enrichment_dd,
            self._rag_settings_reranker_switch,
        ):
            if ctrl is not None:
                ctrl.disabled = disabled
                if _ctrl_on_page(ctrl):
                    ctrl.update()

    def _set_rag_index_progress(self, visible: bool, *, current: int = 0, total: int = 0, name: str = "") -> None:
        self._rag_index_progress_visible = visible
        self._rag_index_progress_current = current
        self._rag_index_progress_total = total
        self._rag_index_progress_name = name
        if visible:
            self._rag_status_line_value = self._rag_progress_status_text(
                current=current, total=total, name=name
            )
        self._apply_rag_job_ui()

    def _rag_progress_callback(self) -> Any:
        async def progress_cb(current: int, total: int, name: str) -> None:
            self._set_rag_index_progress(True, current=current, total=total, name=name)
            if current > 1:
                self._refresh_rag_settings_status()
            await asyncio.sleep(0)

        return progress_cb

    async def _rag_reindex_all_from_settings(self) -> None:
        if self._rag_index_running:
            self._snack("Indexing already in progress")
            return
        self._rag_index_running = True
        self._snack("Indexing workspace…")
        self._set_rag_index_progress(True, current=0, total=0)
        enrichment = self._rag_enrichment_mode()
        llm, llm_model = self._rag_llm_bundle()
        progress_cb = self._rag_progress_callback()

        cancelled = False
        try:
            with session_scope() as session:
                result = await self._index_all_with_progress(
                    session,
                    enrichment=enrichment,
                    llm=llm,
                    llm_model=llm_model,
                    progress_cb=progress_cb,
                    force_reindex=True,
                )
            summary = (
                f"Updated {result.updated} · scanned {result.scanned}"
                f" · unchanged {result.skipped_unchanged}"
            )
            self._set_rag_status_line(f"Done — {summary}")
            from iterthink.services.rag.index_status import clear_workspace_markdown_count_cache

            clear_workspace_markdown_count_cache()
            self._refresh_rag_settings_status()
            self._snack(f"Search index: {summary}")
        except asyncio.CancelledError:
            cancelled = True
            raise
        except BaseException as ex:
            self._set_rag_status_line(f"Failed: {ex}")
            self._snack(f"Indexing failed: {ex}")
        finally:
            self._rag_index_running = False
            if not cancelled:
                self._set_rag_index_progress(False)
                self._ensure_ki_tier_tabs_enabled()

    async def _rag_startup_index_async(self) -> None:
        if not config.RAG_INDEX_ON_STARTUP or self.page.web:
            return
        await asyncio.sleep(2.0)
        if self._rag_index_running:
            return
        self._rag_index_running = True
        self._snack("Indexing workspace…")
        self._set_rag_index_progress(True, current=0, total=0)
        enrichment = self._rag_enrichment_mode()
        llm, llm_model = self._rag_llm_bundle()
        progress_cb = self._rag_progress_callback()

        cancelled = False
        try:
            with session_scope() as session:
                result = await self._index_all_with_progress(
                    session,
                    enrichment=enrichment,
                    llm=llm,
                    llm_model=llm_model,
                    progress_cb=progress_cb,
                    force_reindex=False,
                )
            from iterthink.services.rag.index_status import compute_rag_index_status, format_status_line

            with session_scope() as session:
                status = compute_rag_index_status(self._db, session)
            summary = f"Updated {result.updated} · scanned {result.scanned}"
            self._set_rag_status_line(f"Done — {summary}")
            self._refresh_rag_settings_status()
            msg = format_status_line(status)
            if result.updated:
                msg += f" · {result.updated} updated"
            self._snack(msg)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except BaseException as ex:
            self._set_rag_status_line(f"Failed: {ex}")
            self._snack(f"Indexing failed: {ex}")
        finally:
            self._rag_index_running = False
            if not cancelled:
                self._set_rag_index_progress(False)
                self._ensure_ki_tier_tabs_enabled()

    def _ensure_ki_tier_tabs_enabled(self) -> None:
        """ft.Tabs do not reliably re-enable after disabled=True; reset after indexing."""
        for ctrl in (
            getattr(self, "_ki_tier_tabs", None),
            getattr(self, "_settings_ki_tier_tabs", None),
        ):
            if ctrl is None:
                continue
            if getattr(ctrl, "disabled", False):
                ctrl.disabled = False
                if _ctrl_on_page(ctrl):
                    ctrl.update()

    async def _index_all_with_progress(
        self,
        session: Any,
        *,
        enrichment: str,
        llm: Any | None,
        llm_model: str | None = None,
        progress_cb: Any | None = None,
        force_reindex: bool = False,
    ) -> Any:
        from iterthink.services.rag.workspace_indexer import index_all_documents

        tier = self._rag_enrichment_tier()
        return await index_all_documents(
            session,
            self._db,
            enrichment_mode=enrichment,
            ki_tier=tier,
            llm=llm,
            llm_model=llm_model,
            progress_cb=progress_cb,
            latest_version_only=self._rag_latest_version_only(),
            force_reindex=force_reindex,
        )

    async def _rag_reindex_path_async(self, path: Path | None) -> None:
        target = path or self.current_path
        if target is None or not target.is_file():
            return
        enrichment = self._rag_enrichment_mode()
        tier = self._rag_enrichment_tier()
        llm, llm_model = self._rag_llm_bundle()
        owns_status = not self._rag_index_running
        if owns_status:
            self._rag_background_index_count += 1
            self._set_rag_status_line(f"Indexing — {target.name}")
        outcome: str | None = None
        try:
            with session_scope() as session:
                from iterthink.services.rag.workspace_indexer import index_document_path

                outcome = await index_document_path(
                    session,
                    self._db,
                    target.resolve(),
                    enrichment_mode=enrichment,
                    ki_tier=tier,
                    llm=llm,
                    llm_model=llm_model,
                    latest_version_only=self._rag_latest_version_only(),
                    force_reindex=True,
                )
                if outcome == "updated":
                    self._snack(f"Indexed {target.name}")
            if owns_status and outcome == "updated":
                self._set_rag_status_line(f"Done — updated {target.name}")
            self._refresh_rag_settings_status()
        except asyncio.CancelledError:
            raise
        except BaseException as ex:
            if owns_status:
                self._set_rag_status_line(f"Failed: {ex}")
            self._snack(f"Indexing failed: {ex}")
        finally:
            if owns_status:
                self._rag_background_index_count -= 1
