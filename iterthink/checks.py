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


def _neutral_symbol_for_check(check: Check) -> str:
    """Pick a neutral / no-effect symbol from the check's legend, for unchanged paragraphs."""
    sym_set = {s.symbol for s in check.symbol_set}
    for p in ("~", "●"):
        if p in sym_set:
            return p
    for s in check.symbol_set:
        low = s.label.lower()
        if "neutral" in low or "no meaningful" in low or "no effect" in low:
            return s.symbol
    if "?" in sym_set:
        return "?"
    if check.symbol_set:
        return check.symbol_set[0].symbol
    return "?"


def _set_at_dotted_path(root: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = root
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def unchanged_paragraph_payload(check: Check) -> dict[str, Any]:
    """
    JSON-shaped result used when OLD and NEW hashes match: no LLM call, but eval cells
    and cache use the same shape as ``run_paragraph`` outputs.
    """
    sym = _neutral_symbol_for_check(check)
    msg = "Paragraph text is unchanged; analysis skipped."
    out: dict[str, Any] = {
        check.symbol_field: sym,
        "confidence": 1.0,
        "recommendations": [],
    }
    # DGNB / project-style: metrics object + summary under metrics_path
    if (
        check.metric_keys
        and check.metrics_path
        and check.summary_path
        and "None" in check.metric_value_set
        and check.summary_path.startswith(check.metrics_path + ".")
    ):
        out["technical_label"] = "Editorial change"
        block: dict[str, Any] = {mk.key: "None" for mk in check.metric_keys}
        rel = check.summary_path[len(check.metrics_path) + 1 :]
        _set_at_dotted_path(block, rel, msg)
        _set_at_dotted_path(out, check.metrics_path, block)
        return out

    # Readability / LinkedIn: nested score dicts under *.old / *.new
    if check.metric_keys and check.metrics_path.endswith(".new"):
        root = check.metrics_path[: -len(".new")]
        scores = {mk.key: 0.0 for mk in check.metric_keys}
        out[root] = {"old": dict(scores), "new": dict(scores)}
        if check.id == "readability":
            out["readability_impact"] = "neutral"
        elif check.id == "linkedin_virality":
            out["virality_impact"] = "neutral"
        out["problems"] = []
        if check.summary_path:
            _set_at_dotted_path(out, check.summary_path, msg)
        return out

    if check.summary_path:
        _set_at_dotted_path(out, check.summary_path, msg)
    else:
        out["summary"] = msg
    return out


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


_OVERRIDE_KEY = "_override"


def _override_block(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    ov = payload.get(_OVERRIDE_KEY)
    return ov if isinstance(ov, dict) else None


def is_overridden(payload: dict | None) -> bool:
    ov = _override_block(payload)
    return bool(ov and ov.get("active") is True)


def _primary_recommendation_raw(payload: dict | None) -> str:
    recs = extract_recommendations(payload, limit=1)
    if not recs:
        return ""
    item = recs[0]
    return str(item.get("action") or item.get("recommendation") or "").strip()


def effective_symbol(check: Check, payload: dict | None) -> str:
    ov = _override_block(payload)
    if ov and ov.get("active") is True:
        sym = ov.get("symbol")
        if isinstance(sym, str) and sym.strip():
            return sym.strip()
    return extract_symbol(check, payload)


def effective_primary_recommendation(payload: dict | None) -> str:
    ov = _override_block(payload)
    if ov and ov.get("active") is True:
        rec = ov.get("recommendation")
        if isinstance(rec, str):
            return rec.strip()
    return _primary_recommendation_raw(payload)


def model_symbol(check: Check, payload: dict | None) -> str:
    ov = _override_block(payload)
    if ov and isinstance(ov.get("model_symbol"), str) and ov["model_symbol"].strip():
        return ov["model_symbol"].strip()
    return extract_symbol(check, payload)


def model_primary_recommendation(payload: dict | None) -> str:
    ov = _override_block(payload)
    if ov and isinstance(ov.get("model_recommendation"), str):
        return ov["model_recommendation"].strip()
    return _primary_recommendation_raw(payload)


def model_summary(check: Check, payload: dict | None) -> str:
    ov = _override_block(payload)
    if ov and isinstance(ov.get("model_summary"), str):
        return ov["model_summary"].strip()
    return extract_summary(check, payload)


def model_recommendations(payload: dict | None, *, limit: int = 3) -> list[dict[str, Any]]:
    """Recommendation list as the model returned them (before user override)."""
    if not isinstance(payload, dict):
        return []
    recs = extract_recommendations(payload, limit=limit)
    if not is_overridden(payload):
        return recs
    primary = model_primary_recommendation(payload)
    if not primary and not recs:
        return []
    if not recs:
        return [{"action": primary}]
    first = dict(recs[0])
    first["action"] = primary
    return [first, *recs[1:]]


def _set_primary_recommendation(out: dict[str, Any], recommendation: str) -> None:
    rec = (recommendation or "").strip()
    raw = out.get("recommendations")
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        first = dict(raw[0])
        first["action"] = rec
        out["recommendations"] = [first, *raw[1:]]
    elif rec:
        out["recommendations"] = [{"action": rec}]


def apply_check_override(
    payload: dict[str, Any],
    check: Check,
    *,
    symbol: str,
    recommendation: str,
) -> dict[str, Any]:
    import time

    out = dict(payload)
    sym = str(symbol).strip()
    rec = str(recommendation or "").strip()
    ov = _override_block(out) or {}
    if not ov.get("model_symbol"):
        ov = {
            **ov,
            "model_symbol": extract_symbol(check, out),
            "model_recommendation": _primary_recommendation_raw(out),
            "model_summary": extract_summary(check, out),
        }
    out[_OVERRIDE_KEY] = {
        **ov,
        "active": True,
        "symbol": sym,
        "recommendation": rec,
        "saved_at": time.time(),
    }
    out[check.symbol_field] = sym
    _set_primary_recommendation(out, rec)
    return out


def clear_check_override(payload: dict[str, Any], check: Check) -> dict[str, Any]:
    ov = _override_block(payload)
    if not ov or not ov.get("active"):
        return payload
    out = dict(payload)
    ms = ov.get("model_symbol")
    mr = ov.get("model_recommendation")
    if isinstance(ms, str) and ms.strip():
        out[check.symbol_field] = ms.strip()
    if isinstance(mr, str):
        _set_primary_recommendation(out, mr.strip())
    out.pop(_OVERRIDE_KEY, None)
    return out


def format_prior_override_context(
    check: Check,
    *,
    paragraph_index: int,
    payload: dict[str, Any],
) -> str:
    sym = effective_symbol(check, payload)
    rec = effective_primary_recommendation(payload)
    if not sym and not rec:
        return ""
    line = f"[PRIOR REVIEW] paragraph={paragraph_index + 1} symbol={sym}"
    if rec:
        return f"{line}\nrecommendation={rec}"
    return line


reload()
