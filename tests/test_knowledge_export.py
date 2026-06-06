"""Tests for Settings → Knowledge export payload."""

from __future__ import annotations

import json

import pytest

from iterthink import checks as checks_mod
from iterthink.db.session import session_scope
from iterthink.persistence import impact_annotations as impact_ann
from iterthink.persistence import paragraph_user_comments as user_comments
from iterthink.persistence import store_db
from iterthink.services import checks_runner, knowledge_export


def _minimal_check() -> checks_mod.Check:
    return checks_mod.Check(
        id="unit_knowledge_check",
        label="Unit knowledge",
        accent="#5AB0FF",
        system_prompt="Return JSON.",
        user_template="OLD:\n{old}\nNEW:\n{new}",
        symbol_field="symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(
            checks_mod.CheckSymbol(symbol="~", label="Neutral", color="#9AA0A6"),
            checks_mod.CheckSymbol(symbol="!", label="Change", color="#E5484D"),
        ),
    )


@pytest.mark.usefixtures("ephemeral_store")
def test_build_export_payload_includes_comments_and_overrides() -> None:
    check = _minimal_check()
    base = {
        "symbol": "~",
        "summary": "Model summary",
        "recommendations": [{"action": "Model rec"}],
    }
    patched = checks_mod.apply_check_override(
        base, check, symbol="!", recommendation="Human rec"
    )
    checks_runner.save_result(
        check.id,
        "Old paragraph",
        "New paragraph",
        "test-model",
        patched,
        document_path_key="doc-key-1",
    )

    with session_scope() as s:
        user_comments.upsert(
            s,
            content_version_id=7,
            paragraph_index=2,
            body="Writer note",
            content_hash="abc",
        )
        impact_ann.upsert_model_result(
            s,
            content_version_id=7,
            paragraph_index=1,
            prompt_id="norm_compliance",
            status="warning",
            comment="Model impact",
            details=None,
        )
        impact_ann.set_override(
            s,
            content_version_id=7,
            paragraph_index=1,
            prompt_id="norm_compliance",
            status="ok",
            override_comment="Accepted",
        )
        s.commit()

    store_conn = store_db.connect()
    store_db.init_schema(store_conn)
    store_db.impact_override_context_upsert(
        store_conn,
        content_version_id=7,
        paragraph_index=1,
        prompt_id="norm_compliance",
        paragraph_text_hash="hash1",
        status="ok",
        override_comment="Accepted",
        embed_text="embed body",
        vec_rowid=42,
        embed_model_id="nomic-embed-text",
    )

    with session_scope() as s:
        payload = knowledge_export.build_export_payload(s, store_conn=store_conn)

    assert payload["export_version"] == knowledge_export.EXPORT_VERSION
    assert payload["counts"]["paragraph_user_comments"] == 1
    assert payload["counts"]["impact_annotations"] == 1
    assert payload["counts"]["difference_check_overrides"] == 1
    assert payload["counts"]["impact_override_embeddings"] == 1

    note = payload["paragraph_user_comments"][0]
    assert note["content_version_id"] == 7
    assert note["body"] == "Writer note"

    impact = payload["impact_annotations"][0]
    assert impact["overridden"] is True
    assert impact["effective_comment"] == "Accepted"
    assert impact["model_status"] == "warning"

    diff = payload["difference_check_overrides"][0]
    assert diff["check_id"] == check.id
    assert diff["effective_recommendation"] == "Human rec"
    assert diff["model_recommendation"] == "Model rec"

    with session_scope() as s:
        text = knowledge_export.export_json_text(s, store_conn=store_conn)
    parsed = json.loads(text)
    assert parsed["counts"] == payload["counts"]
