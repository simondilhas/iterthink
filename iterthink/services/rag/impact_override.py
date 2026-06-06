"""Human Impact review overrides: embed paragraph+verdict for future context."""

from __future__ import annotations

from typing import Any

from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID
from iterthink.compare.paragraph_semantics import blob_to_floats, cosine_sim, embed_texts_cached, text_hash
from iterthink.persistence import store_db

from .context_format import format_override_context_block


def override_cache_key(content_version_id: int, prompt_id: str) -> str:
    return f"impact_override::{int(content_version_id)}::{prompt_id}"


def build_override_embed_text(
    *,
    paragraph_text: str,
    paragraph_index: int,
    prompt_id: str,
    status: str,
    override_comment: str,
    doc_title: str = "Untitled",
) -> str:
    parts = [
        f"Title: {doc_title.strip() or 'Untitled'}",
        f"Check: {prompt_id}",
        f"Paragraph: {paragraph_index + 1}",
        f"Human status: {status}",
        f"Human recommendation: {override_comment.strip()}",
        "---",
        paragraph_text.strip(),
    ]
    return "\n".join(parts)


async def upsert_override_embedding(
    conn: Any,
    *,
    content_version_id: int,
    paragraph_index: int,
    prompt_id: str,
    paragraph_text: str,
    status: str,
    override_comment: str,
    doc_title: str = "Untitled",
) -> None:
    embed_text = build_override_embed_text(
        paragraph_text=paragraph_text,
        paragraph_index=paragraph_index,
        prompt_id=prompt_id,
        status=status,
        override_comment=override_comment,
        doc_title=doc_title,
    )
    cache_key = override_cache_key(content_version_id, prompt_id)
    await embed_texts_cached(conn, cache_key, [embed_text])
    h = text_hash(embed_text)
    vec_rowid = store_db.embedding_cache_vec_rowid(
        conn, cache_key, h, LOCAL_EMBEDDING_MODEL_ID
    )
    if vec_rowid is None:
        return
    store_db.impact_override_context_upsert(
        conn,
        content_version_id=content_version_id,
        paragraph_index=paragraph_index,
        prompt_id=prompt_id,
        paragraph_text_hash=text_hash(paragraph_text),
        status=status,
        override_comment=override_comment,
        embed_text=embed_text,
        vec_rowid=vec_rowid,
        embed_model_id=LOCAL_EMBEDDING_MODEL_ID,
    )


def delete_override_embedding(
    conn: Any,
    *,
    content_version_id: int,
    paragraph_index: int,
    prompt_id: str,
) -> None:
    store_db.impact_override_context_delete(
        conn,
        content_version_id=content_version_id,
        paragraph_index=paragraph_index,
        prompt_id=prompt_id,
    )


async def ensure_override_embedding(
    conn: Any,
    *,
    content_version_id: int,
    paragraph_index: int,
    prompt_id: str,
    paragraph_text: str,
    status: str,
    override_comment: str,
    doc_title: str = "Untitled",
) -> None:
    """Re-embed when paragraph text changed but human verdict is kept."""
    current_hash = text_hash(paragraph_text)
    existing = conn.execute(
        """
        SELECT paragraph_text_hash FROM impact_override_context
        WHERE content_version_id = ? AND paragraph_index = ? AND prompt_id = ?
        """,
        (int(content_version_id), int(paragraph_index), str(prompt_id)),
    ).fetchone()
    if existing is not None and str(existing[0]) == current_hash:
        return
    await upsert_override_embedding(
        conn,
        content_version_id=content_version_id,
        paragraph_index=paragraph_index,
        prompt_id=prompt_id,
        paragraph_text=paragraph_text,
        status=status,
        override_comment=override_comment,
        doc_title=doc_title,
    )


def retrieve_override_context(
    para_floats: list[float],
    conn: Any,
    *,
    content_version_id: int,
    prompt_id: str,
    exclude_paragraph_index: int,
    top_k: int = 2,
) -> str:
    if not para_floats:
        return ""
    rows = store_db.impact_override_context_fetch_for_version(
        conn, content_version_id=content_version_id, prompt_id=prompt_id
    )
    scored: list[tuple[float, int, str, str, str]] = []
    for pi, status, comment, embed_text, vec_rowid in rows:
        if pi == exclude_paragraph_index:
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
        scored.append((sim, pi, status, comment, embed_text))
    if not scored:
        return ""
    scored.sort(key=lambda t: t[0], reverse=True)
    blocks: list[str] = []
    for _sim, pi, status, comment, embed_text in scored[: max(1, top_k)]:
        block = format_override_context_block(
            paragraph_index=pi,
            status=status,
            override_comment=comment,
            embed_text=embed_text,
        )
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)
