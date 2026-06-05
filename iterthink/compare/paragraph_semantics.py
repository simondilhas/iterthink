"""Paragraph change detection: content hash, local ONNX embeddings, cosine thresholds, LLM tie-break."""

from __future__ import annotations

import asyncio
import hashlib
import math
import struct
from typing import Any, Sequence

from .diff_card import SemanticKind, judge_semantic
from iterthink.persistence import store_db
from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID, embed_batch_sync

# Cosine similarity between successive embeddings at the same slot; outside band → LLM.
COSINE_STABLE = 0.92
COSINE_NEW = 0.78


def text_hash(text: str) -> str:
    """SHA-256 of UTF-8 bytes (exact text; no normalization)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def floats_to_blob(values: Sequence[float]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def blob_to_floats(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine_sim(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


async def embed_texts_cached(
    conn: Any,
    doc_path: str | None,
    inputs: list[str],
) -> list[list[float]]:
    """
    Return one embedding per input (order preserved). Uses sqlite-vec cache keyed by
    (doc_path, content hash, model id); misses run FastEmbed in a worker thread.
    """
    if not inputs:
        return []
    doc_key = doc_path if doc_path is not None else ""
    model_id = LOCAL_EMBEDDING_MODEL_ID
    out: list[list[float] | None] = [None] * len(inputs)
    pending: list[tuple[int, str, str]] = []
    for i, raw in enumerate(inputs):
        h = text_hash(raw)
        blob = store_db.embedding_cache_get(conn, doc_key, h, model_id)
        if blob is not None:
            out[i] = blob_to_floats(blob)
        else:
            pending.append((i, raw, h))
    if not pending:
        return [x for x in out if x is not None]

    by_hash: dict[str, tuple[str, list[int]]] = {}
    for i, raw, h in pending:
        if h not in by_hash:
            by_hash[h] = (raw, [])
        by_hash[h][1].append(i)

    hash_order = sorted(by_hash)
    texts_to_encode = [by_hash[h][0] for h in hash_order]
    try:
        vecs = await asyncio.to_thread(embed_batch_sync, texts_to_encode)
    except BaseException:
        for i, _, _ in pending:
            out[i] = []
        return [x if x is not None else [] for x in out]

    for idx, h in enumerate(hash_order):
        _, indices = by_hash[h]
        row = vecs[idx] if idx < len(vecs) else None
        if row is None or len(row) == 0:
            for i in indices:
                out[i] = []
            continue
        arr = row.astype("float32", copy=False).reshape(-1)
        vec_list = arr.tolist()
        try:
            store_db.embedding_cache_put(conn, doc_key, h, model_id, arr)
        except BaseException:
            for i in indices:
                out[i] = vec_list
            continue
        for i in indices:
            out[i] = vec_list
    return [x if x is not None else [] for x in out]


async def classify_paragraph_slots_batch(
    conn: Any,
    llm_chat: Any,
    *,
    chat_model: str,
    lineage_id: str,
    items: list[tuple[int, str, str]],
    doc_path: str | None = None,
) -> list[tuple[int, SemanticKind]]:
    """
    For each (slot_index, old_text, new_text), batch-embed new paragraphs, classify,
    persist one RAG observation per slot when embedding succeeds.

    ``lineage_id`` keys ``paragraph_observation``; ``doc_path`` (optional) keys the
    embedding cache (defaults to ``lineage_id``).
    """
    cache_key = doc_path if doc_path is not None else lineage_id
    if not items:
        return []

    work = [(i, o, p) for i, o, p in items if text_hash(o) != text_hash(p)]
    if not work:
        return []

    results: list[tuple[int, SemanticKind]] = []
    new_texts = [p for _, _, p in work]

    try:
        new_vecs = await embed_texts_cached(conn, cache_key, new_texts)
    except BaseException:
        for i, o, p in work:
            results.append((i, await judge_semantic(llm_chat, chat_model, o, p)))
        return results

    embed_model = LOCAL_EMBEDDING_MODEL_ID
    for j, (i, o, p) in enumerate(work):
        ho = text_hash(o)
        hp = text_hash(p)
        if j >= len(new_vecs) or not new_vecs[j]:
            results.append((i, await judge_semantic(llm_chat, chat_model, o, p)))
            continue

        new_emb = new_vecs[j]
        latest = store_db.latest_observation(conn, lineage_id, i, embed_model)

        if latest is None:
            kind = await judge_semantic(llm_chat, chat_model, o, p)
            prev_text_hash: str | None = ho
            cosine_to_prev: float | None = None
        else:
            prev_emb = blob_to_floats(latest["embedding"])
            cosine_to_prev = cosine_sim(new_emb, prev_emb)
            prev_text_hash = str(latest["text_hash"])
            if cosine_to_prev >= COSINE_STABLE:
                kind = "STABLE"
            elif cosine_to_prev <= COSINE_NEW:
                kind = "NEW"
            else:
                kind = await judge_semantic(llm_chat, chat_model, o, p)

        new_blob = floats_to_blob(new_emb)
        store_db.paragraph_text_upsert(conn, ho, o)
        store_db.paragraph_text_upsert(conn, hp, p)
        store_db.insert_observation(
            conn,
            lineage_id=lineage_id,
            slot_index=i,
            text_hash=hp,
            embed_model=embed_model,
            embedding=new_blob,
            prev_text_hash=prev_text_hash,
            cosine_to_prev=cosine_to_prev,
            status=kind,
        )
        results.append((i, kind))

    return results
