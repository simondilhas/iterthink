"""Persist plan region impact narratives (PBS) and vision vectors (sqlite-vec)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from iterthink import config
from iterthink.ai.local_vision_embedding import VISION_EMBEDDING_MODEL_ID
from iterthink.db.content_models import Content
from iterthink.persistence import content_repo, impact_annotations, plan_pdf_annotations, store_db
from iterthink.services.plan_region_context import PlanRegionImpactContext

PLAN_REGION_IMPACT_PROMPT_ID = "plan_region_impact"


def plan_region_cache_key(candidate_version_id: int) -> str:
    return f"plan_region::{int(candidate_version_id)}"


def chunk_id_for_region(
    session: Session,
    *,
    doc_path: Path,
    candidate_version_id: int,
    region_key: str,
) -> str:
    lineage_id = content_repo.get_lineage_id_for_path(session, doc_path.resolve())
    lid = lineage_id or f"doc_{int(candidate_version_id)}"
    return f"{lid}:{int(candidate_version_id)}:{region_key}"


def storey_for_version(session: Session, *, content_version_id: int) -> str | None:
    row = session.get(Content, int(content_version_id))
    if row is None:
        return None
    s = (row.storey or "").strip()
    return s or None


def upsert_region_vector(
    *,
    candidate_version_id: int,
    region_key: str,
    vector: list[float],
) -> int:
    cache_key = plan_region_cache_key(candidate_version_id)
    with store_db.connect() as conn:
        store_db.init_schema(conn)
        store_db.embedding_cache_put(
            conn,
            cache_key,
            region_key,
            VISION_EMBEDDING_MODEL_ID,
            vector,
        )
        rid = store_db.embedding_cache_vec_rowid(
            conn, cache_key, region_key, VISION_EMBEDDING_MODEL_ID
        )
    if rid is None:
        raise RuntimeError("Failed to store plan region embedding vector")
    return int(rid)


def upsert_region_impact(
    session: Session,
    *,
    doc_path: Path,
    region: PlanRegionImpactContext,
    impact_narrative: str,
    vec_rowid: int,
    vision_model: str,
) -> None:
    chunk_id = chunk_id_for_region(
        session,
        doc_path=doc_path,
        candidate_version_id=int(region.candidate_version_id),
        region_key=region.region_key,
    )
    storey = storey_for_version(session, content_version_id=int(region.candidate_version_id))
    x0, y0, x1, y1 = region.norm_bbox
    details = {
        "region_id": int(region.annotation_id),
        "region_key": region.region_key,
        "chunk_id": chunk_id,
        "page_index": int(region.page_index),
        "storey": storey,
        "baseline_version_id": int(region.baseline_version_id),
        "norm_bbox": [x0, y0, x1, y1],
        "text_change_ids": list(region.text_change_ids),
        "vec_rowid": int(vec_rowid),
        "embed_model_id": VISION_EMBEDDING_MODEL_ID,
        "vision_model": vision_model,
    }
    impact_annotations.upsert_model_result(
        session,
        content_version_id=int(region.candidate_version_id),
        paragraph_index=int(region.paragraph_index),
        prompt_id=PLAN_REGION_IMPACT_PROMPT_ID,
        status="analyzed",
        comment=impact_narrative,
        details=details,
    )
    plan_pdf_annotations.update_body(
        session,
        annotation_id=int(region.annotation_id),
        body=impact_narrative,
    )
    session.flush()
