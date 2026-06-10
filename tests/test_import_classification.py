"""Import document-function autoclassification and lineage persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from iterthink.contract.document_classification import (
    attrs_from_function,
    build_classification_excerpt,
    classification_from_function,
    classify_document,
    suggest_document_function_fast,
)
from iterthink.contract.document_function_catalog import is_valid_function_id
from iterthink.db.session import session_scope
from iterthink.import_classification_settings import normalize_import_classification_tier
from iterthink.persistence import content_repo


def test_suggest_fast_kbob_brief() -> None:
    dest = Path("projects/Alpha/B01001-brief.md")
    result = suggest_document_function_fast(dest_md_path=dest)
    assert result.function_id == "req_project_briefs"
    assert result.source == "kbob_code"
    assert result.confidence == "high"


def test_suggest_fast_norm_folder(tmp_path: Path) -> None:
    dest = tmp_path / "SIA Norms" / "sia-380.md"
    dest.parent.mkdir(parents=True)
    result = suggest_document_function_fast(dest_md_path=dest)
    assert result.function_id == "reg_norms"
    assert result.source == "path_hint"


def test_suggest_fast_generic_spec_defaults() -> None:
    dest = Path("misc/spec.md")
    result = suggest_document_function_fast(dest_md_path=dest)
    assert result.function_id == "req_functional_specifications"
    assert result.source == "path_hint"


def test_classification_from_function_contract_keys() -> None:
    row = classification_from_function(
        "req_project_briefs",
        source="import_autoclassify",
    )
    assert row["classification_scheme"] == "document-function"
    assert row["classification_code"] == "req_project_briefs"
    assert row["classification_label"]
    assert row["classification_source"] == "import_autoclassify"


def test_attrs_from_function_writes_impact_fields() -> None:
    attrs = attrs_from_function("tec_documents", source="import_manual")
    assert attrs["document_functions"] == ["tec_documents"]
    assert attrs["classification_source"] == "import_manual"
    assert len(attrs["classifications"]) == 1
    assert attrs["classifications"][0]["classification_code"] == "tec_documents"


def test_set_lineage_classification_persists(
    ephemeral_store: None, tmp_path: Path
) -> None:
    md = tmp_path / "note.md"
    md.write_text("# Title\n\nBody.", encoding="utf-8")
    with session_scope() as s:
        content_repo.persist_version_snapshot(s, md.resolve(), md.read_text(), "import")
        content_repo.set_lineage_classification(
            s,
            md.resolve(),
            "reg_norms",
            source="import_autoclassify",
        )
        attrs = content_repo.lineage_stored_classification_attrs(s, md.resolve())
    assert attrs.get("document_functions") == ["reg_norms"]
    classifications = attrs.get("classifications")
    assert isinstance(classifications, list) and classifications
    assert classifications[0]["classification_scheme"] == "document-function"
    assert classifications[0]["classification_source"] == "import_autoclassify"


def test_classify_document_prefers_stored_attrs() -> None:
    stored = attrs_from_function("tec_plans", source="import_manual")
    cl = classify_document(
        Path("misc/spec.md"),
        stored_attrs=stored,
    )
    assert cl.document_functions == ("tec_plans",)
    assert cl.source == "import_manual"


def test_build_classification_excerpt_caps_length() -> None:
    body = "# Title\n\n" + ("word " * 500) + "\n\nSecond paragraph."
    excerpt = build_classification_excerpt(body, max_chars=120)
    assert len(excerpt) <= 120
    assert "Title" in excerpt or "word" in excerpt


def test_normalize_import_classification_tier_defaults_local() -> None:
    assert normalize_import_classification_tier(None) == "local"
    assert normalize_import_classification_tier("cloud") == "cloud"
    assert normalize_import_classification_tier("bogus") == "local"


def test_catalog_validates_picker_ids() -> None:
    assert is_valid_function_id("req_project_briefs")
    assert not is_valid_function_id("not_a_real_function")
