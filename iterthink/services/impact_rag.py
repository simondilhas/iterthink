"""Cross-file RAG for Impact analysis.

Ingests .md project files into the existing paragraph_vec / paragraph_embedding_cache
tables, tracking freshness per file via project_file_manifest.  Only the *latest*
on-disk version of each file is ever used: if the file's mtime changes, its chunks
are re-embedded automatically.

Public API
----------
``ingest_project_file(path, conn)``
    Reads the file, splits on double-newlines, embeds changed/new chunks, updates
    the manifest.  Returns [(chunk_text, vec_rowid), …] for the current version.

``retrieve_context_for_paragraph(para_floats, file_chunks, top_k=3)``
    Ranks all provided chunks by cosine similarity to the query embedding and
    returns a formatted multi-paragraph context string ready to append to an LLM
    prompt.

``ingest_latest_versions_for_document_ids(session, conn, document_ids)``
    Embeds persisted snapshot bodies for each document's **latest** ``DocumentVersion``
    and stores rows in ``impact_version_chunk`` (sqlite). Retrieval uses
    ``MAX(ver_id)`` per ``doc_id`` so stale versions are never used.

``retrieve_context_by_document_ids(para_floats, conn, session, document_ids)``
    Cosine-ranked context from latest-version chunks only.

``rag_chunk_display_body(chunk)``
    Strips optional ``<!-- iterthink-rag-context-start/end -->`` wrapper so prepended
    retrieval text stays in the embedded string but not in norm LLM context.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID
from iterthink.compare.paragraph_semantics import (
    blob_to_floats,
    cosine_sim,
    embed_texts_cached,
    text_hash,
)
from iterthink.persistence import store_db

# Namespace prefix so these embeddings never collide with per-document paragraph keys.
_DOC_KEY_PREFIX = "impact_rag::"

_CHUNK_MAX_CHARS = 600  # truncation limit when building context strings

# Optional: retrieval prefix in HTML comments is embedded with the chunk (header/path
# terms shift the vector) but stripped for norm junk checks and LLM snippets.
_RAG_CTX_START = re.compile(r"<!--\s*iterthink-rag-context-start\s*-->", re.IGNORECASE)
_RAG_CTX_END = re.compile(r"<!--\s*iterthink-rag-context-end\s*-->", re.IGNORECASE)

# Trivial heading-only chunks (### 0, ### –) and TOC dot leaders pollute norm RAG context.
_TRIVIAL_HEADING_CHUNK = re.compile(r"^#{1,6}\s*[\d\-–—.]+\s*$", re.MULTILINE)


def rag_chunk_display_body(chunk: str) -> str:
    """Substantive body for prompts and junk heuristics.

    Text between ``<!-- iterthink-rag-context-start -->`` and
    ``<!-- iterthink-rag-context-end -->`` is kept in the stored chunk for embedding
    quality; this function returns everything after that block. Chunks without
    markers are returned unchanged (stripped).
    """
    s = chunk.strip()
    start_m = _RAG_CTX_START.search(s)
    if not start_m:
        return s
    tail = s[start_m.end() :]
    end_m = _RAG_CTX_END.search(tail)
    if not end_m:
        return s
    body = tail[end_m.end() :].strip()
    return body if body else s


def _chunk_usable_for_norm_context(chunk_full: str) -> bool:
    """Drop low-information chunks for norm RAG; heuristics use display body only."""
    s = rag_chunk_display_body(chunk_full).strip()
    if len(s) < 20:
        return False
    if _TRIVIAL_HEADING_CHUNK.fullmatch(s):
        return False
    if len(s) > 60 and s.count(".") / len(s) > 0.22:
        return False
    if re.search(r"(?:\.\s*){6,}\d", s):
        return False
    return True


def _format_ranked_context_parts(
    scored: list[tuple[float, str, str, int]],
    *,
    top_k: int,
) -> str:
    """Take up to *top_k* highest-similarity chunks that pass quality filter."""
    parts: list[str] = []
    for _sim, fname, chunk, chunk_index in scored:
        raw = chunk.strip()
        if not _chunk_usable_for_norm_context(raw):
            continue
        snip = rag_chunk_display_body(raw).strip()
        if len(snip) > _CHUNK_MAX_CHARS:
            snip = snip[: _CHUNK_MAX_CHARS - 1] + "…"
        para_num = chunk_index + 1
        parts.append(f"[{fname}] chunk_index={chunk_index} paragraph={para_num}\n{snip}")
        if len(parts) >= top_k:
            break
    return "\n\n".join(parts)


def _doc_key(path: Path) -> str:
    return _DOC_KEY_PREFIX + str(path)


async def ingest_project_file(
    path: Path,
    conn: Any,
    embed_model_id: str = LOCAL_EMBEDDING_MODEL_ID,
) -> list[tuple[str, int]]:
    """Embed all paragraphs in *path*, respecting the manifest cache.

    Returns a list of ``(chunk_text, vec_rowid)`` pairs that reflect the
    **current** file content.  Chunks whose content hash is unchanged since
    the last ingest are looked up from ``paragraph_embedding_cache`` without
    re-embedding.  If the file's mtime differs from the manifest the full
    chunk list is re-evaluated.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        current_mtime = path.stat().st_mtime
    except OSError:
        return []

    chunks = [c for c in raw.split("\n\n") if c.strip()]
    if not chunks:
        return []

    current_hashes = [text_hash(c) for c in chunks]
    doc_key = _doc_key(path)

    manifest = store_db.manifest_get(conn, str(path), embed_model_id)

    # Fast-path: same mtime + same hashes → retrieve cached rowids without embedding.
    if manifest is not None and float(manifest["file_mtime"]) == current_mtime:
        old_hashes: list[str] = json.loads(manifest["chunk_hashes"])
        if old_hashes == current_hashes:
            result: list[tuple[str, int]] = []
            for chunk, h in zip(chunks, current_hashes):
                row = conn.execute(
                    """SELECT vec_rowid FROM paragraph_embedding_cache
                       WHERE doc_path = ? AND input_hash = ? AND embed_model_id = ?""",
                    (doc_key, h, embed_model_id),
                ).fetchone()
                if row is not None:
                    result.append((chunk, int(row[0])))
            if len(result) == len(chunks):
                return result
            # Fall through if cache is somehow incomplete.

    # Embed (embed_texts_cached handles per-hash dedup and storage).
    await embed_texts_cached(conn, doc_key, chunks)

    # Update manifest with new mtime + hashes.
    store_db.manifest_put(conn, str(path), embed_model_id, current_mtime, current_hashes)

    # Collect rowids for all current chunks.
    result = []
    for chunk, h in zip(chunks, current_hashes):
        row = conn.execute(
            """SELECT vec_rowid FROM paragraph_embedding_cache
               WHERE doc_path = ? AND input_hash = ? AND embed_model_id = ?""",
            (doc_key, h, embed_model_id),
        ).fetchone()
        if row is not None:
            result.append((chunk, int(row[0])))

    return result


