"""Tests for plan region impact persistence."""

from __future__ import annotations

from pathlib import Path

from iterthink.ai.local_vision_embedding import VISION_EMBEDDING_MODEL_ID
from iterthink.db.session import session_scope
from iterthink.persistence import content_repo, plan_region_impact_store
from iterthink.persistence import store_db
from iterthink.services.plan_change_regions import DetectedChangeRegion
from iterthink.services.plan_region_context import PlanRegionImpactContext


def _persist_doc(tmp_path: Path) -> int:
    md = tmp_path / "note.md"
    md.write_text("A\n\nB", encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), "A\n\nB", "manual")
        assert vid is not None
        return int(vid)


def test_upsert_region_vector_round_trip(ephemeral_store: None, tmp_path: Path) -> None:
    vec = [0.1] * 768
    rid = plan_region_impact_store.upsert_region_vector(
        candidate_version_id=99,
        region_key="rk",
        vector=vec,
    )
    cache_key = plan_region_impact_store.plan_region_cache_key(99)
    with store_db.connect() as conn:
        got = store_db.embedding_cache_get(conn, cache_key, "rk", VISION_EMBEDDING_MODEL_ID)
    assert got is not None
    assert int(rid) > 0


def test_upsert_region_impact_writes_annotation(ephemeral_store: None, tmp_path: Path) -> None:
    from iterthink.persistence import plan_pdf_annotations

    vid = _persist_doc(tmp_path)
    md = tmp_path / "note.md"
    reg = DetectedChangeRegion(
        region_key="rk2",
        page_index=1,
        norm_bbox=(0.1, 0.2, 0.3, 0.4),
        pixel_count=400,
    )
    with session_scope() as s:
        anns = plan_pdf_annotations.sync_auto_change_regions(
            s,
            candidate_version_id=vid,
            baseline_version_id=1,
            regions=[reg],
        )
        ann = anns[0]
        ctx = PlanRegionImpactContext(
            annotation_id=int(ann.id),
            region_key="rk2",
            paragraph_index=int(ann.paragraph_index),
            page_index=1,
            norm_bbox=(0.1, 0.2, 0.3, 0.4),
            baseline_version_id=1,
            candidate_version_id=vid,
            text_change_ids=(),
            body="",
        )
        vec_rowid = plan_region_impact_store.upsert_region_vector(
            candidate_version_id=vid,
            region_key="rk2",
            vector=[0.0] * 768,
        )
        plan_region_impact_store.upsert_region_impact(
            s,
            doc_path=md,
            region=ctx,
            impact_narrative="Wall shifted affecting egress.",
            vec_rowid=int(vec_rowid),
            vision_model="llava:13b",
        )
        from sqlalchemy import select

        from iterthink.db.models import ImpactAnnotation

        row = s.execute(
            select(ImpactAnnotation).where(
                ImpactAnnotation.content_version_id == vid,
                ImpactAnnotation.paragraph_index == int(ann.paragraph_index),
                ImpactAnnotation.prompt_id == plan_region_impact_store.PLAN_REGION_IMPACT_PROMPT_ID,
            )
        ).scalar_one_or_none()
        assert row is not None
        assert "Wall shifted" in (row.comment or "")
