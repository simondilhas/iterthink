"""Block document model: parse markdown into editable blocks and serialize back."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from iterthink.compare.margin import split_paragraphs
from iterthink.studio.markdown_tables import split_markdown_with_tables

BlockKind = Literal[
    "paragraph",
    "heading",
    "bullet",
    "ordered",
    "task",
    "blockquote",
    "table",
    "code_fence",
    "horizontal_rule",
    "raw",
]

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.DOTALL)
_TASK = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+\[([ xX])\]\s*(.*)$", re.DOTALL)
_BULLET = re.compile(r"^(\s*)(?:[-*+])\s+(.*)$", re.DOTALL)
_ORDERED = re.compile(r"^(\s*)(\d+)\.\s+(.*)$", re.DOTALL)
_BLOCKQUOTE_LINE = re.compile(r"^>\s?(.*)$")
_CODE_FENCE_OPEN = re.compile(r"^```(\w*)$")
_HR = re.compile(r"^(\*{3,}|-{3,}|_{3,})\s*$")
# Preserves intentionally empty paragraphs through split_paragraphs round-trip.
_EMPTY_PARA_MARKER = "\u200b"


@dataclass
class WysiwygBlock:
    kind: BlockKind
    text: str = ""
    level: int = 0
    checked: bool = False
    order_num: int = 1
    language: str = ""
    rows: list[list[str]] = field(default_factory=list)
    has_header: bool = False
    raw_text: str = ""
    source_span: tuple[int, int] = (0, 0)


def _classify_paragraph(para: str) -> WysiwygBlock:
    stripped = para.strip()
    if not stripped or stripped == _EMPTY_PARA_MARKER:
        return WysiwygBlock(kind="paragraph", text="")

    first_line = para.split("\n", 1)[0].strip()
    if _HR.match(first_line) and "\n" not in stripped:
        return WysiwygBlock(kind="horizontal_rule")

    if first_line.startswith("```"):
        m = _CODE_FENCE_OPEN.match(first_line)
        if m:
            lang = m.group(1) or ""
            rest = para.split("\n", 1)
            body = rest[1] if len(rest) > 1 else ""
            if body.endswith("```"):
                body = body[: -3].rstrip("\n")
            return WysiwygBlock(kind="code_fence", language=lang, text=body)
        return WysiwygBlock(kind="raw", raw_text=para)

    hm = _HEADING.match(stripped)
    if hm and "\n" not in stripped:
        return WysiwygBlock(
            kind="heading",
            level=len(hm.group(1)),
            text=hm.group(2),
        )

    tm = _TASK.match(stripped)
    if tm and "\n" not in stripped:
        return WysiwygBlock(
            kind="task",
            text=tm.group(3),
            checked=tm.group(2).strip().lower() == "x",
        )

    om = _ORDERED.match(stripped)
    if om and "\n" not in stripped:
        return WysiwygBlock(
            kind="ordered",
            order_num=int(om.group(2)),
            text=om.group(3),
        )

    bm = _BULLET.match(stripped)
    if bm and "\n" not in stripped:
        return WysiwygBlock(kind="bullet", text=bm.group(2))

    if stripped.startswith(">"):
        lines = para.split("\n")
        body_lines: list[str] = []
        for line in lines:
            m = _BLOCKQUOTE_LINE.match(line)
            body_lines.append(m.group(1) if m else line.lstrip("> "))
        return WysiwygBlock(kind="blockquote", text="\n".join(body_lines))

    if _looks_unparsed(para):
        return WysiwygBlock(kind="raw", raw_text=para)

    return WysiwygBlock(kind="paragraph", text=para)


def _looks_unparsed(para: str) -> bool:
    """Heuristic: keep complex markdown lossless in raw blocks."""
    s = para.strip()
    if s.startswith("<") and ">" in s:
        return True
    if "[^" in s or "]: " in s:
        return True
    return False


def _parse_markdown_chunk(text: str) -> list[WysiwygBlock]:
    if not text:
        return [WysiwygBlock(kind="paragraph", text="")]
    paras = split_paragraphs(text)
    if not paras:
        return [WysiwygBlock(kind="paragraph", text="")]
    return [_classify_paragraph(p) for p in paras]


def parse_markdown_blocks(src: str) -> list[WysiwygBlock]:
    """Parse full markdown into WYSIWYG blocks (tables extracted first)."""
    src = src or ""
    if not src:
        return [WysiwygBlock(kind="paragraph", text="")]

    md_blocks = split_markdown_with_tables(src)
    out: list[WysiwygBlock] = []
    for mb in md_blocks:
        if mb.kind == "table":
            out.append(
                WysiwygBlock(
                    kind="table",
                    rows=[list(r) for r in mb.rows],
                    has_header=mb.has_header,
                )
            )
        else:
            out.extend(_parse_markdown_chunk(mb.text))
    if not out:
        out = [WysiwygBlock(kind="paragraph", text="")]
    serialize_markdown_blocks(out)
    return out


def _serialize_block(block: WysiwygBlock) -> str:
    if block.kind == "raw":
        return block.raw_text
    if block.kind == "horizontal_rule":
        return "---"
    if block.kind == "heading":
        level = max(1, min(6, block.level or 1))
        return f"{'#' * level} {block.text}"
    if block.kind == "bullet":
        return f"- {block.text}"
    if block.kind == "ordered":
        n = block.order_num if block.order_num > 0 else 1
        return f"{n}. {block.text}"
    if block.kind == "task":
        mark = "x" if block.checked else " "
        return f"- [{mark}] {block.text}"
    if block.kind == "blockquote":
        lines = (block.text or "").split("\n")
        return "\n".join(f"> {ln}" for ln in lines)
    if block.kind == "code_fence":
        lang = block.language or ""
        body = block.text or ""
        return f"```{lang}\n{body}\n```"
    if block.kind == "table":
        return _serialize_table(block.rows, has_header=block.has_header)
    if block.kind == "paragraph" and not (block.text or ""):
        return _EMPTY_PARA_MARKER
    return block.text or ""


def _serialize_table(rows: list[list[str]], *, has_header: bool) -> str:
    if not rows:
        return "| |\n|---|"
    ncols = max(len(r) for r in rows)
    norm = [list(r) + [""] * (ncols - len(r)) for r in rows]

    def _row(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    lines: list[str] = []
    if has_header and norm:
        lines.append(_row(norm[0]))
        lines.append("| " + " | ".join("---" for _ in range(ncols)) + " |")
        for row in norm[1:]:
            lines.append(_row(row))
    else:
        for row in norm:
            lines.append(_row(row))
    return "\n".join(lines)


def serialize_single_block(block: WysiwygBlock) -> str:
    """Markdown for one block (read-mode ``ft.Markdown`` source)."""
    return _serialize_block(block)


def reorder_blocks(
    blocks: list[WysiwygBlock], old_index: int, new_index: int
) -> list[WysiwygBlock]:
    """Return a new list with the item at ``old_index`` moved to ``new_index``."""
    if not blocks:
        return []
    oi = max(0, min(int(old_index), len(blocks) - 1))
    ni = max(0, min(int(new_index), len(blocks) - 1))
    if oi == ni:
        return list(blocks)
    out = list(blocks)
    item = out.pop(oi)
    out.insert(ni, item)
    return out


def insert_block_after(
    blocks: list[WysiwygBlock], index: int, block: WysiwygBlock
) -> list[WysiwygBlock]:
    """Return a new list with ``block`` inserted after ``index``."""
    if not blocks:
        return [block]
    ix = max(0, min(int(index), len(blocks) - 1))
    out = list(blocks)
    out.insert(ix + 1, block)
    return out


def insert_block_at(
    blocks: list[WysiwygBlock], index: int, block: WysiwygBlock
) -> list[WysiwygBlock]:
    """Return a new list with ``block`` inserted at ``index`` (0 = before first)."""
    if not blocks:
        return [block]
    ix = max(0, min(int(index), len(blocks)))
    out = list(blocks)
    out.insert(ix, block)
    return out


def remove_block_at(blocks: list[WysiwygBlock], index: int) -> list[WysiwygBlock]:
    """Return a new list without the block at ``index``; never returns empty."""
    if not blocks:
        return [WysiwygBlock(kind="paragraph", text="")]
    ix = max(0, min(int(index), len(blocks) - 1))
    out = list(blocks)
    del out[ix]
    if not out:
        return [WysiwygBlock(kind="paragraph", text="")]
    return out


def set_block_kind(
    block: WysiwygBlock,
    kind: BlockKind,
    *,
    level: int = 1,
) -> WysiwygBlock:
    """Convert a text-like block to another kind; body text is preserved."""
    if block.kind in ("table", "horizontal_rule"):
        return block
    body = block.raw_text if block.kind == "raw" else (block.text or "")
    if kind == "heading":
        return WysiwygBlock(
            kind="heading",
            level=max(1, min(3, int(level))),
            text=body,
        )
    if kind == "paragraph":
        return WysiwygBlock(kind="paragraph", text=body)
    if kind == "bullet":
        return WysiwygBlock(kind="bullet", text=body)
    if kind == "ordered":
        return WysiwygBlock(kind="ordered", text=body, order_num=block.order_num or 1)
    if kind == "task":
        return WysiwygBlock(kind="task", text=body, checked=block.checked)
    if kind == "blockquote":
        return WysiwygBlock(kind="blockquote", text=body)
    if kind == "code_fence":
        return WysiwygBlock(kind="code_fence", text=body, language=block.language)
    if kind == "raw":
        return WysiwygBlock(kind="raw", raw_text=body)
    return WysiwygBlock(kind=kind, text=body)


def serialize_markdown_blocks(blocks: list[WysiwygBlock]) -> str:
    """Serialize blocks to markdown; recompute source_span on each block."""
    if not blocks:
        return ""
    parts = [_serialize_block(b) for b in blocks]
    merged: list[str] = []
    for i, part in enumerate(parts):
        if i > 0:
            merged.append("\n\n")
        merged.append(part)
    text = "".join(merged)
    _attach_source_spans_to_text(blocks, text)
    return text


def _attach_source_spans_to_text(blocks: list[WysiwygBlock], text: str) -> None:
    """Set source_span per block from serialized ``text`` (must match serialize order)."""
    if not blocks:
        return
    cursor = 0
    for i, block in enumerate(blocks):
        part = _serialize_block(block)
        if i > 0 and cursor < len(text):
            if text[cursor : cursor + 2] == "\n\n":
                cursor += 2
            elif text[cursor] == "\n":
                cursor += 1
        start = cursor
        end = start + len(part)
        block.source_span = (start, end)
        cursor = end


def block_at_global_offset(blocks: list[WysiwygBlock], offset: int) -> tuple[int, int]:
    """Return (block_index, caret_in_block_text) for a global markdown offset."""
    if not blocks:
        return 0, 0
    off = max(0, int(offset))
    for i, block in enumerate(blocks):
        a, b = block.source_span
        if a <= off < b:
            serialized = _serialize_block(block)
            local = min(off - a, len(serialized))
            return i, _caret_in_block_body(block, local, serialized)
        if i < len(blocks) - 1:
            next_a = blocks[i + 1].source_span[0]
            if b <= off < next_a:
                return i, len(block.text or block.raw_text or "")
    last = len(blocks) - 1
    return last, len(blocks[last].text or blocks[last].raw_text or "")


def _caret_in_block_body(block: WysiwygBlock, local: int, serialized: str) -> int:
    """Map offset inside serialized block to editable body caret."""
    if block.kind == "heading":
        prefix = "#" * max(1, min(6, block.level or 1)) + " "
        return max(0, local - len(prefix))
    if block.kind == "bullet":
        return max(0, local - 2)
    if block.kind == "ordered":
        prefix = f"{block.order_num}. "
        return max(0, local - len(prefix))
    if block.kind == "task":
        return max(0, local - 6)
    if block.kind == "blockquote":
        lines = serialized.split("\n")
        acc = 0
        body_off = 0
        for line in lines:
            plen = len(line) + 1
            if acc + plen > local:
                inner = max(0, local - acc - 2)
                return body_off + inner
            acc += plen
            body_off += len(line) + (1 if body_off else 0)
        return len(block.text or "")
    if block.kind == "code_fence":
        header = f"```{block.language or ''}\n"
        footer = "\n```"
        inner_len = len(block.text or "")
        if local <= len(header):
            return 0
        if local >= len(serialized) - len(footer):
            return inner_len
        return max(0, min(inner_len, local - len(header)))
    if block.kind == "raw":
        return max(0, min(len(block.raw_text or ""), local))
    return max(0, min(len(block.text or ""), local))


def global_span_for_block_selection(
    block: WysiwygBlock,
    sel_start: int,
    sel_end: int,
) -> tuple[int, int]:
    """Map body selection in a block to global markdown offsets."""
    serialized = _serialize_block(block)
    a, _ = block.source_span
    body_start = _body_start_in_serialized(block, serialized)
    g0 = a + body_start + sel_start
    g1 = a + body_start + sel_end
    return g0, g1


def _body_start_in_serialized(block: WysiwygBlock, serialized: str) -> int:
    if block.kind == "heading":
        return len("#" * max(1, min(6, block.level or 1))) + 1
    if block.kind == "bullet":
        return 2
    if block.kind == "ordered":
        return len(f"{block.order_num}. ")
    if block.kind == "task":
        return 6
    if block.kind == "blockquote":
        return 2
    if block.kind == "code_fence":
        return len(f"```{block.language or ''}\n")
    if block.kind == "raw":
        return 0
    return 0


def split_block_on_enter(block: WysiwygBlock, caret: int) -> tuple[WysiwygBlock, WysiwygBlock] | None:
    """Split block at caret on Enter; returns (before, after) or None."""
    text = block.text or ""
    c = max(0, min(caret, len(text)))
    before_text = text[:c]
    after_text = text[c:].lstrip("\n")
    if block.kind == "heading":
        left = WysiwygBlock(kind="heading", level=block.level, text=before_text.rstrip())
        right = WysiwygBlock(kind="paragraph", text=after_text)
        return left, right
    if block.kind in ("bullet", "ordered", "task"):
        left = WysiwygBlock(
            kind=block.kind,
            text=before_text,
            checked=block.checked,
            order_num=block.order_num,
        )
        right = WysiwygBlock(
            kind=block.kind,
            text=after_text,
            checked=False,
            order_num=block.order_num + 1 if block.kind == "ordered" else 1,
        )
        return left, right
    if block.kind == "paragraph":
        left = WysiwygBlock(kind="paragraph", text=before_text)
        right = WysiwygBlock(kind="paragraph", text=after_text)
        return left, right
    if block.kind == "blockquote":
        left = WysiwygBlock(kind="blockquote", text=before_text)
        right = WysiwygBlock(kind="blockquote", text=after_text)
        return left, right
    return None


def merge_blocks(prev: WysiwygBlock, nxt: WysiwygBlock) -> WysiwygBlock | None:
    """Merge two adjacent blocks on Backspace at start of ``nxt``."""
    if prev.kind == "paragraph" and nxt.kind == "paragraph":
        return WysiwygBlock(kind="paragraph", text=(prev.text or "") + (nxt.text or ""))
    if prev.kind == nxt.kind and prev.kind in ("bullet", "ordered", "task", "blockquote"):
        sep = "\n" if prev.kind == "blockquote" else " "
        merged_text = (prev.text or "") + sep + (nxt.text or "")
        return WysiwygBlock(
            kind=prev.kind,
            text=merged_text,
            level=prev.level,
            checked=prev.checked,
            order_num=prev.order_num,
        )
    return None
