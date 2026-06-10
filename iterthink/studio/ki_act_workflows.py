"""KI Act sidebar: yourcompany.os workflow browser and launch."""

from __future__ import annotations

from typing import Any

import flet as ft

from iterthink import config
from iterthink.ai.llm_router import SECRET_YOURCOMPANYOS_API
from iterthink.services.yourcompanyos_client import (
    YcosWorkflow,
    YcosWorkflowCatalog,
    fetch_launch_url,
    fetch_projects,
    fetch_workflows,
    filter_workflows_by_type,
    yourcompanyos_configured,
)

from .constants import (
    PROJECT_PAGE_LINK_LABEL,
    PROJECT_PAGE_URL,
    YOURCOMPANYOS_DISPLAY,
    yourcompanyos_brand_display,
)
from .util import ctrl_on_page as _ctrl_on_page


def yourcompanyos_api_key(studio: Any) -> str:
    ensure = getattr(studio, "ensure_credential_vault_unlocked", None)
    if callable(ensure):
        ensure()
    cache = getattr(studio, "_api_secrets_cache", None)
    if not isinstance(cache, dict):
        return ""
    return (cache.get(SECRET_YOURCOMPANYOS_API) or "").strip()


def studio_yourcompanyos_configured(studio: Any) -> bool:
    return yourcompanyos_configured(
        base_url=getattr(studio, "yourcompanyos_api_base_url", "") or "",
        api_key=yourcompanyos_api_key(studio),
    )


