"""Settings → Privacy shield tab."""

from __future__ import annotations

from typing import Any, Callable

import flet as ft
import yaml

from iterthink import config
from iterthink.privacy_shield_settings import (
    PRIORITY_SECTION_TITLES,
    PrivacyCategory,
    categories_for_ui,
    load_categories,
    save_categories,
)


def _ctrl_on_page(ctrl: ft.Control) -> bool:
    try:
        return ctrl.page is not None
    except RuntimeError:
        return False


def build_privacy_settings_tab(
    *,
    studio: Any,
    bootstrap_data: Callable[[], dict],
    on_saved: Callable[[], None] | None = None,
) -> ft.Container:
    _bd0 = bootstrap_data()
    _ps0 = _bd0.get("privacy_shield_enabled", True)
    if not isinstance(_ps0, bool):
        _ps0 = True
    privacy_shield_sw = ft.Switch(
        label="Privacy shield enabled",
        value=_ps0,
    )
    _psr0 = _bd0.get("privacy_shield_reinject", True)
    if not isinstance(_psr0, bool):
        _psr0 = True
    privacy_shield_reinject_sw = ft.Switch(
        label="Restore original values in AI replies",
        value=_psr0,
        disabled=not _ps0,
    )

    _psm0 = _bd0.get("privacy_shield_show_masked_in_chat", False)
    if not isinstance(_psm0, bool):
        _psm0 = False
    privacy_shield_show_masked_sw = ft.Switch(
        label="Show masked text in KI chat (Office/Cloud)",
        value=_psm0,
        disabled=not _ps0,
    )

    cats = load_categories()
    cat_switches: dict[str, ft.Switch] = {}
    category_rows: list[ft.Control] = []

    def _on_master_switch(_e: ft.ControlEvent | None = None) -> None:
        on = bool(privacy_shield_sw.value)
        privacy_shield_reinject_sw.disabled = not on
        privacy_shield_show_masked_sw.disabled = not on
        if _ctrl_on_page(privacy_shield_reinject_sw):
            privacy_shield_reinject_sw.update()
        if _ctrl_on_page(privacy_shield_show_masked_sw):
            privacy_shield_show_masked_sw.update()

    privacy_shield_sw.on_change = _on_master_switch

    last_priority: int | None = None
    for c in categories_for_ui():
        if c.priority != last_priority:
            last_priority = c.priority
            category_rows.append(
                ft.Text(
                    PRIORITY_SECTION_TITLES.get(c.priority, f"Priority {c.priority}"),
                    weight=ft.FontWeight.W_600,
                    size=13,
                )
            )
        sw = ft.Switch(
            label=f"{c.label}  →  {c.example_token(1)}",
            value=c.enabled,
        )
        cat_switches[c.id] = sw
        category_rows.append(sw)

    async def save_privacy_settings(_e: ft.ControlEvent | None = None) -> None:
        try:
            data = bootstrap_data()
        except (OSError, yaml.YAMLError) as ex:
            studio._snack(f"Could not read app config: {ex}")
            return
        data["privacy_shield_enabled"] = bool(privacy_shield_sw.value)
        data["privacy_shield_reinject"] = bool(privacy_shield_reinject_sw.value)
        data["privacy_shield_show_masked_in_chat"] = bool(privacy_shield_show_masked_sw.value)
        try:
            config.write_bootstrap_yaml_text(
                yaml.safe_dump(
                    data,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                    width=88,
                )
            )
        except (OSError, ValueError, yaml.YAMLError) as ex:
            studio._snack(f"Could not save privacy settings: {ex}")
            return

        updated: dict[str, PrivacyCategory] = {}
        for cid, c in cats.items():
            updated[cid] = PrivacyCategory(
                id=c.id,
                label=c.label,
                priority=c.priority,
                placeholder=c.placeholder,
                mode=c.mode,
                enabled=bool(cat_switches[cid].value),
                description=c.description,
            )
        save_categories(updated)
        studio._snack("Privacy shield settings saved.")
        privacy_shield_sw.value = config.PRIVACY_SHIELD_ENABLED
        privacy_shield_reinject_sw.value = config.PRIVACY_SHIELD_REINJECT
        privacy_shield_reinject_sw.disabled = not config.PRIVACY_SHIELD_ENABLED
        privacy_shield_show_masked_sw.value = config.PRIVACY_SHIELD_SHOW_MASKED_IN_CHAT
        privacy_shield_show_masked_sw.disabled = not config.PRIVACY_SHIELD_ENABLED
        if _ctrl_on_page(privacy_shield_show_masked_sw):
            privacy_shield_show_masked_sw.update()
        if hasattr(studio, "_sync_privacy_shield_icon"):
            studio._sync_privacy_shield_icon()
        if on_saved:
            on_saved()

    privacy_disclaimer = ft.Container(
        padding=ft.padding.all(12),
        border_radius=8,
        bgcolor=ft.Colors.with_opacity(0.08, config.ON_SURFACE),
        border=ft.border.all(1, ft.Colors.with_opacity(0.22, config.OUTLINE)),
        content=ft.Column(
            [
                ft.Text(
                    "🛡️ About the Privacy Shield",
                    weight=ft.FontWeight.W_600,
                    size=14,
                    color=config.ON_SURFACE,
                ),
                ft.Text(
                    "The Privacy Shield utilizes a fast, local AI model to identify and mask "
                    "sensitive details (like names, companies, and keys) before sending data to "
                    "cloud networks. Because language models process data statistically, complete "
                    "accuracy cannot be guaranteed. Please do not solely rely on the shield for "
                    "highly regulated data compliance.",
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
                privacy_disclaimer,
                privacy_shield_sw,
                privacy_shield_reinject_sw,
                privacy_shield_show_masked_sw,
                ft.Divider(height=1, color=ft.Colors.with_opacity(0.15, config.OUTLINE)),
                *category_rows,
                ft.FilledButton(
                    "Save privacy shield",
                    on_click=lambda e: studio.page.run_task(save_privacy_settings, e),
                ),
            ],
            tight=True,
            spacing=8,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
    )
