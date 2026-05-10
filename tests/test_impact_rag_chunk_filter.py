"""RAG chunk quality filter for norm context."""

from iterthink.services.impact_rag import _chunk_usable_for_norm_context


def test_rejects_trivial_heading_chunk() -> None:
    assert not _chunk_usable_for_norm_context("### 0")
    assert not _chunk_usable_for_norm_context("### –")


def test_rejects_short_chunk() -> None:
    assert not _chunk_usable_for_norm_context("### hi")


def test_rejects_toc_dot_leaders() -> None:
    toc = (
        "2.1 Allgemeines   . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 7 "
        "2.2 Überprüfung   . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 7"
    )
    assert not _chunk_usable_for_norm_context(toc)


def test_accepts_substantive_excerpt() -> None:
    s = (
        "Bauzeitabdichtungen, die später als Dampfbremse belassen werden, "
        "müssen den Anforderungen an Dampfbremsen genügen."
    )
    assert _chunk_usable_for_norm_context(s)