def build_ki_act_panel(*, studio: Any, page: ft.Page) -> ft.Column:
    scope_state: dict[str, str] = {"value": "orga"}
    catalog_holder: dict[str, YcosWorkflowCatalog | None] = {"value": None}
    selected_project: dict[str, int | None] = {"value": None}

    status_txt = ft.Text("", size=12, color=config.ON_SURFACE_VARIANT)
    project_dd = ft.Dropdown(
        label="Project",
        options=[],
        dense=True,
        expand=True,
        visible=False,
    )
    workflow_list = ft.Column(spacing=4, tight=True)
    workflow_scroll = ft.Container(
        content=workflow_list,
        expand=True,
    )

    scope_seg = ft.SegmentedButton(
        selected={"orga"},
        allow_empty_selection=False,
        segments=[
            ft.Segment(value="orga", label=ft.Text("Organization")),
            ft.Segment(value="proj", label=ft.Text("Project")),
        ],
    )

    def _rebuild_workflow_rows() -> None:
        workflow_list.controls.clear()
        cat = catalog_holder["value"]
        if cat is None:
            workflow_list.controls.append(
                ft.Text("No workflows loaded.", size=12, color=config.ON_SURFACE_VARIANT)
            )
            if _ctrl_on_page(workflow_list):
                workflow_list.update()
            return
        wt = scope_state["value"]
        rows = filter_workflows_by_type(cat.workflows, wt)
        if not rows:
            workflow_list.controls.append(
                ft.Text("No workflows for this scope.", size=12, color=config.ON_SURFACE_VARIANT)
            )
            if _ctrl_on_page(workflow_list):
                workflow_list.update()
            return
        for wf in rows:
            workflow_list.controls.append(_workflow_row(wf, wt))
        if _ctrl_on_page(workflow_list):
            workflow_list.update()

    def _workflow_row(wf: YcosWorkflow, workflow_type: str) -> ft.Control:
        label = wf.name
        if wf.icon:
            label = f"{wf.icon} {label}"
        subtitle = (wf.description or wf.category or "").strip()

        async def _launch(_e: ft.ControlEvent, _wf: YcosWorkflow = wf, _wt: str = workflow_type) -> None:
            await _launch_workflow(_wf, _wt)

        return ft.Container(
            content=ft.Column(
                [
                    ft.TextButton(
                        content=ft.Text(label, size=13, color=config.ON_SURFACE),
                        style=ft.ButtonStyle(
                            padding=ft.padding.symmetric(horizontal=0, vertical=2),
                        ),
                        on_click=_launch,
                    ),
                    ft.Text(
                        subtitle,
                        size=11,
                        color=config.ON_SURFACE_VARIANT,
                        visible=bool(subtitle),
                    ),
                ],
                spacing=0,
                tight=True,
            ),
            padding=ft.padding.only(bottom=4),
        )

    async def _launch_workflow(wf: YcosWorkflow, workflow_type: str) -> None:
        base = getattr(studio, "yourcompanyos_api_base_url", "") or ""
        key = yourcompanyos_api_key(studio)
        if not yourcompanyos_configured(base_url=base, api_key=key):
            studio._snack(f"Configure {YOURCOMPANYOS_DISPLAY} in Settings.")
            return
        pid: int | None = None
        if workflow_type == "proj":
            pid = selected_project["value"]
            if pid is None:
                studio._snack("Select a project first.")
                return
        status_txt.value = f"Starting {wf.name}…"
        status_txt.color = config.ON_SURFACE_VARIANT
        if _ctrl_on_page(status_txt):
            status_txt.update()
        launch, err = await fetch_launch_url(
            base,
            key,
            process_key=wf.process_key,
            workflow_type=workflow_type,
            project_id=pid,
            autostart=True,
        )
        if err or launch is None:
            status_txt.value = err or "Launch failed."
            status_txt.color = ft.Colors.RED_400
            if _ctrl_on_page(status_txt):
                status_txt.update()
            studio._snack(status_txt.value)
            return
        try:
            await page.launch_url(launch.url)
            status_txt.value = f"Opened {wf.name}"
            status_txt.color = ft.Colors.GREEN_400
        except Exception as exc:  # noqa: BLE001
            status_txt.value = f"Could not open URL: {exc}"
            status_txt.color = ft.Colors.RED_400
            studio._snack(status_txt.value)
        if _ctrl_on_page(status_txt):
            status_txt.update()

    def _on_scope_change(e: ft.ControlEvent) -> None:
        sel = list(getattr(e.control, "selected", []) or [])
        if not sel:
            return
        scope_state["value"] = str(sel[0])
        project_dd.visible = scope_state["value"] == "proj"
        _rebuild_workflow_rows()
        if _ctrl_on_page(project_dd):
            project_dd.update()

    scope_seg.on_change = _on_scope_change

    def _on_project_change(e: ft.ControlEvent) -> None:
        raw = getattr(e.control, "value", None)
        if raw is None or raw == "":
            selected_project["value"] = None
            return
        try:
            selected_project["value"] = int(raw)
        except (TypeError, ValueError):
            selected_project["value"] = None

    project_dd.on_change = _on_project_change

    async def refresh_act_workflows() -> None:
        if not studio_yourcompanyos_configured(studio):
            _show_unconfigured()
            return
        _show_configured()
        base = getattr(studio, "yourcompanyos_api_base_url", "") or ""
        key = yourcompanyos_api_key(studio)
        status_txt.value = "Loading workflows…"
        status_txt.color = config.ON_SURFACE_VARIANT
        if _ctrl_on_page(status_txt):
            status_txt.update()
        catalog, err = await fetch_workflows(base, key)
        if err or catalog is None:
            status_txt.value = err or "Failed to load workflows."
            status_txt.color = ft.Colors.RED_400
            catalog_holder["value"] = None
            _rebuild_workflow_rows()
            if _ctrl_on_page(status_txt):
                status_txt.update()
            return
        catalog_holder["value"] = catalog
        heading.value = f"{yourcompanyos_brand_display(catalog.tenant.name)} workflows"
        status_txt.value = f"{len(catalog.workflows)} workflow(s)"
        status_txt.color = config.ON_SURFACE_VARIANT
        if _ctrl_on_page(heading):
            heading.update()
        if _ctrl_on_page(status_txt):
            status_txt.update()
        _rebuild_workflow_rows()

        proj_list, proj_err = await fetch_projects(base, key)
        project_dd.options = []
        selected_project["value"] = None
        if proj_list is not None and not proj_err:
            for p in proj_list.projects:
                label = p.project_name
                if p.project_number:
                    label = f"{p.project_name} ({p.project_number})"
                project_dd.options.append(ft.dropdown.Option(str(p.id), label))
            if proj_list.projects:
                project_dd.value = str(proj_list.projects[0].id)
                selected_project["value"] = proj_list.projects[0].id
        if _ctrl_on_page(project_dd):
            project_dd.update()

    async def _open_settings_async() -> None:
        from . import settings_ui

        await settings_ui.open_settings_dialog(studio)
        focus = getattr(studio, "_focus_yourcompanyos_settings_panel", None)
        if callable(focus):
            import asyncio

            await asyncio.sleep(0.35)
            focus()

    def _open_settings(_e: ft.ControlEvent) -> None:
        page.run_task(_open_settings_async)

    _link_style = ft.TextStyle(
        color=config.PRIMARY_COLOR,
        size=12,
        decoration=ft.TextDecoration.UNDERLINE,
    )

    unconfigured_col = ft.Column(
        [
            ft.Text(
                spans=[
                    ft.TextSpan("Connect "),
                    ft.TextSpan(
                        YOURCOMPANYOS_DISPLAY,
                        style=ft.TextStyle(
                            color=config.ON_SURFACE_VARIANT,
                            size=12,
                            weight=ft.FontWeight.W_600,
                        ),
                    ),
                    ft.TextSpan(
                        " to start workflows from document changes.",
                        style=ft.TextStyle(color=config.ON_SURFACE_VARIANT, size=12),
                    ),
                ],
                selectable=True,
            ),
            ft.Text(
                spans=[
                    ft.TextSpan(PROJECT_PAGE_LINK_LABEL, url=PROJECT_PAGE_URL, style=_link_style),
                ],
            ),
            ft.TextButton(
                content=ft.Text("Settings", size=11),
                on_click=_open_settings,
                style=ft.ButtonStyle(
                    color=config.ON_SURFACE_VARIANT,
                    padding=ft.padding.symmetric(horizontal=0, vertical=0),
                ),
            ),
        ],
        spacing=6,
        tight=True,
    )

    configured_col = ft.Column(
        [
            scope_seg,
            project_dd,
            ft.Row(
                [
                    ft.OutlinedButton(
                        "Refresh",
                        on_click=lambda _e: page.run_task(refresh_act_workflows),
                    ),
                ],
            ),
            status_txt,
            workflow_scroll,
        ],
        spacing=8,
        expand=True,
    )

    heading = ft.Text("Workflows", size=13, weight=ft.FontWeight.W_600, color=config.ON_SURFACE)
    body_host = ft.Container(content=unconfigured_col, expand=True)

    def _show_unconfigured() -> None:
        body_host.content = unconfigured_col
        if _ctrl_on_page(body_host):
            body_host.update()

    def _show_configured() -> None:
        body_host.content = configured_col
        project_dd.visible = scope_state["value"] == "proj"
        if _ctrl_on_page(body_host):
            body_host.update()

    panel = ft.Column(
        [heading, body_host],
        spacing=8,
        expand=True,
    )

    studio._ki_act_refresh_workflows = refresh_act_workflows
    studio._ki_act_show_configured = _show_configured
    studio._ki_act_show_unconfigured = _show_unconfigured

    if studio_yourcompanyos_configured(studio):
        _show_configured()
    else:
        _show_unconfigured()

    return panel
