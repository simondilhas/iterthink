"""Margin prompt actions (per-paragraph).

Runtime loads ``config.STORE_DIR / "prompts.yaml"`` only; ``iterthink/defaults/prompts.yaml`` seeds that file when missing.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from iterthink import config

_DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"

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


MARGIN_ACTIONS: tuple[MarginAction, ...] = ()


def _prompts_path() -> Path:
    return config.STORE_DIR / "prompts.yaml"


def _parse_actions(data: Any) -> tuple[MarginAction, ...]:
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


def _ensure_prompts_file() -> None:
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _prompts_path()
    if not path.is_file():
        shutil.copy(_DEFAULTS_DIR / "prompts.yaml", path)


def reload() -> None:
    """Reload prompts.yaml into MARGIN_ACTIONS."""
    global MARGIN_ACTIONS
    _ensure_prompts_file()
    raw = _prompts_path().read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    MARGIN_ACTIONS = _parse_actions(data)


def read_prompts_yaml_text() -> str:
    _ensure_prompts_file()
    return _prompts_path().read_text(encoding="utf-8")


def write_prompts_yaml_text(text: str) -> None:
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(text)
    _parse_actions(data)  # validate before write
    _prompts_path().write_text(text.rstrip() + "\n", encoding="utf-8")
    reload()


def write_margin_actions_dicts(actions: list[dict[str, Any]]) -> None:
    """Validate and write margin_actions only (structured editor)."""
    data = {"margin_actions": actions}
    _parse_actions(data)
    dumped = yaml.safe_dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=88,
    )
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    _prompts_path().write_text(dumped.rstrip() + "\n", encoding="utf-8")
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


reload()
