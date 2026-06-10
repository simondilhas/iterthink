"""yourcompany.os public API client (workflows, projects, launch URLs)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

API_PREFIX = "/api/v1/public"


def normalize_api_base_url(base_url: str) -> str:
    u = (base_url or "").strip().rstrip("/")
    for suffix in ("/api/v1/public/workflows", "/api/v1/public"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    return u


def yourcompanyos_configured(*, base_url: str, api_key: str) -> bool:
    return bool(normalize_api_base_url(base_url) and (api_key or "").strip())


@dataclass(frozen=True)
class YcosTenant:
    tenant_id: str
    name: str


@dataclass(frozen=True)
class YcosWorkflow:
    process_key: str
    name: str
    description: str
    scope: str
    workflow_type: str
    category: str
    icon: str
    tags: tuple[str, ...]
    launch_path: str


@dataclass(frozen=True)
class YcosProject:
    id: int
    project_name: str
    project_number: str


@dataclass(frozen=True)
class YcosWorkflowCatalog:
    tenant: YcosTenant
    workflows: tuple[YcosWorkflow, ...]


@dataclass(frozen=True)
class YcosProjectList:
    tenant: YcosTenant
    projects: tuple[YcosProject, ...]


@dataclass(frozen=True)
class YcosLaunchUrl:
    process_key: str
    workflow_type: str
    scope: str
    launch_path: str
    url: str
    project_id: int | None
    autostart: bool


def filter_workflows_by_type(
    workflows: tuple[YcosWorkflow, ...],
    workflow_type: str,
) -> tuple[YcosWorkflow, ...]:
    wt = (workflow_type or "").strip().lower()
    return tuple(w for w in workflows if (w.workflow_type or "").strip().lower() == wt)


def _parse_tenant(raw: Any) -> YcosTenant | None:
    if not isinstance(raw, dict):
        return None
    tid = raw.get("tenant_id")
    name = raw.get("name")
    if not isinstance(tid, str) or not isinstance(name, str):
        return None
    return YcosTenant(tenant_id=tid.strip(), name=name.strip())


def _parse_workflow(raw: Any) -> YcosWorkflow | None:
    if not isinstance(raw, dict):
        return None
    pk = raw.get("process_key")
    name = raw.get("name")
    if not isinstance(pk, str) or not isinstance(name, str):
        return None
    desc = raw.get("description")
    scope = raw.get("scope")
    wtype = raw.get("workflow_type")
    category = raw.get("category")
    icon = raw.get("icon")
    launch_path = raw.get("launch_path")
    tags_raw = raw.get("tags")
    tags: list[str] = []
    if isinstance(tags_raw, list):
        for t in tags_raw:
            if isinstance(t, str) and t.strip():
                tags.append(t.strip())
    return YcosWorkflow(
        process_key=pk.strip(),
        name=name.strip(),
        description=(desc or "").strip() if isinstance(desc, str) else "",
        scope=(scope or "").strip() if isinstance(scope, str) else "",
        workflow_type=(wtype or "").strip() if isinstance(wtype, str) else "",
        category=(category or "").strip() if isinstance(category, str) else "",
        icon=(icon or "").strip() if isinstance(icon, str) else "",
        tags=tuple(tags),
        launch_path=(launch_path or "").strip() if isinstance(launch_path, str) else "",
    )


def _parse_project(raw: Any) -> YcosProject | None:
    if not isinstance(raw, dict):
        return None
    pid = raw.get("id")
    pname = raw.get("project_name")
    if pid is None or not isinstance(pname, str):
        return None
    try:
        project_id = int(pid)
    except (TypeError, ValueError):
        return None
    pnum = raw.get("project_number")
    return YcosProject(
        id=project_id,
        project_name=pname.strip(),
        project_number=(pnum or "").strip() if isinstance(pnum, str) else "",
    )


def _api_headers(api_key: str, *, accept_language: str | None = None) -> dict[str, str]:
    headers = {"X-API-Key": (api_key or "").strip()}
    if accept_language:
        headers["Accept-Language"] = accept_language.strip()
    return headers


def _http_error(exc: httpx.HTTPStatusError) -> str:
    body = (exc.response.text or "")[:200].strip()
    if body:
        return f"HTTP {exc.response.status_code}: {body}"
    return f"HTTP {exc.response.status_code}"


async def fetch_workflows(
    base_url: str,
    api_key: str,
    *,
    accept_language: str | None = None,
) -> tuple[YcosWorkflowCatalog | None, str | None]:
    base = normalize_api_base_url(base_url)
    key = (api_key or "").strip()
    if not base:
        return None, "API base URL required."
    if not key:
        return None, "API key required."
    url = f"{base}{API_PREFIX}/workflows"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                url,
                headers=_api_headers(key, accept_language=accept_language),
                timeout=45.0,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as exc:
        return None, _http_error(exc)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"

    if not isinstance(data, dict):
        return None, "Unexpected workflows response."
    tenant = _parse_tenant(data.get("tenant"))
    if tenant is None:
        return None, "Unexpected workflows response (tenant)."
    rows = data.get("workflows")
    workflows: list[YcosWorkflow] = []
    if isinstance(rows, list):
        for row in rows:
            w = _parse_workflow(row)
            if w is not None:
                workflows.append(w)
    return YcosWorkflowCatalog(tenant=tenant, workflows=tuple(workflows)), None


async def fetch_projects(
    base_url: str,
    api_key: str,
) -> tuple[YcosProjectList | None, str | None]:
    base = normalize_api_base_url(base_url)
    key = (api_key or "").strip()
    if not base:
        return None, "API base URL required."
    if not key:
        return None, "API key required."
    url = f"{base}{API_PREFIX}/projects"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                url,
                headers=_api_headers(key),
                timeout=45.0,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as exc:
        return None, _http_error(exc)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"

    if not isinstance(data, dict):
        return None, "Unexpected projects response."
    tenant = _parse_tenant(data.get("tenant"))
    if tenant is None:
        return None, "Unexpected projects response (tenant)."
    rows = data.get("projects")
    projects: list[YcosProject] = []
    if isinstance(rows, list):
        for row in rows:
            p = _parse_project(row)
            if p is not None:
                projects.append(p)
    return YcosProjectList(tenant=tenant, projects=tuple(projects)), None


async def fetch_launch_url(
    base_url: str,
    api_key: str,
    *,
    process_key: str,
    workflow_type: str,
    project_id: int | None = None,
    autostart: bool = True,
) -> tuple[YcosLaunchUrl | None, str | None]:
    base = normalize_api_base_url(base_url)
    key = (api_key or "").strip()
    pk = (process_key or "").strip()
    wt = (workflow_type or "").strip().lower()
    if not base:
        return None, "API base URL required."
    if not key:
        return None, "API key required."
    if not pk:
        return None, "process_key required."
    if wt not in ("orga", "proj"):
        return None, "workflow_type must be orga or proj."
    if wt == "proj" and project_id is None:
        return None, "project_id required for project workflows."

    params: dict[str, str] = {
        "workflow_type": wt,
        "autostart": "true" if autostart else "false",
    }
    if project_id is not None:
        params["project_id"] = str(project_id)
    qs = urlencode(params)
    url = f"{base}{API_PREFIX}/workflows/{pk}/launch-url?{qs}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                url,
                headers=_api_headers(key),
                timeout=45.0,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as exc:
        return None, _http_error(exc)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"

    if not isinstance(data, dict):
        return None, "Unexpected launch-url response."
    out_pk = data.get("process_key")
    out_url = data.get("url")
    if not isinstance(out_pk, str) or not isinstance(out_url, str):
        return None, "Unexpected launch-url response (url)."
    out_wt = data.get("workflow_type")
    out_scope = data.get("scope")
    out_path = data.get("launch_path")
    out_pid = data.get("project_id")
    out_auto = data.get("autostart")
    pid: int | None
    if out_pid is None:
        pid = None
    else:
        try:
            pid = int(out_pid)
        except (TypeError, ValueError):
            pid = None
    return YcosLaunchUrl(
        process_key=out_pk.strip(),
        workflow_type=(out_wt or "").strip() if isinstance(out_wt, str) else wt,
        scope=(out_scope or "").strip() if isinstance(out_scope, str) else "",
        launch_path=(out_path or "").strip() if isinstance(out_path, str) else "",
        url=out_url.strip(),
        project_id=pid,
        autostart=bool(out_auto) if isinstance(out_auto, bool) else autostart,
    ), None
