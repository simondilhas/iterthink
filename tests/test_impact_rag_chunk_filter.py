"""RAG chunk quality filter for norm context."""

from iterthink.services.rag.impact_rag import _chunk_usable_for_norm_context, rag_chunk_display_body


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


def test_rag_context_wrapper_stripped_for_display() -> None:
    inner = (
        "2.1 Foo   . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 7\n"
        "2.2 Bar   . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 7"
    )
    body = (
        "Bauzeitabdichtungen, die später als Dampfbremse belassen werden, "
        "müssen den Anforderungen an Dampfbremsen genügen."
    )
    wrapped = (
        f"<!-- iterthink-rag-context-start -->\n{inner}\n<!-- iterthink-rag-context-end -->\n\n{body}"
    )
    assert rag_chunk_display_body(wrapped).strip() == body
    assert _chunk_usable_for_norm_context(wrapped)


def test_rag_context_wrapper_body_still_rejected_if_junk() -> None:
    wrapped = (
        "<!-- iterthink-rag-context-start -->\n# ok\n<!-- iterthink-rag-context-end -->\n\n### 0"
    )
    assert not _chunk_usable_for_norm_context(wrapped)
