"""Cross-file RAG for Impact analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID
from iterthink.compare.paragraph_semantics import (
    blob_to_floats,
    cosine_sim,
    embed_texts_cached,
    text_hash,
)
from iterthink.db.content_models import Content
from iterthink.persistence import content_repo, store_db

from .chunk_type import ChunkType, classify_chunk_type, parse_chunk_type
from .context_format import format_rag_context_block, rag_chunk_display_body

_DOC_KEY_PREFIX = "impact_rag::"


def _format_ranked_context_parts(
    scored: list[
        tuple[float, str, str, int, ChunkType, str, str, str]
    ],
    *,
    top_k: int,
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
) -> str:
    enriched = [
        (sim, fname, chunk, idx, ct, "", "", "")
        for sim, fname, chunk, idx, ct in scored
    ]
    return _format_ranked_context_parts(enriched, top_k=top_k)


def _doc_key(path: Path) -> str:
    return _DOC_KEY_PREFIX + str(path)


async def ingest_project_file(
    path: Path,
    conn: Any,
    embed_model_id: str = LOCAL_EMBEDDING_MODEL_ID,
) -> list[tuple[str, int]]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        current_mtime = path.stat().st_mtime
    except OSError:
        return []

    chunks = [c for c in raw.split("\n\n") if c.strip()]
    if not chunks:
        return []

    current_hashes = [text_hash(c) for c in chunks]
    cache_key = _doc_key(path)
    manifest = store_db.manifest_get(conn, str(path), embed_model_id)

    if manifest is not None and float(manifest["file_mtime"]) == current_mtime:
        old_hashes: list[str] = json.loads(manifest["chunk_hashes"])
        if old_hashes == current_hashes:
            result: list[tuple[str, int]] = []
            for chunk, h in zip(chunks, current_hashes):
                row = conn.execute(
                    """SELECT vec_rowid FROM paragraph_embedding_cache
                       WHERE lineage_id = ? AND input_hash = ? AND embed_model_id = ?""",
                    (cache_key, h, embed_model_id),
                ).fetchone()
                if row is not None:
                    result.append((chunk, int(row[0])))
            if len(result) == len(chunks):
                return result

    await embed_texts_cached(conn, cache_key, chunks)
    store_db.manifest_put(conn, str(path), embed_model_id, current_mtime, current_hashes)

    result = []
    for chunk, h in zip(chunks, current_hashes):
        row = conn.execute(
            """SELECT vec_rowid FROM paragraph_embedding_cache
               WHERE lineage_id = ? AND input_hash = ? AND embed_model_id = ?""",
            (cache_key, h, embed_model_id),
        ).fetchone()
        if row is not None:
            result.append((chunk, int(row[0])))
    return result


def retrieve_context_for_paragraph(
    para_floats: list[float],
    file_chunks: dict[Path, list[tuple[str, int]]],
    conn: Any,
    top_k: int = 3,
    *,
    chunk_types_include: frozenset[ChunkType] | None = None,
) -> str:
    if not para_floats or not file_chunks:
        return ""
    scored: list[tuple[float, str, str, int, ChunkType]] = []
    for path, chunks in file_chunks.items():
        for chunk_index, (chunk_text, vec_rowid) in enumerate(chunks):
            ct = classify_chunk_type(rag_chunk_display_body(chunk_text))
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
            scored.append((sim, path.name, chunk_text, chunk_index, ct))
    if not scored:
        return ""
    scored.sort(key=lambda t: t[0], reverse=True)
    return _format_ranked_context_parts_legacy(scored, top_k=top_k)


def doc_key_version(lineage_id: str, content_version_id: int) -> str:
    return f"impact_ver::{lineage_id}::{content_version_id}"


def _anchor_to_lineage_id(session: Any, anchor_id: int) -> str | None:
    row = session.get(Content, int(anchor_id))
    return row.lineage_id if row is not None else None


async def ingest_latest_versions_for_document_ids(
    session: Any,
    conn: Any,
    document_ids: list[int],
    embed_model_id: str = LOCAL_EMBEDDING_MODEL_ID,
) -> None:
    """``document_ids`` are lineage anchor ``content.id`` values."""
    from iterthink.services.rag.workspace_indexer import index_document_path

    for anchor_id in document_ids:
        row = session.get(Content, int(anchor_id))
        if row is None:
            continue
        attrs = content_repo.content_attrs(row)
        rp = attrs.get("resolved_path")
        if not rp:
            continue
        try:
            path = Path(str(rp)).resolve()
        except (TypeError, ValueError, OSError):
            continue
        if not path.is_file():
            continue
        try:
            await index_document_path(
                session,
                conn,
                path,
                enrichment_mode="skip",
                embed_model_id=embed_model_id,
            )
            session.commit()
        except BaseException:
            session.rollback()
            continue


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


def retrieve_context_by_lineage_ids(
    para_floats: list[float],
    conn: Any,
    lineage_ids: list[str],
    labels: dict[str, str],
    top_k: int = 3,
    *,
    chunk_types_include: frozenset[ChunkType] | None = None,
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
        return _format_ranked_context_parts_legacy(legacy_scored, top_k=top_k)
    if not scored:
        return ""
    scored.sort(key=lambda t: t[0], reverse=True)
    return _format_ranked_context_parts(scored, top_k=top_k)


def retrieve_context_by_document_ids(
    para_floats: list[float],
    conn: Any,
    document_ids: list[int],
    labels: dict[int, str],
    top_k: int = 3,
    *,
    chunk_types_include: frozenset[ChunkType] | None = None,
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
    )
