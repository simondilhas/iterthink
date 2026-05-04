"""Paragraph-level alignment (TF-IDF + fuzzy guardrail) vs saved text; uses margin.split_paragraphs."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from html import escape
from typing import Optional

from iterthink.margin import split_paragraphs

try:
    import numpy as np
    from rapidfuzz import fuzz
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    HAS_TFIDF = True
except ImportError:
    np = None  # type: ignore[assignment]
    HAS_TFIDF = False


@dataclass
class Match:
    old_idx: int
    new_idx: int
    similarity: float
    match_type: str = "similarity"
    quality_score: float = 0.0


@dataclass
class DiffParagraph:
    old_text: str
    new_text: str
    status: str
    label: str
    old_index: int
    new_index: int
    sim_score: float | None = None
    para_id: str | None = None
    old_inline_html: str | None = None
    new_inline_html: str | None = None
    severity: str | None = None
    carried_from_para_id: str | None = None
    old_raw_markdown: str | None = None
    new_raw_markdown: str | None = None


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "he",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "that",
    "the",
    "to",
    "was",
    "will",
    "with",
}


def tokenize(text: str) -> list[str]:
    text = text.lower()
    words = re.findall(r"\b\w+\b", text)
    return [w for w in words if w not in STOPWORDS]


def jaccard_similarity(set1: set[str], set2: set[str]) -> float:
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def compute_tfidf_similarity(
    old_paras: list[str], new_paras: list[str]
) -> tuple[Optional["np.ndarray"], Optional["np.ndarray"]]:
    if not HAS_TFIDF or np is None:
        return None, None
    if not old_paras or not new_paras:
        z = np.zeros((len(old_paras), len(new_paras)))
        return z, z

    old_word = [preprocess_text(p)[0] for p in old_paras]
    new_word = [preprocess_text(p)[0] for p in new_paras]
    old_char = [preprocess_text(p)[1] for p in old_paras]
    new_char = [preprocess_text(p)[1] for p in new_paras]

    word_vectorizer = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        min_df=1,
        lowercase=False,
    )
    try:
        word_old_matrix = word_vectorizer.fit_transform(old_word)
        word_new_matrix = word_vectorizer.transform(new_word)
        word_sim = cosine_similarity(word_old_matrix, word_new_matrix)
    except ValueError:
        word_sim = np.zeros((len(old_paras), len(new_paras)))

    char_vectorizer = TfidfVectorizer(
        analyzer="char",
        ngram_range=(3, 5),
        min_df=1,
        lowercase=False,
    )
    try:
        char_old_matrix = char_vectorizer.fit_transform(old_char)
        char_new_matrix = char_vectorizer.transform(new_char)
        char_sim = cosine_similarity(char_old_matrix, char_new_matrix)
    except ValueError:
        char_sim = np.zeros((len(old_paras), len(new_paras)))

    return word_sim, char_sim


def compute_token_ratio_matrix(
    old_paras: list[str],
    new_paras: list[str],
    tfidf_prefilter: Optional["np.ndarray"] = None,
    tfidf_threshold: float = 0.3,
) -> Optional["np.ndarray"]:
    if not HAS_TFIDF or np is None:
        return None
    ratios = np.zeros((len(old_paras), len(new_paras)))
    if tfidf_prefilter is not None:
        for i, old_para in enumerate(old_paras):
            for j, new_para in enumerate(new_paras):
                if i < tfidf_prefilter.shape[0] and j < tfidf_prefilter.shape[1]:
                    if float(tfidf_prefilter[i, j]) > tfidf_threshold:
                        ratios[i, j] = fuzz.token_sort_ratio(old_para, new_para) / 100.0
    else:
        for i, old_para in enumerate(old_paras):
            for j, new_para in enumerate(new_paras):
                ratios[i, j] = fuzz.token_sort_ratio(old_para, new_para) / 100.0
    return ratios


def compute_combined_similarity(
    word_sim: Optional["np.ndarray"],
    char_sim: Optional["np.ndarray"],
    token_ratio: Optional["np.ndarray"],
    weight_word: float = 0.6,
    weight_char: float = 0.4,
    min_token_ratio: float = 0.50,
) -> "np.ndarray":
    if np is None:
        raise RuntimeError("numpy is required for compute_combined_similarity")
    if word_sim is None or char_sim is None:
        return np.zeros((1, 1))

    combined = weight_word * word_sim + weight_char * char_sim
    if token_ratio is not None:
        mask = token_ratio < min_token_ratio
        combined[mask] = 0.0
    return combined


def tfidf_similarity_pair(old_text: str, new_text: str) -> float:
    if not HAS_TFIDF:
        return jaccard_similarity(set(tokenize(old_text)), set(tokenize(new_text)))

    word_sim, char_sim = compute_tfidf_similarity([old_text], [new_text])
    if word_sim is None or char_sim is None or np is None:
        return jaccard_similarity(set(tokenize(old_text)), set(tokenize(new_text)))

    ratio = fuzz.token_sort_ratio(old_text, new_text) / 100.0
    token_ratio = np.array([[ratio]])
    combined = compute_combined_similarity(word_sim, char_sim, token_ratio)
    return float(combined[0, 0]) if combined.size > 0 else 0.0


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def normalize_whitespace(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text)
    return normalized.strip()


def preprocess_text(text: str) -> tuple[str, str]:
    text = normalize_unicode(text)
    view_word = re.sub(r"[^\w\s]", " ", text.lower())
    view_word = normalize_whitespace(view_word)
    view_char = text.lower()
    return view_word, view_char


def para_id_for(text: str) -> str:
    normalized = normalize_whitespace(text.lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def word_diff_html(old: str, new: str) -> tuple[str, str]:
    if not old or not new:
        return old, new
    old_words = old.split()
    new_words = new.split()
    sm = SequenceMatcher(a=old_words, b=new_words)
    old_html: list[str] = []
    new_html: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            tok_old = " ".join(old_words[i1:i2])
            tok_new = " ".join(new_words[j1:j2])
            old_html.append(escape(tok_old))
            new_html.append(escape(tok_new))
        elif op == "delete":
            old_html.append(f"<del>{escape(' '.join(old_words[i1:i2]))}</del>")
        elif op == "insert":
            new_html.append(f"<ins>{escape(' '.join(new_words[j1:j2]))}</ins>")
        elif op == "replace":
            old_html.append(f"<del>{escape(' '.join(old_words[i1:i2]))}</del>")
            new_html.append(f"<ins>{escape(' '.join(new_words[j1:j2]))}</ins>")
    return " ".join(old_html), " ".join(new_html)


def _handle_empty_case(old_paras: list[str], new_paras: list[str]) -> list[DiffParagraph]:
    result: list[DiffParagraph] = []
    if not old_paras and new_paras:
        for idx, para in enumerate(new_paras):
            result.append(
                DiffParagraph(
                    old_text="",
                    new_text=para,
                    status="new",
                    label="added",
                    old_index=-1,
                    new_index=idx,
                    sim_score=0.0,
                )
            )
    elif old_paras and not new_paras:
        for idx, para in enumerate(old_paras):
            result.append(
                DiffParagraph(
                    old_text=para,
                    new_text="",
                    status="deleted",
                    label="deleted",
                    old_index=idx,
                    new_index=-1,
                    sim_score=0.0,
                )
            )
    return result


def _detect_splits_merges(
    result: list[DiffParagraph],
    old_paras: list[str],
    _new_paras: list[str],
    selected_old: set[int],
    _selected_new: set[int],
    final_matches: list[Match],
) -> list[DiffParagraph]:
    new_to_old: dict[int, list[Match]] = {}
    for match in final_matches:
        new_to_old.setdefault(match.new_idx, []).append(match)

    for new_idx, matches in new_to_old.items():
        if len(matches) > 1:
            old_indices = sorted(m.old_idx for m in matches)
            for item in result:
                if item.new_index == new_idx:
                    item.label = "merged"
                    item.old_text = "\n\n".join(old_paras[idx] for idx in old_indices)
                    break

    for old_idx in range(len(old_paras)):
        if old_idx in selected_old:
            matching_new_indices = [m.new_idx for m in final_matches if m.old_idx == old_idx]
            if matching_new_indices:
                new_idx = matching_new_indices[0]
                if abs(old_idx - new_idx) > 2:
                    for item in result:
                        if item.old_index == old_idx and item.new_index == new_idx:
                            if item.label == "unchanged":
                                item.label = "split"
                            break

    return result


def compute_alignment(old_text: str, new_text: str, threshold: float = 0.55) -> list[DiffParagraph]:
    old_paras = split_paragraphs(old_text)
    new_paras = split_paragraphs(new_text)

    if not old_paras or not new_paras:
        return _handle_empty_case(old_paras, new_paras)

    old_hashes = {i: compute_hash(p) for i, p in enumerate(old_paras)}
    new_hashes = {i: compute_hash(p) for i, p in enumerate(new_paras)}

    matched_old: set[int] = set()
    matched_new: set[int] = set()
    matches: list[Match] = []

    old_hash_to_indices: dict[str, list[int]] = {}
    for old_idx, hash_val in old_hashes.items():
        old_hash_to_indices.setdefault(hash_val, []).append(old_idx)

    new_hash_to_indices: dict[str, list[int]] = {}
    for new_idx, hash_val in new_hashes.items():
        new_hash_to_indices.setdefault(hash_val, []).append(new_idx)

    for hash_val, old_indices in old_hash_to_indices.items():
        if hash_val in new_hash_to_indices:
            new_indices = new_hash_to_indices[hash_val]
            for old_idx in old_indices:
                if old_idx not in matched_old:
                    best_new_idx: int | None = None
                    min_distance = float("inf")
                    for new_idx in new_indices:
                        if new_idx not in matched_new:
                            distance = abs(old_idx - new_idx)
                            if distance < min_distance:
                                min_distance = distance
                                best_new_idx = new_idx
                    if best_new_idx is not None:
                        matches.append(Match(old_idx=old_idx, new_idx=best_new_idx, similarity=1.0))
                        matched_old.add(old_idx)
                        matched_new.add(best_new_idx)

    unmatched_old = [i for i in range(len(old_paras)) if i not in matched_old]
    unmatched_new = [i for i in range(len(new_paras)) if i not in matched_new]

    if unmatched_old or unmatched_new:
        unmatched_old_paras = [old_paras[i] for i in unmatched_old]
        unmatched_new_paras = [new_paras[i] for i in unmatched_new]

        if HAS_TFIDF and unmatched_old_paras and unmatched_new_paras:
            word_sim_matrix, char_sim_matrix = compute_tfidf_similarity(unmatched_old_paras, unmatched_new_paras)
            preliminary_tfidf = 0.6 * word_sim_matrix + 0.4 * char_sim_matrix
            token_ratio_matrix = compute_token_ratio_matrix(
                unmatched_old_paras,
                unmatched_new_paras,
                tfidf_prefilter=preliminary_tfidf,
                tfidf_threshold=0.3,
            )
            combined_sim_matrix = compute_combined_similarity(word_sim_matrix, char_sim_matrix, token_ratio_matrix)
        else:
            combined_sim_matrix = None

        for i, old_idx in enumerate(unmatched_old):
            for j, new_idx in enumerate(unmatched_new):
                if combined_sim_matrix is not None:
                    sim = float(combined_sim_matrix[i, j])
                else:
                    sim = tfidf_similarity_pair(old_paras[old_idx], new_paras[new_idx])
                if sim >= threshold:
                    matches.append(Match(old_idx=old_idx, new_idx=new_idx, similarity=sim))

    matches.sort(key=lambda m: m.similarity, reverse=True)
    selected_old: set[int] = set()
    selected_new: set[int] = set()
    final_matches: list[Match] = []

    for match in matches:
        if match.old_idx not in selected_old and match.new_idx not in selected_new:
            selected_old.add(match.old_idx)
            selected_new.add(match.new_idx)
            final_matches.append(match)

    result: list[DiffParagraph] = []

    for match in final_matches:
        old_para = old_paras[match.old_idx]
        new_para = new_paras[match.new_idx]
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
        if match.similarity >= 0.98 and not has_word_changes:
            status = "stable"
        elif match.similarity >= 0.75 or has_word_changes:
            status = "minor"
        elif match.similarity >= 0.55:
            status = "medium"
        else:
            status = "major"
        label = "unchanged" if match.old_idx == match.new_idx else "moved"
        result.append(
            DiffParagraph(
                old_text=old_para,
                new_text=new_para,
                status=status,
                label=label,
                old_index=match.old_idx,
                new_index=match.new_idx,
                sim_score=match.similarity,
                para_id=para_id,
                old_inline_html=old_inline_html,
                new_inline_html=new_inline_html,
                severity=None,
            )
        )

    result = _detect_splits_merges(result, old_paras, new_paras, selected_old, selected_new, final_matches)

    for old_idx in range(len(old_paras)):
        if old_idx not in selected_old:
            result.append(
                DiffParagraph(
                    old_text=old_paras[old_idx],
                    new_text="",
                    status="deleted",
                    label="deleted",
                    old_index=old_idx,
                    new_index=-1,
                    sim_score=0.0,
                    para_id=para_id_for(old_paras[old_idx]),
                    severity=None,
                )
            )

    for new_idx in range(len(new_paras)):
        if new_idx not in selected_new:
            result.append(
                DiffParagraph(
                    old_text="",
                    new_text=new_paras[new_idx],
                    status="new",
                    label="added",
                    old_index=-1,
                    new_index=new_idx,
                    sim_score=0.0,
                    para_id=para_id_for(new_paras[new_idx]),
                    severity=None,
                )
            )

    def sort_key(p: DiffParagraph) -> tuple[int, int]:
        if p.old_index >= 0 and p.new_index < 0:
            return (p.old_index, 0)
        if p.old_index >= 0:
            return (p.new_index, p.old_index)
        return (p.new_index, len(old_paras))

    return sorted(result, key=sort_key)


def old_text_per_new_slot(old_text: str, new_text: str, *, threshold: float = 0.55) -> list[str]:
    """For each new-document paragraph slot, aligned old paragraph text (or \"\" if added)."""
    cur = split_paragraphs(new_text)
    n = len(cur)
    if n == 0:
        return []
    diffs = compute_alignment(old_text, new_text, threshold=threshold)
    out = [""] * n
    for d in diffs:
        if 0 <= d.new_index < n:
            out[d.new_index] = d.old_text or ""
    return out


def serialize_diffs(diffs: list[DiffParagraph]) -> str:
    return json.dumps([asdict(d) for d in diffs], ensure_ascii=False)


def deserialize_diffs(json_str: str) -> list[DiffParagraph]:
    data = json.loads(json_str)
    return [DiffParagraph(**item) for item in data]
