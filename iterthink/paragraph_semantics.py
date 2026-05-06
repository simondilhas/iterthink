"""Paragraph change detection: content hash, Ollama embeddings, cosine thresholds, LLM tie-break."""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Any, Sequence

from iterthink.diff_card import SemanticKind, judge_semantic
from iterthink import store_db

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


async def embed_texts(client: Any, model: str, inputs: list[str]) -> list[list[float]]:
    """Batch embed via Ollama; returns one vector per input (same order)."""
    if not inputs:
        return []
    resp = await client.embed(model=model, input=inputs)
    embs = getattr(resp, "embeddings", None) or []
    out: list[list[float]] = []
    for row in embs:
        out.append([float(x) for x in row])
    return out


async def classify_paragraph_slots_batch(
    conn: Any,
    embed_client: Any,
    llm_chat: Any,
    *,
    chat_model: str,
    embed_model: str,
    doc_path: str,
    items: list[tuple[int, str, str]],
) -> list[tuple[int, SemanticKind]]:
    """
    For each (slot_index, old_text, new_text), batch-embed new paragraphs, classify,
    persist one observation per slot when embedding succeeds.
    """
    if not items:
        return []

    work = [(i, o, p) for i, o, p in items if text_hash(o) != text_hash(p)]
    if not work:
        return []

    results: list[tuple[int, SemanticKind]] = []
    new_texts = [p for _, _, p in work]

    try:
        new_vecs = await embed_texts(embed_client, embed_model, new_texts)
    except BaseException:
        for i, o, p in work:
            results.append((i, await judge_semantic(llm_chat, chat_model, o, p)))
        return results

    for j, (i, o, p) in enumerate(work):
        ho = text_hash(o)
        hp = text_hash(p)
        if j >= len(new_vecs) or not new_vecs[j]:
            results.append((i, await judge_semantic(llm_chat, chat_model, o, p)))
            continue

        new_emb = new_vecs[j]
        latest = store_db.latest_observation(conn, doc_path, i, embed_model)

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
            doc_path=doc_path,
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
