"""Settings dialog: Ollama models, filesystem paths, app YAML, margin prompts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import flet as ft
import yaml

from iterthink import config, impact_checks, licensing, prompts
from iterthink.persistence import store_db, vault_store
from iterthink.services import remote_model_list
from iterthink.ai import passphrase_keyring
from iterthink.ai.llm_router import (
    DEFAULT_COMPANY_OPENAI_BASE,
    SECRET_CLOUD_ANTHROPIC,
    SECRET_CLOUD_GOOGLE,
    SECRET_CLOUD_OPENAI,
    SECRET_COMPANY_OPENAI,
)
from iterthink.prompts import TOPIC_CHANGE, TOPIC_DISCUSS, TOPIC_EVALUATE, VALID_TOPICS
from iterthink.ai.ollama_models import classify_installed_models
from iterthink.ai.ollama_util import ollama_error_message
from .constants import KI_TIER_TAB_ICON_PX, SIDEBAR_TOOLBAR_ROW_H_PX
from .llm_backend import build_llm_tier_tabs, sync_llm_tier_tab_icons
from .util import (
    CLOUD_VENDOR_ANTHROPIC,
    CLOUD_VENDOR_GOOGLE,
    CLOUD_VENDOR_OPENAI,
    KI_TIER_CLOUD,
    KI_TIER_COMPANY,
    KI_TIER_LOCAL,
    KI_TIERS,
    normalize_cloud_vendor,
    normalize_ki_tier,
)


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
    try:
        await _open_settings_dialog(studio)
    except BaseException as ex:
        import traceback
        studio._snack(f"Settings error: {ex}")
        traceback.print_exc()


async def _open_settings_dialog(studio: Any) -> None:
    page = studio.page
    ollama = studio.ollama

    studio.ensure_file_pickers()

    chat_opts: list[str] = []
    try:
        chat_opts, _embed_opts = await classify_installed_models(ollama)
    except BaseException as ex:
        studio._snack(f"Could not list Ollama models (Local section): {ollama_error_message(ex)}")
    if not chat_opts:
        studio._snack("No chat models found locally. Try: ollama pull llama3.2")

    chat_dd = ft.Dropdown(
        label="Chat model (Ollama — Local tier)",
        options=[ft.dropdown.Option(n) for n in chat_opts],
        value=studio.ollama_model if studio.ollama_model in chat_opts else (chat_opts[0] if chat_opts else None),
        expand=True,
        disabled=not chat_opts,
    )

    status_txt = ft.Text(
        f"{len(chat_opts)} chat model(s) on Ollama. Paragraph compare uses GTE-Multilingual-Base (ONNX): "
        "downloaded once into your store folder from Hugging Face, then offline.",
        size=12,
        color=ft.Colors.GREY_500,
    )

    async def refresh_lists(_e: ft.ControlEvent | None = None) -> None:
        nonlocal chat_opts
        try:
            chat_opts, _ = await classify_installed_models(ollama)
        except BaseException as ex:
            status_txt.value = ollama_error_message(ex)
            if _ctrl_on_page(status_txt):
                status_txt.update()
            return
        chat_dd.options = [ft.dropdown.Option(n) for n in chat_opts]
        if chat_dd.value not in chat_opts and chat_opts:
            chat_dd.value = chat_opts[0]
        status_txt.value = (
            f"{len(chat_opts)} chat model(s) on Ollama. Paragraph compare uses GTE-Multilingual-Base (ONNX): "
            "downloaded once into your store folder from Hugging Face, then offline."
        )
        if _ctrl_on_page(chat_dd):
            chat_dd.update()
            status_txt.update()

    async def save_local_models(_e: ft.ControlEvent | None = None) -> None:
        nonlocal ollama
        cm = (chat_dd.value or "").strip()
        if not cm:
            studio._snack("Select a chat model.")
            return

        try:
            data = _bootstrap_data()
        except (OSError, yaml.YAMLError) as ex:
            studio._snack(f"Could not read app config: {ex}")
            return
        host_raw = (ollama_host_tf.value or "").strip()
        data["ollama_host"] = host_raw if host_raw else None
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
            studio._snack(f"Could not save Ollama host: {ex}")
            return

        store_db.settings_set(studio._db, store_db.SETTINGS_CHAT, cm)
        studio.ollama_model = cm
        studio.refresh_ollama_client()
        ollama = studio.ollama
        studio._refresh_chat_model_button()
        studio._snack("Local Ollama models saved.")

    cloud_vendor_seg = ft.SegmentedButton(
        segments=[
            ft.Segment(value="anthropic", label=ft.Text("Claude")),
            ft.Segment(value="openai", label=ft.Text("ChatGPT")),
            ft.Segment(value="google", label=ft.Text("Gemini")),
        ],
        selected=[normalize_cloud_vendor(studio.cloud_vendor)],
        expand=True,
        show_selected_icon=False,
    )

    def _on_cloud_vendor_change(e: ft.ControlEvent) -> None:
        sel = list(getattr(e.control, "selected", []) or [])
        if not sel:
            return
        studio.cloud_vendor = normalize_cloud_vendor(sel[0])
        store_db.settings_set(studio._db, store_db.SETTINGS_CLOUD_VENDOR, studio.cloud_vendor)

    def _model_dd_options(ids: list[str], *, current: str) -> list[ft.dropdown.Option]:
        cur = (current or "").strip()
        ordered: list[str] = []
        seen: set[str] = set()
        if cur:
            ordered.append(cur)
            seen.add(cur)
        for x in ids:
            x = (x or "").strip()
            if x and x not in seen:
                ordered.append(x)
                seen.add(x)
        return [ft.dropdown.Option(x) for x in ordered]

    def _vault_key(name: str) -> str:
        cache = getattr(studio, "_api_secrets_cache", None)
        if not isinstance(cache, dict):
            return ""
        return (cache.get(name) or "").strip()

    company_key_tf = ft.TextField(
        label="OpenAI API key (stored encrypted)",
        password=True,
        can_reveal_password=True,
        expand=True,
        dense=True,
    )
    company_base_tf = ft.TextField(
        label="Full company API URL",
        value=studio.company_openai_base_url or "https://api.openai.com/v1",
        hint_text="Full path, no trailing slash. OpenAI: …/v1 · Infomaniak: …/2/ai/…/openai/v1",
        expand=True,
        dense=True,
    )
    company_model_tf = ft.TextField(
        label="Chat model id",
        value=studio.company_openai_model or "gpt-4o-mini",
        expand=True,
        dense=True,
    )

    cloud_anthropic_key_tf = ft.TextField(
        label="Anthropic API key",
        password=True,
        can_reveal_password=True,
        expand=True,
        dense=True,
    )
    _am = (studio.cloud_anthropic_model or "").strip()
    cloud_anthropic_model_dd = ft.Dropdown(
        label="Claude model",
        value=_am,
        options=_model_dd_options([_am], current=_am) if _am else [],
        hint_text="Use «List Claude models» (needs API key) to load ids from Anthropic.",
        editable=True,
        enable_search=True,
        dense=True,
        expand=True,
    )
    cloud_openai_key_tf = ft.TextField(
        label="OpenAI API key (ChatGPT / OpenAI cloud)",
        password=True,
        can_reveal_password=True,
        expand=True,
        dense=True,
    )
    _com = (studio.cloud_openai_model or "").strip()
    cloud_openai_model_dd = ft.Dropdown(
        label="OpenAI model (cloud)",
        value=_com,
        options=_model_dd_options([_com], current=_com) if _com else [],
        hint_text="Use «List OpenAI models» (needs API key) to load ids from OpenAI.",
        editable=True,
        enable_search=True,
        dense=True,
        expand=True,
    )
    cloud_google_key_tf = ft.TextField(
        label="Google AI API key",
        password=True,
        can_reveal_password=True,
        expand=True,
        dense=True,
    )
    _gm = (studio.cloud_google_model or "").strip()
    cloud_google_model_dd = ft.Dropdown(
        label="Gemini model",
        value=_gm,
        options=_model_dd_options([_gm], current=_gm) if _gm else [],
        hint_text="Use «List Gemini models» (needs API key) to load ids from Google.",
        editable=True,
        enable_search=True,
        dense=True,
        expand=True,
    )

    async def refresh_cloud_anthropic_models(_e: ft.ControlEvent | None = None) -> None:
        key = (cloud_anthropic_key_tf.value or "").strip() or _vault_key(SECRET_CLOUD_ANTHROPIC)
        ids, err = await remote_model_list.fetch_anthropic_models(key)
        if err:
            studio._snack(err)
            return
        if not ids:
            studio._snack("No Anthropic models in response.")
            return
        cur = (cloud_anthropic_model_dd.value or "").strip()
        cloud_anthropic_model_dd.options = _model_dd_options(ids, current=cur)
        cloud_anthropic_model_dd.value = cur if (cur in ids or cur) else ids[0]
        if _ctrl_on_page(cloud_anthropic_model_dd):
            cloud_anthropic_model_dd.update()
        studio._snack(f"Loaded {len(ids)} Claude models.")

    async def refresh_cloud_openai_models(_e: ft.ControlEvent | None = None) -> None:
        key = (cloud_openai_key_tf.value or "").strip() or _vault_key(SECRET_CLOUD_OPENAI)
        ids, err = await remote_model_list.fetch_openai_compatible_models(DEFAULT_COMPANY_OPENAI_BASE, key)
        if err:
            studio._snack(err)
            return
        if not ids:
            studio._snack("No OpenAI models in response.")
            return
        cur = (cloud_openai_model_dd.value or "").strip()
        cloud_openai_model_dd.options = _model_dd_options(ids, current=cur)
        cloud_openai_model_dd.value = cur if (cur in ids or cur) else ids[0]
        if _ctrl_on_page(cloud_openai_model_dd):
            cloud_openai_model_dd.update()
        studio._snack(f"Loaded {len(ids)} OpenAI models.")

    async def refresh_cloud_google_models(_e: ft.ControlEvent | None = None) -> None:
        key = (cloud_google_key_tf.value or "").strip() or _vault_key(SECRET_CLOUD_GOOGLE)
        ids, err = await remote_model_list.fetch_google_generative_models(key)
        if err:
            studio._snack(err)
            return
        if not ids:
            studio._snack("No Gemini models in response.")
            return
        cur = (cloud_google_model_dd.value or "").strip()
        cloud_google_model_dd.options = _model_dd_options(ids, current=cur)
        cloud_google_model_dd.value = cur if (cur in ids or cur) else ids[0]
        if _ctrl_on_page(cloud_google_model_dd):
            cloud_google_model_dd.update()
        studio._snack(f"Loaded {len(ids)} Gemini models.")

    crypto_passphrase_tf = ft.TextField(
        label="Encryption passphrase (for API keys — never stored in plaintext)",
        password=True,
        can_reveal_password=True,
        expand=True,
        dense=True,
    )
    crypto_feedback_txt = ft.Text("", size=12, color=ft.Colors.GREY_500)

    async def unlock_vault(_e: ft.ControlEvent | None = None) -> None:
        phrase = (crypto_passphrase_tf.value or "").strip()
        ok, msg = studio.try_unlock_credential_vault(phrase)
        crypto_feedback_txt.value = msg
        crypto_feedback_txt.color = ft.Colors.GREEN_400 if ok else ft.Colors.ORANGE_400
        if _ctrl_on_page(crypto_feedback_txt):
            crypto_feedback_txt.update()
        studio._snack(msg)

    async def save_passphrase_to_keyring(_e: ft.ControlEvent | None = None) -> None:
        phrase = (crypto_passphrase_tf.value or "").strip()
        if not phrase:
            studio._snack("Enter the encryption passphrase first.")
            return
        if not vault_store.vault_exists():
            studio._snack("Save API keys first (office or cloud) before storing a passphrase.")
            return
        ok, msg = studio.try_unlock_credential_vault(phrase)
        if not ok:
            crypto_feedback_txt.value = msg
            crypto_feedback_txt.color = ft.Colors.ORANGE_400
            if _ctrl_on_page(crypto_feedback_txt):
                crypto_feedback_txt.update()
            studio._snack(msg)
            return
        ok2, msg2 = passphrase_keyring.set_stored_passphrase(phrase)
        crypto_feedback_txt.value = msg2
        crypto_feedback_txt.color = ft.Colors.GREEN_400 if ok2 else ft.Colors.RED_400
        if _ctrl_on_page(crypto_feedback_txt):
            crypto_feedback_txt.update()
        studio._snack(msg2)

    async def remove_passphrase_from_keyring(_e: ft.ControlEvent | None = None) -> None:
        ok, msg = passphrase_keyring.delete_stored_passphrase()
        crypto_feedback_txt.value = msg
        crypto_feedback_txt.color = ft.Colors.GREEN_400 if ok else ft.Colors.RED_400
        if _ctrl_on_page(crypto_feedback_txt):
            crypto_feedback_txt.update()
        studio._snack(msg)

    async def save_company_settings(_e: ft.ControlEvent | None = None) -> None:
        store_db.settings_set(studio._db, store_db.SETTINGS_COMPANY_OPENAI_BASE_URL, (company_base_tf.value or "").strip())
        store_db.settings_set(studio._db, store_db.SETTINGS_COMPANY_OPENAI_MODEL, (company_model_tf.value or "").strip())
        studio.company_openai_base_url = (company_base_tf.value or "").strip() or "https://api.openai.com/v1"
        studio.company_openai_model = (company_model_tf.value or "").strip() or "gpt-4o-mini"
        key = (company_key_tf.value or "").strip()
        if key:
            phrase = (crypto_passphrase_tf.value or "").strip()
            if not phrase:
                studio._snack("Enter the encryption passphrase to save the office API key.")
                crypto_feedback_txt.value = "Passphrase required to encrypt the API key."
                crypto_feedback_txt.color = ft.Colors.ORANGE_400
                if _ctrl_on_page(crypto_feedback_txt):
                    crypto_feedback_txt.update()
                return
            ok, msg = studio.save_credential_vault_entries(phrase, {SECRET_COMPANY_OPENAI: key})
            crypto_feedback_txt.value = msg
            crypto_feedback_txt.color = ft.Colors.GREEN_400 if ok else ft.Colors.RED_400
            if _ctrl_on_page(crypto_feedback_txt):
                crypto_feedback_txt.update()
            studio._snack(msg)
            return
        crypto_feedback_txt.value = ""
        if _ctrl_on_page(crypto_feedback_txt):
            crypto_feedback_txt.update()
        studio._snack("Office settings saved (base URL and model).")

    async def save_cloud_settings(_e: ft.ControlEvent | None = None) -> None:
        sel = list(getattr(cloud_vendor_seg, "selected", []) or [])
        if sel:
            studio.cloud_vendor = normalize_cloud_vendor(sel[0])
            store_db.settings_set(studio._db, store_db.SETTINGS_CLOUD_VENDOR, studio.cloud_vendor)
        store_db.settings_set(studio._db, store_db.SETTINGS_CLOUD_ANTHROPIC_MODEL, (cloud_anthropic_model_dd.value or "").strip())
        store_db.settings_set(studio._db, store_db.SETTINGS_CLOUD_OPENAI_MODEL, (cloud_openai_model_dd.value or "").strip())
        store_db.settings_set(studio._db, store_db.SETTINGS_CLOUD_GOOGLE_MODEL, (cloud_google_model_dd.value or "").strip())
        studio.cloud_anthropic_model = (cloud_anthropic_model_dd.value or "").strip()
        studio.cloud_openai_model = (cloud_openai_model_dd.value or "").strip()
        studio.cloud_google_model = (cloud_google_model_dd.value or "").strip()
        updates = {
            SECRET_CLOUD_ANTHROPIC: (cloud_anthropic_key_tf.value or "").strip(),
            SECRET_CLOUD_OPENAI: (cloud_openai_key_tf.value or "").strip(),
            SECRET_CLOUD_GOOGLE: (cloud_google_key_tf.value or "").strip(),
        }
        keys_nonempty = {k: v for k, v in updates.items() if v}
        if keys_nonempty:
            phrase = (crypto_passphrase_tf.value or "").strip()
            if not phrase:
                studio._snack("Enter the encryption passphrase to save cloud API keys.")
                crypto_feedback_txt.value = "Passphrase required to encrypt API keys."
                crypto_feedback_txt.color = ft.Colors.ORANGE_400
                if _ctrl_on_page(crypto_feedback_txt):
                    crypto_feedback_txt.update()
                return
            ok, msg = studio.save_credential_vault_entries(phrase, keys_nonempty)
            crypto_feedback_txt.value = msg
            crypto_feedback_txt.color = ft.Colors.GREEN_400 if ok else ft.Colors.RED_400
            if _ctrl_on_page(crypto_feedback_txt):
                crypto_feedback_txt.update()
            studio._snack(msg)
            return
        crypto_feedback_txt.value = ""
        if _ctrl_on_page(crypto_feedback_txt):
            crypto_feedback_txt.update()
        studio._snack("Cloud settings saved (vendor and models).")

    docs_tf = ft.TextField(
        label="Documents root (Markdown tree)",
        value=str(config.DOCUMENTS),
        expand=True,
        dense=True,
    )
    store_tf = ft.TextField(
        label="Store directory (database + prompts.yaml + impact_checks.yaml)",
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

    _ap0 = (_bd0.get("appearance") or "dark").strip().lower()
    if _ap0 not in ("dark", "light"):
        _ap0 = "dark"
    appearance_dd = ft.Dropdown(
        label="Appearance",
        value=_ap0,
        options=[
            ft.dropdown.Option("dark", "Dark (focus)"),
            ft.dropdown.Option("light", "Light (clean)"),
        ],
        dense=True,
        width=220,
    )
    chat_system_tf = ft.TextField(
        label="Chat system prompt",
        multiline=True,
        min_lines=4,
        max_lines=12,
        value=_s("chat_system", config.CHAT_SYSTEM),
        expand=True,
    )

    _sdl0 = _bd0.get("startup_daily_log", True)
    if not isinstance(_sdl0, bool):
        _sdl0 = True
    daily_log_sw = ft.Switch(
        label="Open today's daily log on startup",
        value=_sdl0,
    )
    new_note_template_tf = ft.TextField(
        label="New note name template (when daily log is off)",
        value=_s("new_note_name_template", "unnamed-{n}.md"),
        hint_text='Must contain exactly one "{n}" (e.g. unnamed-{n}.md)',
        expand=True,
        dense=True,
        disabled=_sdl0,
    )

    def _on_daily_log_switch(_e: ft.ControlEvent | None = None) -> None:
        new_note_template_tf.disabled = bool(daily_log_sw.value)
        if _ctrl_on_page(new_note_template_tf):
            new_note_template_tf.update()

    daily_log_sw.on_change = _on_daily_log_switch

    async def save_app_fields(_e: ft.ControlEvent | None = None) -> None:
        before_store = studio._store_dir_resolved
        try:
            data = _bootstrap_data()
        except (OSError, yaml.YAMLError) as ex:
            studio._snack(f"Could not read app config: {ex}")
            return
        ap = (appearance_dd.value or "dark").strip().lower()
        data["appearance"] = ap if ap in ("dark", "light") else "dark"
        data["chat_system"] = (chat_system_tf.value or "").strip() or config.CHAT_SYSTEM
        tmpl = (new_note_template_tf.value or "").strip()
        if tmpl.count("{n}") != 1:
            studio._snack('New note name template must contain exactly one "{n}" (e.g. unnamed-{n}.md).')
            return
        data["new_note_name_template"] = tmpl
        data["startup_daily_log"] = bool(daily_log_sw.value)
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
        new_note_template_tf.value = config.NEW_NOTE_NAME_TEMPLATE
        daily_log_sw.value = config.STARTUP_DAILY_LOG
        new_note_template_tf.disabled = config.STARTUP_DAILY_LOG
        if _ctrl_on_page(docs_tf):
            docs_tf.update()
            store_tf.update()
        if _ctrl_on_page(new_note_template_tf):
            new_note_template_tf.update()
        if _ctrl_on_page(daily_log_sw):
            daily_log_sw.update()
        _apply_paths_and_theme(studio, store_changed=store_changed)

    margin_rows: list[dict[str, Any]] = []
    impact_rows: list[dict[str, Any]] = []
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

    def _collect_impact_dicts() -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for r in impact_rows:
            out.append(
                {
                    "id": (r["id"].value or "").strip(),
                    "label": (r["label"].value or "").strip(),
                    "system_prompt": (r["system"].value or "").strip(),
                    "user_template": (r["user"].value or "").strip(),
                }
            )
        return out

    def _rebuild_prompts_list() -> None:
        prompts_list.controls.clear()
        prompts_list.controls.append(
            ft.Text("Margin / KI (Discuss · Change · Evaluate)", weight=ft.FontWeight.W_600, size=13)
        )
        for r in margin_rows:
            prompts_list.controls.append(r["card"])
        prompts_list.controls.append(
            ft.Container(
                padding=ft.padding.only(top=8),
                content=ft.Text(
                    "Impact tab (Review → Impact). Templates need {text} and {context}.",
                    weight=ft.FontWeight.W_600,
                    size=13,
                ),
            )
        )
        for r in impact_rows:
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

    def _remove_impact_row(row: dict[str, Any], _e: ft.ControlEvent | None = None) -> None:
        impact_rows.remove(row)
        _rebuild_prompts_list()

    def _add_impact_row(
        *,
        aid: str = "",
        label: str = "",
        system_prompt: str = "",
        user_template: str = "{text}\n\n{context}",
        rebuild: bool = True,
    ) -> None:
        id_f = ft.TextField(label="id", value=aid, dense=True, expand=True)
        label_f = ft.TextField(label="Label", value=label, dense=True, expand=True)
        system_f = ft.TextField(
            label="System prompt",
            multiline=True,
            min_lines=2,
            max_lines=8,
            value=system_prompt,
            expand=True,
        )
        user_f = ft.TextField(
            label="User template ({text} = paragraph, {context} = RAG)",
            multiline=True,
            min_lines=2,
            max_lines=6,
            value=user_template,
            expand=True,
        )
        row: dict[str, Any] = {"id": id_f, "label": label_f, "system": system_f, "user": user_f}
        del_btn = ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINE,
            tooltip="Remove impact check",
            on_click=lambda e, rr=row: _remove_impact_row(rr, e),
        )
        row["card"] = ft.Container(
            padding=10,
            border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=6,
            content=ft.Column(
                [
                    ft.Row(
                        [id_f, label_f, del_btn],
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    system_f,
                    user_f,
                ],
                tight=True,
                spacing=8,
            ),
        )
        impact_rows.append(row)
        if rebuild:
            _rebuild_prompts_list()

    prompts.reload()
    for act in prompts.MARGIN_ACTIONS:
        _add_margin_row(
            aid=act.id,
            label=act.label,
            topic=act.topic,
            system_prompt=act.system_prompt,
            user_template=act.user_template,
            rebuild=False,
        )
    impact_checks.reload()
    for act in impact_checks.IMPACT_CHECKS:
        _add_impact_row(
            aid=act.id,
            label=act.label,
            system_prompt=act.system_prompt,
            user_template=act.user_template,
            rebuild=False,
        )
    _rebuild_prompts_list()

    async def save_margin_prompts(_e: ft.ControlEvent | None = None) -> None:
        try:
            prompts.write_margin_actions_dicts(_collect_margin_dicts())
            impact_checks.write_impact_checks_dicts(_collect_impact_dicts())
        except (OSError, ValueError, yaml.YAMLError) as ex:
            studio._snack(f"Could not save prompts: {ex}")
            return
        try:
            if hasattr(studio, "_rebuild_topic_pills"):
                studio._rebuild_topic_pills()
            if hasattr(studio, "_rebuild_impact_prompt_pills"):
                studio._rebuild_impact_prompt_pills()
            studio._margin_gen += 1
            page.run_task(studio._debounced_compose_rebuild, studio._margin_gen)
        except Exception as ex:
            studio._snack(f"Prompts saved, but UI refresh failed: {ex}")
            return
        studio._snack("Prompts saved.")

    def _on_settings_ki_tier_tabs_change(e: ft.ControlEvent) -> None:
        try:
            idx = int(e.data)
        except (TypeError, ValueError):
            idx = int(getattr(e.control, "selected_index", 0))
        if not (0 <= idx < len(KI_TIERS)):
            return
        studio.ki_tier = KI_TIERS[idx]
        store_db.settings_set(studio._db, store_db.SETTINGS_KI_TIER, studio.ki_tier)
        if hasattr(studio, "_sync_ki_tier_tabs_ui"):
            studio._sync_ki_tier_tabs_ui()
        if hasattr(studio, "_sync_chat_model_ui"):
            studio._sync_chat_model_ui()

    _models_tier_k = normalize_ki_tier(studio.ki_tier)
    _cv0 = normalize_cloud_vendor(studio.cloud_vendor)

    wrap_home = ft.Container(
        visible=_models_tier_k == KI_TIER_LOCAL,
        content=ft.Column(
            [
                ft.Text("Local (Ollama)", weight=ft.FontWeight.W_600, size=14),
                ft.Text(
                    "Used when Home is selected in the KI panel. "
                    "Compare-tab paragraph embeddings run locally (GTE-Multilingual-Base ONNX, fetched once into your store folder); "
                    "no Ollama embedding model is required.",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                ollama_host_tf,
                chat_dd,
                status_txt,
                ft.Row(
                    [ft.TextButton("Refresh Ollama list", on_click=lambda e: page.run_task(refresh_lists, e))],
                    alignment=ft.MainAxisAlignment.START,
                ),
                ft.FilledButton("Save local models", on_click=lambda e: page.run_task(save_local_models, e)),
            ],
            tight=True,
            spacing=12,
        ),
    )

    wrap_crypto = ft.Container(
        visible=_models_tier_k in (KI_TIER_COMPANY, KI_TIER_CLOUD),
        content=ft.Column(
            [
                ft.Text("Encryption", weight=ft.FontWeight.W_600, size=14),
                ft.Text(
                    "API keys are stored encrypted in the database, not as plaintext. "
                    "Enter this passphrase when saving new keys (Save office / Save cloud). "
                    "Optionally store it in the OS keyring so the app can unlock the vault on startup.",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                crypto_passphrase_tf,
                ft.Row(
                    [
                        ft.OutlinedButton("Unlock session", on_click=lambda e: page.run_task(unlock_vault, e)),
                        ft.OutlinedButton(
                            "Save passphrase to keyring",
                            on_click=lambda e: page.run_task(save_passphrase_to_keyring, e),
                        ),
                        ft.OutlinedButton(
                            "Remove passphrase from keyring",
                            on_click=lambda e: page.run_task(remove_passphrase_from_keyring, e),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.START,
                    wrap=True,
                ),
                crypto_feedback_txt,
            ],
            tight=True,
            spacing=12,
        ),
    )

    wrap_office = ft.Container(
        visible=_models_tier_k == KI_TIER_COMPANY,
        content=ft.Column(
            [
                ft.Text("Office (OpenAI-compatible)", weight=ft.FontWeight.W_600, size=14),
                ft.Text(
                    "Use the full API URL from your organisation (not only the hostname).",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                company_key_tf,
                company_base_tf,
                company_model_tf,
                ft.Row(
                    [ft.FilledButton("Save office", on_click=lambda e: page.run_task(save_company_settings, e))],
                    alignment=ft.MainAxisAlignment.START,
                ),
            ],
            tight=True,
            spacing=12,
        ),
    )

    cloud_anthropic_wrap = ft.Container(
        visible=_cv0 == CLOUD_VENDOR_ANTHROPIC,
        content=ft.Column(
            [
                cloud_anthropic_key_tf,
                ft.Row(
                    [
                        cloud_anthropic_model_dd,
                        ft.TextButton(
                            "List Claude models",
                            on_click=lambda e: page.run_task(refresh_cloud_anthropic_models, e),
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            tight=True,
            spacing=12,
        ),
    )
    cloud_openai_wrap = ft.Container(
        visible=_cv0 == CLOUD_VENDOR_OPENAI,
        content=ft.Column(
            [
                cloud_openai_key_tf,
                ft.Row(
                    [
                        cloud_openai_model_dd,
                        ft.TextButton(
                            "List OpenAI models",
                            on_click=lambda e: page.run_task(refresh_cloud_openai_models, e),
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            tight=True,
            spacing=12,
        ),
    )
    cloud_google_wrap = ft.Container(
        visible=_cv0 == CLOUD_VENDOR_GOOGLE,
        content=ft.Column(
            [
                cloud_google_key_tf,
                ft.Row(
                    [
                        cloud_google_model_dd,
                        ft.TextButton(
                            "List Gemini models",
                            on_click=lambda e: page.run_task(refresh_cloud_google_models, e),
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            tight=True,
            spacing=12,
        ),
    )

    wrap_cloud = ft.Container(
        visible=_models_tier_k == KI_TIER_CLOUD,
        content=ft.Column(
            [
                ft.Text("Cloud vendor", weight=ft.FontWeight.W_600, size=14),
                ft.Text("", size=11, color=ft.Colors.GREY_500),
                cloud_vendor_seg,
                cloud_anthropic_wrap,
                cloud_openai_wrap,
                cloud_google_wrap,
                ft.Row(
                    [ft.FilledButton("Save cloud", on_click=lambda e: page.run_task(save_cloud_settings, e))],
                    alignment=ft.MainAxisAlignment.START,
                ),
            ],
            tight=True,
            spacing=12,
        ),
    )

    def _sync_cloud_vendor_panel() -> None:
        v = normalize_cloud_vendor(studio.cloud_vendor)
        cloud_anthropic_wrap.visible = v == CLOUD_VENDOR_ANTHROPIC
        cloud_openai_wrap.visible = v == CLOUD_VENDOR_OPENAI
        cloud_google_wrap.visible = v == CLOUD_VENDOR_GOOGLE
        for c in (cloud_anthropic_wrap, cloud_openai_wrap, cloud_google_wrap):
            if _ctrl_on_page(c):
                c.update()

    def _sync_settings_models_tier_panels() -> None:
        t = normalize_ki_tier(studio.ki_tier)
        wrap_home.visible = t == KI_TIER_LOCAL
        wrap_crypto.visible = t in (KI_TIER_COMPANY, KI_TIER_CLOUD)
        wrap_office.visible = t == KI_TIER_COMPANY
        wrap_cloud.visible = t == KI_TIER_CLOUD
        if t == KI_TIER_CLOUD:
            _sync_cloud_vendor_panel()
        for w in (wrap_home, wrap_crypto, wrap_office, wrap_cloud):
            if _ctrl_on_page(w):
                w.update()

    def _on_settings_ki_tier_tabs_wrapped(e: ft.ControlEvent) -> None:
        _on_settings_ki_tier_tabs_change(e)
        sync_llm_tier_tab_icons(settings_ki_tier_tabs)
        _sync_settings_models_tier_panels()

    def _on_cloud_vendor_ui(e: ft.ControlEvent) -> None:
        _on_cloud_vendor_change(e)
        _sync_cloud_vendor_panel()

    cloud_vendor_seg.on_change = _on_cloud_vendor_ui

    _settings_tier_ix = KI_TIERS.index(normalize_ki_tier(studio.ki_tier))
    settings_ki_tier_tabs = build_llm_tier_tabs(
        selected_index=_settings_tier_ix,
        on_change=_on_settings_ki_tier_tabs_wrapped,
        icon_size=KI_TIER_TAB_ICON_PX,
        tab_bar_height=max(float(SIDEBAR_TOOLBAR_ROW_H_PX), 40.0),
        tab_texts=("Home", "Office", "Cloud"),
    )
    sync_llm_tier_tab_icons(settings_ki_tier_tabs)

    models_models_column = ft.Column(
        [
            ft.Text("Models", size=18, weight=ft.FontWeight.W_600),
            ft.Text(
                "Default KI tier matches the KI panel (Home · Office · Cloud).",
                size=11,
                color=ft.Colors.GREY_500,
            ),
            settings_ki_tier_tabs,
            ft.Divider(height=12),
            wrap_home,
            wrap_crypto,
            wrap_office,
            wrap_cloud,
        ],
        tight=True,
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
    )

    tab_models = ft.Container(
        padding=8,
        content=models_models_column,
    )

    _sync_settings_models_tier_panels()
    _sync_cloud_vendor_panel()

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

    export_author_tf = ft.TextField(
        label="Author (Word export)",
        value=store_db.settings_get(studio._db, store_db.SETTINGS_EXPORT_AUTHOR) or "",
        expand=True,
        dense=True,
        hint_text="Used for {Author} and {Name} in export templates",
    )

    async def save_export_settings(_e: ft.ControlEvent | None = None) -> None:
        store_db.settings_set(
            studio._db,
            store_db.SETTINGS_EXPORT_AUTHOR,
            (export_author_tf.value or "").strip(),
        )
        studio._snack("Export settings saved.")

    tab_export = ft.Container(
        padding=8,
        content=ft.Column(
            [
                ft.Text("Word export", weight=ft.FontWeight.W_500, size=13),
                export_author_tf,
                ft.Text(
                    "Fills the {Author} and {Name} placeholders in .docx templates (same stored value). "
                    "{Titel} and {Date} come from the document name and export date.",
                    size=11,
                    color=config.ON_SURFACE_VARIANT,
                ),
                ft.FilledButton("Save export settings", on_click=lambda e: page.run_task(save_export_settings, e)),
            ],
            tight=True,
            spacing=10,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
    )

    tab_app = ft.Container(
        padding=8,
        content=ft.Column(
            [
                ft.Text("Theme", weight=ft.FontWeight.W_500, size=13),
                appearance_dd,
                chat_system_tf,
                ft.Text("Notes & startup", weight=ft.FontWeight.W_500, size=13),
                daily_log_sw,
                new_note_template_tf,
                ft.Text(
                    "When daily log is on, the app opens or creates one YYYYMMDD-n.md per day in the documents folder. "
                    "When off, it opens the first note in the tree (same order as the sidebar) or creates the next file from the template.",
                    size=11,
                    color=config.ON_SURFACE_VARIANT,
                ),
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
                    f"Margin prompts: {config.STORE_DIR / 'prompts.yaml'}. "
                    f"Impact tab checks: {config.STORE_DIR / 'impact_checks.yaml'}. "
                    "Margin uses {{text}} and KI topic. Impact uses {{text}} + {{context}}.",
                    size=12,
                    color=ft.Colors.GREY_500,
                    selectable=True,
                ),
                ft.Container(content=prompts_list, expand=True),
                ft.Row(
                    [
                        ft.OutlinedButton(
                            "Add margin action",
                            icon=ft.Icons.ADD,
                            on_click=lambda _e: _add_margin_row(),
                        ),
                        ft.OutlinedButton(
                            "Add impact check",
                            icon=ft.Icons.ADD,
                            on_click=lambda _e: _add_impact_row(),
                        ),
                        ft.FilledButton("Save prompts", on_click=lambda e: page.run_task(save_margin_prompts, e)),
                    ],
                    spacing=8,
                    wrap=True,
                ),
            ],
            expand=True,
            spacing=10,
        ),
    )

    def _license_status_label() -> str:
        return "Status: Licensed" if licensing.is_licensed() else "Status: Free for personal use"

    license_status_txt = ft.Text(_license_status_label(), size=14)

    license_buy_block = ft.Container(
        visible=not licensing.is_licensed(),
        padding=ft.padding.all(12),
        border_radius=8,
        bgcolor=ft.Colors.with_opacity(0.12, config.PRIMARY_COLOR),
        border=ft.border.all(1, ft.Colors.with_opacity(0.4, config.PRIMARY_COLOR)),
        content=ft.Text(
            spans=[
                ft.TextSpan(
                    "Buy a licence ",
                    style=ft.TextStyle(
                        color=config.ON_SURFACE,
                        size=14,
                        weight=ft.FontWeight.W_500,
                    ),
                ),
                ft.TextSpan(
                    "www.iterthink.com/#pricing",
                    url=licensing.PRICING_URL,
                    style=ft.TextStyle(
                        color=config.PRIMARY_COLOR,
                        size=14,
                        weight=ft.FontWeight.W_600,
                        decoration=ft.TextDecoration.UNDERLINE,
                    ),
                ),
            ],
        ),
    )

    # TextField.value can lag the last keystrokes until blur; mirror via on_change.
    _license_phrase_mirror: list[str] = [""]

    def _on_license_passphrase_change(e: ft.ControlEvent) -> None:
        _license_phrase_mirror[0] = e.control.value or ""

    license_passphrase_tf = ft.TextField(
        label="License passphrase",
        password=True,
        can_reveal_password=True,
        expand=True,
        dense=True,
        on_change=_on_license_passphrase_change,
    )
    license_feedback_txt = ft.Text("", size=12, selectable=False)
    license_remove_btn = ft.TextButton(
        "Remove license",
        visible=licensing.is_licensed(),
        on_click=lambda _e: None,
    )

    def _sync_license_tab_ui() -> None:
        license_status_txt.value = _license_status_label()
        licensed = licensing.is_licensed()
        license_remove_btn.visible = licensed
        license_buy_block.visible = not licensed
        license_entry_block.visible = not licensed
        if _ctrl_on_page(license_status_txt):
            license_status_txt.update()
        if _ctrl_on_page(license_remove_btn):
            license_remove_btn.update()
        if _ctrl_on_page(license_buy_block):
            license_buy_block.update()
        if _ctrl_on_page(license_entry_block):
            license_entry_block.update()

    def activate_license(_e: ft.ControlEvent | None = None) -> None:
        phrase = (license_passphrase_tf.value or _license_phrase_mirror[0] or "").strip()
        if not phrase:
            license_feedback_txt.value = "Enter a passphrase."
            license_feedback_txt.color = ft.Colors.ORANGE_400
            if _ctrl_on_page(license_feedback_txt):
                license_feedback_txt.update()
            studio._snack("Enter a passphrase.")
            return
        if licensing.activate(phrase):
            license_passphrase_tf.value = ""
            _license_phrase_mirror[0] = ""
            if _ctrl_on_page(license_passphrase_tf):
                license_passphrase_tf.update()
            _sync_license_tab_ui()
            if hasattr(studio, "_refresh_license_banner"):
                studio._refresh_license_banner()
            license_feedback_txt.value = "License activated."
            license_feedback_txt.color = ft.Colors.GREEN_400
            if _ctrl_on_page(license_feedback_txt):
                license_feedback_txt.update()
            studio._snack("License activated.")
        else:
            license_feedback_txt.value = "Invalid passphrase."
            license_feedback_txt.color = ft.Colors.RED_400
            if _ctrl_on_page(license_feedback_txt):
                license_feedback_txt.update()
            studio._snack("Invalid passphrase.")

    def remove_license(_e: ft.ControlEvent | None = None) -> None:
        licensing.deactivate()
        _license_phrase_mirror[0] = ""
        _sync_license_tab_ui()
        if hasattr(studio, "_refresh_license_banner"):
            studio._refresh_license_banner()
        license_feedback_txt.value = "License removed."
        license_feedback_txt.color = ft.Colors.GREY_400
        if _ctrl_on_page(license_feedback_txt):
            license_feedback_txt.update()
        studio._snack("License removed.")

    license_remove_btn.on_click = remove_license

    license_activate_btn = ft.FilledButton("Activate", on_click=activate_license)
    license_entry_block = ft.Container(
        visible=not licensing.is_licensed(),
        content=ft.Column(
            [license_passphrase_tf, license_activate_btn],
            tight=True,
            spacing=10,
        ),
    )

    tab_license = ft.Container(
        padding=8,
        content=ft.Column(
            [
                license_status_txt,
                license_buy_block,
                license_entry_block,
                license_feedback_txt,
                license_remove_btn,
            ],
            tight=True,
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
        ),
    )

    panels = [tab_app, tab_paths, tab_export, tab_license, tab_models, tab_prompts]
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
            ft.NavigationRailDestination(icon=ft.Icons.PALETTE_OUTLINED, label="App"),
            ft.NavigationRailDestination(icon=ft.Icons.FOLDER_OUTLINED, label="Paths"),
            ft.NavigationRailDestination(icon=ft.Icons.DESCRIPTION_OUTLINED, label="Export"),
            ft.NavigationRailDestination(icon=ft.Icons.KEY, label="License"),
            ft.NavigationRailDestination(icon=ft.Icons.SMART_TOY_OUTLINED, label="Models"),
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
    # Yield so the File menu overlay finishes closing before the modal is stacked.
    await asyncio.sleep(0)
    page.show_dialog(dlg)
