"""Orchestrate plan change-region detection and DB sync."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from iterthink.persistence import content_repo, plan_pdf_annotations
from iterthink.services.plan_change_regions import detect_change_regions
from iterthink.services.plan_text_extract import load_plan_text_sidecar


def sync_detected_change_regions(
    session: Session,
    *,
    doc_path: Path,
    baseline_version_id: int,
    candidate_version_id: int,
) -> list[plan_pdf_annotations.PlanAnnotation]:
    """Detect changed plan areas and upsert ``change_region`` annotations on the candidate."""
    if int(baseline_version_id) == int(candidate_version_id):
        return plan_pdf_annotations.list_change_regions_for_version(
            session, content_version_id=int(candidate_version_id)
        )
    base_rel = content_repo.get_version_pdf_relpath(session, int(baseline_version_id))
    cand_rel = content_repo.get_version_pdf_relpath(session, int(candidate_version_id))
    if not base_rel or not cand_rel:
        return []
    try:
        base_pdf = content_repo.pdf_asset_abs_path(base_rel)
        cand_pdf = content_repo.pdf_asset_abs_path(cand_rel)
    except (ValueError, OSError):
        return []
    if not base_pdf.is_file() or not cand_pdf.is_file():
        return []
    resolved = doc_path.resolve()
    base_geo = load_plan_text_sidecar(resolved, int(baseline_version_id)) or {"pages": []}
    cand_geo = load_plan_text_sidecar(resolved, int(candidate_version_id)) or {"pages": []}
    detected = detect_change_regions(base_pdf, cand_pdf, base_geo, cand_geo)
    plan_pdf_annotations.sync_auto_change_regions(
        session,
        candidate_version_id=int(candidate_version_id),
        baseline_version_id=int(baseline_version_id),
        regions=detected,
    )
    return plan_pdf_annotations.list_change_regions_for_version(
        session, content_version_id=int(candidate_version_id)
    )
