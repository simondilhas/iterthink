"""Settings → {yourcompany}os tab."""

from __future__ import annotations

from typing import Any, Callable

import flet as ft

from iterthink import config
from iterthink.ai.llm_router import SECRET_YOURCOMPANYOS_API
from iterthink.ai import passphrase_keyring
from iterthink.persistence import store_db
from iterthink.services.yourcompanyos_client import fetch_workflows, normalize_api_base_url

from .constants import PROJECT_PAGE_LINK_LABEL, PROJECT_PAGE_URL, YOURCOMPANYOS_DISPLAY


def _ctrl_on_page(ctrl: ft.Control) -> bool:
    try:
        return ctrl.page is not None
    except RuntimeError:
        return False


def build_yourcompanyos_settings_tab(
    *,
    studio: Any,
    crypto_passphrase_tf: ft.TextField,
    crypto_feedback_txt: ft.Text,
    on_saved: Callable[[], None] | None = None,
) -> ft.Container:
    def _vault_key() -> str:
        cache = getattr(studio, "_api_secrets_cache", None)
        if not isinstance(cache, dict):
            return ""
        return (cache.get(SECRET_YOURCOMPANYOS_API) or "").strip()

    base_tf = ft.TextField(
        label="API base URL",
        hint_text="https://www.yourcompanyos.io",
        value=getattr(studio, "yourcompanyos_api_base_url", "") or "",
        expand=True,
        dense=True,
    )
    key_tf = ft.TextField(
        label="API key (stored encrypted)",
        password=True,
        can_reveal_password=True,
        expand=True,
        dense=True,
    )
    status_txt = ft.Text("", size=12, color=config.ON_SURFACE_VARIANT)

    async def save_settings(_e: ft.ControlEvent | None = None) -> None:
        base = normalize_api_base_url(base_tf.value or "")
        store_db.settings_set(studio._db, store_db.SETTINGS_YOURCOMPANYOS_API_BASE_URL, base)
        studio.yourcompanyos_api_base_url = base
        key = (key_tf.value or "").strip()
        if key:
            phrase = (crypto_passphrase_tf.value or "").strip()
            if not phrase:
                studio._snack("Enter the encryption passphrase to save the API key.")
                crypto_feedback_txt.value = "Passphrase required to encrypt the API key."
                crypto_feedback_txt.color = ft.Colors.ORANGE_400
                if _ctrl_on_page(crypto_feedback_txt):
                    crypto_feedback_txt.update()
                return
            ok, msg = studio.save_credential_vault_entries(phrase, {SECRET_YOURCOMPANYOS_API: key})
            crypto_feedback_txt.value = msg
            crypto_feedback_txt.color = ft.Colors.GREEN_400 if ok else ft.Colors.RED_400
            if _ctrl_on_page(crypto_feedback_txt):
                crypto_feedback_txt.update()
            studio._snack(msg)
            if ok:
                passphrase_keyring.set_stored_passphrase(phrase)
            if ok and on_saved:
                on_saved()
            return
        crypto_feedback_txt.value = ""
        if _ctrl_on_page(crypto_feedback_txt):
            crypto_feedback_txt.update()
        studio._snack(f"{YOURCOMPANYOS_DISPLAY} settings saved.")
        if on_saved:
            on_saved()

    async def test_connection(_e: ft.ControlEvent | None = None) -> None:
        base = normalize_api_base_url(base_tf.value or "") or normalize_api_base_url(
            getattr(studio, "yourcompanyos_api_base_url", "") or ""
        )
        if not base:
            status_txt.value = "Enter API base URL."
            status_txt.color = ft.Colors.ORANGE_400
            if _ctrl_on_page(status_txt):
                status_txt.update()
            return

        studio.ensure_credential_vault_unlocked(passphrase=(crypto_passphrase_tf.value or ""))
        key = (key_tf.value or "").strip() or _vault_key()
        if not key:
            status_txt.value = "Enter API key above and Save."
            status_txt.color = ft.Colors.ORANGE_400
            if _ctrl_on_page(status_txt):
                status_txt.update()
            return
        status_txt.value = "Connecting…"
        status_txt.color = config.ON_SURFACE_VARIANT
        if _ctrl_on_page(status_txt):
            status_txt.update()
        catalog, err = await fetch_workflows(base, key)
        if err:
            status_txt.value = err
            status_txt.color = ft.Colors.RED_400
        elif catalog is not None:
            n = len(catalog.workflows)
            status_txt.value = f"Connected — {catalog.tenant.name} ({n} workflow{'s' if n != 1 else ''})"
            status_txt.color = ft.Colors.GREEN_400
        if _ctrl_on_page(status_txt):
            status_txt.update()

    return ft.Container(
        padding=8,
        content=ft.Column(
            [
                ft.Text(YOURCOMPANYOS_DISPLAY, size=18, weight=ft.FontWeight.W_600),
                ft.Text(
                    spans=[
                        ft.TextSpan(
                            PROJECT_PAGE_LINK_LABEL,
                            url=PROJECT_PAGE_URL,
                            style=ft.TextStyle(
                                color=config.PRIMARY_COLOR,
                                size=12,
                                decoration=ft.TextDecoration.UNDERLINE,
                            ),
                        ),
                    ],
                ),
                base_tf,
                key_tf,
                ft.Row(
                    [
                        ft.FilledButton("Save", on_click=save_settings),
                        ft.OutlinedButton("Test connection", on_click=test_connection),
                    ],
                    spacing=8,
                ),
                status_txt,
                ft.Text(
                    "API key is sent as X-API-Key.",
                    size=12,
                    color=config.ON_SURFACE_VARIANT,
                    selectable=True,
                ),
            ],
            tight=True,
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
    )
