"""HTMX fragments and APIs for assistant checks."""

from __future__ import annotations

import copy
import json
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app_server.core import jinja_env
from app_server.core.checks_loader import get_check_definition
from app_server.core.db import get_db
from app_server.db.models import AssistantCheckResult, Comment, Document, Version, VersionParagraph
from app_server.services.auth import get_current_user
from app_server.services.checks import CheckRegistry, CheckResult, ImpactOnProjectRunner
from app_server.services.checks.impact_feedback import (
    save_manual_override,
)
from app_server.services.documents import (
    verify_document_access,
    verify_version_access,
)
from app_server.services.feature_access import check_feature_access, record_llm_usage
from app_server.services.checks.reference_document_helpers import get_project_reference_documents

router = APIRouter()


@router.get(
    "/assistant/checks/reference-documents",
    response_class=JSONResponse,
)
async def get_reference_documents(
    request: Request,
    doc_id: int = Query(..., description="Document id"),
    content_types: str = Query(..., description="Comma-separated list of content types"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Get reference documents for a project matching the specified content types."""
    import logging
    logger = logging.getLogger(__name__)
    
    current_user = get_current_user(request, db)
    doc = verify_document_access(doc_id, current_user, db)
    
    content_types_list = [ct.strip() for ct in content_types.split(",") if ct.strip()]
    logger.info(f"Getting reference documents for doc_id={doc_id}, project={doc.project}, content_types={content_types_list}")
    
    if not content_types_list:
        return JSONResponse(content={"reference_documents": []})
    
    reference_docs = get_project_reference_documents(
        project=doc.project,
        content_types=content_types_list,
        db=db,
    )
    
    logger.info(f"Found {len(reference_docs)} reference documents")
    for ref_doc in reference_docs:
        logger.info(f"  - {ref_doc.title} (id={ref_doc.id}, content_type={ref_doc.content_type}, status={ref_doc.status}, project={ref_doc.project})")
    
    result = [
        {
            "id": ref_doc.id,
            "title": ref_doc.title,
            "content_type": ref_doc.content_type or "unknown",
            "status": ref_doc.status or "draft",
        }
        for ref_doc in reference_docs
    ]
    
    return JSONResponse(content={"reference_documents": result})


@router.get("/assistant/checks/{check_id}", response_class=HTMLResponse)
async def load_check_panel(
    check_id: str,
    doc_id: int | None = Query(None),
    context: str | None = Query(None),
    old_version_id: int | None = Query(None),
    new_version_id: int | None = Query(None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Return the static HTML fragment for the requested check.
    
    If old_version_id and new_version_id are provided, checks for existing results
    and passes them to the template for auto-rendering.
    """

    try:
        # Try to get check definition, passing context if available
        definition = get_check_definition(check_id, context)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    template = jinja_env.get_template(definition.panel_template)
    
    # Convert legend to dict for JSON serialization in template
    legend_dict = None
    if definition.legend:
        legend_dict = {
            "symbols": [
                {
                    "symbol": sym.symbol,
                    "label": sym.label,
                    "color": sym.color,
                }
                for sym in definition.legend.symbols
            ]
        }
    
    # Check for existing results - handle both version comparison and document comparison
    existing_results = None
    has_existing_results = False
    if doc_id:
        if definition.category == "document_comparison":
            # Document comparison: only need version_id
            if new_version_id:  # Use new_version_id as the version_id for document comparison
                result = (
                    db.query(AssistantCheckResult)
                    .filter(
                        AssistantCheckResult.check_id == check_id,
                        AssistantCheckResult.doc_id == doc_id,
                        AssistantCheckResult.old_version_id.is_(None),  # Document comparison has NULL old_version_id
                        AssistantCheckResult.new_version_id == new_version_id,
                        AssistantCheckResult.status == "completed",
                    )
                    .order_by(AssistantCheckResult.completed_at.desc())
                    .first()
                )
                
                if result and result.result_json:
                    existing_results = result.result_json
                    has_existing_results = True
        else:
            # Version comparison: need both old and new version IDs
            if old_version_id and new_version_id:
                result = (
                    db.query(AssistantCheckResult)
                    .filter(
                        AssistantCheckResult.check_id == check_id,
                        AssistantCheckResult.doc_id == doc_id,
                        AssistantCheckResult.old_version_id == old_version_id,
                        AssistantCheckResult.new_version_id == new_version_id,
                        AssistantCheckResult.status == "completed",
                    )
                    .order_by(AssistantCheckResult.completed_at.desc())
                    .first()
                )
                
                if result and result.result_json:
                    existing_results = result.result_json
                    has_existing_results = True
    
    # Create a dict version of check for template rendering
    check_dict = {
        "id": definition.id,
        "label": definition.label,
        "description": definition.description,
        "icon": definition.icon,
        "panel_template": definition.panel_template,
        "category": definition.category,
        "legend": legend_dict,
        "system_prompt": definition.system_prompt,
        "prompt_instructions": definition.prompt_instructions,
        "reference_content_types": definition.reference_content_types,
    }
    
    html = template.render(
        check=check_dict,
        doc_id=doc_id,
        context=context,
        old_version_id=old_version_id,
        new_version_id=new_version_id,
        existing_results=existing_results,
        has_existing_results=has_existing_results,
    )
    return HTMLResponse(content=html)


@router.get(
    "/assistant/checks/impact_on_project/results",
    response_class=JSONResponse,
)
async def run_impact_on_project_check(
    request: Request,
    doc_id: int = Query(..., description="Document id"),
    old_version_id: Optional[int] = Query(
        None, description="Old version id for comparison"
    ),
    new_version_id: Optional[int] = Query(
        None, description="New version id for comparison"
    ),
    refresh: bool = Query(
        False,
        description="Force refresh even if cached result exists",
    ),
    version_number: Optional[int] = Query(
        None, description="Specific version number to load (defaults to latest)"
    ),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Compute or fetch the impact-on-project assessment."""

    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")

    current_user = get_current_user(request, db)
    doc = verify_document_access(doc_id, current_user, db)

    resolved_old_id, resolved_new_id = _resolve_version_ids(
        doc_id=doc.id,
        explicit_old=old_version_id,
        explicit_new=new_version_id,
        db=db,
    )

    if not resolved_old_id or not resolved_new_id:
        return JSONResponse(
            content={
                "status": "missing_versions",
                "message": "Select two versions in Iterating view to analyze impact.",
            }
        )

    old_version = verify_version_access(resolved_old_id, current_user, db)
    new_version = verify_version_access(resolved_new_id, current_user, db)

    anonymous_session_id = _get_anonymous_session_id(request)
    
    # If version_number is specified and not refreshing, load that specific version
    if version_number is not None and not refresh:
        from app_server.db.models import AssistantCheckResult
        import json
        
        version_query = (
            db.query(AssistantCheckResult)
            .filter(
                AssistantCheckResult.check_id == "impact_on_project",
                AssistantCheckResult.doc_id == doc.id,
                AssistantCheckResult.old_version_id == resolved_old_id,
                AssistantCheckResult.new_version_id == resolved_new_id,
                AssistantCheckResult.version_number == version_number,
                AssistantCheckResult.status == "completed",
                AssistantCheckResult.paragraph_index.is_(None),
            )
        )
        specific_version = version_query.first()
        
        if specific_version and specific_version.result_json:
            try:
                payload = json.loads(specific_version.result_json) if isinstance(specific_version.result_json, str) else specific_version.result_json
                # Apply manual overrides
                runner = CheckRegistry.get_runner(
                    check_id="impact_on_project",
                    db=db,
                    user_id=current_user.id if current_user else None,
                )
                if hasattr(runner, '_apply_manual_overrides'):
                    payload = runner._apply_manual_overrides(
                        payload,
                        doc.id,
                        resolved_old_id,
                        resolved_new_id,
                    )
                payload["_from_cache"] = True
                return JSONResponse(content=payload)
            except (json.JSONDecodeError, AttributeError):
                pass  # Fall through to normal flow
    
    has_cached_result = False
    if not refresh:
        has_cached_result = _has_cached_completed_result(
            db=db,
            doc_id=doc.id,
            old_version_id=resolved_old_id,
            new_version_id=resolved_new_id,
            check_id="impact_on_project",
        )

    requires_llm_call = refresh or not has_cached_result

    if requires_llm_call:
        allowed, message = check_feature_access(
            current_user,
            "llm_query",
            db,
            anonymous_session_id=anonymous_session_id,
        )
        if not allowed:
            raise HTTPException(status_code=403, detail=message or "LLM quota exceeded")

    # Use registry to get runner (backward compatible - still works the same)
    try:
        runner = CheckRegistry.get_runner(
            check_id="impact_on_project",
            db=db,
            user_id=current_user.id if current_user else None,
        )
        payload = runner.run(
            doc=doc,
            old_version=old_version,
            new_version=new_version,
            refresh=refresh,
        )
    except HTTPException:
        # Re-raise HTTPException so FastAPI can handle it properly
        raise
    except Exception as exc:
        # Catch any other exceptions and return a proper error response
        raise HTTPException(
            status_code=500,
            detail=f"Error running impact on project check: {str(exc)}"
        ) from exc

    used_cache = payload.pop("_from_cache", False)
    usage = payload.get("usage") or {}
    total_tokens = usage.get("total_tokens")
    try:
        tokens_int = int(total_tokens or 0)
    except (TypeError, ValueError):
        tokens_int = 0
    if tokens_int > 0 and not used_cache:
        record_llm_usage(
            current_user,
            tokens_int,
            anonymous_session_id=anonymous_session_id,
            db=db,
        )

    return JSONResponse(content=payload)


@router.get(
    "/assistant/checks/readability/results",
    response_class=JSONResponse,
)
async def run_readability_check(
    request: Request,
    doc_id: int = Query(..., description="Document id"),
    old_version_id: Optional[int] = Query(
        None, description="Old version id for comparison"
    ),
    new_version_id: Optional[int] = Query(
        None, description="New version id for comparison"
    ),
    refresh: bool = Query(
        False,
        description="Force refresh even if cached result exists",
    ),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Compute or fetch the readability analysis."""

    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")

    current_user = get_current_user(request, db)
    doc = verify_document_access(doc_id, current_user, db)

    resolved_old_id, resolved_new_id = _resolve_version_ids(
        doc_id=doc.id,
        explicit_old=old_version_id,
        explicit_new=new_version_id,
        db=db,
    )

    if not resolved_old_id or not resolved_new_id:
        return JSONResponse(
            content={
                "status": "missing_versions",
                "message": "Select two versions in Iterating view to analyze readability.",
            }
        )

    old_version = verify_version_access(resolved_old_id, current_user, db)
    new_version = verify_version_access(resolved_new_id, current_user, db)

    anonymous_session_id = _get_anonymous_session_id(request)
    has_cached_result = False
    if not refresh:
        has_cached_result = _has_cached_completed_result(
            db=db,
            doc_id=doc.id,
            old_version_id=resolved_old_id,
            new_version_id=resolved_new_id,
            check_id="readability",
        )

    requires_llm_call = refresh or not has_cached_result

    if requires_llm_call:
        allowed, message = check_feature_access(
            current_user,
            "llm_query",
            db,
            anonymous_session_id=anonymous_session_id,
        )
        if not allowed:
            raise HTTPException(status_code=403, detail=message or "LLM quota exceeded")

    # Use registry to get runner
    try:
        runner = CheckRegistry.get_runner(
            check_id="readability",
            db=db,
            user_id=current_user.id if current_user else None,
        )
        payload = runner.run(
            doc=doc,
            old_version=old_version,
            new_version=new_version,
            refresh=refresh,
        )
    except HTTPException:
        # Re-raise HTTPException so FastAPI can handle it properly
        raise
    except Exception as exc:
        # Catch any other exceptions and return a proper error response
        raise HTTPException(
            status_code=500,
            detail=f"Error running readability check: {str(exc)}"
        ) from exc

    used_cache = payload.get("_from_cache", False)
    usage = payload.get("usage") or {}
    total_tokens = usage.get("total_tokens")
    try:
        tokens_int = int(total_tokens or 0)
    except (TypeError, ValueError):
        tokens_int = 0
    if tokens_int > 0 and not used_cache:
        record_llm_usage(
            current_user,
            tokens_int,
            anonymous_session_id=anonymous_session_id,
            db=db,
        )

    return JSONResponse(content=payload)


@router.get(
    "/assistant/checks/linkedin_virality/results",
    response_class=JSONResponse,
)
async def run_linkedin_virality_check(
    request: Request,
    doc_id: int = Query(..., description="Document id"),
    old_version_id: Optional[int] = Query(
        None, description="Old version id for comparison"
    ),
    new_version_id: Optional[int] = Query(
        None, description="New version id for comparison"
    ),
    refresh: bool = Query(
        False,
        description="Force refresh even if cached result exists",
    ),
    version_number: Optional[int] = Query(
        None, description="Specific version number to load (defaults to latest)"
    ),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Compute or fetch the LinkedIn virality analysis."""

    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")

    current_user = get_current_user(request, db)
    doc = verify_document_access(doc_id, current_user, db)

    resolved_old_id, resolved_new_id = _resolve_version_ids(
        doc_id=doc.id,
        explicit_old=old_version_id,
        explicit_new=new_version_id,
        db=db,
    )

    if not resolved_old_id or not resolved_new_id:
        return JSONResponse(
            content={
                "status": "missing_versions",
                "message": "Select two versions in Iterating view to analyze LinkedIn virality.",
            }
        )

    old_version = verify_version_access(resolved_old_id, current_user, db)
    new_version = verify_version_access(resolved_new_id, current_user, db)

    anonymous_session_id = _get_anonymous_session_id(request)
    
    # If version_number is specified and not refreshing, load that specific version
    if version_number is not None and not refresh:
        from app_server.db.models import AssistantCheckResult
        import json
        
        version_query = (
            db.query(AssistantCheckResult)
            .filter(
                AssistantCheckResult.check_id == "linkedin_virality",
                AssistantCheckResult.doc_id == doc.id,
                AssistantCheckResult.old_version_id == resolved_old_id,
                AssistantCheckResult.new_version_id == resolved_new_id,
                AssistantCheckResult.version_number == version_number,
                AssistantCheckResult.status == "completed",
                AssistantCheckResult.paragraph_index.is_(None),
            )
        )
        specific_version = version_query.first()
        
        if specific_version and specific_version.result_json:
            try:
                payload = json.loads(specific_version.result_json) if isinstance(specific_version.result_json, str) else specific_version.result_json
                payload["_from_cache"] = True
                return JSONResponse(content=payload)
            except (json.JSONDecodeError, AttributeError):
                pass  # Fall through to normal flow
    
    has_cached_result = False
    if not refresh:
        has_cached_result = _has_cached_completed_result(
            db=db,
            doc_id=doc.id,
            old_version_id=resolved_old_id,
            new_version_id=resolved_new_id,
            check_id="linkedin_virality",
        )

    requires_llm_call = refresh or not has_cached_result

    if requires_llm_call:
        allowed, message = check_feature_access(
            current_user,
            "llm_query",
            db,
            anonymous_session_id=anonymous_session_id,
        )
        if not allowed:
            raise HTTPException(status_code=403, detail=message or "LLM quota exceeded")

    # Use registry to get runner
    try:
        runner = CheckRegistry.get_runner(
            check_id="linkedin_virality",
            db=db,
            user_id=current_user.id if current_user else None,
        )
        payload = runner.run(
            doc=doc,
            old_version=old_version,
            new_version=new_version,
            refresh=refresh,
        )
    except HTTPException:
        # Re-raise HTTPException so FastAPI can handle it properly
        raise
    except Exception as exc:
        # Catch any other exceptions and return a proper error response
        raise HTTPException(
            status_code=500,
            detail=f"Error running LinkedIn virality check: {str(exc)}"
        ) from exc

    used_cache = payload.get("_from_cache", False)
    usage = payload.get("usage") or {}
    total_tokens = usage.get("total_tokens")
    try:
        tokens_int = int(total_tokens or 0)
    except (TypeError, ValueError):
        tokens_int = 0
    if tokens_int > 0 and not used_cache:
        record_llm_usage(
            current_user,
            tokens_int,
            anonymous_session_id=anonymous_session_id,
            db=db,
        )

    return JSONResponse(content=payload)


@router.get(
    "/assistant/checks/{check_id}/prompt",
    response_class=JSONResponse,
)
async def get_check_prompt(
    check_id: str,
    context: str | None = Query(None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Get the system prompt and instructions for a check from config."""
    try:
        # Get check definition from config
        definition = get_check_definition(check_id, context)
        
        if not definition.system_prompt or not definition.prompt_instructions:
            return JSONResponse(
                content={
                    "system_prompt": None,
                    "prompt_instructions": None,
                    "error": "Prompts not available for this check"
                }
            )
        
        return JSONResponse(
            content={
                "system_prompt": definition.system_prompt,
                "prompt_instructions": definition.prompt_instructions,
            }
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving prompt for check: {str(exc)}"
        ) from exc


@router.post(
    "/assistant/checks/impact_on_project/override",
    response_class=JSONResponse,
)
async def override_impact_symbol(
    request: Request,
    body: dict | None = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    data = body or await request.json()
    doc_id = data.get("doc_id")
    old_version_id = data.get("old_version_id")
    new_version_id = data.get("new_version_id")
    paragraph_index = data.get("paragraph_index")
    new_symbol = data.get("new_symbol")

    if (
        doc_id is None
        or old_version_id is None
        or new_version_id is None
        or paragraph_index is None
        or new_symbol is None
    ):
        raise HTTPException(status_code=400, detail="Missing required fields for override.")

    current_user = get_current_user(request, db)
    doc = verify_document_access(int(doc_id), current_user, db)
    old_version = verify_version_access(int(old_version_id), current_user, db)
    new_version = verify_version_access(int(new_version_id), current_user, db)
    anonymous_session_id = _get_anonymous_session_id(request)

    override_result = save_manual_override(
        db=db,
        check_id="impact_on_project",
        doc_id=doc.id,
        old_version_id=old_version.id,
        new_version_id=new_version.id,
        paragraph_index=int(paragraph_index),
        new_symbol=new_symbol,
        original_symbol=data.get("original_symbol"),
        reason_code=data.get("reason_code"),
        reason_text=data.get("reason_text"),
        row_snapshot=data.get("row_snapshot"),
        user_id=current_user.id if current_user else None,
        anonymous_session_id=anonymous_session_id,
    )

    # Parse result_json to get override data
    import json
    override_data = json.loads(override_result.result_json) if override_result.result_json else {}
    new_symbol_value = override_data.get("new_symbol", new_symbol)
    reason_code_value = override_data.get("reason_code")
    reason_text_value = override_data.get("reason_text")

    # Override is now stored in AssistantCheckResult with source="override"

    return JSONResponse(
        content={
            "paragraph_index": override_result.paragraph_index,
            "impact_symbol": new_symbol_value,
            "override": {
                "new_symbol": new_symbol_value,
                "original_symbol": override_data.get("original_symbol"),
                "reason_code": reason_code_value,
                "reason_text": reason_text_value,
                "updated_at": override_result.updated_at.isoformat() if override_result.updated_at else None,
            },
        }
    )


@router.get(
    "/assistant/checks/impact_on_sustainability/results",
    response_class=JSONResponse,
)
async def run_impact_on_sustainability_check(
    request: Request,
    doc_id: int = Query(..., description="Document id"),
    old_version_id: Optional[int] = Query(
        None, description="Old version id for comparison"
    ),
    new_version_id: Optional[int] = Query(
        None, description="New version id for comparison"
    ),
    refresh: bool = Query(
        False,
        description="Force refresh even if cached result exists",
    ),
    version_number: Optional[int] = Query(
        None, description="Specific version number to load (defaults to latest)"
    ),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Compute or fetch the impact-on-sustainability assessment."""

    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")

    current_user = get_current_user(request, db)
    doc = verify_document_access(doc_id, current_user, db)

    resolved_old_id, resolved_new_id = _resolve_version_ids(
        doc_id=doc.id,
        explicit_old=old_version_id,
        explicit_new=new_version_id,
        db=db,
    )

    if not resolved_old_id or not resolved_new_id:
        return JSONResponse(
            content={
                "status": "missing_versions",
                "message": "Select two versions in Iterating view to analyze sustainability impact.",
            }
        )

    old_version = verify_version_access(resolved_old_id, current_user, db)
    new_version = verify_version_access(resolved_new_id, current_user, db)

    anonymous_session_id = _get_anonymous_session_id(request)
    
    # If version_number is specified and not refreshing, load that specific version
    if version_number is not None and not refresh:
        from app_server.db.models import AssistantCheckResult
        import json
        
        version_query = (
            db.query(AssistantCheckResult)
            .filter(
                AssistantCheckResult.check_id == "impact_on_sustainability",
                AssistantCheckResult.doc_id == doc.id,
                AssistantCheckResult.old_version_id == resolved_old_id,
                AssistantCheckResult.new_version_id == resolved_new_id,
                AssistantCheckResult.version_number == version_number,
                AssistantCheckResult.status == "completed",
                AssistantCheckResult.paragraph_index.is_(None),
            )
        )
        specific_version = version_query.first()
        
        if specific_version and specific_version.result_json:
            try:
                payload = json.loads(specific_version.result_json) if isinstance(specific_version.result_json, str) else specific_version.result_json
                # Apply manual overrides
                runner = CheckRegistry.get_runner(
                    check_id="impact_on_sustainability",
                    db=db,
                    user_id=current_user.id if current_user else None,
                )
                if hasattr(runner, '_apply_manual_overrides'):
                    payload = runner._apply_manual_overrides(
                        payload,
                        doc.id,
                        resolved_old_id,
                        resolved_new_id,
                    )
                payload["_from_cache"] = True
                return JSONResponse(content=payload)
            except (json.JSONDecodeError, AttributeError):
                pass  # Fall through to normal flow
    
    has_cached_result = False
    if not refresh:
        has_cached_result = _has_cached_completed_result(
            db=db,
            doc_id=doc.id,
            old_version_id=resolved_old_id,
            new_version_id=resolved_new_id,
            check_id="impact_on_sustainability",
        )

    requires_llm_call = refresh or not has_cached_result

    if requires_llm_call:
        allowed, message = check_feature_access(
            current_user,
            "llm_query",
            db,
            anonymous_session_id=anonymous_session_id,
        )
        if not allowed:
            raise HTTPException(status_code=403, detail=message or "LLM quota exceeded")

    # Use registry to get runner
    try:
        runner = CheckRegistry.get_runner(
            check_id="impact_on_sustainability",
            db=db,
            user_id=current_user.id if current_user else None,
        )
        payload = runner.run(
            doc=doc,
            old_version=old_version,
            new_version=new_version,
            refresh=refresh,
        )
    except HTTPException:
        # Re-raise HTTPException so FastAPI can handle it properly
        raise
    except Exception as exc:
        # Catch any other exceptions and return a proper error response
        raise HTTPException(
            status_code=500,
            detail=f"Error running impact on sustainability check: {str(exc)}"
        ) from exc

    used_cache = payload.pop("_from_cache", False)
    usage = payload.get("usage") or {}
    total_tokens = usage.get("total_tokens")
    try:
        tokens_int = int(total_tokens or 0)
    except (TypeError, ValueError):
        tokens_int = 0
    if tokens_int > 0 and not used_cache:
        record_llm_usage(
            current_user,
            tokens_int,
            anonymous_session_id=anonymous_session_id,
            db=db,
        )

    return JSONResponse(content=payload)


@router.get(
    "/assistant/checks/change_summary/results",
    response_class=JSONResponse,
)
async def run_change_summary_check(
    request: Request,
    doc_id: int = Query(..., description="Document id"),
    old_version_id: Optional[int] = Query(
        None, description="Old version id for comparison"
    ),
    new_version_id: Optional[int] = Query(
        None, description="New version id for comparison"
    ),
    refresh: bool = Query(
        False,
        description="Force refresh even if cached result exists",
    ),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Compute or fetch the change summary analysis."""

    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")

    current_user = get_current_user(request, db)
    doc = verify_document_access(doc_id, current_user, db)

    resolved_old_id, resolved_new_id = _resolve_version_ids(
        doc_id=doc.id,
        explicit_old=old_version_id,
        explicit_new=new_version_id,
        db=db,
    )

    if not resolved_old_id or not resolved_new_id:
        return JSONResponse(
            content={
                "status": "missing_versions",
                "message": "Select two versions in Iterating view to generate change summary.",
            }
        )

    old_version = verify_version_access(resolved_old_id, current_user, db)
    new_version = verify_version_access(resolved_new_id, current_user, db)

    anonymous_session_id = _get_anonymous_session_id(request)
    has_cached_result = False
    if not refresh:
        has_cached_result = _has_cached_completed_result(
            db=db,
            doc_id=doc.id,
            old_version_id=resolved_old_id,
            new_version_id=resolved_new_id,
            check_id="change_summary",
        )

    requires_llm_call = refresh or not has_cached_result

    if requires_llm_call:
        allowed, message = check_feature_access(
            current_user,
            "llm_query",
            db,
            anonymous_session_id=anonymous_session_id,
        )
        if not allowed:
            raise HTTPException(status_code=403, detail=message or "LLM quota exceeded")

    # Use registry to get runner
    try:
        runner = CheckRegistry.get_runner(
            check_id="change_summary",
            db=db,
            user_id=current_user.id if current_user else None,
        )
        payload = runner.run(
            doc=doc,
            old_version=old_version,
            new_version=new_version,
            refresh=refresh,
        )
    except HTTPException:
        # Re-raise HTTPException so FastAPI can handle it properly
        raise
    except Exception as exc:
        # Catch any other exceptions and return a proper error response
        raise HTTPException(
            status_code=500,
            detail=f"Error running change summary check: {str(exc)}"
        ) from exc

    used_cache = payload.pop("_from_cache", False)
    usage = payload.get("usage") or {}
    total_tokens = usage.get("total_tokens")
    try:
        tokens_int = int(total_tokens or 0)
    except (TypeError, ValueError):
        tokens_int = 0
    if tokens_int > 0 and not used_cache:
        record_llm_usage(
            current_user,
            tokens_int,
            anonymous_session_id=anonymous_session_id,
            db=db,
        )

    return JSONResponse(content=payload)


@router.get(
    "/assistant/checks/check_against_norms/results",
    response_class=JSONResponse,
)
async def run_check_against_norms(
    request: Request,
    doc_id: int = Query(..., description="Document id"),
    version_id: Optional[int] = Query(
        None, description="Version id to check (defaults to latest)"
    ),
    refresh: bool = Query(
        False,
        description="Force refresh even if cached result exists",
    ),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Check document against reference documents (norms, standards, guidelines, briefs)."""

    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")

    current_user = get_current_user(request, db)
    doc = verify_document_access(doc_id, current_user, db)

    # Resolve version (use provided or latest)
    if version_id:
        version = verify_version_access(version_id, current_user, db)
    else:
        # Get latest version
        versions = (
            db.query(Version)
            .filter(Version.document_id == doc_id, Version.deleted_at.is_(None))
            .order_by(Version.created_at.desc())
            .limit(1)
            .all()
        )
        if not versions:
            return JSONResponse(
                content={
                    "status": "missing_version",
                    "message": "No versions found for this document.",
                }
            )
        version = verify_version_access(versions[0].id, current_user, db)

    anonymous_session_id = _get_anonymous_session_id(request)
    has_cached_result = False
    if not refresh:
        has_cached_result = _has_cached_completed_result_predefined(
            db=db,
            doc_id=doc.id,
            version_id=version.id,
            check_id="check_against_norms",
        )

    requires_llm_call = refresh or not has_cached_result

    if requires_llm_call:
        allowed, message = check_feature_access(
            current_user,
            "llm_query",
            db,
            anonymous_session_id=anonymous_session_id,
        )
        if not allowed:
            raise HTTPException(status_code=403, detail=message or "LLM quota exceeded")

    # Use registry to get runner
    try:
        runner = CheckRegistry.get_runner(
            check_id="check_against_norms",
            db=db,
            user_id=current_user.id if current_user else None,
        )
        result = runner.run(
            doc=doc,
            version=version,
            predefined_data={},  # Empty for now, could be extended
            refresh=refresh,
        )
        payload = result.result_data
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error running check against norms: {str(exc)}"
        ) from exc

    used_cache = result.metadata.get("from_cache", False)
    # Note: usage tracking would need to be added if LLM calls are made
    # For now, the runner handles LLM calls internally

    return JSONResponse(content=payload)


@router.post(
    "/assistant/checks/impact_on_sustainability/override",
    response_class=JSONResponse,
)
async def override_sustainability_symbol(
    request: Request,
    body: dict | None = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    data = body or await request.json()
    doc_id = data.get("doc_id")
    old_version_id = data.get("old_version_id")
    new_version_id = data.get("new_version_id")
    paragraph_index = data.get("paragraph_index")
    new_symbol = data.get("new_symbol")

    if (
        doc_id is None
        or old_version_id is None
        or new_version_id is None
        or paragraph_index is None
        or new_symbol is None
    ):
        raise HTTPException(status_code=400, detail="Missing required fields for override.")

    current_user = get_current_user(request, db)
    doc = verify_document_access(int(doc_id), current_user, db)
    old_version = verify_version_access(int(old_version_id), current_user, db)
    new_version = verify_version_access(int(new_version_id), current_user, db)
    anonymous_session_id = _get_anonymous_session_id(request)

    override_result = save_manual_override(
        db=db,
        check_id="impact_on_sustainability",
        doc_id=doc.id,
        old_version_id=old_version.id,
        new_version_id=new_version.id,
        paragraph_index=int(paragraph_index),
        new_symbol=new_symbol,
        original_symbol=data.get("original_symbol"),
        reason_code=data.get("reason_code"),
        reason_text=data.get("reason_text"),
        row_snapshot=data.get("row_snapshot"),
        user_id=current_user.id if current_user else None,
        anonymous_session_id=anonymous_session_id,
    )

    # Parse result_json to get override data
    import json
    override_data = json.loads(override_result.result_json) if override_result.result_json else {}
    new_symbol_value = override_data.get("new_symbol", new_symbol)
    reason_code_value = override_data.get("reason_code")
    reason_text_value = override_data.get("reason_text")

    # Override is now stored in AssistantCheckResult with source="override"

    return JSONResponse(
        content={
            "paragraph_index": override_result.paragraph_index,
            "sustainability_symbol": new_symbol_value,
            "override": {
                "new_symbol": new_symbol_value,
                "original_symbol": override_data.get("original_symbol"),
                "reason_code": reason_code_value,
                "reason_text": reason_text_value,
                "updated_at": override_result.updated_at.isoformat() if override_result.updated_at else None,
            },
        }
    )


@router.get(
    "/assistant/checks/impact_on_sustainability/events",
    response_class=JSONResponse,
)
async def get_sustainability_events(
    request: Request,
    doc_id: int = Query(..., description="Document id"),
    old_version_id: Optional[int] = Query(None, description="Old version id"),
    new_version_id: Optional[int] = Query(None, description="New version id"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Get interaction events for sustainability check."""
    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")

    current_user = get_current_user(request, db)
    doc = verify_document_access(doc_id, current_user, db)

    resolved_old_id, resolved_new_id = _resolve_version_ids(
        doc_id=doc.id,
        explicit_old=old_version_id,
        explicit_new=new_version_id,
        db=db,
    )

    if not resolved_old_id or not resolved_new_id:
        return JSONResponse(content={"events": []})

    # Query events from AssistantCheckResult (ratings and overrides)
    events = (
        db.query(AssistantCheckResult)
        .filter(
            AssistantCheckResult.check_id == "impact_on_sustainability",
            AssistantCheckResult.doc_id == doc.id,
            AssistantCheckResult.old_version_id == resolved_old_id,
            AssistantCheckResult.new_version_id == resolved_new_id,
            AssistantCheckResult.source.in_(["rating", "override"]),
        )
        .order_by(AssistantCheckResult.created_at.desc())
        .limit(100)
        .all()
    )

    # Format events
    filtered_events = []
    for event in events:
        try:
            payload = json.loads(event.result_json) if event.result_json else {}
            # Map source to event_type for backward compatibility
            event_type = "tooltip_feedback_useful" if (event.source == "rating" and payload.get("useful")) else \
                        "tooltip_feedback_not_useful" if (event.source == "rating" and not payload.get("useful")) else \
                        "override" if event.source == "override" else "unknown"
            
            filtered_events.append({
                "id": event.id,
                "event_type": event_type,
                "paragraph_index": event.paragraph_index,
                "elapsed_ms": None,  # Not stored in AssistantCheckResult
                "event_payload": payload,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            })
        except (json.JSONDecodeError, TypeError):
            continue

    return JSONResponse(content={"events": filtered_events})


@router.post(
    "/assistant/checks/impact_on_project/events",
    response_class=JSONResponse,
)
async def record_impact_event(
    request: Request,
    body: dict | None = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    data = body or await request.json()
    doc_id = data.get("doc_id")
    old_version_id = data.get("old_version_id")
    new_version_id = data.get("new_version_id")
    if doc_id is None or old_version_id is None or new_version_id is None:
        raise HTTPException(status_code=400, detail="Missing required fields for event logging.")

    current_user = get_current_user(request, db)
    doc = verify_document_access(int(doc_id), current_user, db)
    old_version = verify_version_access(int(old_version_id), current_user, db)
    new_version = verify_version_access(int(new_version_id), current_user, db)
    anonymous_session_id = _get_anonymous_session_id(request)

    # Events are now stored in AssistantCheckResult if needed
    # For analytics events, consider using AssistantCheckResult with source="analytics"
    
    return JSONResponse(content={"status": "ok"})


@router.post(
    "/assistant/checks/feedback",
    response_class=JSONResponse,
)
async def record_check_feedback(
    request: Request,
    body: dict | None = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Record tooltip feedback (useful/not useful) for any check type."""
    data = body or await request.json()
    doc_id = data.get("doc_id")
    check_id = data.get("check_id")
    useful = data.get("useful")  # True for useful, False for not useful
    comment = data.get("comment")  # Optional comment for "not useful" feedback
    version_id = data.get("version_id")  # For document comparison checks
    old_version_id = data.get("old_version_id")  # For version comparison checks
    new_version_id = data.get("new_version_id")  # For version comparison checks
    paragraph_index = data.get("paragraph_index")
    
    if doc_id is None:
        raise HTTPException(status_code=400, detail="Missing doc_id for feedback.")
    
    if useful is None:
        raise HTTPException(status_code=400, detail="Missing useful flag for feedback.")
    
    current_user = get_current_user(request, db)
    doc = verify_document_access(int(doc_id), current_user, db)
    anonymous_session_id = _get_anonymous_session_id(request)
    
    # Resolve version IDs based on check type
    # For document comparison checks: use same version_id for both old and new
    # For version comparison checks: use provided old_version_id and new_version_id
    if version_id is not None:
        # Document comparison check - use same version for both
        version = verify_version_access(int(version_id), current_user, db)
        resolved_old_version_id = version.id
        resolved_new_version_id = version.id
    elif old_version_id is not None and new_version_id is not None:
        # Version comparison check
        old_version = verify_version_access(int(old_version_id), current_user, db)
        new_version = verify_version_access(int(new_version_id), current_user, db)
        resolved_old_version_id = old_version.id
        resolved_new_version_id = new_version.id
    else:
        # Try to resolve from doc (use latest version)
        versions = (
            db.query(Version)
            .filter(Version.document_id == doc.id, Version.deleted_at.is_(None))
            .order_by(Version.created_at.desc())
            .limit(1)
            .all()
        )
        if not versions:
            raise HTTPException(status_code=400, detail="No version found for document.")
        version = verify_version_access(versions[0].id, current_user, db)
        resolved_old_version_id = version.id
        resolved_new_version_id = version.id
    
    # Save feedback to AssistantCheckResult with source="rating"
    from datetime import UTC, datetime
    
    feedback_data = {
        "useful": bool(useful),
    }
    
    # Include comment if provided
    if comment:
        feedback_data["comment"] = comment
    
    # Create or update rating (allow multiple ratings per context)
    rating = AssistantCheckResult(
        check_id=check_id or "unknown",
        doc_id=doc.id,
        old_version_id=resolved_old_version_id,
        new_version_id=resolved_new_version_id,
        paragraph_index=paragraph_index,
        source="rating",
        status="completed",
        result_json=json.dumps(feedback_data, ensure_ascii=False),
        user_id=current_user.id if current_user else None,
        anonymous_session_id=anonymous_session_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    db.add(rating)
    db.commit()
    
    return JSONResponse(content={"status": "ok"})


# ============================================================================
# Helper functions for manual check result operations
# ============================================================================

def parse_paragraph_index(paragraph_index_raw: str | int | None) -> int | None:
    """Parse paragraph_index from various input types."""
    if paragraph_index_raw is None or paragraph_index_raw == "":
        return None
    if isinstance(paragraph_index_raw, int):
        return paragraph_index_raw
    if isinstance(paragraph_index_raw, str) and paragraph_index_raw.strip():
        try:
            return int(paragraph_index_raw)
        except (ValueError, TypeError):
            return None
    return None


def normalize_analysis_text(analysis: str | None) -> str:
    """Normalize analysis text to a clean string.
    
    The analysis field is always a string:
    - From database: stored as string in project_impact.summary or sustainability_impact.summary
    - From frontend form: textarea sends string value
    - From AI prompts: all prompts define summary as string
    
    Args:
        analysis: The analysis text (string or None)
    
    Returns:
        Stripped string, or empty string if None
    """
    if analysis is None:
        return ""
    return str(analysis).strip()


def create_impact_dict(check_id: str, analysis: str, impact_metrics: dict | None) -> dict:
    """Create a clean impact dict (project_impact or sustainability_impact) with only summary and user-set metrics."""
    impact_dict = {"summary": analysis}
    
    if not impact_metrics:
        return impact_dict
    
    if check_id == "impact_on_project":
        metric_keys = ["cost", "time", "scope", "quality"]
    elif check_id == "impact_on_sustainability":
        metric_keys = ["environmental", "economic", "social_functional", "technical", "process", "site"]
    else:
        return impact_dict
    
    for key in metric_keys:
        value = impact_metrics.get(key)
        if value and value != "none":
            impact_dict[key] = value.upper() if isinstance(value, str) else value
    
    return impact_dict


# REMOVED: update_row_with_manual_data() - No longer needed with new manual_overrides approach
# REMOVED: create_new_row() - No longer needed with new manual_overrides approach


def load_latest_check_version(
    db: Session,
    check_id: str,
    doc_id: int,
    old_version_id: int | None,
    new_version_id: int | None,
) -> AssistantCheckResult | None:
    """Load the latest version of a check result."""
    import logging
    _logger = logging.getLogger(__name__)
    
    # Expire any cached objects to ensure we get fresh data
    db.expire_all()
    
    # Force a fresh query by starting a new session context
    # This ensures we see the latest committed data
    import logging
    _logger = logging.getLogger(__name__)
    _logger.info(f"[load_latest_check_version] Querying for check={check_id}, doc={doc_id}, old={old_version_id}, new={new_version_id}")
    
    result = (
        db.query(AssistantCheckResult)
        .filter(
            AssistantCheckResult.check_id == check_id,
            AssistantCheckResult.doc_id == doc_id,
            AssistantCheckResult.old_version_id == old_version_id,
            AssistantCheckResult.new_version_id == new_version_id,
            AssistantCheckResult.status == "completed",
            AssistantCheckResult.paragraph_index.is_(None),
        )
        .order_by(
            AssistantCheckResult.version_number.desc().nulls_last(),
            AssistantCheckResult.created_at.desc()
        )
        .first()
    )
    
    if result:
        _logger.info(f"[load_latest_check_version] Found version {result.version_number} (id={result.id}) for check={check_id}, doc={doc_id}, old={old_version_id}, new={new_version_id}")
    else:
        _logger.warning(f"[load_latest_check_version] No version found for check={check_id}, doc={doc_id}, old={old_version_id}, new={new_version_id}")
        # Debug: Check what versions exist
        all_versions = (
            db.query(AssistantCheckResult)
            .filter(
                AssistantCheckResult.check_id == check_id,
                AssistantCheckResult.doc_id == doc_id,
                AssistantCheckResult.status == "completed",
                AssistantCheckResult.paragraph_index.is_(None),
            )
            .all()
        )
        _logger.warning(f"[load_latest_check_version] DEBUG: Found {len(all_versions)} total versions for check={check_id}, doc={doc_id}")
        for v in all_versions:
            _logger.warning(f"[load_latest_check_version] DEBUG: Version {v.version_number} (id={v.id}): old={v.old_version_id}, new={v.new_version_id}, looking for old={old_version_id}, new={new_version_id}")
    
    return result


def get_next_version_number(
    db: Session,
    check_id: str,
    doc_id: int,
    old_version_id: int | None,
    new_version_id: int | None,
    latest_version: AssistantCheckResult | None,
) -> int:
    """Calculate the next version number for a check result."""
    if latest_version and latest_version.version_number:
        return latest_version.version_number + 1
    
    # If latest version has no version_number, check for max
    max_version = (
        db.query(AssistantCheckResult)
        .filter(
            AssistantCheckResult.check_id == check_id,
            AssistantCheckResult.doc_id == doc_id,
            AssistantCheckResult.old_version_id == old_version_id,
            AssistantCheckResult.new_version_id == new_version_id,
        )
        .order_by(AssistantCheckResult.version_number.desc().nulls_last())
        .first()
    )
    
    if max_version and max_version.version_number:
        return max_version.version_number + 1
    
    return 2  # First manual version after AI


def load_rows_from_version(version: AssistantCheckResult | None) -> list[dict]:
    """Load and parse rows from a check result version."""
    if not version or not version.result_json:
        return []
    
    try:
        version_data = json.loads(version.result_json) if isinstance(version.result_json, str) else version.result_json
        return copy.deepcopy(version_data.get("rows", []))
    except (json.JSONDecodeError, TypeError, AttributeError):
        return []


def load_version_json(version: AssistantCheckResult | None) -> dict:
    """Load full JSON structure from a version (rows + manual_overrides)."""
    if not version or not version.result_json:
        return {"rows": [], "manual_overrides": {}}
    
    try:
        version_data = json.loads(version.result_json) if isinstance(version.result_json, str) else version.result_json
        return copy.deepcopy(version_data)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return {"rows": [], "manual_overrides": {}}


def load_original_ai_version(
    db: Session,
    check_id: str,
    doc_id: int,
    old_version_id: int | None,
    new_version_id: int | None,
) -> AssistantCheckResult | None:
    """Load the original AI-generated version (source='ai', version_number=1)."""
    # Try to find AI-generated version first
    original_version = (
        db.query(AssistantCheckResult)
        .filter(
            AssistantCheckResult.check_id == check_id,
            AssistantCheckResult.doc_id == doc_id,
            AssistantCheckResult.old_version_id == old_version_id,
            AssistantCheckResult.new_version_id == new_version_id,
            AssistantCheckResult.status == "completed",
            AssistantCheckResult.paragraph_index.is_(None),
            AssistantCheckResult.source == "ai",
        )
        .order_by(AssistantCheckResult.version_number.asc())
        .first()
    )
    
    # If no AI version found, get the first version (might be version 1 before source was tracked)
    if not original_version:
        original_version = (
            db.query(AssistantCheckResult)
            .filter(
                AssistantCheckResult.check_id == check_id,
                AssistantCheckResult.doc_id == doc_id,
                AssistantCheckResult.old_version_id == old_version_id,
                AssistantCheckResult.new_version_id == new_version_id,
                AssistantCheckResult.status == "completed",
                AssistantCheckResult.paragraph_index.is_(None),
            )
            .order_by(AssistantCheckResult.version_number.asc())
            .first()
        )
    
    return original_version


def get_current_values_from_overrides(
    ai_row: dict | None,
    manual_overrides: dict,
    paragraph_index: int,
    check_id: str,
) -> dict:
    """Get current display values: use latest override if exists, else AI values."""
    para_key = str(paragraph_index)
    overrides = manual_overrides.get(para_key, [])
    latest_override = overrides[-1] if overrides else None
    
    if latest_override:
        # Use latest manual override
        # Recommendations from override are list of strings (action text only)
        recs = latest_override.get("recommendations", [])
        if not isinstance(recs, list):
            recs = []
        # Convert to list of dicts for template
        recommendations = [{"action": str(r)} for r in recs]
        
        return {
            "symbol": latest_override.get("symbol", "~"),
            "analysis": latest_override.get("analysis", ""),
            "recommendations": recommendations,
            "history": overrides,
        }
    elif ai_row:
        # Use original AI values
        if check_id == "impact_on_project":
            impact_dict = ai_row.get("project_impact", {})
            analysis = impact_dict.get("summary", "") if isinstance(impact_dict, dict) else ""
        elif check_id == "impact_on_sustainability":
            impact_dict = ai_row.get("sustainability_impact", {})
            analysis = impact_dict.get("summary", "") if isinstance(impact_dict, dict) else ""
        else:
            analysis = ai_row.get("summary", "")
        
        raw_response = ai_row.get("raw_response", {})
        recommendations = raw_response.get("recommendations", [])
        # Keep as list of dicts (preserve full structure)
        if not isinstance(recommendations, list):
            recommendations = []
        
        return {
            "symbol": ai_row.get("impact_symbol") or ai_row.get("sustainability_symbol") or "~",
            "analysis": analysis,
            "recommendations": recommendations,  # Already list of dicts from AI
            "history": [],
        }
    else:
        # No data available
        return {
            "symbol": "~",
            "analysis": "",
            "recommendations": [],
            "history": [],
        }


# REMOVED: update_row_in_json() - No longer needed with new manual_overrides approach


def extract_impact_dict(paragraph_row: dict, check_id: str) -> dict:
    """Extract and normalize impact dict from a paragraph row."""
    if check_id == "impact_on_project":
        impact_key = "project_impact"
    elif check_id == "impact_on_sustainability":
        impact_key = "sustainability_impact"
    else:
        return {}
    
    impact_dict = paragraph_row.get(impact_key, {})
    
    # Ensure it's a dict
    if not isinstance(impact_dict, dict):
        try:
            if isinstance(impact_dict, str):
                impact_dict = json.loads(impact_dict)
            else:
                impact_dict = {}
        except (json.JSONDecodeError, TypeError):
            impact_dict = {}
    
    return impact_dict


def extract_summary_text(impact_dict: dict, is_manual: bool = False) -> str:
    """Extract summary text from an impact dict, handling various formats."""
    summary_value = impact_dict.get("summary", '')
    
    if isinstance(summary_value, dict):
        return summary_value.get("text", summary_value.get("content", str(summary_value))).strip()
    elif summary_value is None:
        return ''
    else:
        # Return the summary as-is - don't filter or clear anything
        # User-entered text should always be preserved
        return str(summary_value).strip()


def extract_metrics_from_impact(impact_dict: dict, check_id: str) -> dict:
    """Extract metrics from an impact dict for form display."""
    if check_id == "impact_on_project":
        metric_keys = ["cost", "time", "scope", "quality"]
    elif check_id == "impact_on_sustainability":
        metric_keys = ["environmental", "economic", "social_functional", "technical", "process", "site"]
    else:
        return {}
    
    metrics = {}
    for key in metric_keys:
        value = impact_dict.get(key)
        metrics[key] = value.lower() if isinstance(value, str) else "none"
    
    return metrics


def find_paragraph_row(rows: list[dict], paragraph_index: int) -> dict | None:
    """Find a paragraph row by paragraph_index."""
    for row in rows:
        if row.get("paragraph_index") == paragraph_index:
            return row
    return None


def extract_form_data_from_row(paragraph_row: dict, check_id: str, paragraph_index: int | None = None) -> dict:
    """Extract all form data (symbol, analysis, metrics, recommendations) from a paragraph row."""
    import logging
    _logger = logging.getLogger(__name__)
    
    # Extract symbol
    current_symbol = (
        paragraph_row.get("impact_symbol") or
        paragraph_row.get("sustainability_symbol") or
        paragraph_row.get("readability_symbol") or
        paragraph_row.get("virality_symbol") or
        '~'
    )
    
    # Extract analysis and metrics based on check type
    if check_id in ["impact_on_project", "impact_on_sustainability"]:
        impact_dict = extract_impact_dict(paragraph_row, check_id)
        is_manual = paragraph_row.get("source") == "manual" or paragraph_row.get("override")
        
        # Log data integrity issues for manual overrides
        if is_manual and not impact_dict and paragraph_index is not None:
            _logger.warning(f"[get_edit_form] Manual override row has empty impact dict! para_idx={paragraph_index}, row keys={list(paragraph_row.keys())}")
        
        # Log what we're loading
        if paragraph_index is not None:
            _logger.info(
                f"[get_edit_form] Loading analysis for para_idx={paragraph_index}, "
                f"is_manual={is_manual}, impact_dict keys={list(impact_dict.keys())}"
            )
        
        current_analysis = extract_summary_text(impact_dict, is_manual)
        current_metrics = extract_metrics_from_impact(impact_dict, check_id)
        
        # Log if we got raw metadata for manual override
        if is_manual and current_analysis and ("similarity" in current_analysis.lower() or "minor" in current_analysis.lower() or "unchanged" in current_analysis.lower()):
            _logger.error(
                f"[get_edit_form] Manual override has raw metadata in summary instead of user text! "
                f"Clearing it. para_idx={paragraph_index}, value={current_analysis[:100]}"
            )
    else:
        # For other checks (readability, linkedin_virality), summary is directly on the row
        # Always a string per prompt definitions
        summary_value = paragraph_row.get("summary", '')
        current_analysis = str(summary_value).strip() if summary_value else ''
        current_metrics = {}
    
    # Extract recommendations as list of dicts (preserve structure)
    raw_response = paragraph_row.get("raw_response", {})
    # Ensure raw_response is a dict
    if not isinstance(raw_response, dict):
        raw_response = {}
    
    recommendations = raw_response.get("recommendations", [])
    _logger.info(f"[extract_form_data] para_idx={paragraph_index}, raw_response type={type(paragraph_row.get('raw_response'))}, recommendations={recommendations}, type={type(recommendations)}")
    # Ensure it's a list of dicts
    if not isinstance(recommendations, list):
        _logger.warning(f"[extract_form_data] Recommendations is not a list: {recommendations}, converting to empty list")
        recommendations = []
    # Ensure each item is a dict with at least an "action" field
    current_recommendations = []
    for rec in recommendations:
        if not isinstance(rec, dict):
            _logger.warning(f"[extract_form_data] Invalid recommendation format (not a dict): {rec}, skipping")
            continue
        if "action" not in rec:
            _logger.warning(f"[extract_form_data] Recommendation missing 'action' field: {rec}, skipping")
            continue
        current_recommendations.append(rec)
    _logger.info(f"[extract_form_data] Extracted {len(current_recommendations)} recommendations for para_idx={paragraph_index}")
    
    return {
        "symbol": current_symbol,
        "analysis": current_analysis,
        "metrics": current_metrics,
        "recommendations": current_recommendations,  # List of dicts, not string
    }


# ============================================================================
# Helper functions for check comments
# ============================================================================

def get_check_comment(
    db: Session,
    paragraph_id: int,
    check_id: str,
    doc_id: int | None = None,
) -> Optional:
    """Get latest check evaluation comment for a paragraph and check.
    
    Args:
        db: Database session
        paragraph_id: Paragraph ID to search for
        check_id: Check ID to match
        doc_id: Optional document ID to ensure comment belongs to same document
    """
    from app_server.db.models import Comment, Version
    
    query = db.query(Comment).filter(
        Comment.paragraph_id == paragraph_id,
        Comment.tag == "check",
    )
    
    # If doc_id provided, ensure comment's version belongs to same document
    if doc_id is not None:
        query = query.join(Version, Comment.version_id == Version.id).filter(
            Version.document_id == doc_id
        )
    
    comments = query.order_by(Comment.created_at.desc()).all()
    
    for comment in comments:
        try:
            meta = getattr(comment, 'comment_metadata', None)
        except AttributeError:
            # Column doesn't exist yet - skip
            continue
        if meta is None:
            continue
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                continue
        
        if isinstance(meta, dict) and meta.get("check_id") == check_id:
            return comment
    
    return None


def get_paragraph_id_from_index(
    db: Session,
    paragraph_index: int,
    version_id: int,
) -> Optional[int]:
    """Get paragraph_id from paragraph_index for a given version."""
    from app_server.db.models import VersionParagraph
    
    vp = db.query(VersionParagraph).filter(
        VersionParagraph.version_id == version_id,
        VersionParagraph.paragraph_index == paragraph_index,
    ).first()
    
    return vp.paragraph_id if vp else None


def format_check_comment_message(analysis: str, recommendations: list) -> str:
    """Format check evaluation data into comment message."""
    parts = []
    if analysis:
        parts.append(f"Analysis:\n{analysis}")
    if recommendations:
        recs_text = "\n".join(f"- {r}" for r in recommendations if r and str(r).strip())
        if recs_text:
            parts.append(f"\nRecommendations:\n{recs_text}")
    return "\n\n".join(parts) if parts else "AI Evaluation"


# ============================================================================
# Route handlers
# ============================================================================

@router.post(
    "/assistant/checks/{check_id}/comment",
    response_class=JSONResponse,
)
async def save_check_comment(
    check_id: str,
    request: Request,
    body: dict | None = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Save a check evaluation comment (user-edited analysis, recommendations, symbol)."""
    from datetime import UTC, datetime
    import logging
    from app_server.db.models import Comment
    
    _logger = logging.getLogger(__name__)
    
    # Parse request data
    data = body or await request.json()
    doc_id = data.get("doc_id")
    old_version_id = data.get("old_version_id")
    new_version_id = data.get("new_version_id")
    paragraph_index = parse_paragraph_index(data.get("paragraph_index"))
    
    if paragraph_index is None:
        raise HTTPException(
            status_code=400,
            detail="paragraph_index is required"
        )
    
    symbol = data.get("symbol", "~")
    analysis = normalize_analysis_text(data.get("analysis", ""))
    recommendations = data.get("recommendations", [])
    if not isinstance(recommendations, list):
        recommendations = []
    
    if doc_id is None:
        raise HTTPException(status_code=400, detail="Missing doc_id.")
    
    current_user = get_current_user(request, db)
    doc = verify_document_access(int(doc_id), current_user, db)
    
    # Resolve version IDs
    resolved_old_version_id, resolved_new_version_id = _resolve_version_ids_for_save(
        db=db,
        doc=doc,
        current_user=current_user,
        check_id=check_id,
        old_version_id=old_version_id,
        new_version_id=new_version_id,
    )
    
    if not resolved_new_version_id:
        raise HTTPException(status_code=400, detail="new_version_id is required")
    
    # Get paragraph_id
    para_id = get_paragraph_id_from_index(db, paragraph_index, resolved_new_version_id)
    if not para_id:
        raise HTTPException(status_code=404, detail="Paragraph not found")
    
    # Find or create check comment (ensure it belongs to same document)
    check_comment = get_check_comment(db, para_id, check_id, doc_id=doc.id)
    
    # Get check label/name
    from app_server.core.checks_loader import get_check_definitions
    check_definitions = get_check_definitions("comparing")
    check_def = next((c for c in check_definitions if c.id == check_id), None)
    check_name = check_def.label if check_def else check_id
    
    metadata = {
        "check_id": check_id,
        "check_name": check_name,  # Store check name separately
        "symbol": symbol,
        "analysis": analysis,
        "recommendations": recommendations,
    }
    
    message = format_check_comment_message(analysis, recommendations)
    
    # Encrypt message using project encryption key
    from app_server.services.projects import get_project_encryption_key_from_document
    from app_server.core.encryption_utils import encrypt_project_data
    project_key = get_project_encryption_key_from_document(doc.id, db)
    encrypted_message = encrypt_project_data(message, project_key)
    
    if check_comment:
        # Update existing comment
        try:
            setattr(check_comment, 'comment_metadata', json.dumps(metadata, ensure_ascii=False))
            check_comment.message = encrypted_message
            check_comment.user_id = current_user.id if current_user else None
            check_comment.version_id = resolved_new_version_id
        except AttributeError:
            raise HTTPException(
                status_code=500,
                detail="Database migration required. Please run: python scripts/add_comment_columns.py"
            )
    else:
        # Create new comment
        try:
            check_comment = Comment(
                paragraph_id=para_id,
                version_id=resolved_new_version_id,
                tag="check",
                message=encrypted_message,
                user_id=current_user.id if current_user else None,
                status="open",
            )
            setattr(check_comment, 'comment_metadata', json.dumps(metadata, ensure_ascii=False))
            db.add(check_comment)
        except AttributeError:
            raise HTTPException(
                status_code=500,
                detail="Database migration required. Please run: python scripts/add_comment_columns.py"
            )
    
    db.commit()
    db.refresh(check_comment)
    
    return JSONResponse(content={
        "status": "ok",
        "comment_id": check_comment.id
    })


@router.post(
    "/assistant/checks/{check_id}/comment/accept",
    response_class=JSONResponse,
)
async def accept_check_comment(
    check_id: str,
    request: Request,
    comment_id: int = Form(...),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Accept a check comment - marks it as implemented and syncs to mouseover."""
    from datetime import datetime, UTC
    import json
    
    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Login required")
    
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Verify this is a check comment for the specified check_id
    try:
        meta = getattr(comment, 'comment_metadata', None)
        if meta is None:
            raise HTTPException(status_code=400, detail="Comment has no metadata")
        
        if isinstance(meta, str):
            meta = json.loads(meta)
        
        if meta.get("check_id") != check_id:
            raise HTTPException(status_code=400, detail="Check ID mismatch")
    except (AttributeError, json.JSONDecodeError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid comment metadata: {e}")
    
    # Get old status
    old_status = comment.status
    
    # Update status to implemented (accepted)
    comment.status = "implemented"
    
    # Update metadata with acceptance info
    meta["accepted_by"] = current_user.id
    meta["accepted_at"] = datetime.now(UTC).isoformat()
    
    # Update status history
    try:
        status_history = []
        if comment.status_history:
            try:
                status_history = json.loads(comment.status_history) if isinstance(comment.status_history, str) else comment.status_history
                if not isinstance(status_history, list):
                    status_history = []
            except:
                status_history = []
        
        status_change = {
            "from": old_status,
            "to": "implemented",
            "name": current_user.name if current_user else "Anonymous",
            "email": current_user.email if current_user else "anonymous@example.com",
            "datetime": datetime.now(UTC).isoformat()
        }
        status_history.append(status_change)
        
        setattr(comment, 'status_history', json.dumps(status_history, ensure_ascii=False))
    except AttributeError:
        # If status_history column doesn't exist yet, skip
        pass
    except Exception as e:
        _logger.warning(f"Could not update status history: {e}")
    
    try:
        setattr(comment, 'comment_metadata', json.dumps(meta, ensure_ascii=False))
        db.commit()
        db.refresh(comment)
    except AttributeError:
        raise HTTPException(
            status_code=500,
            detail="Database migration required. Please run: python scripts/add_comment_columns.py"
        )
    
    return JSONResponse(content={
        "status": "accepted",
        "comment_id": comment.id,
        "accepted_by": current_user.id,
        "accepted_at": meta["accepted_at"]
    })


@router.post(
    "/assistant/checks/{check_id}/comment/decline",
    response_class=JSONResponse,
)
async def decline_check_comment(
    check_id: str,
    request: Request,
    comment_id: int = Form(...),
    correction: str = Form(...),
    symbol: str = Form("✗"),  # Default to ✗ if not provided
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Decline a check comment - marks it as ignored, saves user correction, and updates symbol."""
    from datetime import datetime, UTC
    import json
    from app_server.core.encryption_utils import encrypt_project_data
    from app_server.services.projects import get_project_encryption_key_from_document
    
    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Login required")
    
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Verify this is a check comment for the specified check_id
    try:
        meta = getattr(comment, 'comment_metadata', None)
        if meta is None:
            raise HTTPException(status_code=400, detail="Comment has no metadata")
        
        if isinstance(meta, str):
            meta = json.loads(meta)
        
        if meta.get("check_id") != check_id:
            raise HTTPException(status_code=400, detail="Check ID mismatch")
    except (AttributeError, json.JSONDecodeError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid comment metadata: {e}")
    
    # Get old status
    old_status = comment.status
    
    # Update status to ignored (declined)
    comment.status = "ignored"
    
    # Update symbol to user-selected symbol
    meta["symbol"] = symbol
    meta["user_correction"] = correction
    meta["declined_by"] = current_user.id
    meta["declined_at"] = datetime.now(UTC).isoformat()
    
    # Update comment message with user correction
    version = db.query(Version).filter(Version.id == comment.version_id).first()
    if version:
        project_key = get_project_encryption_key_from_document(version.document_id, db)
        encrypted_correction = encrypt_project_data(correction, project_key if project_key else None)
        comment.message = encrypted_correction
    
    # Update status history
    try:
        status_history = []
        if comment.status_history:
            try:
                status_history = json.loads(comment.status_history) if isinstance(comment.status_history, str) else comment.status_history
                if not isinstance(status_history, list):
                    status_history = []
            except:
                status_history = []
        
        status_change = {
            "from": old_status,
            "to": "ignored",
            "name": current_user.name if current_user else "Anonymous",
            "email": current_user.email if current_user else "anonymous@example.com",
            "datetime": datetime.now(UTC).isoformat()
        }
        status_history.append(status_change)
        
        setattr(comment, 'status_history', json.dumps(status_history, ensure_ascii=False))
    except AttributeError:
        # If status_history column doesn't exist yet, skip
        pass
    except Exception as e:
        _logger.warning(f"Could not update status history: {e}")
    
    try:
        setattr(comment, 'comment_metadata', json.dumps(meta, ensure_ascii=False))
        db.commit()
        db.refresh(comment)
    except AttributeError:
        raise HTTPException(
            status_code=500,
            detail="Database migration required. Please run: python scripts/add_comment_columns.py"
        )
    
    return JSONResponse(content={
        "status": "declined",
        "comment_id": comment.id,
        "declined_by": current_user.id,
        "declined_at": meta["declined_at"],
        "symbol": symbol
    })


@router.get(
    "/assistant/checks/{check_id}/symbol-button/{paragraph_id}",
    response_class=HTMLResponse,
)
async def get_check_symbol_button(
    check_id: str,
    paragraph_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return HTML for a single check symbol button - for HTMX partial updates."""
    import logging
    logger = logging.getLogger(__name__)
    
    # Query the comment for this paragraph and check
    comment = db.query(Comment).filter(
        Comment.paragraph_id == paragraph_id,
        Comment.tag == 'check'
    ).first()
    
    # Default values
    symbol = '?'
    classes = 'impact-arrow-empty impact-arrow-neutral'
    has_check_result = False
    para_idx = None
    
    if comment and comment.comment_metadata:
        try:
            meta = json.loads(comment.comment_metadata) if isinstance(comment.comment_metadata, str) else comment.comment_metadata
            if meta.get('check_id') == check_id:
                symbol = meta.get('symbol', '?')
                has_check_result = True
                
                # Determine classes based on status
                if comment.status == 'implemented':
                    classes = 'impact-arrow-btn accepted'
                elif comment.status == 'ignored':
                    classes = 'impact-arrow-btn declined'
                else:
                    classes = 'impact-arrow-btn'
                
                # Get paragraph index from version_paragraphs
                from app_server.db.models import VersionParagraph
                vp = db.query(VersionParagraph).filter(
                    VersionParagraph.paragraph_id == paragraph_id,
                    VersionParagraph.version_id == comment.version_id
                ).first()
                if vp:
                    para_idx = vp.paragraph_index
        except Exception as e:
            logger.warning(f"Error parsing comment metadata: {e}")
    
    # Render button HTML matching template structure
    button_html = f'''<button class="{classes}"
            type="button"
            data-para-id="{paragraph_id}"
            data-para-idx="{para_idx or ''}"
            data-symbol="{symbol}"
            {"data-has-check-result=\"true\"" if has_check_result else ""}
            aria-label="Impact details">{symbol}</button>'''
    
    return HTMLResponse(content=button_html)


@router.get(
    "/assistant/checks/{check_id}/legend-symbols",
    response_class=JSONResponse,
)
async def get_check_legend_symbols(
    check_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Return legend symbols for a check from JSON config files."""
    from app_server.core.checks_loader import get_check_definition
    
    # Try to get check definition (searches both version_comparison and document_comparison)
    try:
        check_def = get_check_definition(check_id, context=None)
    except KeyError:
        # Check not found in either config
        return JSONResponse(content={"symbols": []})
    
    # Extract legend symbols
    symbols = []
    if check_def.legend and check_def.legend.symbols:
        symbols = [
            {
                "symbol": sym.symbol,
                "label": sym.label,
                "color": sym.color,
            }
            for sym in check_def.legend.symbols
        ]
    
    return JSONResponse(content={"symbols": symbols})


def _resolve_version_ids_for_save(
    db: Session,
    doc: Document,
    current_user,
    check_id: str,
    old_version_id: int | None,
    new_version_id: int | None,
) -> tuple[int | None, int | None]:
    """Resolve and validate version IDs for saving manual check results."""
    # Get check definition to determine if this is a version comparison check
    try:
        check_def = get_check_definition(check_id, context="comparing")
        is_version_comparison = check_def.category == "version_comparison"
    except (KeyError, AttributeError):
        is_version_comparison = True
    
    # Resolve explicit version IDs
    resolved_old_version_id = None
    resolved_new_version_id = None
    
    if old_version_id:
        old_version = verify_version_access(int(old_version_id), current_user, db)
        resolved_old_version_id = old_version.id
    if new_version_id:
        new_version = verify_version_access(int(new_version_id), current_user, db)
        resolved_new_version_id = new_version.id
    
    # For version comparison checks, if version IDs are missing, try to resolve from document
    if is_version_comparison and (not resolved_old_version_id or not resolved_new_version_id):
        versions = (
            db.query(Version)
            .filter(Version.document_id == doc.id, Version.deleted_at.is_(None))
            .order_by(Version.created_at.desc())
            .limit(2)
            .all()
        )
        
        if len(versions) >= 2:
            if not resolved_new_version_id:
                resolved_new_version_id = versions[0].id
            if not resolved_old_version_id:
                resolved_old_version_id = versions[1].id
        elif len(versions) == 1:
            if not resolved_new_version_id:
                resolved_new_version_id = versions[0].id
            if not resolved_old_version_id:
                resolved_old_version_id = versions[0].id
    
    # Validate that we have at least new_version_id for version comparison checks
    if is_version_comparison and not resolved_new_version_id:
        raise HTTPException(
            status_code=400,
            detail=f"Version comparison check '{check_id}' requires at least new_version_id. "
                   f"Please provide version IDs or ensure the document has versions."
        )
    
    return resolved_old_version_id, resolved_new_version_id


@router.get(
    "/assistant/checks/{check_id}/edit",
    response_class=HTMLResponse,
)
async def get_check_edit_form(
    check_id: str,
    request: Request,
    doc_id: int = Query(...),
    old_version_id: int | None = Query(None),
    new_version_id: int | None = Query(None),
    paragraph_index: str | int | None = Query(None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Get edit form for a check result in the sidebar."""
    import logging
    
    _logger = logging.getLogger(__name__)
    current_user = get_current_user(request, db)
    doc = verify_document_access(doc_id, current_user, db)
    
    # Parse paragraph_index
    parsed_paragraph_index = parse_paragraph_index(paragraph_index)
    
    if parsed_paragraph_index is None:
        raise HTTPException(
            status_code=400,
            detail="paragraph_index is required"
        )
    
    # Resolve version IDs
    resolved_old_version_id, resolved_new_version_id = _resolve_version_ids_for_save(
        db=db,
        doc=doc,
        current_user=current_user,
        check_id=check_id,
        old_version_id=old_version_id,
        new_version_id=new_version_id,
    )
    
    if not resolved_new_version_id:
        raise HTTPException(status_code=400, detail="new_version_id is required")
    
    # Get paragraph_id
    para_id = get_paragraph_id_from_index(db, parsed_paragraph_index, resolved_new_version_id)
    if not para_id:
        raise HTTPException(status_code=404, detail="Paragraph not found")
    
    # Try to load check comment first
    check_comment = get_check_comment(db, para_id, check_id)
    
    if check_comment:
        try:
            meta = getattr(check_comment, 'comment_metadata', None)
        except AttributeError:
            meta = None
        if meta:
            # User has overridden - use check comment metadata
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except:
                    meta = {}
            
            if isinstance(meta, dict):
                form_data = {
                    "check_id": check_id,
                    "doc_id": doc_id,
                    "old_version_id": resolved_old_version_id,
                    "new_version_id": resolved_new_version_id,
                    "paragraph_index": parsed_paragraph_index,
                    "symbol": meta.get("symbol", "~"),
                    "analysis": meta.get("analysis", ""),
                    "recommendations": meta.get("recommendations", []),
                    "metrics": {},
                    "history": [],  # TODO: Load comment history if needed
                }
            else:
                # Invalid metadata, fall back to AI
                form_data = None
        else:
            form_data = None
    else:
        form_data = None
    
    # Fall back to AI result if no check comment
    if form_data is None:
        # Load original AI version
        original_ai_version = load_original_ai_version(
            db, check_id, doc.id, resolved_old_version_id, resolved_new_version_id
        )
        
        if not original_ai_version:
            form_data = {
                "check_id": check_id,
                "doc_id": doc_id,
                "old_version_id": resolved_old_version_id,
                "new_version_id": resolved_new_version_id,
                "paragraph_index": parsed_paragraph_index,
                "symbol": '~',
                "analysis": '',
                "recommendations": [],
                "metrics": {},
                "history": [],
            }
        else:
            original_ai_json = load_version_json(original_ai_version)
            original_ai_rows = original_ai_json.get("rows", [])
            ai_row = find_paragraph_row(original_ai_rows, parsed_paragraph_index)
            
            if ai_row:
                form_data = extract_form_data_from_row(ai_row, check_id, parsed_paragraph_index)
                # Ensure required fields are set
                form_data["check_id"] = check_id
                form_data["doc_id"] = doc_id
                form_data["old_version_id"] = resolved_old_version_id
                form_data["new_version_id"] = resolved_new_version_id
                form_data["paragraph_index"] = parsed_paragraph_index
            else:
                form_data = {
                    "check_id": check_id,
                    "doc_id": doc_id,
                    "old_version_id": resolved_old_version_id,
                    "new_version_id": resolved_new_version_id,
                    "paragraph_index": parsed_paragraph_index,
                    "symbol": '~',
                    "analysis": '',
                    "recommendations": [],
                    "metrics": {},
                    "history": [],
                }
    
    _logger.info(f"[get_edit_form] Final form_data for check_id={check_id}, para_idx={parsed_paragraph_index}:")
    _logger.info(f"[get_edit_form] original_ai_analysis_length={len(form_data.get('original_ai_analysis', ''))}, original_ai_recommendations_count={len(form_data.get('original_ai_recommendations', []))}, history_count={len(form_data.get('history', []))}")
    
    # Get check definition for symbol options
    try:
        check_def = get_check_definition(check_id, context="comparing")
    except (KeyError, AttributeError):
        check_def = None
    
    # Render edit form
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates")
    
    return templates.TemplateResponse(
        "assistant_panel/checks/edit_sidebar.html",
        {
            "request": request,
            **form_data,
            "check_def": check_def,
            "comment_analytics_enabled": current_user.comment_analytics_enabled if current_user else False,
        }
    )


def _resolve_version_ids(
    *, doc_id: int, explicit_old: Optional[int], explicit_new: Optional[int], db: Session
) -> Tuple[Optional[int], Optional[int]]:
    if explicit_old and explicit_new:
        return explicit_old, explicit_new

    versions = (
        db.query(Version)
        .filter(Version.document_id == doc_id, Version.deleted_at.is_(None))
        .order_by(Version.created_at.desc())
        .limit(2)
        .all()
    )

    if not versions:
        return None, None

    if len(versions) == 1:
        version_id = versions[0].id
        return version_id, version_id

    newest = versions[0].id
    previous = versions[1].id
    return previous, newest


def _has_cached_completed_result(
    *,
    db: Session,
    doc_id: int,
    old_version_id: int,
    new_version_id: int,
    check_id: str = "impact_on_project",
) -> bool:
    """Check if a completed result exists in cache for a check."""
    record = (
        db.query(AssistantCheckResult)
        .filter(
            AssistantCheckResult.check_id == check_id,
            AssistantCheckResult.doc_id == doc_id,
            AssistantCheckResult.old_version_id == old_version_id,
            AssistantCheckResult.new_version_id == new_version_id,
        )
        .first()
    )

    if not record or record.status != "completed" or not record.result_json:
        return False

    try:
        json.loads(record.result_json)
    except json.JSONDecodeError:
        return False

    return True


def _has_cached_completed_result_predefined(
    *,
    db: Session,
    doc_id: int,
    version_id: int,
    check_id: str,
) -> bool:
    """Check if a completed result exists in cache for a predefined data check."""
    record = (
        db.query(AssistantCheckResult)
        .filter(
            AssistantCheckResult.check_id == check_id,
            AssistantCheckResult.doc_id == doc_id,
            AssistantCheckResult.old_version_id.is_(None),  # NULL for predefined data checks
            AssistantCheckResult.new_version_id == version_id,
        )
        .first()
    )

    if not record or record.status != "completed" or not record.result_json:
        return False

    try:
        json.loads(record.result_json)
    except json.JSONDecodeError:
        return False

    return True


def _get_anonymous_session_id(request: Request) -> Optional[str]:
    try:
        cookies = request.cookies if isinstance(request.cookies, dict) else {}
        return cookies.get("anon_session_id")
    except Exception:  # pragma: no cover - defensive
        return None




