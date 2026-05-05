"""Endpoints related to document import and creation."""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app_server.core import jinja_env
from app_server.core.security import get_csrf_token, verify_csrf_token
from app_server.services.auth import get_current_user
from app_server.services.projects import ensure_project_membership
from app_server.services.security_utils import api_guard, check_rate_limit, get_client_ip
from app_server.core.db import get_db
from app_server.services.feature_access import check_feature_access
from app_server.db.models import Document, Version, VersionParagraph
from app_server.services.services import assign_paragraph_ids
from app_server.services.openstack_storage import openstack_storage
from app_server.services.document_importer import (
    import_document,
    store_pdf_assets_locally,
)
from app_server.services.document_preview import extract_preview_for_classification
from app_server.services.document_classifier import classify_document_type
from app_server.core.document_types import is_valid_content_type

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/documents/new")
async def new_document():
    """Show form to create new document (legacy endpoint for backwards compatibility)."""

    template = jinja_env.get_template("_new_document_form.html")
    return HTMLResponse(content=template.render())


@router.get("/documents/new-form", response_class=HTMLResponse)
async def new_document_form(request: Request, response: Response):
    """Get new document form fragment (with tabs for new and import)."""

    get_csrf_token(request, response)
    template = jinja_env.get_template("_new_import_form.html")
    return HTMLResponse(content=template.render())


@router.get("/documents/import-form", response_class=HTMLResponse)
async def import_document_form(request: Request, response: Response):
    """Get import document form fragment (legacy - redirects to new-form with import tab)."""

    get_csrf_token(request, response)
    template = jinja_env.get_template("_new_import_form.html")
    html = template.render()
    html = html.replace(
        "</script>",
        """
    // Switch to import tab after load
    setTimeout(() => {
        if (typeof switchNewImportTab === 'function') {
            switchNewImportTab('import');
        }
    }, 100);
</script>""",
    )
    return HTMLResponse(content=html)


