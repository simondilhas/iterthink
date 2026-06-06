"""Parent/child RAG chunking aligned with ``split_paragraphs``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from iterthink.compare.margin import split_paragraphs
from iterthink.studio.content_tree import parse_markdown_headings

_ATX_HEADING = re.compile(r"^#{1,6}\s+")


@dataclass(frozen=True)
class ChildChunk:
    slot_index: int
    raw_text: str
    section_header: str
    overlap_text: str
    summary: str = ""
    questions: tuple[str, str, str] = ("", "", "")

    def build_embed_text(self, *, doc_title: str, project_label: str | None = None) -> str:
        q1, q2, q3 = self.questions
        parts: list[str] = []
        if project_label and project_label.strip():
            parts.append(f"Project: {project_label.strip()}")
        parts.extend([
            f"Title: {doc_title}",
            f"Header: {self.section_header}",
        ])
        if self.summary.strip():
            parts.append(f"Summary: {self.summary.strip()}")
        qs = " | ".join(q for q in (q1, q2, q3) if q.strip())
        if qs:
            parts.append(f"Questions: {qs}")
        if self.overlap_text.strip():
            parts.append(f"Overlap: {self.overlap_text.strip()}")
        parts.append("---")
        parts.append(self.raw_text.strip())
        return "\n".join(parts)


def build_retrieval_query_text(
    raw_text: str,
    *,
    doc_title: str = "Untitled",
    section_header: str = "",
    project_label: str | None = None,
) -> str:
    """Embed text aligned with ``ChildChunk.build_embed_text`` (without enrichment fields)."""
    parts: list[str] = []
    if project_label and project_label.strip():
        parts.append(f"Project: {project_label.strip()}")
    parts.extend([
        f"Title: {doc_title}",
        f"Header: {section_header}",
        "---",
        raw_text.strip(),
    ])
    return "\n".join(parts)


@dataclass
class ParentChunk:
    parent_index: int
    section_header: str
    children: list[ChildChunk] = field(default_factory=list)

    @property
    def parent_text(self) -> str:
        return "\n\n".join(c.raw_text for c in self.children if c.raw_text.strip())


def document_title(body: str, filename: str) -> str:
    for h in parse_markdown_headings(body):
        if h.level == 1:
            return h.title
    stem = filename
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    return stem or "Untitled"


def _heading_title(line: str) -> str:
    m = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+\s*)?$", line.strip())
    return m.group(1).strip() if m else line.strip()


def _overlap_tail(text: str, overlap_chars: int) -> str:
    s = text.strip()
    if not s or overlap_chars <= 0:
        return ""
    if len(s) <= overlap_chars:
        return s
    return s[-overlap_chars:]


def build_parent_child_chunks(
    body: str,
    *,
    doc_title: str,
    overlap_chars: int = 200,
) -> list[ParentChunk]:
    """Split *body* into section parents and paragraph children."""
    paragraphs = split_paragraphs(body)
    if not paragraphs:
        return []

    parents: list[ParentChunk] = []
    current_header = doc_title
    current_parent: ParentChunk | None = None
    parent_index = 0
    prev_raw = ""

    for slot_index, para in enumerate(paragraphs):
        raw = (para or "").strip()
        if not raw:
            continue

        if _ATX_HEADING.match(raw):
            current_header = _heading_title(raw)
            current_parent = ParentChunk(parent_index=parent_index, section_header=current_header)
            current_parent.children.append(
                ChildChunk(
                    slot_index=slot_index,
                    raw_text=raw,
                    section_header=current_header,
                    overlap_text=_overlap_tail(prev_raw, overlap_chars),
                )
            )
            parents.append(current_parent)
            parent_index += 1
            prev_raw = raw
            continue

        if current_parent is None:
            current_parent = ParentChunk(parent_index=parent_index, section_header=current_header)
            parents.append(current_parent)
            parent_index += 1

        current_parent.children.append(
            ChildChunk(
                slot_index=slot_index,
                raw_text=raw,
                section_header=current_header,
                overlap_text=_overlap_tail(prev_raw, overlap_chars),
            )
        )
        prev_raw = raw

    return parents
