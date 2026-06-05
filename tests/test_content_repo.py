"""PBS content repository on empty dual-DB fixtures."""

from __future__ import annotations

from pathlib import Path

from iterthink.contract import enums as pbs
from iterthink.db.session import session_scope
from iterthink.persistence import content_repo


def test_open_path_persist_version_and_list(ephemeral_store: None, tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("Hello\n\nWorld", encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), md.read_text(), "manual")
        assert vid is not None
        snaps = content_repo.list_snapshots(s, md.resolve())
        assert len(snaps) == 1
        assert snaps[0].version_id == vid
        body = content_repo.load_version_body(s, vid)
        assert "Hello" in body
        assert (
            content_repo.get_lineage_artifact_kind(s, resolved_doc=md.resolve())
            == pbs.ARTIFACT_KIND_TEXT_DOCUMENT
        )
