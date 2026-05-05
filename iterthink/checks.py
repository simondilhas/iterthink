"""Per-paragraph LLM check definitions (Project / Sustainability / Readability / LinkedIn).

Loads ``config.STORE_DIR / "checks.yaml"``; ``iterthink/defaults/checks.yaml``
seeds it when missing — same pattern as ``iterthink/prompts.py``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from iterthink import config

_DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"


@dataclass(frozen=True)
class CheckSymbol:
    symbol: str
    label: str
    color: str  # hex, e.g. "#3FBE6B"


@dataclass(frozen=True)
class MetricKey:
    key: str
    label: str


@dataclass(frozen=True)
class Check:
    id: str
    label: str
    accent: str  # hex accent color for the KI button + result card header
    system_prompt: str
    user_template: str  # must contain "{old}" and "{new}"
    symbol_field: str  # JSON field holding the headline symbol
    summary_path: str  # dotted path into payload, e.g. "project_impact.summary"
    metrics_path: str  # dotted path to a dict of metric values (or "" if none)
    metric_keys: tuple[MetricKey, ...]
    metric_value_set: tuple[str, ...]  # e.g. ("None", "Low", "Medium", "High"); empty for free-form scores
    symbol_set: tuple[CheckSymbol, ...]
    _symbol_color_map: dict[str, str] = field(default_factory=dict, compare=False, hash=False)

    def color_for_symbol(self, symbol: str) -> str:
        if not self._symbol_color_map:
            for s in self.symbol_set:
                self._symbol_color_map[s.symbol] = s.color
        return self._symbol_color_map.get(symbol, "#9AA0A6")


CHECKS: tuple[Check, ...] = ()


def _checks_path() -> Path:
    return config.STORE_DIR / "checks.yaml"


def _ensure_checks_file() -> None:
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _checks_path()
    if not path.is_file():
        shutil.copy(_DEFAULTS_DIR / "checks.yaml", path)


def _parse_metric_keys(raw: Any) -> tuple[MetricKey, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[MetricKey] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        k = item.get("key")
        l = item.get("label")
        if isinstance(k, str) and isinstance(l, str) and k.strip():
            out.append(MetricKey(key=k.strip(), label=l.strip()))
    return tuple(out)


def _parse_symbol_set(raw: Any) -> tuple[CheckSymbol, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[CheckSymbol] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol")
        lbl = item.get("label")
        col = item.get("color", "#9AA0A6")
        if isinstance(sym, str) and isinstance(lbl, str):
            out.append(CheckSymbol(symbol=sym, label=lbl, color=str(col)))
    return tuple(out)


def _parse_checks(data: Any) -> tuple[Check, ...]:
    if not isinstance(data, dict):
        raise ValueError("checks.yaml must be a mapping with 'checks' list.")
    raw_list = data.get("checks")
    if not isinstance(raw_list, list) or not raw_list:
        raise ValueError("checks must be a non-empty list.")
    out: list[Check] = []
    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise ValueError(f"checks[{i}] must be a mapping.")
        cid = item.get("id")
        label = item.get("label")
        sp = item.get("system_prompt")
        ut = item.get("user_template")
        if not all(isinstance(x, str) and str(x).strip() for x in (cid, label, sp, ut)):
            raise ValueError(f"checks[{i}] needs id, label, system_prompt, user_template strings.")
        ut_str = str(ut)
        if "{old}" not in ut_str or "{new}" not in ut_str:
            raise ValueError(f"checks[{i}].user_template must contain '{{old}}' and '{{new}}'.")
        accent = str(item.get("accent") or "#5AB0FF")
        symbol_field = str(item.get("symbol_field") or "symbol").strip() or "symbol"
        summary_path = str(item.get("summary_path") or "")
        metrics_path = str(item.get("metrics_path") or "")
        mvs = item.get("metric_value_set")
        metric_value_set: tuple[str, ...] = (
            tuple(str(v) for v in mvs) if isinstance(mvs, list) else ()
        )
        out.append(
            Check(
                id=str(cid).strip(),
                label=str(label).strip(),
                accent=accent,
                system_prompt=str(sp).strip(),
                user_template=ut_str,
                symbol_field=symbol_field,
                summary_path=summary_path,
                metrics_path=metrics_path,
                metric_keys=_parse_metric_keys(item.get("metric_keys")),
                metric_value_set=metric_value_set,
                symbol_set=_parse_symbol_set(item.get("symbol_set")),
            )
        )
    return tuple(out)


def reload() -> None:
    """Reload checks.yaml into CHECKS."""
    global CHECKS
    _ensure_checks_file()
    raw = _checks_path().read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    CHECKS = _parse_checks(data)


def get_check(check_id: str) -> Check | None:
    for c in CHECKS:
        if c.id == check_id:
            return c
    return None


# ---------------------------------------------------------------------------
# Payload extractors (defensive against missing keys / legacy field names).
# ---------------------------------------------------------------------------

# Fallback aliases for the headline symbol field across legacy schemas
# (mirrors old/assistant.py extract_form_data_from_row).
_SYMBOL_FALLBACK_FIELDS: tuple[str, ...] = (
    "symbol",
    "impact_symbol",
    "sustainability_symbol",
    "readability_symbol",
    "virality_symbol",
)


def _walk(payload: Any, path: str) -> Any:
    if not path:
        return payload
    cur: Any = payload
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def extract_symbol(check: Check, payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return "?"
    val = payload.get(check.symbol_field)
    if isinstance(val, str) and val.strip():
        return val.strip()
    for f in _SYMBOL_FALLBACK_FIELDS:
        v = payload.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "?"


def extract_summary(check: Check, payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    val = _walk(payload, check.summary_path) if check.summary_path else None
    if isinstance(val, str):
        return val.strip()
    s = payload.get("summary")
    if isinstance(s, str):
        return s.strip()
    return ""


def extract_metrics(check: Check, payload: dict | None) -> list[tuple[str, str, Any]]:
    """Return ``[(key, label, value), ...]`` for the active metric_keys."""
    if not isinstance(payload, dict) or not check.metric_keys:
        return []
    metrics_obj = _walk(payload, check.metrics_path) if check.metrics_path else payload
    if not isinstance(metrics_obj, dict):
        return []
    out: list[tuple[str, str, Any]] = []
    for mk in check.metric_keys:
        out.append((mk.key, mk.label, metrics_obj.get(mk.key)))
    return out


def extract_recommendations(payload: dict | None, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("recommendations")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = item.get("action") or item.get("recommendation")
        if not isinstance(action, str) or not action.strip():
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


def extract_confidence(payload: dict | None) -> float | None:
    if not isinstance(payload, dict):
        return None
    val = payload.get("confidence")
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, f))


def extract_label(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for f in ("technical_label", "readability_impact", "virality_impact"):
        v = payload.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


reload()
