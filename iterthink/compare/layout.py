"""Pair saved vs candidate paragraphs for side-by-side Compare rows."""

from __future__ import annotations

from dataclasses import dataclass

from .margin import split_paragraphs
from .paragraph_align import compute_alignment, old_text_per_new_slot


@dataclass(frozen=True)
class ReviewRow:
    """One Review row spec.

    ``kind`` is one of ``"equal" | "replace" | "delete" | "insert"``.
    ``cand_idx`` is the index in the candidate (right) document for ``replace``,
    ``insert``, and ``equal`` rows; ``None`` for ``delete`` rows that have no
    candidate paragraph (so the eval cell can render an empty placeholder).
    """

    kind: str
    old_text: str
    new_text: str
    cand_idx: int | None


def aligned_review_rows(current: str, ai: str) -> list[ReviewRow]:
    """Diff-aware row list for the Review tab: emits gap rows for pure deletions.

    Output ordering interleaves ``delete`` rows next to their nearest candidate
    neighbour so the visual diff reads top-to-bottom: every ``delete`` row appears
    just before the candidate slot that took its place (or at the end if none).
    Other rows (equal/replace/insert) are sorted by ``cand_idx``.
    """
    diffs = compute_alignment(current, ai)
    if not diffs:
        return []
    cand_paras = split_paragraphs(ai)
    n_cand = len(cand_paras)

    deletes: list[tuple[int, ReviewRow]] = []
    by_cand: dict[int, ReviewRow] = {}

    for d in diffs:
        if d.label == "deleted" or d.new_index < 0:
            deletes.append(
                (
                    d.old_index,
                    ReviewRow(
                        kind="delete",
                        old_text=d.old_text,
                        new_text="",
                        cand_idx=None,
                    ),
                )
            )
            continue
        if d.label == "added" or d.old_index < 0:
            kind = "insert"
        elif d.old_text == d.new_text:
            kind = "equal"
        else:
            kind = "replace"
        by_cand[d.new_index] = ReviewRow(
            kind=kind,
            old_text=d.old_text or "",
            new_text=d.new_text or "",
            cand_idx=d.new_index,
        )

    # Map each deleted-old-index to the candidate slot it should appear before.
    # Heuristic: insert before the first candidate row whose old_index >= this
    # deleted old_index; otherwise append at the end.
    cand_to_old: dict[int, int] = {}
    for i in range(n_cand):
        row = by_cand.get(i)
        if row is None:
            continue
        # Find the original old_index for this candidate row from diffs.
        for d in diffs:
            if d.new_index == i and d.old_index >= 0:
                cand_to_old[i] = d.old_index
                break

    inserts_before: dict[int, list[ReviewRow]] = {}
    appended: list[ReviewRow] = []
    for old_idx, drow in sorted(deletes, key=lambda t: t[0]):
        target: int | None = None
        for i in range(n_cand):
            mapped = cand_to_old.get(i)
            if mapped is not None and mapped >= old_idx:
                target = i
                break
            if mapped is None and i >= old_idx:
                target = i
                break
        if target is None:
            appended.append(drow)
        else:
            inserts_before.setdefault(target, []).append(drow)

    out: list[ReviewRow] = []
    for i in range(n_cand):
        for drow in inserts_before.get(i, []):
            out.append(drow)
        row = by_cand.get(i)
        if row is not None:
            out.append(row)
    out.extend(appended)
    return out


def aligned_compare_pairs(baseline: str, candidate: str) -> list[tuple[str, str]]:
    """
    One row per **candidate** paragraph index *i*: *(aligned baseline paragraph, candidate paragraph i)*.

    The left text is chosen via ``old_text_per_new_slot`` (global paragraph alignment), so reordering
    still diffs against the correct baseline paragraph. Decline/accept semantics use index *i* on the
    compose document separately (see ``iterthink.studio.history``: ``_compare_row_stable_texts``).
    """
    cand_paras = split_paragraphs(candidate)
    if not cand_paras:
        return []
    lefts = old_text_per_new_slot(baseline, candidate)
    n = len(cand_paras)
    if len(lefts) != n:
        if len(lefts) < n:
            lefts = [*lefts, *([""] * (n - len(lefts)))]
        else:
            lefts = lefts[:n]
    return [(lefts[i], cand_paras[i]) for i in range(n)]


def pair_paragraphs_for_compare(baseline: str, candidate: str) -> list[tuple[str, str]]:
    """
    One row per **candidate** paragraph index *i*: *(baseline paragraph i, candidate paragraph i)*.

    Baseline text always comes from the Compose document at the same index (empty string if
    the candidate has more paragraphs than the baseline). This keeps the left column stable
    while the user edits the right: inline diff only compares corresponding indices, instead
    of following TF-IDF alignment (which could remap “old” text and make the left side mirror
    the candidate).
    """
    cand_paras = split_paragraphs(candidate)
    if not cand_paras:
        return []
    base_paras = split_paragraphs(baseline)
    return [
        (base_paras[i] if i < len(base_paras) else "", cand_paras[i])
        for i in range(len(cand_paras))
    ]
