"""Orchestrate plan change-region impact: crops, LLaVA, vision embed, DB persist."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from iterthink import config
from iterthink.ai.local_vision_embedding import embed_image_crop_sync
from iterthink.ai.ollama_plan_impact import (
    assess_plan_region_impact_async,
    check_plan_impact_vision_ready,
)
from iterthink.persistence import content_repo, plan_region_impact_store
from iterthink.persistence.content_changes import enrich_plan_region_impact_geometry
from iterthink.services.document_import import render_pdf_to_png_pages
from iterthink.services.plan_region_context import PlanRegionImpactContext, list_region_contexts
from iterthink.services.plan_region_crops import build_region_crop_set
from iterthink.services.plan_text_diff import diff_plan_geometry
from iterthink.services.plan_text_extract import load_plan_text_sidecar

ProgressCb = Callable[[int, int], Awaitable[None] | None]


@dataclass(frozen=True)
class PlanRegionImpactResult:
    region_id: int
    bbox: tuple[float, float, float, float]
    impact_narrative: str
    embedding_id: str


def _text_hints_for_region(
    doc_path: Path,
    region: PlanRegionImpactContext,
    *,
    baseline_version_id: int,
    candidate_version_id: int,
) -> str:
    if not region.text_change_ids:
        return ""
    base_geo = load_plan_text_sidecar(doc_path, baseline_version_id) or {"pages": []}
    cand_geo = load_plan_text_sidecar(doc_path, candidate_version_id) or {"pages": []}
    changes = diff_plan_geometry(base_geo, cand_geo)
    by_id = {c.change_id: c for c in changes}
    lines: list[str] = []
    for tid in region.text_change_ids:
        ch = by_id.get(str(tid))
        if ch is None:
            continue
        if ch.kind == "added" and ch.new_text:
            lines.append(f"+ {ch.new_text}")
        elif ch.kind == "removed" and ch.old_text:
            lines.append(f"- {ch.old_text}")
        elif ch.kind == "modified":
            lines.append(f"~ {ch.old_text or ''} -> {ch.new_text or ''}")
        elif ch.display_text:
            lines.append(ch.display_text)
    return "\n".join(lines)


def _page_pngs_for_pair(
    session: Session,
    *,
    doc_path: Path,
    baseline_version_id: int,
    candidate_version_id: int,
) -> tuple[list[Path], list[Path]]:
    base_rel = content_repo.get_version_pdf_relpath(session, int(baseline_version_id))
    cand_rel = content_repo.get_version_pdf_relpath(session, int(candidate_version_id))
    if not base_rel or not cand_rel:
        return [], []
    base_pdf = content_repo.pdf_asset_abs_path(base_rel)
    cand_pdf = content_repo.pdf_asset_abs_path(cand_rel)
    if not base_pdf.is_file() or not cand_pdf.is_file():
        return [], []
    return (
        render_pdf_to_png_pages(base_pdf, pdf_profile="plan"),
        render_pdf_to_png_pages(cand_pdf, pdf_profile="plan"),
    )


async def analyze_plan_change_regions(
    ollama: Any,
    session: Session,
    *,
    doc_path: Path,
    baseline_version_id: int,
    candidate_version_id: int,
    region_ids: list[int] | None = None,
    progress_cb: ProgressCb | None = None,
) -> list[PlanRegionImpactResult]:
    ready, msg = await check_plan_impact_vision_ready(ollama)
    if not ready:
        raise RuntimeError(msg)

    regions = list_region_contexts(session, candidate_version_id=int(candidate_version_id))
    if region_ids is not None:
        wanted = {int(r) for r in region_ids}
        regions = [r for r in regions if int(r.annotation_id) in wanted]
    if not regions:
        return []

    base_pages, cand_pages = await asyncio.to_thread(
        _page_pngs_for_pair,
        session,
        doc_path=doc_path,
        baseline_version_id=int(baseline_version_id),
        candidate_version_id=int(candidate_version_id),
    )
    if not base_pages or not cand_pages:
        raise RuntimeError("Plan PDF page renders missing for baseline/candidate")

    vision_model = (config.PLAN_REGION_IMPACT_VISION_MODEL or "llava:13b").strip()
    results: list[PlanRegionImpactResult] = []
    total = len(regions)

    for i, region in enumerate(regions):
        if progress_cb is not None:
            maybe = progress_cb(i + 1, total)
            if asyncio.iscoroutine(maybe):
                await maybe
        pi = int(region.page_index)
        if pi < 0 or pi >= min(len(base_pages), len(cand_pages)):
            continue
        try:
            crops = await asyncio.to_thread(
                build_region_crop_set,
                doc_path=doc_path.resolve(),
                candidate_version_id=int(candidate_version_id),
                region_key=region.region_key,
                base_page_png=base_pages[pi],
                cand_page_png=cand_pages[pi],
                norm_bbox=region.norm_bbox,
            )
            hints = _text_hints_for_region(
                doc_path,
                region,
                baseline_version_id=int(baseline_version_id),
                candidate_version_id=int(candidate_version_id),
            )
            narrative = await assess_plan_region_impact_async(
                ollama,
                crop_before=crops.crop_before,
                crop_after=crops.crop_after,
                context_crop=crops.context_crop,
                model=vision_model,
                text_hints=hints,
            )
            vector = await asyncio.to_thread(embed_image_crop_sync, crops.crop_after)
            vec_rowid = await asyncio.to_thread(
                plan_region_impact_store.upsert_region_vector,
                candidate_version_id=int(candidate_version_id),
                region_key=region.region_key,
                vector=vector,
            )
            chunk_id = plan_region_impact_store.chunk_id_for_region(
                session,
                doc_path=doc_path,
                candidate_version_id=int(candidate_version_id),
                region_key=region.region_key,
            )
            await asyncio.to_thread(
                plan_region_impact_store.upsert_region_impact,
                session,
                doc_path=doc_path.resolve(),
                region=region,
                impact_narrative=narrative,
                vec_rowid=int(vec_rowid),
                vision_model=vision_model,
            )
            enrich_plan_region_impact_geometry(
                session,
                annotation_id=int(region.annotation_id),
                impact_narrative=narrative,
                vec_rowid=int(vec_rowid),
                chunk_id=chunk_id,
            )
            results.append(
                PlanRegionImpactResult(
                    region_id=int(region.annotation_id),
                    bbox=region.norm_bbox,
                    impact_narrative=narrative,
                    embedding_id=str(vec_rowid),
                )
            )
        except BaseException:
            continue

    return results
