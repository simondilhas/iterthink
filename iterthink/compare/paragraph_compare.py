"""Per-slot comparison labels: alignment + embedding + LLM for major vs rewritten."""

from __future__ import annotations

import math
import sqlite3
from typing import Any, Literal

from .diff_card import judge_rewritten_vs_major
from .margin import split_paragraphs
from .paragraph_align import (
    DiffParagraph,
    compute_alignment,
    compute_hash,
    para_id_for,
    word_diff_html,
)
from .paragraph_semantics import embed_texts_cached

SlotKind = Literal["unchanged", "minor", "major", "rewritten", "new", "deleted"]

# Cosine between paragraph embeddings (old vs new); outside bands use LLM.
_COSINE_MAJOR_SURFACE = 0.82
_COSINE_REWRITTEN = 0.62

# Neighbor context for embedding when identical paragraphs must be disambiguated.
_CTX_TAIL = 200
_CTX_HEAD = 200


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


def _neighbor_context(paras: list[str], idx: int) -> str:
    prev = paras[idx - 1][-_CTX_TAIL:] if idx > 0 else ""
    mid = paras[idx]
    nxt = paras[idx + 1][:_CTX_HEAD] if idx + 1 < len(paras) else ""
    return f"{prev}\n<<<\n{mid}\n>>>\n{nxt}"


def _diff_paragraph_from_match(
    old_para: str, new_para: str, old_idx: int, new_idx: int, similarity: float
) -> DiffParagraph:
    para_id = para_id_for(old_para)
    old_inline_html: str | None = None
    new_inline_html: str | None = None
    has_word_changes = False
    if old_para and new_para:
        old_inline_html, new_inline_html = word_diff_html(old_para, new_para)
        has_word_changes = (
            "<del>" in old_inline_html
            or "<ins>" in old_inline_html
            or "<del>" in new_inline_html
            or "<ins>" in new_inline_html
        )
    if similarity >= 0.98 and not has_word_changes:
        status = "stable"
    elif similarity >= 0.75 or has_word_changes:
        status = "minor"
    elif similarity >= 0.55:
        status = "major"
    else:
        status = "major"
    label = "unchanged" if old_idx == new_idx else "moved"
    return DiffParagraph(
        old_text=old_para,
        new_text=new_para,
        status=status,
        label=label,
        old_index=old_idx,
        new_index=new_idx,
        sim_score=similarity,
        para_id=para_id,
        old_inline_html=old_inline_html,
        new_inline_html=new_inline_html,
        severity=None,
    )


def _greedy_max_bipartite(sim: list[list[float]]) -> list[tuple[int, int]]:
    n = len(sim)
    if n == 0 or not sim[0]:
        return []
    m = len(sim[0])
    if n != m:
        return []
    used_r: set[int] = set()
    used_c: set[int] = set()
    order: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(n):
            order.append((sim[i][j], i, j))
    order.sort(key=lambda t: t[0], reverse=True)
    pairs: list[tuple[int, int]] = []
    for _s, i, j in order:
        if i in used_r or j in used_c:
            continue
        used_r.add(i)
        used_c.add(j)
        pairs.append((i, j))
        if len(pairs) == n:
            break
    return pairs if len(pairs) == n else []


async def refine_alignment_diffs_duplicate_hash_async(
    conn: sqlite3.Connection,
    doc_path: str | None,
    old_paras: list[str],
    new_paras: list[str],
    diffs: list[DiffParagraph],
) -> list[DiffParagraph]:
    """
    Re-pair duplicate identical paragraphs (same hash) using neighbor-context embeddings.

    When ``compute_alignment`` ties hash matches by index distance, context embeddings can pick
    a better permutation. If embedding fails or matching is incomplete, returns ``diffs`` unchanged.
    """
    matched = [d for d in diffs if d.old_index >= 0 and d.new_index >= 0]
    if len(matched) < 2:
        return diffs

    by_hash: dict[str, list[DiffParagraph]] = {}
    for d in matched:
        h = compute_hash(d.new_text or "")
        by_hash.setdefault(h, []).append(d)

    overrides: dict[int, tuple[int, str]] = {}

    for _h, group in by_hash.items():
        if len(group) < 2:
            continue
        o_ix = sorted({d.old_index for d in group})
        n_ix = sorted({d.new_index for d in group})
        if len(o_ix) < 2 or len(n_ix) < 2 or len(o_ix) != len(n_ix):
            continue
        k = len(o_ix)
        texts_o = [_neighbor_context(old_paras, i) for i in o_ix]
        texts_n = [_neighbor_context(new_paras, j) for j in n_ix]
        try:
            emb_o = await embed_texts_cached(conn, doc_path, texts_o)
            emb_n = await embed_texts_cached(conn, doc_path, texts_n)
        except BaseException:
            continue
        sim: list[list[float]] = []
        for i in range(k):
            row: list[float] = []
            vo = emb_o[i] if i < len(emb_o) else []
            for j in range(k):
                vn = emb_n[j] if j < len(emb_n) else []
                if not vo or not vn:
                    row.append(0.0)
                else:
                    row.append(_cosine([float(x) for x in vo], [float(x) for x in vn]))
            sim.append(row)
        pairs = _greedy_max_bipartite(sim)
        if len(pairs) != k:
            continue
        for local_i, local_j in pairs:
            gi_o = o_ix[local_i]
            gi_n = n_ix[local_j]
            overrides[gi_n] = (gi_o, old_paras[gi_o])

    if not overrides:
        return diffs

    rebuilt: list[DiffParagraph] = []
    for d in diffs:
        if d.old_index >= 0 and d.new_index >= 0 and d.new_index in overrides:
            oi, ot = overrides[d.new_index]
            nt = new_paras[d.new_index]
            if oi == d.old_index and ot == (d.old_text or ""):
                rebuilt.append(d)
            else:
                rebuilt.append(_diff_paragraph_from_match(ot, nt, oi, d.new_index, 1.0))
        else:
            rebuilt.append(d)

    def sort_key(p: DiffParagraph) -> tuple[int, int]:
        if p.old_index >= 0 and p.new_index < 0:
            return (p.old_index, 0)
        if p.old_index >= 0:
            return (p.new_index, p.old_index)
        return (p.new_index, len(old_paras))

    return sorted(rebuilt, key=sort_key)


