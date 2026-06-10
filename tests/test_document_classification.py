"""Document classification and Impact context scope (contract v0.1.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from iterthink.contract.document_classification import classify_document
from iterthink.services.impact_analysis_runner import _impact_query_metadata
from iterthink.services.impact_context_scope import (
    default_context_paths,
    path_is_impact_context_candidate,
    project_scoped_paths,
)
from iterthink.services.rag.project_scope import project_slug_for_path


def test_classify_kbob_code_in_filename() -> None:
    cl = classify_document(Path("B01001-projektbrief.md"))
    assert cl.kbob_code == "B01001"
    assert "req_project_briefs" in cl.document_functions
    assert cl.source == "kbob_code"


def test_classify_norm_folder_hint(tmp_path: Path) -> None:
    p = tmp_path / "SIA Norms" / "sia-380.md"
    p.parent.mkdir(parents=True)
    p.write_text("# Norm\n\nBody.", encoding="utf-8")
    cl = classify_document(p)
    assert "reg_norms" in cl.document_functions


def test_classify_frontmatter_kbob(tmp_path: Path) -> None:
    p = tmp_path / "note.md"
    p.write_text("document_type: V03001\n\n# Plan\n", encoding="utf-8")
    cl = classify_document(p, body=p.read_text(encoding="utf-8"))
    assert cl.kbob_code == "V03001"
    assert "tec_plans" in cl.document_functions


def test_project_scoped_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "docs"
    (root / "Alpha").mkdir(parents=True)
    (root / "Beta").mkdir(parents=True)
    target = root / "Alpha" / "target.md"
    sibling = root / "Alpha" / "sibling.md"
    other = root / "Beta" / "b.md"
    target.write_text("t", encoding="utf-8")
    sibling.write_text("s", encoding="utf-8")
    other.write_text("b", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", root)
    monkeypatch.setattr("iterthink.services.impact_context_scope.config.DOCUMENTS", root)

    scoped = project_scoped_paths(target_path=target)
    assert target.resolve() not in scoped
    assert sibling.resolve() in scoped
    assert other.resolve() not in scoped


def test_norm_compliance_includes_shared_norm_library(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "docs"
    proj = root / "Tower" / "spec.md"
    norm = root / "SIA Norms" / "sia.md"
    proj.parent.mkdir(parents=True)
    norm.parent.mkdir(parents=True)
    proj.write_text("spec", encoding="utf-8")
    norm.write_text("norm body", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", root)
    monkeypatch.setattr("iterthink.services.impact_context_scope.config.DOCUMENTS", root)

    assert path_is_impact_context_candidate(
        norm, check_id="norm_compliance", target_path=proj
    )
    assert not path_is_impact_context_candidate(
        norm, check_id="design_intent", target_path=proj
    )


def test_default_context_paths_respects_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "docs"
    brief = root / "Site" / "B01001-brief.md"
    spec = root / "Site" / "spec.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("brief", encoding="utf-8")
    spec.write_text("spec", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", root)
    monkeypatch.setattr("iterthink.services.impact_context_scope.config.DOCUMENTS", root)

    design = default_context_paths(check_id="design_intent", target_path=spec)
    assert brief.resolve() in {p.resolve() for p in design}
    assert spec.resolve() not in {p.resolve() for p in design}
    consistency = default_context_paths(check_id="impact_consistency", target_path=spec)
    assert brief.resolve() in {p.resolve() for p in consistency}


def test_impact_query_metadata_sets_project_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "docs"
    md = root / "ProjX" / "note.md"
    md.parent.mkdir(parents=True)
    md.write_text("# Title\n\nPara.", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", root)

    class _Row:
        lineage_id = "lineage-abc"

    class _Session:
        def get(self, _model: object, _id: int) -> _Row:
            return _Row()

    _cache, title, slug = _impact_query_metadata(_Session(), 1, md)
    assert title == "Title"
    assert slug == "ProjX"
    assert project_slug_for_path(md) == "ProjX"
