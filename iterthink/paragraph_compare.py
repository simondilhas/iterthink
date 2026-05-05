"""Per-slot comparison labels: alignment + embedding + LLM for major vs rewritten."""

from __future__ import annotations

import math
from typing import Any, Literal

from iterthink.diff_card import judge_rewritten_vs_major
from iterthink.margin import split_paragraphs
from iterthink.paragraph_align import DiffParagraph, compute_alignment, compute_hash
from iterthink.paragraph_semantics import embed_texts

SlotKind = Literal["unchanged", "minor", "major", "rewritten", "new", "deleted"]

# Cosine between paragraph embeddings (old vs new); outside bands use LLM.
_COSINE_MAJOR_SURFACE = 0.82
_COSINE_REWRITTEN = 0.62


def _cosine(a: list[float], b: list[float]) -> float:
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


def _diff_by_new_index(diffs: list[DiffParagraph], n_new: int) -> dict[int, DiffParagraph]:
    m: dict[int, DiffParagraph] = {}
    for d in diffs:
        if 0 <= d.new_index < n_new:
            m[d.new_index] = d
    return m


def _base_kind_from_diff(d: DiffParagraph) -> SlotKind:
    if d.status == "new":
        return "new"
    if d.status == "deleted":
        return "deleted"
    o, p = d.old_text or "", d.new_text or ""
    if compute_hash(o) == compute_hash(p):
        return "unchanged"
    if d.status == "stable":
        return "unchanged"
    if d.status == "minor":
        return "minor"
    if d.status == "major":
        return "major"
    return "minor"


async def classify_slots_async(
    ollama: Any,
    *,
    chat_model: str,
    embed_model: str,
    baseline_text: str,
    new_text: str,
) -> list[SlotKind]:
    """
    Return one ``SlotKind`` per **new** paragraph slot (same length as ``split_paragraphs(new_text)``).
    """
    new_paras = split_paragraphs(new_text)
    n = len(new_paras)
    if n == 0:
        return []

    diffs = compute_alignment(baseline_text, new_text)
    by_new = _diff_by_new_index(diffs, n)

    out: list[SlotKind] = []
    major_work: list[tuple[int, str, str]] = []

    for i in range(n):
        d = by_new.get(i)
        if d is None:
            out.append("unchanged")
            continue
        kind = _base_kind_from_diff(d)
        if kind == "major":
            o, p = d.old_text or "", d.new_text or ""
            if o.strip() and p.strip():
                major_work.append((i, o, p))
                out.append("major")
            else:
                out.append("major")
        else:
            out.append(kind)

    if not major_work:
        return out

    olds = [o for _, o, _ in major_work]
    news = [p for _, _, p in major_work]
    try:
        emb_old = await embed_texts(ollama, embed_model, olds)
        emb_new = await embed_texts(ollama, embed_model, news)
    except BaseException:
        for _j, (slot_i, o, p) in enumerate(major_work):
            verdict = await judge_rewritten_vs_major(ollama, chat_model, o, p)
            out[slot_i] = "rewritten" if verdict == "rewritten" else "major"
        return out

    for j_idx, (slot_i, o, p) in enumerate(major_work):
        vo = emb_old[j_idx] if j_idx < len(emb_old) else []
        vn = emb_new[j_idx] if j_idx < len(emb_new) else []
        if not vo or not vn:
            verdict = await judge_rewritten_vs_major(ollama, chat_model, o, p)
            out[slot_i] = "rewritten" if verdict == "rewritten" else "major"
            continue
        c = _cosine([float(x) for x in vo], [float(x) for x in vn])
        if c <= _COSINE_REWRITTEN:
            out[slot_i] = "rewritten"
        elif c >= _COSINE_MAJOR_SURFACE:
            out[slot_i] = "major"
        else:
            verdict = await judge_rewritten_vs_major(ollama, chat_model, o, p)
            out[slot_i] = "rewritten" if verdict == "rewritten" else "major"

    return out


def slot_kind_label(kind: SlotKind) -> str:
    return {
        "unchanged": "—",
        "minor": "minor",
        "major": "major",
        "rewritten": "rewritten",
        "new": "new",
        "deleted": "deleted",
    }[kind]


def slot_kinds_heuristic(baseline: str, candidate: str) -> list[SlotKind]:
    """Fast slot labels from alignment only (before embedding / LLM refinement)."""
    new_paras = split_paragraphs(candidate)
    n = len(new_paras)
    if n == 0:
        return []
    diffs = compute_alignment(baseline, candidate)
    by_new = _diff_by_new_index(diffs, n)
    out: list[SlotKind] = []
    for i in range(n):
        d = by_new.get(i)
        if d is None:
            out.append("unchanged")
            continue
        out.append(_base_kind_from_diff(d))
    return out
