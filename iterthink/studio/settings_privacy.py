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

_TIER_COL_W = 72


def _ctrl_on_page(ctrl: ft.Control) -> bool:
    try:
        return ctrl.page is not None
    except RuntimeError:
        return False


def _bool_or(value: Any, default: bool) -> bool:
    return bool(value) if isinstance(value, bool) else default


def _legacy_privacy_enabled(bootstrap: dict) -> bool:
    legacy = bootstrap.get("privacy_shield_enabled")
    if isinstance(legacy, bool):
        return legacy
    company = bootstrap.get("privacy_shield_company_enabled")
    cloud = bootstrap.get("privacy_shield_cloud_enabled")
    if isinstance(company, bool) or isinstance(cloud, bool):
        return _bool_or(company, True) or _bool_or(cloud, True)
    return True


def _privacy_master_on(company_sw: ft.Switch, cloud_sw: ft.Switch) -> bool:
    return bool(company_sw.value) or bool(cloud_sw.value)


def build_privacy_settings_tab(
    *,
    studio: Any,
    bootstrap_data: Callable[[], dict],
    on_saved: Callable[[], None] | None = None,
) -> ft.Container:
    _bd0 = bootstrap_data()
    _legacy_on = _legacy_privacy_enabled(_bd0)
    _psc0 = _bd0.get("privacy_shield_company_enabled")
    _psz0 = _bd0.get("privacy_shield_cloud_enabled")
    privacy_shield_company_sw = ft.Switch(
        label="Company",
        value=_bool_or(_psc0, _legacy_on),
    )
    privacy_shield_cloud_sw = ft.Switch(
        label="Public",
        value=_bool_or(_psz0, _legacy_on),
    )

    _psr0 = _bd0.get("privacy_shield_reinject", True)
    privacy_shield_reinject_sw = ft.Switch(
        label="Restore original values in AI replies",
        value=_bool_or(_psr0, True),
        disabled=not _privacy_master_on(privacy_shield_company_sw, privacy_shield_cloud_sw),
    )

    _psm0 = _bd0.get("privacy_shield_show_masked_in_chat", False)
    privacy_shield_show_masked_sw = ft.Switch(
        label="Show masked text in KI chat (Office/Cloud)",
        value=_bool_or(_psm0, False),
        disabled=not _privacy_master_on(privacy_shield_company_sw, privacy_shield_cloud_sw),
    )

    cats = load_categories()
    cat_company_switches: dict[str, ft.Switch] = {}
    cat_cloud_switches: dict[str, ft.Switch] = {}
    category_rows: list[ft.Control] = []

    def _sync_dependent_switches() -> None:
        on = _privacy_master_on(privacy_shield_company_sw, privacy_shield_cloud_sw)
        privacy_shield_reinject_sw.disabled = not on
        privacy_shield_show_masked_sw.disabled = not on
        for ctrl in (privacy_shield_reinject_sw, privacy_shield_show_masked_sw):
            if _ctrl_on_page(ctrl):
                ctrl.update()

    def _on_master_switch(_e: ft.ControlEvent | None = None) -> None:
        _sync_dependent_switches()

    privacy_shield_company_sw.on_change = _on_master_switch
    privacy_shield_cloud_sw.on_change = _on_master_switch

    category_rows.append(
        ft.Row(
            [
                ft.Container(ft.Text("Category", weight=ft.FontWeight.W_600, size=12), expand=True),
                ft.Container(
                    ft.Text("Company", weight=ft.FontWeight.W_600, size=12, text_align=ft.TextAlign.CENTER),
                    width=_TIER_COL_W,
                ),
                ft.Container(
                    ft.Text("Public", weight=ft.FontWeight.W_600, size=12, text_align=ft.TextAlign.CENTER),
                    width=_TIER_COL_W,
                ),
            ],
            spacing=8,
        )
    )

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
        sw_company = ft.Switch(value=c.enabled_company)
        sw_cloud = ft.Switch(value=c.enabled_cloud)
        cat_company_switches[c.id] = sw_company
        cat_cloud_switches[c.id] = sw_cloud
        category_rows.append(
            ft.Row(
                [
                    ft.Container(
                        ft.Text(f"{c.label}  →  {c.example_token(1)}", size=13),
                        expand=True,
                    ),
                    ft.Container(sw_company, width=_TIER_COL_W, alignment=ft.Alignment.CENTER),
                    ft.Container(sw_cloud, width=_TIER_COL_W, alignment=ft.Alignment.CENTER),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )

    async def save_privacy_settings(_e: ft.ControlEvent | None = None) -> None:
        try:
            data = bootstrap_data()
        except (OSError, yaml.YAMLError) as ex:
            studio._snack(f"Could not read app config: {ex}")
            return
        data["privacy_shield_company_enabled"] = bool(privacy_shield_company_sw.value)
        data["privacy_shield_cloud_enabled"] = bool(privacy_shield_cloud_sw.value)
        data.pop("privacy_shield_enabled", None)
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
                enabled_company=bool(cat_company_switches[cid].value),
                enabled_cloud=bool(cat_cloud_switches[cid].value),
                description=c.description,
            )
        save_categories(updated)
        studio._snack("Privacy shield settings saved.")
        privacy_shield_company_sw.value = config.PRIVACY_SHIELD_COMPANY_ENABLED
        privacy_shield_cloud_sw.value = config.PRIVACY_SHIELD_CLOUD_ENABLED
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
                ft.Text("Privacy shield enabled", weight=ft.FontWeight.W_600, size=13),
                ft.Row(
                    [
                        privacy_shield_company_sw,
                        privacy_shield_cloud_sw,
                    ],
                    spacing=16,
                ),
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
