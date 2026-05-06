"""LLM routing: build ``LlmChatBackend`` from studio state."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from iterthink import config, crypto_vault, store_db, vault_store
from iterthink.llm_router import LlmChatBackend
from iterthink.studio_util import KI_TIER_CLOUD, KI_TIER_COMPANY, KI_TIER_LOCAL


def build_ki_tier_tabs(
    *,
    selected_index: int,
    on_change: Callable[[ft.ControlEvent], Any],
    icon_size: int,
    tab_bar_height: float,
) -> ft.Tabs:
    """Underline + divider like KI topic tabs; outlined icons, compact size, start-aligned cluster."""
    tier_tab_bar = ft.TabBar(
        tabs=[
            ft.Tab(
                icon=ft.Icon(ft.Icons.MONITOR_OUTLINED, size=icon_size),
                tooltip="Private: we use a local model on this machine (Ollama).",
                height=tab_bar_height,
            ),
            ft.Tab(
                icon=ft.Icon(ft.Icons.HOME_OUTLINED, size=icon_size),
                tooltip="Work: the model is shared with the approved LLM provider (OpenAI-compatible endpoint).",
                height=tab_bar_height,
            ),
            ft.Tab(
                icon=ft.Icon(ft.Icons.CLOUD_OUTLINED, size=icon_size),
                tooltip="Cloud: chat is sent to the selected vendor API using vault credentials.",
                height=tab_bar_height,
            ),
        ],
        scrollable=True,
        secondary=True,
        tab_alignment=ft.TabAlignment.START,
        indicator_color=config.FEDORA_BLUE,
        divider_color=ft.Colors.with_opacity(0.2, ft.Colors.GREY_700),
        label_padding=ft.padding.symmetric(horizontal=6, vertical=0),
        indicator_thickness=1.5,
        height=tab_bar_height,
    )
    tier_pages = ft.TabBarView(
        controls=[
            ft.Container(height=0),
            ft.Container(height=0),
            ft.Container(height=0),
        ],
        height=0,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )
    return ft.Tabs(
        content=ft.Column([tier_tab_bar, tier_pages], tight=True, spacing=0),
        length=3,
        selected_index=selected_index,
        on_change=on_change,
        expand=True,
    )


def ki_tier_display_name(tier: str) -> str:
    """Short tier name for status lines (no leading symbol)."""
    return {
        KI_TIER_LOCAL: "Private",
        KI_TIER_COMPANY: "Work",
        KI_TIER_CLOUD: "Cloud",
    }.get(tier, tier)


class MarkdownStudioLlm:
    """Mixed into ``MarkdownStudio``; expects ``ollama``, ``_db``, tier fields, ``_api_secrets_cache``."""

    ollama: Any
    _db: Any
    ollama_model: str
    ki_tier: str
    cloud_vendor: str
    company_openai_model: str
    company_openai_base_url: str
    cloud_anthropic_model: str
    cloud_openai_model: str
    cloud_google_model: str
    _api_secrets_cache: dict[str, str] | None

    def _make_llm_backend(self) -> LlmChatBackend:
        secrets = self._api_secrets_cache if isinstance(self._api_secrets_cache, dict) else {}
        return LlmChatBackend(
            self.ollama,
            tier=self.ki_tier,
            cloud_vendor=self.cloud_vendor,
            local_model=self.ollama_model,
            company_openai_model=self.company_openai_model,
            company_openai_base_url=self.company_openai_base_url,
            cloud_anthropic_model=self.cloud_anthropic_model,
            cloud_openai_model=self.cloud_openai_model,
            cloud_google_model=self.cloud_google_model,
            secrets=secrets,
        )

    def chat_model_for_requests(self) -> str:
        return self._make_llm_backend().effective_model(None)

    def _persist_ki_tier(self) -> None:
        store_db.settings_set(self._db, store_db.SETTINGS_KI_TIER, self.ki_tier)

    def _persist_cloud_vendor(self) -> None:
        store_db.settings_set(self._db, store_db.SETTINGS_CLOUD_VENDOR, self.cloud_vendor)

    def try_unlock_credential_vault(self, passphrase: str) -> tuple[bool, str]:
        row = vault_store.vault_read()
        if row is None:
            return False, "No encrypted credentials saved yet."
        salt, ciphertext, verifier = row
        try:
            data = crypto_vault.decrypt_secrets_dict(passphrase, salt, ciphertext, verifier)
        except ValueError as exc:
            return False, str(exc)
        self._api_secrets_cache = {str(k): str(v) for k, v in data.items() if v is not None}
        return True, "Credentials unlocked for this session."

    def save_credential_vault_entries(self, passphrase: str, updates: dict[str, str]) -> tuple[bool, str]:
        """Merge ``updates`` (non-empty values) into the vault JSON and re-encrypt."""
        phrase = passphrase.strip()
        if not phrase:
            return False, "Enter an encryption passphrase."
        row = vault_store.vault_read()
        if row is None:
            salt = crypto_vault.new_salt()
            merged: dict[str, str] = {}
        else:
            salt, ciphertext, verifier = row
            try:
                merged = {
                    str(k): str(v)
                    for k, v in crypto_vault.decrypt_secrets_dict(phrase, salt, ciphertext, verifier).items()
                }
            except ValueError as exc:
                return False, str(exc)
        for k, v in updates.items():
            v = (v or "").strip()
            if v:
                merged[str(k)] = v
        try:
            ciphertext, verifier = crypto_vault.encrypt_secrets_dict(phrase, salt, merged)
        except Exception as exc:  # noqa: BLE001
            return False, f"Encrypt failed: {exc}"
        vault_store.vault_write(kdf_salt=salt, ciphertext=ciphertext, verifier=verifier)
        self._api_secrets_cache = dict(merged)
        return True, "Encrypted credentials saved."

    def clear_credential_unlock(self) -> None:
        self._api_secrets_cache = None
