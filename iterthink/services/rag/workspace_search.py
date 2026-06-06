"""Semantic workspace search over indexed RAG child chunks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iterthink import config
from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID, embed_batch_sync
from iterthink.compare.paragraph_semantics import blob_to_floats, cosine_sim
from iterthink.persistence import content_repo, store_db
from iterthink.services.rag.enrichment import generate_query_variants
from iterthink.services.rag.index_status import is_lineage_index_stale

FILENAME_PREFIX = "/f"
PROJECT_PREFIX = "/p"


@dataclass(frozen=True)
class ParsedSearchQuery:
    query: str
    filename_mode: bool = False
    project_slug: str | None = None


@dataclass(frozen=True)
class SearchHit:
    lineage_id: str
    resolved_path: Path
    doc_title: str
    section_header: str
    raw_text: str
    parent_text: str
    slot_index: int
    score: float


def parse_search_query(raw: str) -> ParsedSearchQuery:
    """Parse sidebar search: ``/f`` filename filter, ``/p <project> <query>`` project scope."""
    s = (raw or "").strip()
    if s.lower().startswith(FILENAME_PREFIX):
        rest = s[len(FILENAME_PREFIX) :].strip()
        return ParsedSearchQuery(query=rest, filename_mode=True)
    if s.lower().startswith(PROJECT_PREFIX):
        rest = s[len(PROJECT_PREFIX) :].strip()
        if not rest:
            return ParsedSearchQuery(query="")
        slug, _, tail = rest.partition(" ")
        slug = slug.strip()
        if not slug:
            return ParsedSearchQuery(query="")
        return ParsedSearchQuery(query=tail.strip(), project_slug=slug)
    return ParsedSearchQuery(query=s)


def _embed_query_sync(text: str) -> list[float]:
    vecs = embed_batch_sync([text])
    if not vecs:
        return []
    row = vecs[0]
    return row.astype("float32", copy=False).reshape(-1).tolist()


async def _query_embeddings(
    query: str,
    *,
    llm: Any | None,
    llm_model: str | None,
    enrichment_mode: str,
    ki_tier: str,
) -> list[list[float]]:
    texts = [query]
    from iterthink.services.rag.enrichment import enrichment_allowed_for_tier

    if enrichment_allowed_for_tier(ki_tier, enrichment_mode) and llm is not None and llm_model:
        variants = await generate_query_variants(query, llm=llm, model=llm_model)
        texts.extend(q for q in variants if q.strip())
    out: list[list[float]] = []
    for t in texts:
        vec = _embed_query_sync(t)
        if vec:
            out.append(vec)
    return out


async def search_workspace(
    query: str,
    conn: Any,
    session: Any,
    *,
    llm: Any | None = None,
    llm_model: str | None = None,
    enrichment_mode: str = "skip",
    ki_tier: str = "local",
    top_k: int = 10,
    rerank: bool | None = None,
    latest_version_only: bool = True,
    project_slug: str | None = None,
    project_id: int | None = None,
) -> list[SearchHit]:
    parsed = parse_search_query(query)
    if parsed.filename_mode or not parsed.query:
        return []
    q = parsed.query

    scoped_slug = project_slug if project_slug is not None else parsed.project_slug
    rows = store_db.rag_child_fetch_latest_rows(
        conn,
        None,
        project_slug=scoped_slug,
        project_id=project_id,
    )
    if not rows:
        return []

    query_vecs = await _query_embeddings(
        q,
        llm=llm,
        llm_model=llm_model,
        enrichment_mode=enrichment_mode,
        ki_tier=ki_tier,
    )
    if not query_vecs:
        return []

    candidates: list[tuple[float, tuple]] = []
    for row in rows:
        lid = str(row[1])
        indexed_vid = int(row[2])
        if latest_version_only and is_lineage_index_stale(session, lid, indexed_vid):
            continue
        vec_rowid = int(row[8])
        emb_row = conn.execute(
            "SELECT embedding FROM paragraph_vec WHERE rowid = ?",
            (vec_rowid,),
        ).fetchone()
        if emb_row is None:
            continue
        chunk_floats = blob_to_floats(bytes(emb_row[0]))
        if not chunk_floats:
            continue
        best = max(cosine_sim(qv, chunk_floats) for qv in query_vecs)
        candidates.append((best, row))

    candidates.sort(key=lambda t: t[0], reverse=True)
    pool = candidates[:30]
    if not pool:
        return []

    use_rerank = rerank if rerank is not None else bool(getattr(config, "RAG_RERANKER_ENABLED", True))
    if use_rerank and len(pool) > 1:
        from iterthink.ai.local_reranker import rerank_sync

        docs = [str(r[1][4]) for r in pool]
        try:
            scores = rerank_sync(q, docs)
            pool = [(float(scores[i]), pool[i][1]) for i in range(len(pool))]
            pool.sort(key=lambda t: t[0], reverse=True)
        except BaseException:
            pass

    hits: list[SearchHit] = []
    seen_slots: set[tuple[str, int]] = set()
    seen_parents: set[int] = set()
    for score, row in pool:
        lid = str(row[1])
        slot_index = int(row[3])
        parent_id = int(row[9])
        slot_key = (lid, slot_index)
        if slot_key in seen_slots:
            continue
        if parent_id in seen_parents:
            continue
        seen_slots.add(slot_key)
        seen_parents.add(parent_id)

        doc_title = str(row[10])
        section_header = str(row[11])
        parent_text = str(row[12])
        raw_text = str(row[4])

        resolved = _resolve_path_for_lineage(session, lid)
        if resolved is None:
            continue

        hits.append(
            SearchHit(
                lineage_id=lid,
                resolved_path=resolved,
                doc_title=doc_title,
                section_header=section_header,
                raw_text=raw_text,
                parent_text=parent_text,
                slot_index=slot_index,
                score=float(score),
            )
        )
        if len(hits) >= top_k:
            break
    return hits


def _resolve_path_for_lineage(session: Any, lineage_id: str) -> Path | None:
    from sqlalchemy import select

    from iterthink.db.content_models import Content

    row = session.execute(
        select(Content).where(Content.lineage_id == lineage_id).where(Content.is_latest.is_(True)).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    attrs = content_repo.content_attrs(row)
    rp = attrs.get("resolved_path")
    if not rp:
        return None
    try:
        return Path(str(rp)).resolve()
    except (TypeError, ValueError, OSError):
        return None


def unique_files_from_hits(hits: list[SearchHit]) -> list[tuple[Path, float]]:
    best: dict[str, tuple[Path, float]] = {}
    for h in hits:
        key = str(h.resolved_path)
        prev = best.get(key)
        if prev is None or h.score > prev[1]:
            best[key] = (h.resolved_path, h.score)
    ordered = sorted(best.values(), key=lambda t: t[1], reverse=True)
    return ordered
