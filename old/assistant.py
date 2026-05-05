"""Assistant panel HTMX endpoints."""

from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse

from app_server.core import jinja_env, get_csrf_token
from app_server.core.checks_loader import get_check_definitions
from app_server.core.assistant_panel import AssistantMode, assistant_panel_context

router = APIRouter()

TEMPLATE_BY_MODE: Dict[AssistantMode, str] = {
    "edit": "assistant_panel/assistant_edit.html",
    "analyse": "assistant_panel/assistant_analyse.html",
    "check": "assistant_panel/assistant_check.html",
}


@router.get("/assistant/panel", response_class=HTMLResponse)
async def get_assistant_panel_fragment(
    request: Request,
    response: Response,
    mode: Optional[str] = Query(None, description="Requested assistant mode"),
    context: Optional[str] = Query(None, description="UI context (writing, reviewing, etc.)"),
    doc_id: Optional[int] = Query(None, description="Document id if available"),
    doc_status: Optional[str] = Query(None, description="Current document status if available"),
) -> HTMLResponse:
    """Return the assistant panel content for a specific mode/context."""

    has_document = doc_id is not None
    config = assistant_panel_context(
        active_tab=context, has_document=has_document, doc_status=doc_status
    )
    available_modes = config.get("available_modes", [])

    resolved_mode: AssistantMode
    if mode and mode in available_modes:
        resolved_mode = mode  # type: ignore[assignment]
    else:
        resolved_mode = config["default_mode"]  # type: ignore[assignment]

    template_name = TEMPLATE_BY_MODE.get(resolved_mode)
    if not template_name:
        raise HTTPException(status_code=404, detail=f"No template for mode '{resolved_mode}'")

    template = jinja_env.get_template(template_name)
    # Load checks based on context: comparing -> version_comparison, dashboard -> document_comparison
    context_for_checks = config.get("context")
    check_definitions = get_check_definitions(context_for_checks) if resolved_mode == "check" else []
    # Convert CheckDefinition objects to dicts for template rendering
    checks = []
    for check_def in check_definitions:
        legend_dict = None
        if check_def.legend:
            legend_dict = {
                "symbols": [
                    {
                        "symbol": sym.symbol,
                        "label": sym.label,
                        "color": sym.color,
                    }
                    for sym in check_def.legend.symbols
                ]
            }
        checks.append({
            "id": check_def.id,
            "label": check_def.label,
            "description": check_def.description,
            "icon": check_def.icon,
            "panel_template": check_def.panel_template,
            "category": check_def.category,
            "legend": legend_dict,
        })
    csrf_token = get_csrf_token(request, response) or ""
    html = template.render(
        mode=resolved_mode,
        context=config.get("context"),
        doc_id=doc_id,
        assistant_config=config,
        checks=checks,
        csrf_token=csrf_token,
    )
    return HTMLResponse(content=html)


