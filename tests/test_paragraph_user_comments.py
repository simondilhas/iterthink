"""Tests for hash-anchored paragraph user comment resolution."""

from __future__ import annotations

from pathlib import Path

from iterthink.compare.paragraph_align import compute_hash
from iterthink.db.session import session_scope
from iterthink.persistence import content_repo, paragraph_user_comments
from iterthink.persistence.paragraph_user_comments import StoredComment


def _persist_doc(tmp_path: Path, body: str) -> tuple[int, int]:
    md = tmp_path / "note.md"
    md.write_text(body, encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), body, "manual")
        assert vid is not None
        doc = content_repo.get_document_by_resolved_path(s, md.resolve())
        assert doc is not None
        return int(doc.id), int(vid)


def test_resolve_insert_at_front_keeps_comment_on_paragraph_b() -> None:
    anchor = "Intro\n\nParagraph B\n\nOutro"
    display = "New top\n\nIntro\n\nParagraph B\n\nOutro"
    h_b = compute_hash("Paragraph B")
    stored = [StoredComment(paragraph_index=1, body="note on B", content_hash=h_b)]
    resolved = paragraph_user_comments.resolve_comments_for_body(anchor, display, stored)
    assert resolved.get(2) == "note on B"
    assert 0 not in resolved or resolved[0] != "note on B"


def test_resolve_alignment_fallback_when_text_edited_slightly() -> None:
    anchor = "Alpha\n\nBeta\n\nGamma"
    display = "Alpha\n\nBeta edited\n\nGamma"
    stored = [StoredComment(paragraph_index=1, body="beta note", content_hash=None)]
    resolved = paragraph_user_comments.resolve_comments_for_body(anchor, display, stored)
    assert resolved.get(1) == "beta note"


def test_resolve_orphan_merges_into_first_paragraph() -> None:
    anchor = "Only\n\nRemoved"
    display = "Only"
    h_removed = compute_hash("Removed")
    stored = [StoredComment(paragraph_index=1, body="orphan note", content_hash=h_removed)]
    resolved = paragraph_user_comments.resolve_comments_for_body(anchor, display, stored)
    assert resolved.get(0) == "orphan note"


def test_migrate_comments_to_new_version_preserves_hash_anchored_notes(
    ephemeral_store: None, tmp_path: Path
) -> None:
    old_body = "A\n\nB\n\nC"
    new_body = "Z\n\nA\n\nB\n\nC"
    _doc_id, parent_vid = _persist_doc(tmp_path, old_body)
    h_b = compute_hash("B")
    with session_scope() as s:
        paragraph_user_comments.upsert(
            s,
            content_version_id=parent_vid,
            paragraph_index=1,
            body="on B",
            content_hash=h_b,
        )
    md = tmp_path / "note.md"
    with session_scope() as s:
        new_vid = content_repo.persist_version_snapshot(s, md.resolve(), new_body, "manual")
        assert new_vid is not None
    with session_scope() as s:
        resolved = paragraph_user_comments.map_resolved_for_display(
            s,
            content_version_id=int(new_vid),
            anchor_body=new_body,
            display_body=new_body,
        )
    assert resolved.get(2) == "on B"


def test_legacy_rows_without_hash_use_alignment(ephemeral_store: None, tmp_path: Path) -> None:
    old_body = "One\n\nTwo"
    new_body = "Zero\n\nOne\n\nTwo"
    _doc_id, parent_vid = _persist_doc(tmp_path, old_body)
    with session_scope() as s:
        paragraph_user_comments.upsert(
            s,
            content_version_id=parent_vid,
            paragraph_index=1,
            body="on two",
            content_hash=None,
        )
    md = tmp_path / "note.md"
    with session_scope() as s:
        new_vid = content_repo.persist_version_snapshot(s, md.resolve(), new_body, "manual")
        assert new_vid is not None
    with session_scope() as s:
        resolved = paragraph_user_comments.map_resolved_for_display(
            s,
            content_version_id=int(new_vid),
            anchor_body=new_body,
            display_body=new_body,
        )
    assert resolved.get(2) == "on two"
