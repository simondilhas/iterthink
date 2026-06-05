"""PBS content_changes persistence."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from iterthink.db.change_models import ContentChange
from iterthink.db.session import session_scope
from iterthink.persistence import content_changes, content_repo


def test_record_semantic_compare_batch(ephemeral_store: None, tmp_path: Path) -> None:
    md = tmp_path / "a.md"
    md.write_text("A\n\nB", encoding="utf-8")
    with session_scope() as s:
        v1 = content_repo.persist_version_snapshot(s, md.resolve(), "A\n\nB", "manual")
        v2 = content_repo.persist_version_snapshot(s, md.resolve(), "A\n\nC", "manual")
        assert v1 is not None and v2 is not None
        content_changes.record_semantic_compare_batch(
            s,
            newer_content_version_id=int(v2),
            baseline_content_version_id=int(v1),
            pairs=[(1, "B", "C", "NEW")],
        )
        s.commit()
    with session_scope() as s:
        rows = s.scalars(select(ContentChange)).all()
        assert len(rows) == 1
        assert rows[0].intent_verdict == "NEW"
        assert rows[0].from_revision == 1
        assert rows[0].to_revision == 2


def test_record_paragraph_semantic_change(ephemeral_store: None, tmp_path: Path) -> None:
    md = tmp_path / "c.md"
    md.write_text("A\n\nB", encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), md.read_text(), "manual")
        assert vid is not None
        content_changes.record_paragraph_semantic_change(
            s,
            content_version_id=int(vid),
            paragraph_index=1,
            old_text="B",
            new_text="B revised",
            kind="NEW",
        )
        s.commit()
