"""Review dialog when bundled margin prompts change an existing store id."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import flet as ft

from iterthink import config, prompts
from iterthink.compare.diff_card import build_new_side_spans, build_old_side_spans

from . import ui_theme
from .constants import COMPARE_COL_FONT_SIZE, COMPARE_COL_LINE_HEIGHT
from .util import ctrl_on_page as _ctrl_on_page

_FIELD_LABELS: dict[str, str] = {
    "label": "Label",
    "topic": "Topic (KI tab)",
    "system_prompt": "System prompt",
    "user_template": "User template",
}

_PROMPT_DIFF_CLIP_CAP = 80_000


def _prompt_diff_clip(old: str, new: str) -> tuple[str, str]:
    if len(old) + len(new) > _PROMPT_DIFF_CLIP_CAP:
        half = _PROMPT_DIFF_CLIP_CAP // 2
        return old[:half] + "\n…", new[:half] + "\n…"
    return old, new


def _prompt_insertion_diff_colors() -> tuple[str, str]:
    bg_alpha = 0.5 if config.IS_LIGHT else 0.24
    return ui_theme.editor_text_color(), ft.Colors.with_opacity(bg_alpha, config.SUCCESS)


def _prompt_diff_text_style(*, mono: bool) -> ft.TextStyle:
    kw: dict[str, Any] = {
        "font_family": "monospace" if mono else None,
        "size": COMPARE_COL_FONT_SIZE,
        "height": COMPARE_COL_LINE_HEIGHT,
        "color": ui_theme.editor_text_color(),
    }
    return ft.TextStyle(**{k: v for k, v in kw.items() if v is not None})


def _prompt_diff_side_spans(
    store: str,
    bundled: str,
    *,
    side: Literal["old", "new"],
    mono: bool,
) -> list[ft.TextSpan]:
    old_t, new_t = _prompt_diff_clip(store, bundled)
    ins_fg, ins_bg = _prompt_insertion_diff_colors()
    common = {
        "base_size": COMPARE_COL_FONT_SIZE,
        "base_color": ui_theme.editor_text_color(),
        "font_family": "monospace" if mono else None,
        "line_height": COMPARE_COL_LINE_HEIGHT,
    }
    if side == "old":
        return build_old_side_spans(old_t, new_t, **common)
    return build_new_side_spans(
        old_t,
        new_t,
        **common,
        insert_color=ins_fg,
        insert_bgcolor=ins_bg,
    )


def _prompt_diff_column(label: str, spans: list[ft.TextSpan], *, mono: bool, min_height: int) -> ft.Control:
    return ft.Container(
        expand=True,
        content=ft.Column(
            [
                ft.Text(label, size=11, color=config.ON_SURFACE_VARIANT),
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                spans=spans,
                                style=_prompt_diff_text_style(mono=mono),
                                selectable=True,
                                no_wrap=False,
                            ),
                        ],
                        scroll=ft.ScrollMode.AUTO,
                        expand=True,
                    ),
                    padding=8,
                    border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
                    border_radius=6,
                    height=min_height,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                ),
            ],
            tight=True,
            spacing=4,
        ),
    )


def _refresh_studio_prompt_ui(studio: Any) -> None:
    try:
        if hasattr(studio, "_rebuild_topic_pills"):
            studio._rebuild_topic_pills()
        if hasattr(studio, "_rebuild_impact_prompt_pills"):
            studio._rebuild_impact_prompt_pills()
        studio._margin_gen += 1
        studio.page.run_task(studio._debounced_compose_rebuild, studio._margin_gen)
    except Exception as ex:
        studio._snack(f"Prompts updated, but UI refresh failed: {ex}")


def _field_compare_row(field: str, store_val: str, bundled_val: str) -> ft.Control:
    label = _FIELD_LABELS.get(field, field)
    mono = field in ("system_prompt", "user_template")
    min_height = 200 if field == "system_prompt" else 120
    old_spans = _prompt_diff_side_spans(store_val, bundled_val, side="old", mono=mono)
    new_spans = _prompt_diff_side_spans(store_val, bundled_val, side="new", mono=mono)
    return ft.Column(
        [
            ft.Text(label, size=12, weight=ft.FontWeight.W_500),
            ft.Row(
                [
                    _prompt_diff_column("Yours", old_spans, mono=mono, min_height=min_height),
                    _prompt_diff_column("New default", new_spans, mono=mono, min_height=min_height),
                ],
                vertical_alignment=ft.CrossAxisAlignment.START,
                expand=True,
            ),
        ],
        tight=True,
        spacing=4,
    )


def _build_detail_panel(conflict: prompts.PromptConflict) -> ft.Control:
    rows: list[ft.Control] = [
        ft.Text(conflict.label, size=14, weight=ft.FontWeight.W_600),
        ft.Text(conflict.action_id, size=11, font_family="monospace", color=config.ON_SURFACE_VARIANT),
    ]
    for field in conflict.changed_fields:
        rows.append(
            _field_compare_row(
                field,
                conflict.store.get(field, ""),
                conflict.bundled.get(field, ""),
            )
        )
    return ft.Container(
        expand=True,
        content=ft.Column(rows, spacing=10, scroll=ft.ScrollMode.AUTO),
    )


async def show_prompt_merge_dialog(
    page: ft.Page,
    studio: Any,
    conflicts: tuple[prompts.PromptConflict, ...] | None = None,
    *,
    on_done: Any | None = None,
) -> None:
    """Show bundled-vs-store review for pending prompt conflicts."""
    pending = conflicts if conflicts is not None else prompts.pending_conflicts()
    if not pending:
        return

    await asyncio.sleep(0)

    state: dict[str, Any] = {"index": 0, "pending": list(pending)}
    detail_host = ft.Container(expand=True)
    list_host = ft.Column(spacing=2, tight=True)
    title_txt = ft.Text("", size=13, color=config.ON_SURFACE_VARIANT)

    def _current() -> prompts.PromptConflict | None:
        idx = int(state["index"])
        pl = state["pending"]
        if not pl or idx < 0 or idx >= len(pl):
            return None
        return pl[idx]

    def _sync_detail() -> None:
        cur = _current()
        if cur is None:
            page.pop_dialog()
            return
        title_txt.value = f"{state['index'] + 1} of {len(state['pending'])}"
        detail_host.content = _build_detail_panel(cur)
        list_host.controls.clear()
        for i, c in enumerate(state["pending"]):
            selected = i == state["index"]
            list_host.controls.append(
                ft.ListTile(
                    title=ft.Text(c.label, size=13, weight=ft.FontWeight.W_600 if selected else None),
                    subtitle=ft.Text(c.action_id, size=11, font_family="monospace"),
                    bgcolor=ft.Colors.with_opacity(0.08, config.PRIMARY_COLOR) if selected else None,
                    on_click=lambda e, ix=i: _select(ix),
                )
            )
        if _ctrl_on_page(detail_host):
            detail_host.update()
            list_host.update()
            title_txt.update()

    def _select(ix: int) -> None:
        state["index"] = ix
        _sync_detail()

    def _finish() -> None:
        if on_done is not None:
            on_done()

    def _after_resolve() -> None:
        state["pending"] = list(prompts.pending_conflicts())
        if not state["pending"]:
            page.pop_dialog()
            _refresh_studio_prompt_ui(studio)
            _finish()
            return
        if state["index"] >= len(state["pending"]):
            state["index"] = max(0, len(state["pending"]) - 1)
        _sync_detail()

    def _keep_mine(_e: ft.ControlEvent | None = None) -> None:
        cur = _current()
        if cur is None:
            return
        prompts.resolve_conflict_keep_mine(cur.action_id)
        _after_resolve()

    def _use_bundled(_e: ft.ControlEvent | None = None) -> None:
        cur = _current()
        if cur is None:
            return
        prompts.resolve_conflict_use_bundled(cur.action_id)
        _after_resolve()

    def _keep_all(_e: ft.ControlEvent | None = None) -> None:
        prompts.resolve_all_conflicts_keep_mine()
        page.pop_dialog()
        _refresh_studio_prompt_ui(studio)
        _finish()

    def _review_later(_e: ft.ControlEvent | None = None) -> None:
        page.pop_dialog()
        _finish()

    _sync_detail()

    actions: list[ft.Control] = [
        ft.TextButton("Review later", on_click=_review_later),
    ]
    if len(state["pending"]) > 1:
        actions.append(ft.TextButton("Keep all mine", on_click=_keep_all))
    actions.extend(
        [
            ft.TextButton("Keep mine", on_click=_keep_mine),
            ft.FilledButton("Use new default", on_click=_use_bundled),
        ]
    )

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Prompt updates from defaults", weight=ft.FontWeight.W_600),
        content=ft.Container(
            width=780,
            height=460,
            padding=ft.padding.only(top=4),
            content=ft.Column(
                [
                    title_txt,
                    ft.Row(
                        [
                            ft.Container(
                                width=200,
                                content=ft.Column(
                                    [list_host],
                                    scroll=ft.ScrollMode.AUTO,
                                    expand=True,
                                ),
                            ),
                            ft.VerticalDivider(width=1),
                            detail_host,
                        ],
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                ],
                expand=True,
                spacing=8,
            ),
        ),
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dlg)
