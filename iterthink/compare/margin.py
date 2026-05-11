"""Paragraph splitting and height estimation for editor–margin vertical sync."""

from __future__ import annotations

import re


def _paragraph_strings_and_spans(text: str) -> tuple[list[str], list[tuple[int, int]]]:
    """
    Markdown-ish paragraph boundaries (same rules as split_paragraphs).
    Returns (paragraphs, spans) where spans[i] is [start, end) into ``text``
    covering the raw lines that produced paragraphs[i].
    """
    if not text:
        return [""], [(0, 0)]

    paragraphs: list[str] = []
    spans: list[tuple[int, int]] = []

    n = len(text)
    block_start = 0

    while block_start <= n:
        m = re.search(r"\n\n+", text[block_start:])
        if m is None:
            block_end = n
            raw_block = text[block_start:block_end]
            _consume_block(raw_block, block_start, paragraphs, spans)
            break
        block_end = block_start + m.start()
        raw_block = text[block_start:block_end]
        _consume_block(raw_block, block_start, paragraphs, spans)
        block_start = block_start + m.end()

    if not paragraphs:
        return [""], [(0, min(1, n))]
    return paragraphs, spans


def _consume_block(
    raw_block: str,
    block_abs_start: int,
    paragraphs: list[str],
    spans: list[tuple[int, int]],
) -> None:
    """Parse one \\n\\n-separated block; mutates paragraphs and spans."""
    if not raw_block:
        return

    current_lines: list[str] = []
    span_start: int | None = None
    span_end: int | None = None

    def flush() -> None:
        nonlocal current_lines, span_start, span_end
        if not current_lines:
            return
        assert span_start is not None and span_end is not None
        paragraphs.append("\n".join(current_lines))
        spans.append((span_start, span_end))
        current_lines = []
        span_start = None
        span_end = None

    pos = 0
    lim = len(raw_block)
    while pos < lim:
        nl = raw_block.find("\n", pos)
        if nl == -1:
            line_raw = raw_block[pos:]
            next_pos = lim
        else:
            line_raw = raw_block[pos:nl]
            next_pos = nl + 1

        line_abs_start = block_abs_start + pos
        line_abs_end = block_abs_start + next_pos
        line_stripped = line_raw.strip()

        if not line_stripped:
            if current_lines:
                current_lines.append("")
            pos = next_pos
            continue

        if re.match(r"^#{1,6}\s+", line_stripped):
            flush()
            paragraphs.append(line_stripped)
            spans.append((line_abs_start, line_abs_end))
            pos = next_pos
            continue

        if re.match(r"^-\s+", line_stripped):
            flush()
            paragraphs.append(line_stripped)
            spans.append((line_abs_start, line_abs_end))
            pos = next_pos
            continue

        if re.match(r"^\d+\.\s+", line_stripped):
            flush()
            paragraphs.append(line_stripped)
            spans.append((line_abs_start, line_abs_end))
            pos = next_pos
            continue

        if span_start is None:
            span_start = line_abs_start
        span_end = line_abs_end
        current_lines.append(line_stripped)
        pos = next_pos

    flush()


def join_paragraphs(parts: list[str]) -> str:
    """Inverse of ``split_paragraphs`` for round-tripping paragraph lists (``\\n\\n`` between slots)."""
    if not parts:
        return ""
    return "\n\n".join(parts)


def replace_paragraph_at_index(text: str, idx: int, new_paragraph: str) -> str:
    """Return text with paragraph at ``idx`` replaced (same rules as ``split_paragraphs``)."""
    parts = split_paragraphs(text)
    if not parts:
        parts = [""]
    if idx < 0 or idx >= len(parts):
        return text
    parts[idx] = new_paragraph
    return join_paragraphs(parts)


def remove_paragraph_at_index(text: str, idx: int) -> str:
    """Return text with paragraph at ``idx`` removed (empty string if no paragraphs left)."""
    parts = split_paragraphs(text)
    if not parts:
        return text
    if idx < 0 or idx >= len(parts):
        return text
    del parts[idx]
    if not parts:
        return ""
    return join_paragraphs(parts)


def insert_paragraph_after_old_index(text: str, insert_after_old: int, new_paragraph: str) -> str:
    """Insert ``new_paragraph`` after compose index ``insert_after_old`` (-1 = before first)."""
    parts = split_paragraphs(text)
    if not parts:
        parts = [""]
    # Single empty slot: inserting at document start becomes one paragraph.
    if len(parts) == 1 and parts[0] == "" and insert_after_old < 0:
        return join_paragraphs([new_paragraph])
    at = insert_after_old + 1
    if at < 0:
        at = 0
    if at > len(parts):
        at = len(parts)
    parts.insert(at, new_paragraph)
    return join_paragraphs(parts)


