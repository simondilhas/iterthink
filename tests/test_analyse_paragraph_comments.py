"""Tests for version-scoped analyse comments in paragraph_user_comments."""

from __future__ import annotations

from pathlib import Path

from iterthink import checks as checks_mod
from iterthink.checks import Check, CheckSymbol
from iterthink.compare.paragraph_align import compute_hash
from iterthink.db.session import session_scope
from iterthink.persistence import content_repo, paragraph_user_comments
from iterthink.studio.ki_comments_sidebar import KiCommentsSidebarMixin


def _persist_doc(tmp_path: Path, body: str) -> tuple[int, int]:
    md = tmp_path / "note.md"
    md.write_text(body, encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), body, "manual")
        assert vid is not None
        doc = content_repo.get_document_by_resolved_path(s, md.resolve())
        assert doc is not None
        return int(doc.id), int(vid)


def _test_check() -> Check:
    return Check(
        id="unit_analyse",
        label="Unit analyse",
        accent="#5AB0FF",
        system_prompt="JSON only.",
        user_template="OLD:\n{old}\nNEW:\n{new}",
        symbol_field="impact_symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(
            CheckSymbol(symbol="!", label="Issue", color="#FF0000"),
            CheckSymbol(symbol="~", label="Unchanged", color="#9AA0A6"),
        ),
    )


def _sample_payload(*, summary: str = "Needs work", symbol: str = "!") -> dict:
    return {
        "impact_symbol": symbol,
        "summary": summary,
        "recommendations": [{"action": "Revise."}],
    }


def test_upsert_analyse_and_list_for_version(
    ephemeral_store: None, tmp_path: Path
) -> None:
    body = "Alpha\n\nBeta\n\nGamma"
    _doc_id, vid = _persist_doc(tmp_path, body)
    payload = _sample_payload()
    with session_scope() as s:
        paragraph_user_comments.upsert_analyse(
            s,
            content_version_id=vid,
            paragraph_index=1,
            check_id="unit_analyse",
            body="Needs work",
            symbol="!",
            payload=payload,
            candidate_paragraph="Beta",
        )
        rows = paragraph_user_comments.list_analyse_for_version(
            s, content_version_id=vid, check_id="unit_analyse"
        )
        check_ids = paragraph_user_comments.list_analyse_check_ids_for_version(
            s, content_version_id=vid
        )
    assert len(rows) == 1
    assert rows[0].body == "Needs work"
    assert rows[0].symbol == "!"
    assert check_ids == ["unit_analyse"]


def test_map_analyse_resolved_for_display_follows_paragraph_drift(
    ephemeral_store: None, tmp_path: Path
) -> None:
    anchor = "Intro\n\nParagraph B\n\nOutro"
    display = "New top\n\nIntro\n\nParagraph B\n\nOutro"
    _doc_id, vid = _persist_doc(tmp_path, anchor)
    h_b = compute_hash("Paragraph B")
    payload = _sample_payload(summary="On B")
    with session_scope() as s:
        paragraph_user_comments.upsert_analyse(
            s,
            content_version_id=vid,
            paragraph_index=1,
            check_id="unit_analyse",
            body="On B",
            symbol="!",
            payload=payload,
            content_hash=h_b,
        )
    with session_scope() as s:
        resolved = paragraph_user_comments.map_analyse_resolved_for_display(
            s,
            content_version_id=vid,
            anchor_body=anchor,
            display_body=display,
        )
    assert len(resolved) == 1
    assert resolved[0].display_paragraph_index == 2
    assert resolved[0].payload is not None
    assert resolved[0].payload.get("summary") == "On B"


def test_upsert_analyse_override_updates_details_json(
    ephemeral_store: None, tmp_path: Path
) -> None:
    body = "One\n\nTwo"
    _doc_id, vid = _persist_doc(tmp_path, body)
    base = _sample_payload(summary="First", symbol="!")
    with session_scope() as s:
        paragraph_user_comments.upsert_analyse(
            s,
            content_version_id=vid,
            paragraph_index=0,
            check_id="unit_analyse",
            body="First",
            symbol="!",
            payload=base,
            candidate_paragraph="One",
        )
    patched = dict(base)
    patched["impact_symbol"] = "~"
    patched["summary"] = "Overridden"
    patched["overridden"] = True
    with session_scope() as s:
        paragraph_user_comments.upsert_analyse(
            s,
            content_version_id=vid,
            paragraph_index=0,
            check_id="unit_analyse",
            body="Overridden",
            symbol="~",
            payload=patched,
            overridden=True,
        )
        raw = paragraph_user_comments.get_analyse_payload(
            s,
            content_version_id=vid,
            check_id="unit_analyse",
            paragraph_index=0,
        )
    assert raw is not None
    assert raw.get("summary") == "Overridden"
    assert raw.get("impact_symbol") == "~"


class _KiCommentsStub(KiCommentsSidebarMixin):
    def __init__(self, *, vid: int, anchor: str, display: str) -> None:
        self.current_path = Path("/tmp/note.md")
        self._compare_snapshot_version_id = vid
        self._active_impact_prompt_id = None
        self._check_results = {}
        self._active_check_id = None
        self._anchor = anchor
        self._display = display

    def _ki_comments_for_current_version(self) -> dict[int, str]:
        return {}

    def _ki_analyse_version_line_for_current_ui(self) -> str:
        return "v1"

    def _resolve_impact_version_id(self, session) -> int | None:  # noqa: ANN001
        return int(self._compare_snapshot_version_id)

    def _analyse_display_bodies(self) -> tuple[str, str]:
        return self._anchor, self._display


def test_ki_comment_thread_items_loads_analyse_from_db_without_memory(
    ephemeral_store: None, tmp_path: Path, monkeypatch
) -> None:
    body = "Alpha\n\nBeta"
    _doc_id, vid = _persist_doc(tmp_path, body)
    check = _test_check()
    payload = _sample_payload(summary="Beta issue")
    with session_scope() as s:
        paragraph_user_comments.upsert_analyse(
            s,
            content_version_id=vid,
            paragraph_index=1,
            check_id=check.id,
            body="Beta issue",
            symbol="!",
            payload=payload,
            candidate_paragraph="Beta",
        )
    monkeypatch.setattr(checks_mod, "get_check", lambda cid: check if cid == check.id else None)
    stub = _KiCommentsStub(vid=vid, anchor=body, display=body)
    items = stub._ki_comment_thread_items()
    analyse = [i for i in items if i.kind == "analyse"]
    assert len(analyse) == 1
    assert analyse[0].check_id == check.id
    assert analyse[0].paragraph_index == 1
    assert "Beta issue" in analyse[0].preview