def retrieve_context_for_paragraph(
    para_floats: list[float],
    file_chunks: dict[Path, list[tuple[str, int]]],
    conn: Any,
    top_k: int = 3,
) -> str:
    """Return formatted context from the *top_k* chunks most similar to *para_floats*.

    *file_chunks* maps each selected file ``Path`` to its ``(chunk_text, vec_rowid)``
    list as returned by ``ingest_project_file``.  Embeddings are fetched from
    ``paragraph_vec`` by rowid and ranked by cosine similarity.
    """
    if not para_floats or not file_chunks:
        return ""

    scored: list[tuple[float, str, str, int]] = []  # (similarity, filename, chunk_text, chunk_index)

    for path, chunks in file_chunks.items():
        for chunk_index, (chunk_text, vec_rowid) in enumerate(chunks):
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
            scored.append((sim, path.name, chunk_text, chunk_index))

    if not scored:
        return ""

    scored.sort(key=lambda t: t[0], reverse=True)

    return _format_ranked_context_parts(scored, top_k=top_k)


# --- Version-scoped Impact RAG (DB snapshots + sqlite-vec) -----------------


def doc_key_version(doc_id: int, ver_id: int) -> str:
    return f"impact_ver::{doc_id}::{ver_id}"


async def ingest_latest_versions_for_document_ids(
    session: Any,
    conn: Any,
    document_ids: list[int],
    embed_model_id: str = LOCAL_EMBEDDING_MODEL_ID,
) -> None:
    """Ensure ``impact_version_chunk`` reflects latest snapshot per document id."""
    from iterthink.db.models import DocumentVersion
    from iterthink.persistence import version_storage as vs

    for doc_id in document_ids:
        latest_vid = vs.latest_version_id_for_document(session, doc_id)
        if latest_vid is None:
            continue
        ver = session.get(DocumentVersion, latest_vid)
        if ver is None:
            continue
        body = vs.load_version_body(session, latest_vid)
        chunks = [c for c in body.split("\n\n") if c.strip()]
        sha = ver.content_sha256
        n = len(chunks)
        if n == 0:
            store_db.impact_version_chunk_delete_for_version(conn, doc_id, latest_vid)
            conn.commit()
            continue
        if store_db.impact_version_embeddings_complete(conn, doc_id, latest_vid, sha, n):
            continue
        store_db.impact_version_chunk_delete_for_version(conn, doc_id, latest_vid)
        dk = doc_key_version(doc_id, latest_vid)
        await embed_texts_cached(conn, dk, chunks)
        hashes = [text_hash(c) for c in chunks]
        for i, (chunk, h) in enumerate(zip(chunks, hashes)):
            row = conn.execute(
                """SELECT vec_rowid FROM paragraph_embedding_cache
                   WHERE doc_path = ? AND input_hash = ? AND embed_model_id = ?""",
                (dk, h, embed_model_id),
            ).fetchone()
            if row is None:
                continue
            rid = int(row[0])
            store_db.impact_version_chunk_insert_row(
                conn,
                doc_id=doc_id,
                ver_id=latest_vid,
                chunk_index=i,
                input_hash=h,
                vec_rowid=rid,
                embed_model_id=embed_model_id,
                chunk_text=chunk,
                content_sha=sha,
            )
        conn.commit()