def split_paragraphs(text: str) -> list[str]:
    """
    Split text into paragraphs aligned with common markdown block boundaries.

    - Blocks separated by ``\\n\\n+``
    - Headers ``# ...``, ``- `` list lines, and ``N. `` list lines start their own paragraph
    - Single newlines inside a paragraph are preserved (joined with ``\\n``)
    - Empty buffer: one slot ``[\"\"]`` (margin / semantics indexing)
    """
    paras, _ = _paragraph_strings_and_spans(text)
    return paras


def paragraph_index_at_offset(text: str, offset: int) -> int:
    """Return 0-based paragraph index containing the given code-unit offset (Flet TextField)."""
    parts, spans = _paragraph_strings_and_spans(text)
    if not parts or not spans:
        return 0
    n = len(text)
    off = max(0, min(int(offset), n))
    if off < spans[0][0]:
        return 0
    for i, (a, b) in enumerate(spans):
        if a <= off < b:
            return i
    if off >= spans[-1][1]:
        return len(parts) - 1
    for i in range(len(spans) - 1):
        bi, bj = spans[i][1], spans[i + 1][0]
        if bi <= off < bj:
            return i
    return len(parts) - 1


def visual_line_count_for_paragraph_prefix(
    text: str,
    paragraph_index: int,
    offset_in_buffer: int,
    content_width: float,
) -> int:
    """
    Estimated display lines from the start of ``paragraph_index`` up to ``offset_in_buffer``.

    Uses the same buffer spans as ``split_paragraphs`` / ``paragraph_index_at_offset`` and
    ``wrapped_line_count`` for soft-wrapped lines (explicit ``\\n`` splits logical lines).
    """
    _, spans = _paragraph_strings_and_spans(text)
    if not spans or paragraph_index < 0 or paragraph_index >= len(spans):
        return 0
    a, b = spans[paragraph_index]
    off = max(0, min(int(offset_in_buffer), len(text)))
    end = max(a, min(off, b))
    prefix = text[a:end]
    if not prefix:
        return 0
    total = 0
    for seg in prefix.split("\n"):
        total += max(1, wrapped_line_count(seg, content_width))
    return total


def wrapped_line_count(paragraph: str, content_width: float, char_px: float = 8.15) -> int:
    """Rough wrapped line count for monospace-like text in the editor."""
    cpl = max(12, int(max(40.0, content_width - 16.0) / char_px))
    total = 0
    for line in paragraph.split("\n"):
        ln = len(line)
        total += max(1, (ln + cpl - 1) // cpl)
    return total


def paragraph_slot_weights(paragraphs: list[str], content_width: float) -> list[float]:
    return [float(max(1, wrapped_line_count(p, content_width))) for p in paragraphs]


def paragraph_compose_slot_weights(paragraphs: list[str], content_width: float) -> list[float]:
    """
    Weights for aligning a per-paragraph margin column with a multiline TextField.

    ``join_paragraphs`` inserts ``\\n\\n`` between slots; the editor shows one blank line
    between blocks. Count that as one extra wrapped line on each slot except the last
    so cumulative slot heights track the first line of each paragraph.
    """
    base = paragraph_slot_weights(paragraphs, content_width)
    n = len(base)
    if n <= 1:
        return base
    out: list[float] = []
    for i, w in enumerate(base):
        extra = 1.0 if i < n - 1 else 0.0
        out.append(float(w) + extra)
    return out


def estimate_total_editor_height(
    paragraphs: list[str],
    content_width: float,
    line_height: float = 22.5,
) -> float:
    """Fallback total height when TextField reports viewport instead of full content."""
    wts = paragraph_compose_slot_weights(paragraphs, content_width)
    return sum(max(28.0, w * line_height) for w in wts)


def distribute_heights(weights: list[float], target_total: float, min_slot: float = 28.0) -> list[float]:
    """Proportionally assign pixel heights to slots so they sum to target_total."""
    if not weights:
        return []
    wsum = sum(max(0.01, w) for w in weights)
    out = [max(min_slot, target_total * w / wsum) for w in weights]
    s = sum(out)
    if s > 1 and abs(s - target_total) > 0.5:
        factor = target_total / s
        out = [max(min_slot, h * factor) for h in out]
    return out
