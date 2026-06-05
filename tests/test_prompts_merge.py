"""Tests for bundled margin prompt merge."""

from __future__ import annotations

import json

import yaml

from iterthink import config, prompts
from iterthink.persistence import store_db


def _write_store_actions(actions: list[dict[str, str]]) -> None:
    path = config.STORE_DIR / "prompts.yaml"
    path.write_text(
        yaml.safe_dump({"margin_actions": actions}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _load_store_actions() -> list[dict]:
    data = yaml.safe_load((config.STORE_DIR / "prompts.yaml").read_text(encoding="utf-8"))
    return list(data.get("margin_actions") or [])


def test_sync_on_fresh_store_has_no_conflicts(ephemeral_store: None) -> None:
    result = prompts.sync_with_defaults()
    assert result.added_ids == ()
    assert result.pending == ()
    assert prompts.MARGIN_ACTIONS


def test_sync_adds_missing_bundled_id(ephemeral_store: None) -> None:
    bundled = prompts._load_bundled_dicts()
    assert bundled
    trimmed = [d for d in bundled if d["id"] != "create_management_summary"]
    _write_store_actions(trimmed)
    result = prompts.sync_with_defaults()
    assert "create_management_summary" in result.added_ids
    ids = [a.id for a in prompts.MARGIN_ACTIONS]
    assert "create_management_summary" in ids


def test_sync_queues_conflict_when_bundled_differs(ephemeral_store: None) -> None:
    bundled = {d["id"]: d for d in prompts._load_bundled_dicts()}
    row = dict(bundled["create_summary"])
    row["label"] = "My custom summary label"
    actions = [row if d["id"] == "create_summary" else d for d in bundled.values()]
    _write_store_actions(actions)
    result = prompts.sync_with_defaults()
    assert result.added_ids == ()
    assert len(result.pending) == 1
    assert result.pending[0].action_id == "create_summary"
    assert "label" in result.pending[0].changed_fields


def test_keep_mine_dismisses_conflict_on_resync(ephemeral_store: None) -> None:
    bundled = {d["id"]: d for d in prompts._load_bundled_dicts()}
    row = dict(bundled["create_summary"])
    row["system_prompt"] = row["system_prompt"] + "\nExtra user rule."
    actions = [row if d["id"] == "create_summary" else d for d in bundled.values()]
    _write_store_actions(actions)
    prompts.sync_with_defaults()
    assert prompts.pending_conflicts()
    prompts.resolve_conflict_keep_mine("create_summary")
    assert not prompts.pending_conflicts()
    result = prompts.sync_with_defaults()
    assert not result.pending


def test_use_bundled_replaces_store_row(ephemeral_store: None) -> None:
    bundled = {d["id"]: d for d in prompts._load_bundled_dicts()}
    row = dict(bundled["clarify_intent"])
    row["label"] = "Custom clarify"
    actions = [row if d["id"] == "clarify_intent" else d for d in bundled.values()]
    _write_store_actions(actions)
    prompts.sync_with_defaults()
    prompts.resolve_conflict_use_bundled("clarify_intent")
    store = {d["id"]: d for d in _load_store_actions()}
    assert store["clarify_intent"]["label"] == bundled["clarify_intent"]["label"]
    assert not prompts.pending_conflicts()


def test_removed_bundled_id_not_readded(ephemeral_store: None) -> None:
    bundled = prompts._load_bundled_dicts()
    trimmed = [d for d in bundled if d["id"] != "pirate_tone"]
    _write_store_actions(trimmed)
    prompts.record_removed_action_id("pirate_tone")
    result = prompts.sync_with_defaults()
    assert "pirate_tone" not in result.added_ids
    ids = {a.id for a in prompts.MARGIN_ACTIONS}
    assert "pirate_tone" not in ids


def test_new_id_inserted_in_bundled_order(ephemeral_store: None) -> None:
    bundled = prompts._load_bundled_dicts()
    trimmed = [d for d in bundled if d["id"] != "create_management_summary"]
    custom = {
        "id": "team_custom",
        "label": "Team custom",
        "topic": "discuss",
        "system_prompt": "Custom prompt.",
        "user_template": "\n\n{text}",
    }
    trimmed.append(custom)
    _write_store_actions(trimmed)
    prompts.sync_with_defaults()
    ids = [a.id for a in prompts.MARGIN_ACTIONS]
    assert "create_management_summary" in ids
    assert ids.index("create_summary") < ids.index("create_management_summary")
    assert ids.index("create_management_summary") < ids.index("clarify_intent")
    assert ids[-1] == "team_custom"


def test_dismissed_hash_persisted_in_settings(ephemeral_store: None) -> None:
    bundled = {d["id"]: d for d in prompts._load_bundled_dicts()}
    row = dict(bundled["pros_cons"])
    row["label"] = "Pros/cons (edited)"
    actions = [row if d["id"] == "pros_cons" else d for d in bundled.values()]
    _write_store_actions(actions)
    prompts.sync_with_defaults()
    conflict = prompts.pending_conflicts()[0]
    prompts.resolve_conflict_keep_mine("pros_cons")
    conn = store_db.connect()
    try:
        raw = store_db.settings_get(conn, store_db.SETTINGS_PROMPTS_BUNDLED_DISMISSED)
    finally:
        conn.close()
    assert raw
    dismissed = json.loads(raw)
    assert dismissed["pros_cons"] == conflict.bundled_hash


def test_prompt_merge_diff_spans_mark_delete_and_insert() -> None:
    import flet as ft

    from iterthink.compare.diff_card import _BG_DEL
    from iterthink.studio.prompts_merge_ui import _prompt_diff_side_spans

    old_spans = _prompt_diff_side_spans("alpha beta", "alpha gamma", side="old", mono=False)
    new_spans = _prompt_diff_side_spans("alpha beta", "alpha gamma", side="new", mono=False)

    assert any(
        s.style
        and s.style.decoration == ft.TextDecoration.LINE_THROUGH
        and "beta" in (s.text or "")
        for s in old_spans
    )
    assert any(s.style and s.style.bgcolor == _BG_DEL for s in old_spans)
    assert any(
        s.style and s.style.bgcolor is not None and "gamma" in (s.text or "")
        for s in new_spans
    )
