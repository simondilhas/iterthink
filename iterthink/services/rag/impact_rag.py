"""Cross-file RAG for Impact analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID
from iterthink.compare.paragraph_semantics import (
    blob_to_floats,
    cosine_sim,
    embed_texts_cached,
)
from iterthink.db.content_models import Content
from iterthink.persistence import content_repo, store_db

from .chunk_type import ChunkType, classify_chunk_type, parse_chunk_type
from .context_format import format_rag_context_block, rag_chunk_display_body

IngestOutcomeKind = Literal[
    "updated",
    "unchanged",
    "empty",
    "missing",
    "no_body",
    "no_record",
    "no_path",
    "error",
]


@dataclass(frozen=True)
class IngestFileOutcome:
    path: Path | None
    document_id: int | None
    lineage_id: str | None
    outcome: IngestOutcomeKind
    error: str | None = None


@dataclass(frozen=True)
class IngestResult:
    files: tuple[IngestFileOutcome, ...]

    def indexed_lineage_ids(self) -> list[str]:
        return [
            f.lineage_id
            for f in self.files
            if f.lineage_id and f.outcome in ("updated", "unchanged", "empty")
        ]

    def failures(self) -> list[IngestFileOutcome]:
        return [
            f
            for f in self.files
            if f.outcome in ("error", "missing", "no_body", "no_record", "no_path")
        ]

    def failure_summary(self) -> str:
        parts: list[str] = []
        for f in self.failures():
            name = f.path.name if f.path else str(f.document_id or "?")
            if f.error:
                parts.append(f"{name}: {f.error}")
            else:
                parts.append(f"{name}: {f.outcome}")
        return "; ".join(parts)


def _format_ranked_context_parts(
    scored: list[
        tuple[float, str, str, int, ChunkType, str, str, str]
    ],
    *,
    top_k: int,
    strict_filter: bool,
) -> str:
    """Each score tuple: sim, fname, raw_text, slot_index, chunk_type, parent_text, doc_title, section_header."""
    parts: list[str] = []
    seen_parents: set[int] = set()
    for item in scored:
        _sim, fname, raw_text, slot_index, chunk_type, parent_text, doc_title, section_header = item[:8]
        parent_id = item[8] if len(item) > 8 else None
        if parent_id is not None:
            pid = int(parent_id)
            if pid in seen_parents:
                continue
            seen_parents.add(pid)
        block = format_rag_context_block(
            fname=fname,
            doc_title=doc_title,
            section_header=section_header,
            parent_text=parent_text,
            raw_text=raw_text,
            slot_index=slot_index,
            chunk_type=chunk_type,
            strict_filter=strict_filter,
        )
        if block is None:
            continue
        parts.append(block)
        if len(parts) >= top_k:
            break
    return "\n\n".join(parts)


def _format_ranked_context_parts_legacy(
    scored: list[tuple[float, str, str, int, ChunkType]],
    *,
    top_k: int,
    strict_filter: bool,
) -> str:
    enriched = [
        (sim, fname, chunk, idx, ct, "", "", "")
        for sim, fname, chunk, idx, ct in scored
    ]
    return _format_ranked_context_parts(enriched, top_k=top_k, strict_filter=strict_filter)


def doc_key_version(lineage_id: str, content_version_id: int) -> str:
    return f"impact_ver::{lineage_id}::{content_version_id}"


def _anchor_to_lineage_id(session: Any, anchor_id: int) -> str | None:
    row = session.get(Content, int(anchor_id))
    return row.lineage_id if row is not None else None


def lineage_ids_for_document_ids(session: Any, document_ids: list[int]) -> list[str]:
    lids: list[str] = []
    for did in document_ids:
        lid = _anchor_to_lineage_id(session, int(did))
        if lid:
            lids.append(lid)
    return lids


def lineage_ids_with_index(conn: Any, lineage_ids: list[str]) -> list[str]:
    if not lineage_ids:
        return []
    indexed: list[str] = []
    for lid in lineage_ids:
        if store_db.rag_lineage_index_get(conn, lid) is not None:
            indexed.append(lid)
    return indexed


def active_chunk_count(conn: Any, lineage_ids: list[str]) -> int:
    if not lineage_ids:
        return 0
    placeholders = ",".join("?" * len(lineage_ids))
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM rag_child_chunk c
        INNER JOIN rag_lineage_index li
            ON li.lineage_id = c.lineage_id
           AND li.content_version_id = c.content_version_id
        WHERE c.lineage_id IN ({placeholders})
        """,
        tuple(lineage_ids),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def ingest_latest_versions_for_document_ids(
    session: Any,
    conn: Any,
    document_ids: list[int],
    embed_model_id: str = LOCAL_EMBEDDING_MODEL_ID,
) -> IngestResult:
    """``document_ids`` are lineage anchor ``content.id`` values."""
    from iterthink.services.rag.workspace_indexer import index_document_path

    outcomes: list[IngestFileOutcome] = []
    for anchor_id in document_ids:
        row = session.get(Content, int(anchor_id))
        if row is None:
            outcomes.append(
                IngestFileOutcome(
                    path=None,
                    document_id=int(anchor_id),
                    lineage_id=None,
                    outcome="no_record",
                )
            )
            continue
        attrs = content_repo.content_attrs(row)
        rp = attrs.get("resolved_path")
        if not rp:
            outcomes.append(
                IngestFileOutcome(
                    path=None,
                    document_id=int(anchor_id),
                    lineage_id=row.lineage_id,
                    outcome="no_path",
                )
            )
            continue
        try:
            path = Path(str(rp)).resolve()
        except (TypeError, ValueError, OSError) as exc:
            outcomes.append(
                IngestFileOutcome(
                    path=None,
                    document_id=int(anchor_id),
                    lineage_id=row.lineage_id,
                    outcome="error",
                    error=str(exc),
                )
            )
            continue
        if not path.is_file():
            outcomes.append(
                IngestFileOutcome(
                    path=path,
                    document_id=int(anchor_id),
                    lineage_id=row.lineage_id,
                    outcome="missing",
                )
            )
            continue
        try:
            outcome = await index_document_path(
                session,
                conn,
                path,
                enrichment_mode="skip",
                embed_model_id=embed_model_id,
            )
            session.commit()
            outcomes.append(
                IngestFileOutcome(
                    path=path,
                    document_id=int(anchor_id),
                    lineage_id=row.lineage_id,
                    outcome=outcome,
                )
            )
        except BaseException as exc:  # noqa: BLE001
            session.rollback()
            outcomes.append(
                IngestFileOutcome(
                    path=path,
                    document_id=int(anchor_id),
                    lineage_id=row.lineage_id,
                    outcome="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return IngestResult(files=tuple(outcomes))


def _document_labels(session: Any, doc_ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for did in doc_ids:
        row = session.get(Content, did)
        if row is not None:
            attrs = content_repo.content_attrs(row)
            rp = attrs.get("resolved_path")
            out[did] = Path(str(rp)).name if rp else str(did)
        else:
            out[did] = str(did)
    return out


def document_label_map(session: Any, document_ids: list[int]) -> dict[int, str]:
    uniq = list(dict.fromkeys(int(x) for x in document_ids))
    return _document_labels(session, uniq)


async def embed_paragraph_for_retrieval(
    conn: Any,
    text: str,
    *,
    cache_key: str,
    doc_title: str = "Untitled",
    section_header: str = "",
    project_label: str | None = None,
) -> list[float]:
    from .chunking import build_retrieval_query_text

    query_text = build_retrieval_query_text(
        text,
        doc_title=doc_title,
        section_header=section_header,
        project_label=project_label,
    )
    vecs = await embed_texts_cached(conn, cache_key, [query_text])
    return vecs[0] if vecs else []


def retrieve_context_by_lineage_ids(
    para_floats: list[float],
    conn: Any,
    lineage_ids: list[str],
    labels: dict[str, str],
    top_k: int = 3,
    *,
    chunk_types_include: frozenset[ChunkType] | None = None,
    strict_filter: bool = True,
) -> str:
    if not para_floats or not lineage_ids:
        return ""
    rows = store_db.rag_child_fetch_latest_rows(conn, lineage_ids)
    scored: list[tuple[float, str, str, int, ChunkType, str, str, str, int]] = []
    if rows:
        for row in rows:
            lid = str(row[1])
            slot_index = int(row[3])
            chunk_text = str(row[4])
            vec_rowid = int(row[8])
            parent_id = int(row[9])
            doc_title = str(row[10])
            section_header = str(row[11])
            parent_text = str(row[12])
            ct = classify_chunk_type(rag_chunk_display_body(chunk_text))
            if chunk_types_include is not None and ct not in chunk_types_include:
                continue
            emb_row = conn.execute(
                "SELECT embedding FROM paragraph_vec WHERE rowid = ?",
                (vec_rowid,),
            ).fetchone()
            if emb_row is None:
                continue
            chunk_floats = blob_to_floats(bytes(emb_row[0]))
            if not chunk_floats:
                continue
            sim = cosine_sim(para_floats, chunk_floats)
            scored.append(
                (
                    sim,
                    labels.get(lid, lid[:8]),
                    chunk_text,
                    slot_index,
                    ct,
                    parent_text,
                    doc_title,
                    section_header,
                    parent_id,
                )
            )
    if not scored:
        legacy = store_db.impact_version_chunk_fetch_latest_rows(conn, lineage_ids)
        legacy_scored: list[tuple[float, str, str, int, ChunkType]] = []
        for lid, _ver_id, chunk_idx, vec_rowid, chunk_text, chunk_type_raw in legacy:
            ct = parse_chunk_type(chunk_type_raw)
            if chunk_types_include is not None and ct not in chunk_types_include:
                continue
            row = conn.execute(
                "SELECT embedding FROM paragraph_vec WHERE rowid = ?",
                (vec_rowid,),
            ).fetchone()
            if row is None:
                continue
            chunk_floats = blob_to_floats(bytes(row[0]))
            if not chunk_floats:
                continue
            sim = cosine_sim(para_floats, chunk_floats)
            legacy_scored.append((sim, labels.get(lid, lid[:8]), chunk_text, int(chunk_idx), ct))
        if not legacy_scored:
            return ""
        legacy_scored.sort(key=lambda t: t[0], reverse=True)
        return _format_ranked_context_parts_legacy(
            legacy_scored, top_k=top_k, strict_filter=strict_filter
        )
    scored.sort(key=lambda t: t[0], reverse=True)
    return _format_ranked_context_parts(scored, top_k=top_k, strict_filter=strict_filter)


def retrieve_context_by_document_ids(
    para_floats: list[float],
    conn: Any,
    document_ids: list[int],
    labels: dict[int, str],
    top_k: int = 3,
    *,
    chunk_types_include: frozenset[ChunkType] | None = None,
    strict_filter: bool = True,
) -> str:
    if not para_floats or not document_ids:
        return ""
    lineage_ids: list[str] = []
    label_by_lid: dict[str, str] = {}
    from iterthink.db.session import session_scope

    with session_scope() as session:
        for did in document_ids:
            lid = _anchor_to_lineage_id(session, did)
            if lid is None:
                continue
            lineage_ids.append(lid)
            label_by_lid[lid] = labels.get(int(did), str(did))
    return retrieve_context_by_lineage_ids(
        para_floats,
        conn,
        lineage_ids,
        label_by_lid,
        top_k,
        chunk_types_include=chunk_types_include,
        strict_filter=strict_filter,
    )


def preflight_ingest_message(ingest: IngestResult, conn: Any) -> str | None:
    """Return a user-facing error when selected context files cannot supply RAG excerpts."""
    lids = ingest.indexed_lineage_ids()
    if not lids:
        summary = ingest.failure_summary()
        if summary:
            return f"Could not index context files ({summary}). Sync index in Settings → RAG."
        return "Could not index any selected context files. Sync index in Settings → RAG."

    indexed = lineage_ids_with_index(conn, lids)
    if not indexed:
        return "Selected context files are not in the RAG index. Sync index in Settings → RAG."

    if active_chunk_count(conn, indexed) == 0:
        return "Selected context files have no indexable content (empty or heading-only)."
    return None


async def preflight_retrieval_message(
    conn: Any,
    *,
    context_document_ids: list[int],
    labels: dict[int, str],
    sample_paragraph: str,
    check_id: str,
    cache_key: str,
    doc_title: str = "Untitled",
    section_header: str = "",
    project_label: str | None = None,
    top_k: int = 3,
) -> str | None:
    """Dry-run retrieval on a sample paragraph; return error message if context would be empty."""
    from iterthink.services.rag.chunk_type import NORM_COMPLIANCE_RAG_TYPES

    strict = check_id == "norm_compliance"
    chunk_types = NORM_COMPLIANCE_RAG_TYPES if strict else None
    try:
        vec = await embed_paragraph_for_retrieval(
            conn,
            sample_paragraph,
            cache_key=cache_key,
            doc_title=doc_title,
            section_header=section_header,
            project_label=project_label,
        )
    except BaseException as exc:  # noqa: BLE001
        return f"Embedding failed for context retrieval ({type(exc).__name__}: {exc})."

    if not vec:
        return "Embedding failed for context retrieval. Check the local embedding model."

    ctx = retrieve_context_by_document_ids(
        vec,
        conn,
        context_document_ids,
        labels,
        top_k=top_k,
        chunk_types_include=chunk_types,
        strict_filter=strict,
    )
    if not ctx.strip():
        return (
            "No relevant context retrieved from selected files. "
            "Re-sync the RAG index or choose different context files."
        )
    return None
