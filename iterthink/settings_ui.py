"""Settings dialog: Ollama models, filesystem paths, app YAML, margin prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import flet as ft
import yaml

from iterthink import config, prompts, store_db
from iterthink.prompts import TOPIC_CHANGE, TOPIC_DISCUSS, TOPIC_EVALUATE, VALID_TOPICS
from iterthink.ollama_models import classify_installed_models
from iterthink.ollama_util import ollama_error_message


def _apply_paths_and_theme(studio: Any, *, store_changed: bool) -> None:
    if store_changed:
        return
    studio.refresh_ollama_client()
    studio.apply_config_theme()
    studio._rebuild_tree_ui()
    if _ctrl_on_page(studio.tree_column):
        studio.tree_column.update()
    studio.left_panel.content = studio._build_left_column()
    if _ctrl_on_page(studio.left_panel):
        studio.left_panel.update()


def _ctrl_on_page(ctrl: ft.Control) -> bool:
    try:
        return ctrl.page is not None
    except RuntimeError:
        return False


async def open_settings_dialog(studio: Any) -> None:
    page = studio.page
    ollama = studio.ollama

    studio.ensure_file_pickers()

    chat_opts: list[str] = []
    embed_opts: list[str] = []
    try:
        chat_opts, embed_opts = await classify_installed_models(ollama)
    except BaseException as ex:
        studio._snack(f"Could not list models: {ollama_error_message(ex)}")
        return
    if not embed_opts:
        studio._snack("No embedding models found. Try: ollama pull nomic-embed-text")
    if not chat_opts:
        studio._snack("No chat models found. Try: ollama pull llama3.2")

    chat_dd = ft.Dropdown(
        label="Chat model (margin / LLM)",
        options=[ft.dropdown.Option(n) for n in chat_opts],
        value=studio.ollama_model if studio.ollama_model in chat_opts else (chat_opts[0] if chat_opts else None),
        expand=True,
        disabled=not chat_opts,
    )
    embed_dd = ft.Dropdown(
        label="Embedding model",
        options=[ft.dropdown.Option(n) for n in embed_opts],
        value=studio.ollama_embed_model if studio.ollama_embed_model in embed_opts else (embed_opts[0] if embed_opts else None),
        expand=True,
        disabled=not embed_opts,
    )

    status_txt = ft.Text(
        f"{len(chat_opts)} chat, {len(embed_opts)} embedding models.",
        size=12,
        color=ft.Colors.GREY_500,
    )

    async def refresh_lists(_e: ft.ControlEvent | None = None) -> None:
        nonlocal chat_opts, embed_opts
        try:
            chat_opts, embed_opts = await classify_installed_models(ollama)
        except BaseException as ex:
            status_txt.value = ollama_error_message(ex)
            if _ctrl_on_page(status_txt):
                status_txt.update()
            return
        chat_dd.options = [ft.dropdown.Option(n) for n in chat_opts]
        embed_dd.options = [ft.dropdown.Option(n) for n in embed_opts]
        if chat_dd.value not in chat_opts and chat_opts:
            chat_dd.value = chat_opts[0]
        if embed_dd.value not in embed_opts and embed_opts:
            embed_dd.value = embed_opts[0]
        status_txt.value = f"{len(chat_opts)} chat, {len(embed_opts)} embedding models."
        if _ctrl_on_page(chat_dd):
            chat_dd.update()
            embed_dd.update()
            status_txt.update()

    async def save_models(_e: ft.ControlEvent | None = None) -> None:
        cm = (chat_dd.value or "").strip()
        em = (embed_dd.value or "").strip()
        if not cm:
            studio._snack("Select a chat model.")
            return
        if not em:
            studio._snack("Select an embedding model (e.g. ollama pull nomic-embed-text).")
            return
        try:
            await ollama.embed(model=em, input="ping")
        except BaseException as ex:
            studio._snack(f"Embedding model not usable: {ollama_error_message(ex)}")
            return

        store_db.settings_set(studio._db, store_db.SETTINGS_CHAT, cm)
        store_db.settings_set(studio._db, store_db.SETTINGS_EMBED, em)
        studio.ollama_model = cm
        studio.ollama_embed_model = em
        studio._refresh_chat_model_button()
        page.pop_dialog()
        studio._snack("Model settings saved.")

    docs_tf = ft.TextField(
        label="Documents root (Markdown tree)",
        value=str(config.DOCUMENTS),
        expand=True,
        dense=True,
    )
    store_tf = ft.TextField(
        label="Store directory (database + prompts.yaml)",
        value=str(config.STORE_DIR),
        expand=True,
        dense=True,
    )
    cfg_hint = ft.Text(
        f"App YAML: {config.APP_CONFIG_PATH}",
        size=11,
        color=ft.Colors.GREY_500,
        selectable=True,
    )

    async def pick_docs(_e: ft.ControlEvent | None = None) -> None:
        if page.web:
            studio._snack("Folder picker is not available in web mode.")
            return
        try:
            p = await studio._fp_documents.get_directory_path(
                dialog_title="Choose documents root",
                initial_directory=str(config.DOCUMENTS) if config.DOCUMENTS.is_dir() else None,
            )
        except BaseException as ex:
            studio._snack(f"Picker failed: {ex}")
            return
        if p:
            docs_tf.value = p
            if _ctrl_on_page(docs_tf):
                docs_tf.update()

    async def pick_store(_e: ft.ControlEvent | None = None) -> None:
        if page.web:
            studio._snack("Folder picker is not available in web mode.")
            return
        try:
            p = await studio._fp_store.get_directory_path(
                dialog_title="Choose store directory",
                initial_directory=str(config.STORE_DIR) if config.STORE_DIR.is_dir() else None,
            )
        except BaseException as ex:
            studio._snack(f"Picker failed: {ex}")
            return
        if p:
            store_tf.value = p
            if _ctrl_on_page(store_tf):
                store_tf.update()

    async def save_paths(_e: ft.ControlEvent | None = None) -> None:
        dr = (docs_tf.value or "").strip()
        sd = (store_tf.value or "").strip()
        if not dr or not sd:
            studio._snack("Both paths are required.")
            return
        docp = Path(dr).expanduser()
        if not docp.is_dir():
            studio._snack("Documents root must be an existing directory.")
            return
        try:
            Path(sd).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            studio._snack(f"Could not create store directory: {ex}")
            return

        before_store = studio._store_dir_resolved
        try:
            config.merge_bootstrap_paths(documents_root=dr, store_dir=sd)
        except (OSError, ValueError, yaml.YAMLError) as ex:
            studio._snack(f"Invalid config: {ex}")
            return
        after_store = config.STORE_DIR.resolve()
        store_changed = after_store != before_store
        if store_changed:
            studio._snack("Store location changed — restart the app to use the new database.")
        else:
            studio._snack("Paths saved.")
        _apply_paths_and_theme(studio, store_changed=store_changed)

    def _bootstrap_data() -> dict[str, Any]:
        raw = config.read_bootstrap_yaml_text()
        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}

    _bd0 = _bootstrap_data()

    def _s(key: str, fallback: str) -> str:
        v = _bd0.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else fallback

    _oh = _bd0.get("ollama_host")
    _ollama_host_initial = _oh.strip() if isinstance(_oh, str) and _oh.strip() else ""
    ollama_host_tf = ft.TextField(
        label="Ollama host (optional)",
        hint_text="Empty = default client; env OLLAMA_HOST overrides when set",
        value=_ollama_host_initial,
        expand=True,
        dense=True,
    )

    fedora_blue_tf = ft.TextField(label="Accent (fedora_blue)", value=_s("fedora_blue", "#007BFF"), dense=True, expand=True)
    surface_tf = ft.TextField(label="Surface", value=_s("surface", "#1E1E1E"), dense=True, expand=True)
    surface_variant_tf = ft.TextField(label="Surface variant", value=_s("surface_variant", "#2D2D2D"), dense=True, expand=True)
    sidebar_surface_tf = ft.TextField(label="Sidebar surface", value=_s("sidebar_surface", "#2A2D32"), dense=True, expand=True)
    selection_overlay_tf = ft.TextField(
        label="Selection overlay (ARGB hex)",
        value=_s("selection_overlay", "#59007BFF"),
        dense=True,
        expand=True,
    )
    chat_system_tf = ft.TextField(
        label="Chat system prompt",
        multiline=True,
        min_lines=4,
        max_lines=12,
        value=_s("chat_system", config.CHAT_SYSTEM),
        expand=True,
    )

    async def save_app_fields(_e: ft.ControlEvent | None = None) -> None:
        before_store = studio._store_dir_resolved
        try:
            data = _bootstrap_data()
        except (OSError, yaml.YAMLError) as ex:
            studio._snack(f"Could not read app config: {ex}")
            return
        host_raw = (ollama_host_tf.value or "").strip()
        data["ollama_host"] = host_raw if host_raw else None
        data["fedora_blue"] = (fedora_blue_tf.value or "").strip() or "#007BFF"
        data["surface"] = (surface_tf.value or "").strip() or "#1E1E1E"
        data["surface_variant"] = (surface_variant_tf.value or "").strip() or "#2D2D2D"
        data["sidebar_surface"] = (sidebar_surface_tf.value or "").strip() or "#2A2D32"
        data["selection_overlay"] = (selection_overlay_tf.value or "").strip() or "#59007BFF"
        data["chat_system"] = (chat_system_tf.value or "").strip() or config.CHAT_SYSTEM
        try:
            text = yaml.safe_dump(
                data,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=88,
            )
            config.write_bootstrap_yaml_text(text)
        except (OSError, ValueError, yaml.YAMLError) as ex:
            studio._snack(f"Could not save app config: {ex}")
            return
        after_store = config.STORE_DIR.resolve()
        store_changed = after_store != before_store
        if store_changed:
            studio._snack("Store location changed — restart the app to use the new database.")
        else:
            studio._snack("Appearance & chat defaults saved.")
        docs_tf.value = str(config.DOCUMENTS)
        store_tf.value = str(config.STORE_DIR)
        if _ctrl_on_page(docs_tf):
            docs_tf.update()
            store_tf.update()
        _apply_paths_and_theme(studio, store_changed=store_changed)

    margin_rows: list[dict[str, Any]] = []
    prompts_list = ft.Column(spacing=10, expand=True, scroll=ft.ScrollMode.AUTO)

    def _collect_margin_dicts() -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for r in margin_rows:
            topic_val = (r["topic"].value or TOPIC_CHANGE).strip()
            if topic_val not in VALID_TOPICS:
                topic_val = TOPIC_CHANGE
            out.append(
                {
                    "id": (r["id"].value or "").strip(),
                    "label": (r["label"].value or "").strip(),
                    "topic": topic_val,
                    "system_prompt": (r["system"].value or "").strip(),
                    "user_template": (r["user"].value or "").strip(),
                }
            )
        return out

    def _rebuild_prompts_list() -> None:
        prompts_list.controls.clear()
        for r in margin_rows:
            prompts_list.controls.append(r["card"])
        if _ctrl_on_page(prompts_list):
            prompts_list.update()

    def _remove_margin_row(row: dict[str, Any], _e: ft.ControlEvent | None = None) -> None:
        if len(margin_rows) <= 1:
            studio._snack("Keep at least one margin action.")
            return
        margin_rows.remove(row)
        _rebuild_prompts_list()

    def _add_margin_row(
        *,
        aid: str = "",
        label: str = "",
        topic: str = TOPIC_CHANGE,
        system_prompt: str = "",
        user_template: str = "{text}",
        rebuild: bool = True,
    ) -> None:
        id_f = ft.TextField(label="id", value=aid, dense=True, expand=True)
        label_f = ft.TextField(label="Label", value=label, dense=True, expand=True)
        topic_dd = ft.Dropdown(
            label="Topic (KI tab)",
            value=topic if topic in VALID_TOPICS else TOPIC_CHANGE,
            options=[
                ft.dropdown.Option(TOPIC_DISCUSS, "Discuss"),
                ft.dropdown.Option(TOPIC_CHANGE, "Change"),
                ft.dropdown.Option(TOPIC_EVALUATE, "Evaluate"),
            ],
            dense=True,
            width=160,
        )
        system_f = ft.TextField(
            label="System prompt",
            multiline=True,
            min_lines=2,
            max_lines=8,
            value=system_prompt,
            expand=True,
        )
        user_f = ft.TextField(
            label="User template (must include {text})",
            multiline=True,
            min_lines=2,
            max_lines=6,
            value=user_template,
            expand=True,
        )
        row: dict[str, Any] = {"id": id_f, "label": label_f, "topic": topic_dd, "system": system_f, "user": user_f}
        del_btn = ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINE,
            tooltip="Remove action",
            on_click=lambda e, rr=row: _remove_margin_row(rr, e),
        )
        row["card"] = ft.Container(
            padding=10,
            border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=6,
            content=ft.Column(
                [
                    ft.Row(
                        [id_f, label_f, topic_dd, del_btn],
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    system_f,
                    user_f,
                ],
                tight=True,
                spacing=8,
            ),
        )
        margin_rows.append(row)
        if rebuild:
            _rebuild_prompts_list()

    for act in prompts.MARGIN_ACTIONS:
        _add_margin_row(
            aid=act.id,
            label=act.label,
            topic=act.topic,
            system_prompt=act.system_prompt,
            user_template=act.user_template,
            rebuild=False,
        )
    _rebuild_prompts_list()

    async def save_margin_prompts(_e: ft.ControlEvent | None = None) -> None:
        try:
            prompts.write_margin_actions_dicts(_collect_margin_dicts())
        except (OSError, ValueError, yaml.YAMLError) as ex:
            studio._snack(f"Could not save prompts: {ex}")
            return
        studio._rebuild_margin_slots()
        if hasattr(studio, "_rebuild_topic_pills"):
            studio._rebuild_topic_pills()
        if _ctrl_on_page(studio._margin_column):
            studio._margin_column.update()
        studio._snack("Margin prompts saved.")

    tab_models = ft.Container(
        padding=8,
        content=ft.Column(
            [
                chat_dd,
                embed_dd,
                status_txt,
                ft.Row(
                    [ft.TextButton("Refresh list", on_click=lambda e: page.run_task(refresh_lists, e))],
                    alignment=ft.MainAxisAlignment.START,
                ),
                ft.FilledButton("Save models", on_click=lambda e: page.run_task(save_models, e)),
            ],
            tight=True,
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
        ),
    )

    tab_paths = ft.Container(
        padding=8,
        content=ft.Column(
            [
                cfg_hint,
                docs_tf,
                ft.Row(
                    [ft.OutlinedButton("Browse…", on_click=lambda e: page.run_task(pick_docs, e))],
                    alignment=ft.MainAxisAlignment.START,
                ),
                store_tf,
                ft.Row(
                    [ft.OutlinedButton("Browse…", on_click=lambda e: page.run_task(pick_store, e))],
                    alignment=ft.MainAxisAlignment.START,
                ),
                ft.FilledButton("Save paths", on_click=lambda e: page.run_task(save_paths, e)),
            ],
            tight=True,
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        ),
    )

    tab_app = ft.Container(
        padding=8,
        content=ft.Column(
            [
                ft.Text(
                    "Theme, optional Ollama host, and chat system string. "
                    "Document paths live under Paths; default models under Models.",
                    size=12,
                    color=ft.Colors.GREY_500,
                ),
                ollama_host_tf,
                ft.Text("Theme", weight=ft.FontWeight.W_500, size=13),
                fedora_blue_tf,
                ft.Row([surface_tf, surface_variant_tf], spacing=8, expand=True),
                ft.Row([sidebar_surface_tf, selection_overlay_tf], spacing=8, expand=True),
                chat_system_tf,
                ft.FilledButton("Save app settings", on_click=lambda e: page.run_task(save_app_fields, e)),
            ],
            tight=True,
            spacing=10,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
    )

    tab_prompts = ft.Container(
        padding=8,
        content=ft.Column(
            [
                ft.Text(
                    f"Margin / KI actions ({config.STORE_DIR / 'prompts.yaml'}). "
                    "Each user template must include {{text}}. Topic selects the Discuss / Change / Evaluate tab.",
                    size=12,
                    color=ft.Colors.GREY_500,
                    selectable=True,
                ),
                ft.Container(content=prompts_list, expand=True),
                ft.Row(
                    [
                        ft.OutlinedButton(
                            "Add action",
                            icon=ft.Icons.ADD,
                            on_click=lambda _e: _add_margin_row(),
                        ),
                        ft.FilledButton("Save prompts", on_click=lambda e: page.run_task(save_margin_prompts, e)),
                    ],
                    spacing=8,
                ),
            ],
            expand=True,
            spacing=10,
        ),
    )

    panels = [tab_models, tab_paths, tab_app, tab_prompts]
    tab_stack = ft.Stack(controls=panels, expand=True)
    for i, c in enumerate(panels):
        c.visible = i == 0

    def show_panel(ix: int) -> None:
        for i, c in enumerate(panels):
            c.visible = i == ix
        if _ctrl_on_page(tab_stack):
            tab_stack.update()

    def on_rail_change(e: ft.ControlEvent) -> None:
        idx = int(getattr(e.control, "selected_index", 0))
        show_panel(idx)

    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        extended=True,
        min_width=72,
        min_extended_width=152,
        bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.WHITE),
        destinations=[
            ft.NavigationRailDestination(icon=ft.Icons.SMART_TOY_OUTLINED, label="Models"),
            ft.NavigationRailDestination(icon=ft.Icons.FOLDER_OUTLINED, label="Paths"),
            ft.NavigationRailDestination(icon=ft.Icons.PALETTE_OUTLINED, label="App"),
            ft.NavigationRailDestination(icon=ft.Icons.FORMAT_LIST_BULLETED, label="Prompts"),
        ],
        on_change=on_rail_change,
    )

    body = ft.Row(
        [
            rail,
            ft.VerticalDivider(width=1),
            ft.Container(content=tab_stack, expand=True, padding=ft.padding.only(left=4)),
        ],
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        width=820,
        height=500,
    )

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Settings", weight=ft.FontWeight.W_600),
        content=ft.Container(content=body, padding=ft.padding.only(top=4)),
        actions=[ft.TextButton("Close", on_click=lambda e: page.pop_dialog())],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dlg)
