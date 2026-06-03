"""Margin prompt actions (per-paragraph).

Runtime loads ``config.STORE_DIR / "prompts.yaml"``; ``iterthink/defaults/prompts.yaml`` seeds
when missing. ``sync_with_defaults()`` auto-adds new bundled ids and queues conflicts when bundled
content changes for an existing id.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from iterthink import config
from iterthink.persistence import store_db

_DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
_COMPARE_FIELDS: tuple[str, ...] = ("label", "topic", "system_prompt", "user_template")

TOPIC_DISCUSS = "discuss"
TOPIC_CHANGE = "change"
TOPIC_EVALUATE = "evaluate"
VALID_TOPICS: frozenset[str] = frozenset({TOPIC_DISCUSS, TOPIC_CHANGE, TOPIC_EVALUATE})


@dataclass(frozen=True)
class MarginAction:
    id: str
    label: str
    system_prompt: str
    user_template: str  # must include "{text}"
    topic: str  # discuss | change | evaluate


@dataclass(frozen=True)
class PromptConflict:
    action_id: str
    label: str
    store: dict[str, str]
    bundled: dict[str, str]
    changed_fields: tuple[str, ...]
    bundled_hash: str


@dataclass(frozen=True)
class SyncResult:
    added_ids: tuple[str, ...]
    pending: tuple[PromptConflict, ...]


MARGIN_ACTIONS: tuple[MarginAction, ...] = ()
PENDING_CONFLICTS: tuple[PromptConflict, ...] = ()


def _prompts_path() -> Path:
    return config.STORE_DIR / "prompts.yaml"


def _parse_margin_actions(data: Any) -> tuple[MarginAction, ...]:
    if not isinstance(data, dict):
        raise ValueError("Prompts YAML must be a mapping with 'margin_actions' list.")
    raw_list = data.get("margin_actions")
    if not isinstance(raw_list, list) or not raw_list:
        raise ValueError("margin_actions must be a non-empty list.")
    out: list[MarginAction] = []
    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise ValueError(f"margin_actions[{i}] must be a mapping.")
        aid = item.get("id")
        label = item.get("label")
        sp = item.get("system_prompt")
        ut = item.get("user_template")
        if not all(isinstance(x, str) and str(x).strip() for x in (aid, label, sp, ut)):
            raise ValueError(f"margin_actions[{i}] needs id, label, system_prompt, user_template strings.")
        if "{text}" not in str(ut):
            raise ValueError(f"margin_actions[{i}].user_template must contain '{{text}}'.")
        raw_topic = item.get("topic")
        if raw_topic is None or (isinstance(raw_topic, str) and not str(raw_topic).strip()):
            topic = TOPIC_CHANGE
        elif not isinstance(raw_topic, str) or str(raw_topic).strip() not in VALID_TOPICS:
            raise ValueError(
                f"margin_actions[{i}].topic must be one of: {', '.join(sorted(VALID_TOPICS))}."
            )
        else:
            topic = str(raw_topic).strip()
        out.append(
            MarginAction(
                id=aid.strip(),
                label=label.strip(),
                system_prompt=sp.strip(),
                user_template=str(ut),
                topic=topic,
            )
        )
    return tuple(out)


def _parse_actions(data: Any) -> tuple[MarginAction, ...]:
    """Backward-compatible name: margin actions only."""
    return _parse_margin_actions(data)


def _validate_prompts_document(data: Any) -> None:
    _parse_margin_actions(data)


def _ensure_prompts_file() -> None:
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _prompts_path()
    if not path.is_file():
        shutil.copy(_DEFAULTS_DIR / "prompts.yaml", path)


def _load_bundled_yaml() -> dict[str, Any]:
    raw = yaml.safe_load((_DEFAULTS_DIR / "prompts.yaml").read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _action_dict_from_item(item: dict[str, Any]) -> dict[str, str]:
    aid = str(item.get("id") or "").strip()
    label = str(item.get("label") or "").strip()
    sp = str(item.get("system_prompt") or "").strip()
    ut = str(item.get("user_template") or "")
    raw_topic = item.get("topic")
    if raw_topic is None or (isinstance(raw_topic, str) and not str(raw_topic).strip()):
        topic = TOPIC_CHANGE
    else:
        topic = str(raw_topic).strip()
    return {
        "id": aid,
        "label": label,
        "topic": topic,
        "system_prompt": sp,
        "user_template": ut,
    }


def _load_bundled_dicts() -> list[dict[str, str]]:
    data = _load_bundled_yaml()
    raw_list = data.get("margin_actions")
    if not isinstance(raw_list, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw_list:
        if isinstance(item, dict):
            d = _action_dict_from_item(item)
            if d["id"]:
                out.append(_normalize_action_dict(d))
    return out


def _load_store_dicts() -> list[dict[str, str]]:
    data = _read_prompts_mapping()
    raw_list = data.get("margin_actions")
    if not isinstance(raw_list, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw_list:
        if isinstance(item, dict):
            d = _action_dict_from_item(item)
            if d["id"]:
                out.append(_normalize_action_dict(d))
    return out


def _normalize_action_dict(d: dict[str, str]) -> dict[str, str]:
    topic = d.get("topic") or TOPIC_CHANGE
    if topic not in VALID_TOPICS:
        topic = TOPIC_CHANGE
    return {
        "id": str(d.get("id") or "").strip(),
        "label": str(d.get("label") or "").strip(),
        "topic": str(topic).strip(),
        "system_prompt": str(d.get("system_prompt") or "").strip().replace("\r\n", "\n"),
        "user_template": str(d.get("user_template") or "").replace("\r\n", "\n"),
    }


def _action_content_hash(d: dict[str, str]) -> str:
    norm = _normalize_action_dict(d)
    payload = {k: norm[k] for k in _COMPARE_FIELDS}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _diff_fields(store: dict[str, str], bundled: dict[str, str]) -> tuple[str, ...]:
    s = _normalize_action_dict(store)
    b = _normalize_action_dict(bundled)
    return tuple(f for f in _COMPARE_FIELDS if s.get(f) != b.get(f))


def _merge_ordered_list(
    store_dicts: list[dict[str, str]],
    bundled_dicts: list[dict[str, str]],
    *,
    store_by_id: dict[str, dict[str, str]],
    bundled_by_id: dict[str, dict[str, str]],
    to_add_ids: set[str],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for bd in bundled_dicts:
        bid = bd["id"]
        if bid in store_by_id:
            result.append(store_by_id[bid])
            seen.add(bid)
        elif bid in to_add_ids:
            result.append(bundled_by_id[bid])
            seen.add(bid)
    for sd in store_dicts:
        sid = sd["id"]
        if sid not in seen:
            result.append(sd)
            seen.add(sid)
    return result


def _read_json_settings(key: str) -> Any:
    conn = store_db.connect()
    try:
        raw = store_db.settings_get(conn, key)
    finally:
        conn.close()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _write_json_settings(key: str, value: Any) -> None:
    conn = store_db.connect()
    try:
        store_db.settings_set(conn, key, json.dumps(value, ensure_ascii=False))
    finally:
        conn.close()


def _load_dismissed_hashes() -> dict[str, str]:
    raw = _read_json_settings(store_db.SETTINGS_PROMPTS_BUNDLED_DISMISSED)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def _save_dismissed_hashes(dismissed: dict[str, str]) -> None:
    _write_json_settings(store_db.SETTINGS_PROMPTS_BUNDLED_DISMISSED, dismissed)


def _load_removed_ids() -> set[str]:
    raw = _read_json_settings(store_db.SETTINGS_PROMPTS_REMOVED_IDS)
    if not isinstance(raw, list):
        return set()
    return {str(x).strip() for x in raw if isinstance(x, str) and str(x).strip()}


def _save_removed_ids(removed: set[str]) -> None:
    _write_json_settings(store_db.SETTINGS_PROMPTS_REMOVED_IDS, sorted(removed))


def bundled_action_ids() -> frozenset[str]:
    return frozenset(d["id"] for d in _load_bundled_dicts())


def pending_conflicts() -> tuple[PromptConflict, ...]:
    return PENDING_CONFLICTS


def record_removed_action_id(action_id: str) -> None:
    aid = str(action_id).strip()
    if not aid or aid not in bundled_action_ids():
        return
    removed = _load_removed_ids()
    if aid in removed:
        return
    removed.add(aid)
    _save_removed_ids(removed)


def _write_margin_actions_list(actions: list[dict[str, str]]) -> None:
    data = _read_prompts_mapping()
    data["margin_actions"] = actions
    data.pop("impact_actions", None)
    _validate_prompts_document(data)
    dumped = yaml.safe_dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=88,
    )
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    _prompts_path().write_text(dumped.rstrip() + "\n", encoding="utf-8")


def _compute_merge_plan(
    store_dicts: list[dict[str, str]],
    bundled_dicts: list[dict[str, str]],
    *,
    dismissed: dict[str, str],
    removed_ids: set[str],
) -> tuple[set[str], tuple[PromptConflict, ...]]:
    store_by_id = {d["id"]: d for d in store_dicts}
    bundled_by_id = {d["id"]: d for d in bundled_dicts}
    to_add: set[str] = set()
    conflicts: list[PromptConflict] = []
    for bd in bundled_dicts:
        bid = bd["id"]
        if bid not in store_by_id:
            if bid not in removed_ids:
                to_add.add(bid)
            continue
        bundled_hash = _action_content_hash(bd)
        store_row = store_by_id[bid]
        if _action_content_hash(store_row) == bundled_hash:
            continue
        if dismissed.get(bid) == bundled_hash:
            continue
        changed = _diff_fields(store_row, bd)
        if not changed:
            continue
        label = store_row.get("label") or bd.get("label") or bid
        conflicts.append(
            PromptConflict(
                action_id=bid,
                label=label,
                store=store_row,
                bundled=bd,
                changed_fields=changed,
                bundled_hash=bundled_hash,
            )
        )
    return to_add, tuple(conflicts)


def sync_with_defaults() -> SyncResult:
    """Merge bundled defaults into store: auto-add new ids; queue conflicts for changed ids."""
    global PENDING_CONFLICTS
    _ensure_prompts_file()
    store_dicts = _load_store_dicts()
    bundled_dicts = _load_bundled_dicts()
    dismissed = _load_dismissed_hashes()
    removed_ids = _load_removed_ids()
    store_by_id = {d["id"]: d for d in store_dicts}
    bundled_by_id = {d["id"]: d for d in bundled_dicts}
    to_add, conflicts = _compute_merge_plan(
        store_dicts, bundled_dicts, dismissed=dismissed, removed_ids=removed_ids
    )
    if to_add:
        merged = _merge_ordered_list(
            store_dicts,
            bundled_dicts,
            store_by_id=store_by_id,
            bundled_by_id=bundled_by_id,
            to_add_ids=to_add,
        )
        _write_margin_actions_list(merged)
    PENDING_CONFLICTS = conflicts
    reload()
    return SyncResult(added_ids=tuple(sorted(to_add)), pending=conflicts)


def resolve_conflict_keep_mine(action_id: str) -> None:
    """Keep the store row; dismiss this bundled revision."""
    global PENDING_CONFLICTS
    aid = str(action_id).strip()
    conflict = next((c for c in PENDING_CONFLICTS if c.action_id == aid), None)
    if conflict is None:
        return
    dismissed = _load_dismissed_hashes()
    dismissed[aid] = conflict.bundled_hash
    _save_dismissed_hashes(dismissed)
    PENDING_CONFLICTS = tuple(c for c in PENDING_CONFLICTS if c.action_id != aid)


def resolve_all_conflicts_keep_mine() -> None:
    """Dismiss every pending bundled update."""
    for c in list(PENDING_CONFLICTS):
        resolve_conflict_keep_mine(c.action_id)


def resolve_conflict_use_bundled(action_id: str) -> None:
    """Replace the store row with the bundled default."""
    global PENDING_CONFLICTS
    aid = str(action_id).strip()
    conflict = next((c for c in PENDING_CONFLICTS if c.action_id == aid), None)
    if conflict is None:
        return
    store_dicts = _load_store_dicts()
    bundled_dicts = _load_bundled_dicts()
    store_by_id = {d["id"]: d for d in store_dicts}
    bundled_by_id = {d["id"]: d for d in bundled_dicts}
    store_by_id[aid] = conflict.bundled
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for bd in bundled_dicts:
        bid = bd["id"]
        if bid in store_by_id:
            merged.append(store_by_id[bid])
            seen.add(bid)
    for sd in store_dicts:
        if sd["id"] not in seen:
            merged.append(sd)
            seen.add(sd["id"])
    _write_margin_actions_list(merged)
    dismissed = _load_dismissed_hashes()
    dismissed.pop(aid, None)
    _save_dismissed_hashes(dismissed)
    PENDING_CONFLICTS = tuple(c for c in PENDING_CONFLICTS if c.action_id != aid)
    reload()


def reload() -> None:
    """Reload prompts.yaml into MARGIN_ACTIONS (store file only; no bundled merge)."""
    global MARGIN_ACTIONS
    _ensure_prompts_file()
    raw = _prompts_path().read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        data = {}
    MARGIN_ACTIONS = _parse_margin_actions(data)


def read_prompts_yaml_text() -> str:
    _ensure_prompts_file()
    return _prompts_path().read_text(encoding="utf-8")


def write_prompts_yaml_text(text: str) -> None:
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(text)
    _validate_prompts_document(data)
    _prompts_path().write_text(text.rstrip() + "\n", encoding="utf-8")
    reload()


def _read_prompts_mapping() -> dict[str, Any]:
    _ensure_prompts_file()
    raw = yaml.safe_load(_prompts_path().read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def write_margin_actions_dicts(actions: list[dict[str, Any]]) -> None:
    """Validate and write margin_actions only (drops legacy impact_actions key if present)."""
    normalized = [_normalize_action_dict(_action_dict_from_item(a)) for a in actions if isinstance(a, dict)]
    _write_margin_actions_list(normalized)
    reload()


def get_margin_action(action_id: str) -> MarginAction | None:
    for a in MARGIN_ACTIONS:
        if a.id == action_id:
            return a
    return None


def actions_for_topic(topic: str) -> tuple[MarginAction, ...]:
    """Return actions for the KI rail tab (discuss / change / evaluate)."""
    t = str(topic).strip()
    if t not in VALID_TOPICS:
        return ()
    return tuple(a for a in MARGIN_ACTIONS if a.topic == t)
