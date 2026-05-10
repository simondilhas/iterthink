"""Heuristic chunk classification for Impact RAG filtering."""

from __future__ import annotations

import re
from enum import Enum


class ChunkType(str, Enum):
    NORM = "norm"
    LAW = "law"
    REQUIREMENT = "requirement"
    DECISION = "decision"
    TASK = "task"
    DEFINITION = "definition"
    NARRATIVE = "narrative"
    UNKNOWN = "unknown"


# Cross-doc context for norm compliance: skip low-signal prose and checklists.
NORM_COMPLIANCE_RAG_TYPES: frozenset[ChunkType] = frozenset(
    {
        ChunkType.NORM,
        ChunkType.LAW,
        ChunkType.REQUIREMENT,
        ChunkType.DEFINITION,
        ChunkType.DECISION,
        ChunkType.UNKNOWN,
    }
)


def parse_chunk_type(raw: str | None) -> ChunkType:
    if not raw:
        return ChunkType.UNKNOWN
    try:
        return ChunkType(str(raw).strip().lower())
    except ValueError:
        return ChunkType.UNKNOWN


def classify_chunk_type(text: str) -> ChunkType:
    """Lightweight rules; unknown when nothing matches."""
    t = text.strip()
    if not t:
        return ChunkType.UNKNOWN
    low = t.lower()

    if re.search(r"(?m)^\s*-\s*\[\s*[ xX]?\s*\]", t) or re.search(
        r"(?m)\bTODO:\s|\bFIXME\b", low, re.I
    ):
        return ChunkType.TASK

    if re.search(r"(?m)^>{0,1}\s*\*\*[^*\n]{1,120}\*\*\s*[–—\-:]", t) or re.search(
        r"\b(?:definition|definitions?)(?:\s+of|\s*:|\s+—|\s+–|\s+-)\b",
        low,
    ):
        return ChunkType.DEFINITION

    if re.search(
        r"\b(?:BGB|ZGB|SR\s+[0-9]|EU[- ]?Richtlinie|EU[- ]?Verordnung|"
        r"OR\s+Art\.|Art\.\s*\d+.{0,24}\bAbs\.|DSG\b|VwVfG|StGB|USG)\b",
        t,
        re.I,
    ):
        return ChunkType.LAW

    if re.search(
        r"\b(?:ISO\s*\d+|IEC\s*\d+|DIN\s+(?:EN\s+)?[\w‑-]+|"
        r"EN\s+[0-9]{2,5}(?:-[0-9]+)?(?:-[A-Z]+)?|"
        r"\bSIA\s+\d|ÖNORM|SN\s+EN|ASTM\s+\w)\b",
        t,
        re.I,
    ):
        return ChunkType.NORM

    if re.search(r"\bshall\b", low) or re.search(
        r"\b(?:must\s+(?:not\s+)?(?:be|have|ensure|comply)|"
        r"müssen\b|\bmuss\s+(?:nicht\s+)?|ist\s+zwingend|nicht\s+zulässig)\b",
        low,
    ):
        return ChunkType.REQUIREMENT

    if re.search(
        r"\b(?:ADR[- ]?\d|architecture\s+decision|entscheid(?:ung)?|beschluss|"
        r"meeting[- ]?protokoll|workshop[- ]?ergebnis)\b",
        t,
        re.I,
    ):
        return ChunkType.DECISION

    if len(t) > 220 and t.count(".") >= 2 and not re.search(r"\b(?:DIN|ISO|IEC|EN\s+\d)\b", t, re.I):
        return ChunkType.NARRATIVE

    return ChunkType.UNKNOWN
