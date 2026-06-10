"""PBS content repository on empty dual-DB fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_rename_pdf_import_keeps_lineage_and_pdf_asset(
    ephemeral_store: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pypdf import PdfWriter

    docs = tmp_path / "docs"
    docs.mkdir()
    old_md = docs / "floor_a.md"
    old_md.write_text("<!-- pdf_profile:plan -->\n", encoding="utf-8")
    pdf = tmp_path / "source.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(pdf, "wb") as f:
        w.write(f)

    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(
            s,
            old_md.resolve(),
            old_md.read_text(encoding="utf-8"),
            "import",
            skip_if_unchanged_sha=False,
            pdf_source_path=pdf,
            pdf_profile="plan",
        )
        assert vid is not None
        old_lid = content_repo.get_lineage_id_for_path(s, old_md.resolve())
        old_rel = content_repo.get_version_pdf_relpath(s, vid)
        assert old_rel is not None

    old_resolved = old_md.resolve()
    new_md = docs / "floor_b.md"
    old_md.rename(new_md)
    new_resolved = new_md.resolve()

    with session_scope() as s:
        st = content_repo.update_document_path_after_rename(s, old_resolved, new_resolved)
        assert st == "ok"
        new_lid = content_repo.get_lineage_id_for_path(s, new_resolved)
        assert new_lid is not None
        assert new_lid == content_repo._lineage_id_for_path(new_resolved)
        assert new_lid != old_lid
        hit = content_repo.latest_pdf_version_for_document(s, new_resolved)
        assert hit is not None
        new_vid, new_rel = hit
        assert new_vid == vid
        assert new_rel == old_rel.replace(
            content_repo.path_key_for(old_resolved),
            content_repo.path_key_for(new_resolved),
        )
        pdf_abs = content_repo.pdf_asset_abs_path(new_rel)
        assert pdf_abs.is_file()
