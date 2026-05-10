"""Heuristic chunk classification."""

from __future__ import annotations

from iterthink.services.rag.chunk_type import ChunkType, classify_chunk_type, parse_chunk_type


def test_parse_invalid_is_unknown() -> None:
    assert parse_chunk_type("not-a-type") is ChunkType.UNKNOWN
    assert parse_chunk_type(None) is ChunkType.UNKNOWN
    assert parse_chunk_type("norm") is ChunkType.NORM


def test_classify_norm() -> None:
    assert classify_chunk_type("According to DIN EN 1991-1-4 snow loads shall be …") is ChunkType.NORM
    assert classify_chunk_type("ISO 9001 quality management") is ChunkType.NORM


def test_classify_law() -> None:
    assert classify_chunk_type("Art. 25 Abs. 2 DSG applies to processing.") is ChunkType.LAW


def test_classify_requirement() -> None:
    assert classify_chunk_type("The contractor shall submit shop drawings.") is ChunkType.REQUIREMENT
    assert classify_chunk_type("Die Unterlagen müssen vor Baubeginn vorliegen.") is ChunkType.REQUIREMENT


def test_classify_task() -> None:
    assert classify_chunk_type("- [ ] Order fire-rated doors") is ChunkType.TASK
    assert classify_chunk_type("TODO: verify loads") is ChunkType.TASK


def test_classify_definition() -> None:
    assert classify_chunk_type("**Dampfbremse** — Bauteil mit definiertem sd-Wert.") is ChunkType.DEFINITION


def test_classify_narrative_long_prose() -> None:
    t = (
        "This section explains the historical background of the project. "
        "The site was previously used for logistics. Several constraints remain. "
        "Neighbours raised concerns during consultation. "
        "Further paragraphs describe soil conditions and drainage assumptions. "
        "The narrative does not cite structural Eurocodes or product standards here."
    )
    assert classify_chunk_type(t) is ChunkType.NARRATIVE