def _base_kind_from_diff(d: DiffParagraph) -> SlotKind:
    if d.status == "new":
        return "new"
    if d.status == "deleted":
        return "deleted"
    if d.label in ("merged", "split"):
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


def _aligned_left_texts_from_diffs(diffs: list[DiffParagraph], n: int) -> list[str]:
    by_new = _diff_by_new_index(diffs, n)
    out: list[str] = []
    for i in range(n):
        d = by_new.get(i)
        if d is None or d.old_index < 0:
            out.append("")
        else:
            out.append(d.old_text or "")
    return out


def slot_index_displacements_from_by_new(
    by_new: dict[int, DiffParagraph], n_new: int
) -> list[int | None]:
    """
    Per candidate slot ``i``: baseline index minus candidate index when aligned (``old_index - new_index``).
    Positive ⇒ baseline paragraph originated lower in the file (show ↑). Negative ⇒ ↓. ``None`` if
    not applicable (e.g. new slot with no baseline match).
    """
    out: list[int | None] = []
    for i in range(n_new):
        d = by_new.get(i)
        if d is None or d.old_index < 0 or d.new_index < 0:
            out.append(None)
            continue
        delta = d.old_index - d.new_index
        out.append(None if delta == 0 else delta)
    return out


async def classify_slots_async(
    conn: sqlite3.Connection,
    llm_chat: Any,
    *,
    chat_model: str,
    doc_path: str | None,
    baseline_text: str,
    new_text: str,
) -> tuple[list[SlotKind], list[str], list[int | None]]:
    """
    Return one ``SlotKind`` per **new** paragraph slot, aligned baseline left text per slot, and
    index displacements (``old_index - new_index``) for the small arrow column in Compare.
    """
    new_paras = split_paragraphs(new_text)
    n = len(new_paras)
    if n == 0:
        return [], [], []

    old_paras = split_paragraphs(baseline_text)
    diffs = compute_alignment(baseline_text, new_text)
    try:
        diffs = await refine_alignment_diffs_duplicate_hash_async(
            conn, doc_path, old_paras, new_paras, diffs
        )
    except BaseException:
        pass

    by_new = _diff_by_new_index(diffs, n)
    aligned_lefts = _aligned_left_texts_from_diffs(diffs, n)
    displacements = slot_index_displacements_from_by_new(by_new, n)

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
        return out, aligned_lefts, displacements

    olds = [o for _, o, _ in major_work]
    news = [p for _, _, p in major_work]
    try:
        emb_old = await embed_texts_cached(conn, doc_path, olds)
        emb_new = await embed_texts_cached(conn, doc_path, news)
    except BaseException:
        for _j, (slot_i, o, p) in enumerate(major_work):
            verdict = await judge_rewritten_vs_major(llm_chat, chat_model, o, p)
            out[slot_i] = "rewritten" if verdict == "rewritten" else "major"
        return out, aligned_lefts, displacements

    for j_idx, (slot_i, o, p) in enumerate(major_work):
        vo = emb_old[j_idx] if j_idx < len(emb_old) else []
        vn = emb_new[j_idx] if j_idx < len(emb_new) else []
        if not vo or not vn:
            verdict = await judge_rewritten_vs_major(llm_chat, chat_model, o, p)
            out[slot_i] = "rewritten" if verdict == "rewritten" else "major"
            continue
        c = _cosine([float(x) for x in vo], [float(x) for x in vn])
        if c <= _COSINE_REWRITTEN:
            out[slot_i] = "rewritten"
        elif c >= _COSINE_MAJOR_SURFACE:
            out[slot_i] = "major"
        else:
            verdict = await judge_rewritten_vs_major(llm_chat, chat_model, o, p)
            out[slot_i] = "rewritten" if verdict == "rewritten" else "major"

    return out, aligned_lefts, displacements


def slot_kind_label(kind: SlotKind) -> str:
    return {
        "unchanged": "—",
        "minor": "minor",
        "major": "major",
        "rewritten": "rewritten",
        "new": "new",
        "deleted": "deleted",
    }[kind]


def compare_slots_heuristic(baseline: str, candidate: str) -> tuple[list[SlotKind], list[int | None]]:
    """Slot kinds and index displacements from alignment only (before embedding / LLM refinement)."""
    new_paras = split_paragraphs(candidate)
    n = len(new_paras)
    if n == 0:
        return [], []
    diffs = compute_alignment(baseline, candidate)
    by_new = _diff_by_new_index(diffs, n)
    out: list[SlotKind] = []
    for i in range(n):
        d = by_new.get(i)
        if d is None:
            out.append("unchanged")
            continue
        out.append(_base_kind_from_diff(d))
    disps = slot_index_displacements_from_by_new(by_new, n)
    return out, disps


def slot_kinds_heuristic(baseline: str, candidate: str) -> list[SlotKind]:
    """Fast slot labels only; prefer ``compare_slots_heuristic`` when displacements are needed."""
    kinds, _ = compare_slots_heuristic(baseline, candidate)
    return kinds
