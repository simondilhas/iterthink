"""Pair saved vs candidate paragraphs for side-by-side Compare rows."""

from __future__ import annotations

from iterthink.margin import split_paragraphs


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
