"""Impact-tab per-paragraph checks (Review → Impact) with RAG context.

Loads ``config.STORE_DIR / "impact_checks.yaml"``; ``iterthink/defaults/impact_checks.yaml``
seeds when missing. Legacy ``impact_actions`` inside ``prompts.yaml`` is migrated once
when the impact list would otherwise be empty.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from iterthink import config

_DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
_YAML_KEY = "impact_checks"
_LEGACY_PROMPTS_KEY = "impact_actions"


@dataclass(frozen=True)
class ImpactCheck:
    id: str
    label: str
    system_prompt: str
    user_template: str  # must include "{text}" and "{context}"


IMPACT_CHECKS: tuple[ImpactCheck, ...] = ()


def _impact_checks_path() -> Path:
    return config.STORE_DIR / "impact_checks.yaml"


def _prompts_path() -> Path:
    return config.STORE_DIR / "prompts.yaml"


def _parse_impact_checks_list(raw_list: Any, *, source_label: str) -> tuple[ImpactCheck, ...]:
    if not isinstance(raw_list, list) or not raw_list:
        return ()
    out: list[ImpactCheck] = []
    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise ValueError(f"{source_label}[{i}] must be a mapping.")
        aid = item.get("id")
        label = item.get("label")
        sp = item.get("system_prompt")
        ut = item.get("user_template")
        if not all(isinstance(x, str) and str(x).strip() for x in (aid, label, sp, ut)):
            raise ValueError(f"{source_label}[{i}] needs id, label, system_prompt, user_template strings.")
        ut_str = str(ut)
        if "{text}" not in ut_str:
            raise ValueError(f"{source_label}[{i}].user_template must contain '{{text}}'.")
        if "{context}" not in ut_str:
            raise ValueError(f"{source_label}[{i}].user_template must contain '{{context}}'.")
        out.append(
            ImpactCheck(
                id=str(aid).strip(),
                label=str(label).strip(),
                system_prompt=str(sp).strip(),
                user_template=ut_str,
            )
        )
    return tuple(out)


def _legacy_impact_actions_from_prompts_file() -> tuple[ImpactCheck, ...]:
    path = _prompts_path()
    if not path.is_file():
        return ()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ()
    if not isinstance(data, dict):
        return ()
    raw = data.get(_LEGACY_PROMPTS_KEY)
    if not isinstance(raw, list) or not raw:
        return ()
    # Same shape as impact_checks; tolerate topic field.
    cleaned: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        d = {k: v for k, v in item.items() if k in ("id", "label", "system_prompt", "user_template")}
        if len(d) == 4:
            cleaned.append(d)
    try:
        return _parse_impact_checks_list(cleaned, source_label=_LEGACY_PROMPTS_KEY)
    except ValueError:
        return ()


def _write_impact_checks_file(checks: tuple[ImpactCheck, ...]) -> None:
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    dicts = [
        {"id": c.id, "label": c.label, "system_prompt": c.system_prompt, "user_template": c.user_template}
        for c in checks
    ]
    dumped = yaml.safe_dump(
        {_YAML_KEY: dicts},
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=88,
    )
    _impact_checks_path().write_text(dumped.rstrip() + "\n", encoding="utf-8")


def _ensure_impact_checks_file() -> None:
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _impact_checks_path()
    if not path.is_file():
        shutil.copy(_DEFAULTS_DIR / "impact_checks.yaml", path)


def _maybe_migrate_from_prompts() -> None:
    """If impact_checks list is empty, import from prompts impact_actions or re-seed defaults."""
    path = _impact_checks_path()
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    existing = _load_tuple_lenient(data)
    if existing:
        return
    legacy = _legacy_impact_actions_from_prompts_file()
    if legacy:
        _write_impact_checks_file(legacy)
        return
    shutil.copy(_DEFAULTS_DIR / "impact_checks.yaml", path)


def _validate_document(data: Any) -> tuple[ImpactCheck, ...]:
    if not isinstance(data, dict):
        raise ValueError("impact_checks.yaml must be a mapping with 'impact_checks' list.")
    out = _parse_impact_checks_list(data.get(_YAML_KEY), source_label=_YAML_KEY)
    if not out:
        raise ValueError("impact_checks must be a non-empty list.")
    return out


def _load_tuple_lenient(data: Any) -> tuple[ImpactCheck, ...]:
    if not isinstance(data, dict):
        return ()
    try:
        return _parse_impact_checks_list(data.get(_YAML_KEY), source_label=_YAML_KEY)
    except ValueError:
        return ()


def reload() -> None:
    """Reload impact_checks.yaml into IMPACT_CHECKS."""
    global IMPACT_CHECKS
    _ensure_impact_checks_file()
    _maybe_migrate_from_prompts()
    raw = _impact_checks_path().read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    IMPACT_CHECKS = _load_tuple_lenient(data)
    if IMPACT_CHECKS:
        IMPACT_CHECKS = _validate_document(data)
    else:
        seed = yaml.safe_load((_DEFAULTS_DIR / "impact_checks.yaml").read_text(encoding="utf-8"))
        IMPACT_CHECKS = _validate_document(seed)


def get_impact_check(check_id: str) -> ImpactCheck | None:
    for c in IMPACT_CHECKS:
        if c.id == check_id:
            return c
    return None


def write_impact_checks_dicts(actions: list[dict[str, Any]]) -> None:
    """Validate and write impact_checks list (settings UI)."""
    checks = _parse_impact_checks_list(actions, source_label=_YAML_KEY)
    if not checks:
        raise ValueError("impact_checks must be a non-empty list.")
    _write_impact_checks_file(checks)
    reload()


reload()