@router.post("/documents/preview-classify", response_class=JSONResponse)
async def preview_and_classify_document(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Phase 1: Extract preview and classify document type.
    Returns proposed type for user confirmation.
    """
    if not verify_csrf_token(request, response):
        return JSONResponse(
            content={"status": "error", "message": "Invalid CSRF token"},
            status_code=403,
        )

    user = get_current_user(request, db)
    allowed, message = check_feature_access(user, "upload_documents", db)
    if not allowed:
        raise HTTPException(status_code=403, detail=message)

    api_guard(request)
    ip = get_client_ip(request)
    rate_key = f"preview_classify:{ip}"
    ok, _ = check_rate_limit(rate_key, max_requests=30, window_seconds=3600, db=db)
    if not ok:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

    filename = file.filename
    if not filename:
        return {"status": "error", "message": "No file uploaded"}

    safe_filename = "".join(c for c in filename if c.isalnum() or c in ".-_")[:255]
    if not safe_filename:
        safe_filename = "uploaded_file"
    file_ext = safe_filename.split(".")[-1].lower() if "." in safe_filename else ""

    logger.info("File extension: %s, original filename: %s", file_ext, filename)
    allowed_extensions = {"doc", "docx", "pdf"}
    if file_ext not in allowed_extensions:
        return {
            "status": "error",
            "message": "Unsupported file format. Please upload Word documents (.doc, .docx) or PDF files (.pdf) only.",
        }

    content_type = file.content_type or ""
    temp_file_path: Optional[Path] = None
    
    try:
        temp_filename = f"{secrets.token_hex(16)}.{file_ext}"
        temp_file_path = Path("data/_input") / temp_filename
        temp_file_path.parent.mkdir(parents=True, exist_ok=True)

        MAX_FILE_SIZE = 100 * 1024 * 1024
        file_size = 0
        with open(temp_file_path, "wb") as buffer:
            while True:
                chunk = await file.read(8192)
                if not chunk:
                    break
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE:
                    temp_file_path.unlink(missing_ok=True)
                    return {
                        "status": "error",
                        "message": f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB.",
                    }
                buffer.write(chunk)
        
        if file_size == 0:
            temp_file_path.unlink(missing_ok=True)
            return {"status": "error", "message": "Empty file uploaded"}
        
        # Validate file using magic bytes
        from app_server.core.file_validation import validate_uploaded_file
        is_valid, error_msg = validate_uploaded_file(temp_file_path, filename, content_type)
        if not is_valid:
            temp_file_path.unlink(missing_ok=True)
            return {
                "status": "error",
                "message": error_msg or "File validation failed. Please upload a valid Word document or PDF file.",
            }

        # Extract preview and classify
        logger.info("Running document classifier...")
        preview = extract_preview_for_classification(
            str(temp_file_path),
            file_ext,
            max_pages=3,
            max_chars=5000,
        )
        classification = classify_document_type(
            preview,
            db_session=db,
            user_id=user.id if user else None,
        )

        # Return classification result
        result = {
            "status": "success",
            "filename": filename,
            "file_ext": file_ext,
            "file_size": file_size,
            "page_count": preview.page_count,
            "is_scanned": preview.is_scanned,
        }

        if classification.proposed_type:
            result["proposed_content_type"] = classification.proposed_type
            result["confidence"] = classification.confidence
            result["method"] = classification.method
            result["reasoning"] = classification.reasoning
            logger.info(
                f"Classifier proposed: {classification.proposed_type} "
                f"(confidence: {classification.confidence:.2f}, method: {classification.method})"
            )
        else:
            result["proposed_content_type"] = None
            result["confidence"] = 0.0
            result["method"] = classification.method
            result["reasoning"] = classification.reasoning
            logger.info("Classifier could not determine document type")

        # Store temp file path in session or return it (we'll need to handle this)
        # For now, we'll return a token that can be used to retrieve the file
        # In a real implementation, you might want to store the file temporarily with a token
        # For simplicity, we'll return the temp filename (not secure, but works for now)
        result["temp_file_token"] = temp_filename

        return result

    except Exception as exc:
        logger.error("Error in preview-classify: %s", exc, exc_info=True)
        if temp_file_path and temp_file_path.exists():
            temp_file_path.unlink(missing_ok=True)
        return {
            "status": "error",
            "message": f"Error processing file: {str(exc)}",
        }


@router.post("/documents/import", response_class=JSONResponse)
async def import_document_endpoint(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    title: str = Form(...),
    project: Optional[str] = Form(None),
    tag: Optional[str] = Form(None),
    content_type: Optional[str] = Form(None),  # User-selected content type (can override classifier)
    status: Optional[str] = Form(None),  # Document status (draft, shared, published, archived)
    db: Session = Depends(get_db),
):
    """
    Import a Word document or PDF. (Anonymous users blocked)
    
    NOTE: This endpoint requires session-based authentication (logged-in user via cookies).
    It does NOT support API key authentication via X-API-Key header.
    
    For API key authentication, use the /api/v1/documents/import endpoint (if it exists)
    or create one that uses the authenticate_api_key dependency from app_server.api.v1.auth.
    
    The api_guard() function below is NOT for authentication - it's just a server-side
    guard that checks if API_KEY env var is set. It doesn't authenticate organizational API keys.
    """

    if not verify_csrf_token(request, response):
        return JSONResponse(
            content={"status": "error", "message": "Invalid CSRF token"},
            status_code=403,
        )

    logger.info("Import request received: %s", file.filename if file else "no file")
    # This only checks for session cookies (auth_token), not API keys
    user = get_current_user(request, db)
    allowed, message = check_feature_access(user, "upload_documents", db)
    if not allowed:
        raise HTTPException(status_code=403, detail=message)

    # api_guard() is NOT authentication - it just checks if server has API_KEY env var set
    # It doesn't authenticate organizational API keys from the database
    api_guard(request)
    ip = get_client_ip(request)
    rate_key = f"import_doc:{ip}"
    ok, _ = check_rate_limit(rate_key, max_requests=30, window_seconds=3600, db=db)
    if not ok:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

    filename = file.filename
    if not filename:
        return {"status": "error", "message": "No file uploaded"}

    safe_filename = "".join(c for c in filename if c.isalnum() or c in ".-_")[:255]
    if not safe_filename:
        safe_filename = "uploaded_file"
    file_ext = safe_filename.split(".")[-1].lower() if "." in safe_filename else ""

    logger.info("File extension: %s, original filename: %s", file_ext, filename)
    allowed_extensions = {"doc", "docx", "pdf"}
    if file_ext not in allowed_extensions:
        return {
            "status": "error",
            "message": "Unsupported file format. Please upload Word documents (.doc, .docx) or PDF files (.pdf) only.",
        }

    # Get file MIME type for validation (different from document content_type Form parameter)
    file_mime_type = file.content_type or ""

    temp_file_path: Optional[Path] = None
    try:
        temp_filename = f"{secrets.token_hex(16)}.{file_ext}"
        temp_file_path = Path("data/_input") / temp_filename
        temp_file_path.parent.mkdir(parents=True, exist_ok=True)

        MAX_FILE_SIZE = 100 * 1024 * 1024
        file_size = 0
        with open(temp_file_path, "wb") as buffer:
            while True:
                chunk = await file.read(8192)
                if not chunk:
                    break
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE:
                    temp_file_path.unlink(missing_ok=True)
                    return {
                        "status": "error",
                        "message": f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB.",
                    }
                buffer.write(chunk)
        await file.seek(0)
        
        # Validate file using magic bytes (after writing to disk)
        # Note: file_mime_type is the file's MIME type (e.g., "application/pdf")
        #       content_type is the document content type (e.g., "standard", "law")
        from app_server.core.file_validation import validate_uploaded_file
        is_valid, error_msg = validate_uploaded_file(temp_file_path, filename, file_mime_type)
        if not is_valid:
            temp_file_path.unlink(missing_ok=True)
            return {
                "status": "error",
                "message": error_msg or "File validation failed. Please upload a valid Word document or PDF file.",
            }
    except Exception as exc:
        logger.error("Error reading file: %s", exc)
        if temp_file_path and temp_file_path.exists():
            temp_file_path.unlink()
        return {"status": "error", "message": "Error processing file. Please try again."}

    try:
        os.makedirs("data/documents", exist_ok=True)
        os.makedirs("data/_assets", exist_ok=True)
        os.makedirs("data/_input", exist_ok=True)

        project_name = project.strip() if project and project.strip() else None
        tag_name = tag.strip() if tag and tag.strip() else None

        if project_name and user:
            ensure_project_membership(project_name, user.id, db, role="admin")

        # Validate and use user-provided content type (from Phase 1 confirmation)
        final_content_type = None
        logger.info(f"Received content_type parameter: {repr(content_type)}")
        if content_type and content_type.strip():
            content_type_clean = content_type.strip()
            logger.info(f"Cleaned content_type: {repr(content_type_clean)}")
            if is_valid_content_type(content_type_clean):
                final_content_type = content_type_clean
                logger.info(f"Using user-provided content_type: {final_content_type}")
            else:
                logger.warning(f"Invalid content_type provided: {content_type_clean}, will be NULL")
        else:
            logger.info("No content_type provided, will be NULL (default)")
        
        logger.info(f"Final content_type to save: {repr(final_content_type)}")

        # Validate status if provided
        final_status = None
        if status and status.strip():
            status_clean = status.strip().lower()
            valid_statuses = ["draft", "shared", "published", "archived"]
            if status_clean in valid_statuses:
                final_status = status_clean
                logger.info(f"Using provided status: {final_status}")
            else:
                logger.warning(f"Invalid status provided: {status_clean}, will use default (draft)")

        # Phase 2: Full import with confirmed content_type and status
        markdown_content, doc_id = import_document(
            str(temp_file_path),
            file_ext,
            title,
            db,
            user_id=user.id if user else None,
            project=project_name,
            tag=tag_name,
            content_type=final_content_type,
            status=final_status,
        )

        if temp_file_path and temp_file_path.exists():
            temp_file_path.unlink()

        return {"status": "success", "document_id": doc_id}
    except Exception as exc:
        import traceback

        logger.error("Import failed: %s\n%s", exc, traceback.format_exc())
        if temp_file_path and temp_file_path.exists():
            temp_file_path.unlink()
        return {
            "status": "error",
            "message": "Failed to import document. Please check the file format and try again.",
        }


@router.post("/documents/{doc_id}/import-version", response_class=JSONResponse)
async def import_document_version(
    request: Request,
    response: Response,
    doc_id: int,
    file: UploadFile = File(...),
    commit_message: str = Form(...),
    db: Session = Depends(get_db),
):
    """Import a Word document or PDF as a new version of an existing document."""

    if not verify_csrf_token(request, response):
        return JSONResponse(
            content={"status": "error", "message": "Invalid CSRF token"},
            status_code=403,
        )

    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login required to import versions")

    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        return {"status": "error", "message": "Document not found"}

    filename = file.filename
    if not filename:
        return {"status": "error", "message": "No file uploaded"}

    safe_filename = "".join(c for c in filename if c.isalnum() or c in ".-_")[:255]
    if not safe_filename:
        safe_filename = "uploaded_file"

    file_ext = safe_filename.split(".")[-1].lower() if "." in safe_filename else ""
    if file_ext not in ["doc", "docx", "pdf"]:
        return {
            "status": "error",
            "message": "Unsupported file format. Please upload Word documents (.doc, .docx) or PDF files (.pdf) only.",
        }

    MAX_FILE_SIZE = 100 * 1024 * 1024
    file_size = 0
    temp_file_path: Optional[Path] = None
    try:
        temp_filename = f"{secrets.token_hex(16)}.{file_ext}"
        temp_file_path = Path("data/_input") / temp_filename
        temp_file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(temp_file_path, "wb") as buffer:
            while True:
                chunk = await file.read(8192)
                if not chunk:
                    break
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE:
                    temp_file_path.unlink(missing_ok=True)
                    return {
                        "status": "error",
                        "message": f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB.",
                    }
                buffer.write(chunk)

        if file_size == 0:
            temp_file_path.unlink(missing_ok=True)
            return {"status": "error", "message": "Empty file uploaded"}
        
        # Validate file using magic bytes
        from app_server.core.file_validation import validate_uploaded_file
        is_valid, error_msg = validate_uploaded_file(temp_file_path, filename, file.content_type)
        if not is_valid:
            temp_file_path.unlink(missing_ok=True)
            return {
                "status": "error",
                "message": error_msg or "File validation failed. Please upload a valid Word document or PDF file.",
            }

        os.makedirs("data/documents", exist_ok=True)
        os.makedirs("data/_assets", exist_ok=True)
        os.makedirs("data/_input", exist_ok=True)

        temp_path = str(temp_file_path)
        await file.seek(0)

        extracted_images = []
        if file_ext in ["doc", "docx"]:
            from app_server.services.document_importer import convert_word_to_markdown

            markdown_content = convert_word_to_markdown(temp_path)
        else:
            from app_server.services.document_importer import convert_pdf_to_markdown

            markdown_content, _, _, extracted_images = convert_pdf_to_markdown(temp_path)

        latest_version = (
            db.query(Version)
            .filter(Version.document_id == doc_id)
            .order_by(Version.created_at.desc())
            .first()
        )
        if not latest_version:
            return {"status": "error", "message": "No existing versions found for this document"}

        new_version = Version(
            document_id=doc_id,
            parent_id=latest_version.id,
            commit_message=commit_message,
            title=commit_message,
            source="user",
            branch_id=latest_version.branch_id,
            created_by=user.id,
        )
        db.add(new_version)

        from datetime import UTC, datetime

        doc.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(new_version)

        if file_ext == "pdf":
            pdf_stored = False
            if openstack_storage.is_enabled():
                try:
                    original_blob_name = f"{doc_id}/{new_version.id}/original.pdf"
                    result = openstack_storage.upload_file(temp_path, original_blob_name)
                    pdf_stored = bool(result)

                    for img_idx, img_data in enumerate(extracted_images, start=1):
                        try:
                            if isinstance(img_data, tuple):
                                img_path, clean_name = img_data
                                blob_name = f"{doc_id}/{new_version.id}/pdf_image_{img_idx}_{clean_name}"
                                openstack_storage.upload_file(img_path, blob_name)
                            else:
                                blob_name = f"{doc_id}/{new_version.id}/pdf_image_{img_idx}.png"
                                with open(img_data, "rb") as img_file:
                                    openstack_storage.upload_bytes(img_file.read(), blob_name, content_type="image/png")
                        except Exception as exc:
                            logger.warning("Failed to upload extracted image %s: %s", img_idx, exc)
                except Exception as exc:
                    logger.error("Failed to upload PDF to OpenStack storage: %s", exc, exc_info=True)
                    pdf_stored = False

            if not pdf_stored:
                store_pdf_assets_locally(temp_path, doc_id, new_version.id, extracted_images)

            for img_data in extracted_images:
                try:
                    persistent_path = img_data[0] if isinstance(img_data, tuple) else img_data
                    Path(persistent_path).unlink(missing_ok=True)
                except Exception:
                    pass

        new_version.content = markdown_content
        paragraph_map = assign_paragraph_ids(doc_id, latest_version.id, markdown_content, db)
        for para_index, para_id in paragraph_map.items():
            db.add(
                VersionParagraph(
                    version_id=new_version.id,
                    paragraph_id=para_id,
                    paragraph_index=para_index,
                )
            )

        db.commit()

        if temp_file_path and temp_file_path.exists():
            temp_file_path.unlink()

        return {"status": "success", "version_id": new_version.id}
    except Exception as exc:
        import traceback

        logger.error("Import version failed: %s\n%s", exc, traceback.format_exc())
        if temp_file_path and temp_file_path.exists():
            temp_file_path.unlink()
        return {
            "status": "error",
            "message": "Failed to import version. Please check the file format and try again.",
        }

