"""Tests for yourcompany.os public API client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from iterthink.services.yourcompanyos_client import (
    fetch_launch_url,
    fetch_projects,
    fetch_workflows,
    filter_workflows_by_type,
    normalize_api_base_url,
    yourcompanyos_configured,
    YcosWorkflow,
)


def test_normalize_api_base_url_strips_trailing_slash() -> None:
    assert normalize_api_base_url("https://app.example.com/") == "https://app.example.com"


def test_normalize_api_base_url_strips_api_path_suffix() -> None:
    assert normalize_api_base_url("https://yourcompanyos.io/api/v1/public/workflows") == "https://yourcompanyos.io"
    assert normalize_api_base_url("https://www.yourcompanyos.io/api/v1/public") == "https://www.yourcompanyos.io"


def test_yourcompanyos_configured_requires_both() -> None:
    assert yourcompanyos_configured(base_url="https://app.example.com", api_key="k") is True
    assert yourcompanyos_configured(base_url="", api_key="k") is False
    assert yourcompanyos_configured(base_url="https://app.example.com", api_key="") is False


def test_filter_workflows_by_type() -> None:
    workflows = (
        YcosWorkflow(
            process_key="a",
            name="A",
            description="",
            scope="company",
            workflow_type="orga",
            category="",
            icon="",
            tags=(),
            launch_path="/go/a",
        ),
        YcosWorkflow(
            process_key="b",
            name="B",
            description="",
            scope="project",
            workflow_type="proj",
            category="",
            icon="",
            tags=(),
            launch_path="/go/b",
        ),
    )
    orga = filter_workflows_by_type(workflows, "orga")
    assert len(orga) == 1
    assert orga[0].process_key == "a"
    proj = filter_workflows_by_type(workflows, "proj")
    assert len(proj) == 1
    assert proj[0].process_key == "b"


def _mock_response(*, status_code: int = 200, payload: dict) -> httpx.Response:
    req = httpx.Request("GET", "https://app.example.com/api/v1/public/workflows")
    return httpx.Response(status_code, request=req, content=json.dumps(payload).encode())


@pytest.mark.asyncio
async def test_fetch_workflows_sends_x_api_key() -> None:
    payload = {
        "tenant": {"tenant_id": "acme", "name": "Acme"},
        "workflows": [
            {
                "process_key": "onboard",
                "name": "Onboard",
                "description": "d",
                "scope": "company",
                "workflow_type": "orga",
                "category": "hr",
                "icon": "🧭",
                "tags": ["hr"],
                "launch_path": "/go/acme/workflows/orga/onboard/start",
            }
        ],
    }
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=_mock_response(payload=payload))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("iterthink.services.yourcompanyos_client.httpx.AsyncClient", return_value=mock_client):
        catalog, err = await fetch_workflows("https://app.example.com/", "secret-key")

    assert err is None
    assert catalog is not None
    assert catalog.tenant.name == "Acme"
    assert len(catalog.workflows) == 1
    assert catalog.workflows[0].process_key == "onboard"
    mock_client.get.assert_awaited_once()
    _args, kwargs = mock_client.get.await_args
    assert kwargs["headers"]["X-API-Key"] == "secret-key"
    assert _args[0] == "https://app.example.com/api/v1/public/workflows"


@pytest.mark.asyncio
async def test_fetch_projects_parses_list() -> None:
    payload = {
        "tenant": {"tenant_id": "acme", "name": "Acme"},
        "projects": [
            {"id": 42, "project_name": "Office", "project_number": "2024-001"},
        ],
    }
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=_mock_response(payload=payload))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("iterthink.services.yourcompanyos_client.httpx.AsyncClient", return_value=mock_client):
        listing, err = await fetch_projects("https://app.example.com", "key")

    assert err is None
    assert listing is not None
    assert listing.projects[0].id == 42
    assert listing.projects[0].project_name == "Office"


@pytest.mark.asyncio
async def test_fetch_launch_url_proj_autostart_query() -> None:
    payload = {
        "process_key": "plan_to_cost",
        "workflow_type": "proj",
        "scope": "project",
        "launch_path": "/go/acme/workflows/proj/plan_to_cost/start?project_id=42&autostart=true",
        "url": "https://app.example.com/go/acme/workflows/proj/plan_to_cost/start?project_id=42&autostart=true",
        "project_id": 42,
        "autostart": True,
    }
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=_mock_response(payload=payload))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("iterthink.services.yourcompanyos_client.httpx.AsyncClient", return_value=mock_client):
        launch, err = await fetch_launch_url(
            "https://app.example.com",
            "key",
            process_key="plan_to_cost",
            workflow_type="proj",
            project_id=42,
            autostart=True,
        )

    assert err is None
    assert launch is not None
    assert launch.url.endswith("autostart=true")
    mock_client.get.assert_awaited_once()
    url = mock_client.get.await_args[0][0]
    assert "workflow_type=proj" in url
    assert "project_id=42" in url
    assert "autostart=true" in url
    headers = mock_client.get.await_args[1]["headers"]
    assert headers["X-API-Key"] == "key"


@pytest.mark.asyncio
async def test_fetch_launch_url_requires_project_for_proj() -> None:
    launch, err = await fetch_launch_url(
        "https://app.example.com",
        "key",
        process_key="plan_to_cost",
        workflow_type="proj",
        project_id=None,
    )
    assert launch is None
    assert err is not None
    assert "project_id" in err