def _document_labels(session: Any, doc_ids: list[int]) -> dict[int, str]:
    from iterthink.db.models import Document

    out: dict[int, str] = {}
    for did in doc_ids:
        d = session.get(Document, did)
        if d is not None:
            out[did] = Path(d.resolved_path).name
        else:
            out[did] = str(did)
    return out


def document_label_map(session: Any, document_ids: list[int]) -> dict[int, str]:
    """Basenames for UI / context headers (call from main thread before parallel Impact work)."""
    uniq = list(dict.fromkeys(int(x) for x in document_ids))
    return _document_labels(session, uniq)


def retrieve_context_by_document_ids(
    para_floats: list[float],
    conn: Any,
    document_ids: list[int],
    labels: dict[int, str],
    top_k: int = 3,
) -> str:
    """Rank latest-version chunks from ``document_ids`` by cosine similarity to *para_floats*."""
    if not para_floats or not document_ids:
        return ""

    rows = store_db.impact_version_chunk_fetch_latest_rows(conn, document_ids)
    if not rows:
        return ""
    scored: list[tuple[float, str, str, int]] = []

    for doc_id, _ver_id, chunk_idx, vec_rowid, chunk_text in rows:
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
        scored.append((sim, labels.get(int(doc_id), str(doc_id)), chunk_text, int(chunk_idx)))

    if not scored:
        return ""

    scored.sort(key=lambda t: t[0], reverse=True)

    return _format_ranked_context_parts(scored, top_k=top_k)
