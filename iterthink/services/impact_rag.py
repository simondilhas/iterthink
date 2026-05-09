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
"""

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
from iterthink.persistence import store_db

# Namespace prefix so these embeddings never collide with per-document paragraph keys.
_DOC_KEY_PREFIX = "impact_rag::"

_CHUNK_MAX_CHARS = 600  # truncation limit when building context strings


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

    scored: list[tuple[float, str, str]] = []  # (similarity, filename, chunk_text)

    for path, chunks in file_chunks.items():
        for chunk_text, vec_rowid in chunks:
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
            scored.append((sim, path.name, chunk_text))

    if not scored:
        return ""

    scored.sort(key=lambda t: t[0], reverse=True)

    parts: list[str] = []
    for _sim, fname, chunk in scored[:top_k]:
        snip = chunk.strip()
        if len(snip) > _CHUNK_MAX_CHARS:
            snip = snip[:_CHUNK_MAX_CHARS - 1] + "…"
        parts.append(f"[{fname}]\n{snip}")

    return "\n\n".join(parts)
