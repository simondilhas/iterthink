"""Settings → Knowledge tab (export comments and review overrides)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import flet as ft

from iterthink import config
from iterthink.db.session import session_scope
from iterthink.services import knowledge_export
from .util import ctrl_on_page as _ctrl_on_page, normalize_save_file_path


def build_knowledge_settings_tab(*, studio: Any, page: ft.Page) -> ft.Container:
    summary_txt = ft.Text("", size=12, color=config.ON_SURFACE_SOFT)
    export_btn = ft.FilledButton("Export…", icon=ft.Icons.DOWNLOAD)

    def _refresh_summary() -> None:
        try:
            with session_scope() as session:
                payload = knowledge_export.build_export_payload(
                    session, store_conn=studio._db
                )
        except BaseException:
            summary_txt.value = "Could not read knowledge data."
            if _ctrl_on_page(summary_txt):
                summary_txt.update()
            return
        c = payload.get("counts") or {}
        summary_txt.value = (
            f"Notes {c.get('paragraph_user_comments', 0)} · "
            f"Impact {c.get('impact_annotations', 0)} · "
            f"Difference overrides {c.get('difference_check_overrides', 0)} · "
            f"Override embeddings {c.get('impact_override_embeddings', 0)}"
        )
        if _ctrl_on_page(summary_txt):
            summary_txt.update()

    async def on_export(_e: ft.ControlEvent | None = None) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        default_name = f"iterthink-knowledge-{stamp}.json"
        dest = await studio._fp_knowledge_export.save_file(
            dialog_title="Export knowledge",
            file_name=default_name,
            allowed_extensions=["json"],
        )
        if not dest:
            return
        try:
            out = normalize_save_file_path(
                dest,
                default_file_name=default_name,
                expected_suffix=".json",
            )
            with session_scope() as session:
                text = knowledge_export.export_json_text(session, store_conn=studio._db)
            Path(out).write_text(text, encoding="utf-8")
            studio._snack(f"Exported to {out}")
            _refresh_summary()
        except BaseException as ex:
            studio._snack(f"Export failed: {ex}")

    export_btn.on_click = lambda e: page.run_task(on_export, e)
    studio._refresh_knowledge_settings_summary = _refresh_summary
    _refresh_summary()

    return ft.Container(
        padding=8,
        content=ft.Column(
            [
                ft.Text("Knowledge", weight=ft.FontWeight.W_500, size=13),
                summary_txt,
                export_btn,
            ],
            tight=True,
            spacing=10,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
    )
