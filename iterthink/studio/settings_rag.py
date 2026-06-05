"""Settings → RAG / workspace search index tab."""

from __future__ import annotations

from typing import Any

import flet as ft
from sqlalchemy.exc import OperationalError

from iterthink import config
from iterthink.persistence import store_db
from .util import KI_TIER_CLOUD, KI_TIER_COMPANY, KI_TIER_LOCAL, normalize_ki_tier

_STAT_LABEL_W = 140
_VALUE_STYLE = dict(size=12, color=config.ON_SURFACE_VARIANT)


def _stat_row(label: str, value: ft.Text, *, tooltip: str | None = None) -> ft.Row:
    label_ctrl = ft.Text(label, size=12, color=config.ON_SURFACE_SOFT, width=_STAT_LABEL_W)
    if tooltip:
        label_ctrl.tooltip = tooltip
    return ft.Row([label_ctrl, value], spacing=8)


def _safe_rag_settings_set(studio: Any, key: str, value: str) -> None:
    if getattr(studio, "_rag_index_running", False):
        return
    try:
        store_db.settings_set(studio._db, key, value)
    except OperationalError:
        snack = getattr(studio, "_snack", None)
        if callable(snack):
            snack("Database busy — wait for indexing to finish")


def build_rag_settings_tab(*, studio: Any, page: ft.Page) -> ft.Container:
    progress_visible = bool(getattr(studio, "_rag_index_progress_visible", False))
    progress_current = int(getattr(studio, "_rag_index_progress_current", 0))
    progress_total = int(getattr(studio, "_rag_index_progress_total", 0))
    progress_name = str(getattr(studio, "_rag_index_progress_name", ""))
    status_text = str(getattr(studio, "_rag_status_line_value", "Idle"))

    status_val = ft.Text(status_text, **_VALUE_STYLE)
    documents_val = ft.Text("—", **_VALUE_STYLE)
    index_size_val = ft.Text("—", **_VALUE_STYLE)
    last_indexed_val = ft.Text("—", **_VALUE_STYLE)
    active_chunks_val = ft.Text("—", **_VALUE_STYLE)
    historical_chunks_val = ft.Text("—", **_VALUE_STYLE)
    progress_label = ft.Text(
        (
            f"{progress_current} / {progress_total} — {progress_name}"
            if progress_visible and progress_total > 0
            else ("Starting…" if progress_visible else "")
        ),
        size=12,
        color=config.ON_SURFACE_SOFT,
        visible=progress_visible,
    )
    progress_bar = ft.ProgressBar(
        value=(progress_current / progress_total) if progress_visible and progress_total > 0 else None,
        visible=progress_visible,
    )
    index_btn = ft.FilledButton("Index workspace", icon=ft.Icons.SYNC, disabled=progress_visible)

    studio._rag_settings_status_line_text = status_val
    studio._rag_settings_documents_text = documents_val
    studio._rag_settings_index_size_text = index_size_val
    studio._rag_settings_last_indexed_text = last_indexed_val
    studio._rag_settings_active_chunks_text = active_chunks_val
    studio._rag_settings_historical_chunks_text = historical_chunks_val
    studio._rag_settings_progress_bar = progress_bar
    studio._rag_settings_progress_label = progress_label
    studio._rag_settings_reindex_btn = index_btn
    studio._rag_settings_status_text = None
    studio._rag_settings_chunks_text = None

    _tier = normalize_ki_tier(store_db.settings_get(studio._db, store_db.SETTINGS_RAG_ENRICHMENT_TIER))

    tier_dd = ft.Dropdown(
        label="Enrichment tier",
        tooltip="Home = local Ollama; Office/Cloud = remote APIs configured under Settings → Models",
        value=_tier,
        options=[
            ft.dropdown.Option(KI_TIER_LOCAL, "Home"),
            ft.dropdown.Option(KI_TIER_COMPANY, "Office"),
            ft.dropdown.Option(KI_TIER_CLOUD, "Cloud"),
        ],
        on_select=lambda e: _safe_rag_settings_set(
            studio,
            store_db.SETTINGS_RAG_ENRICHMENT_TIER,
            str(e.control.value or KI_TIER_LOCAL),
        ),
    )
    studio._rag_settings_tier_dd = tier_dd

    async def on_rag_index(_e: ft.ControlEvent | None = None) -> None:
        focus = getattr(studio, "_focus_rag_settings_panel", None)
        if callable(focus):
            focus()
        await studio._rag_reindex_all_from_settings()

    index_btn.on_click = lambda e: page.run_task(on_rag_index, e)

    index_rows = ft.Column(
        [
            _stat_row("Status", status_val),
            _stat_row("Documents", documents_val),
            _stat_row(
                "Index size",
                index_size_val,
                tooltip=str(config.RAG_DB_PATH.resolve()),
            ),
            _stat_row("Last indexed", last_indexed_val),
        ],
        tight=True,
        spacing=6,
    )
    chunk_rows = ft.Column(
        [
            _stat_row("Active", active_chunks_val),
            _stat_row("Historical", historical_chunks_val),
        ],
        tight=True,
        spacing=6,
    )

    _enrich = (store_db.settings_get(studio._db, store_db.SETTINGS_RAG_ENRICHMENT_MODE) or "local").strip().lower()

    latest_only_switch = ft.Switch(
        label="Latest saved version only",
        value=(store_db.settings_get(studio._db, store_db.SETTINGS_RAG_LATEST_VERSION_ONLY) or "true")
        != "false",
        tooltip=(
            "Search uses the latest PBS snapshot per file; indexing still reads .md from disk "
            "when no snapshot exists yet"
        ),
        on_change=lambda e: _safe_rag_settings_set(
            studio,
            store_db.SETTINGS_RAG_LATEST_VERSION_ONLY,
            "true" if e.control.value else "false",
        ),
    )
    studio._rag_settings_latest_only_switch = latest_only_switch

    enrichment_dd = ft.Dropdown(
        label="Enrichment",
        value="skip" if _enrich == "skip" else "local",
        options=[
            ft.dropdown.Option("local", "On"),
            ft.dropdown.Option("skip", "Skip"),
        ],
        on_select=lambda e: _safe_rag_settings_set(
            studio,
            store_db.SETTINGS_RAG_ENRICHMENT_MODE,
            str(e.control.value or "local"),
        ),
    )
    studio._rag_settings_enrichment_dd = enrichment_dd

    reranker_switch = ft.Switch(
        label="Reranker",
        value=(store_db.settings_get(studio._db, store_db.SETTINGS_RAG_RERANKER_ENABLED) or "true")
        != "false",
        on_change=lambda e: _safe_rag_settings_set(
            studio,
            store_db.SETTINGS_RAG_RERANKER_ENABLED,
            "true" if e.control.value else "false",
        ),
    )
    studio._rag_settings_reranker_switch = reranker_switch
    if progress_visible:
        for ctrl in (tier_dd, latest_only_switch, enrichment_dd, reranker_switch):
            ctrl.disabled = True

    apply_job_ui = getattr(studio, "_apply_rag_job_ui", None)
    if callable(apply_job_ui):
        apply_job_ui()

    rag_disclaimer = ft.Container(
        padding=ft.padding.all(12),
        border_radius=8,
        bgcolor=ft.Colors.with_opacity(0.08, config.ON_SURFACE),
        border=ft.border.all(1, ft.Colors.with_opacity(0.22, config.OUTLINE)),
        content=ft.Column(
            [
                ft.Text(
                    "🔍 About RAG",
                    weight=ft.FontWeight.W_600,
                    size=14,
                    color=config.ON_SURFACE,
                ),
                ft.Text(
                    "Workspace markdown is indexed into a local database for semantic search "
                    "and related context. Indexing uses PBS snapshots when available, otherwise "
                    "files on disk. Vector embeddings and the optional reranker run locally on "
                    "your machine.",
                    size=12,
                    color=config.ON_SURFACE_VARIANT,
                    selectable=True,
                ),
                ft.Text(
                    "Chunking splits on blank lines (same as paragraph diffing). Parent chunks "
                    "follow section headings; each paragraph is a child chunk with a short overlap "
                    "from the previous paragraph (default 200 characters). When Enrichment is On, "
                    "each child gets an LLM summary and three questions included in the embedded text.",
                    size=12,
                    color=config.ON_SURFACE_VARIANT,
                    selectable=True,
                ),
                ft.Text(
                    "Enrichment and search query expansion use the model under Enrichment tier "
                    "(Home = local Ollama; Office/Cloud = remote APIs). Index workspace runs one "
                    "LLM call per paragraph chunk when enrichment is enabled—large workspaces can "
                    "incur significant Office/Cloud usage. Set Enrichment to Skip or Home tier to "
                    "avoid remote charges.",
                    size=12,
                    color=config.ON_SURFACE_VARIANT,
                    selectable=True,
                ),
            ],
            tight=True,
            spacing=6,
        ),
    )

    return ft.Container(
        padding=8,
        content=ft.Column(
            [
                rag_disclaimer,
                ft.Text("RAG", size=18, weight=ft.FontWeight.W_600),
                ft.Text("Index", size=14, weight=ft.FontWeight.W_600),
                index_btn,
                progress_bar,
                progress_label,
                index_rows,
                ft.Divider(height=8),
                ft.Text("Chunks", size=14, weight=ft.FontWeight.W_600),
                chunk_rows,
                ft.Divider(height=8),
                ft.Text("Options", size=14, weight=ft.FontWeight.W_600),
                latest_only_switch,
                tier_dd,
                enrichment_dd,
                reranker_switch,
            ],
            tight=True,
            spacing=8,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
    )
