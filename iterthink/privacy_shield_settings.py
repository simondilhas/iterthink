"""Load privacy-shield category config from the store; build regex + LLM instructions."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from iterthink import config

_DEFAULTS_PATH = Path(__file__).resolve().parent / "defaults" / "privacy_shield.yaml"

# Stable Settings UI order within each priority band.
CATEGORY_DISPLAY_ORDER: tuple[str, ...] = (
    "email",
    "phone",
    "address",
    "person",
    "org",
    "password",
    "crypto_wallet",
    "ip_address",
    "url",
    "api_key",
    "credit_card",
    "bank_account",
    "govt_id",
    "project_name",
    "money",
    "date",
)

PRIORITY_SECTION_TITLES: dict[int, str] = {
    1: "Priority 1 — Direct contact information",
    2: "Priority 2 — Credentials & access tokens",
    3: "Priority 3 — Financial & legal identifiers",
    4: "Priority 4 — Corporate & project specifics",
}

# Regex patterns per category id (applied when mode is regex or both).
_CATEGORY_REGEX: dict[str, tuple[re.Pattern[str], ...]] = {
    "email": (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),),
    "phone": (
        re.compile(
            r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{3,4}(?:[\s.-]?\d{1,4})?\b"
        ),
    ),
    "credit_card": (re.compile(r"\b(?:\d[ \t-]*?){13,19}\b"),),
    "bank_account": (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),),
    "govt_id": (
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        re.compile(r"\b[A-Z]{1,2}\d{6,12}\b"),
    ),
    "ip_address": (
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"),
    ),
    "url": (
        re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE),
        re.compile(r"\bwww\.[a-z0-9][-a-z0-9.]+\.[a-z]{2,}[^\s<>\"']*", re.IGNORECASE),
    ),
    "api_key": (
        re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"),
        re.compile(r"\bBearer\s+[a-zA-Z0-9._\-]{16,}\b", re.IGNORECASE),
        re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b"),
        re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b"),
        re.compile(r"\bghp_[0-9A-Za-z]{20,}\b"),
        re.compile(r"\bgho_[0-9A-Za-z]{20,}\b"),
    ),
    "crypto_wallet": (
        re.compile(r"\b0x[a-fA-F0-9]{40}\b"),
        re.compile(r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,39}\b"),
    ),
    "password": (
        re.compile(
            r"(?i)\b(?:password|passwd|passphrase|pwd)\s*[:=]\s*['\"]?[^\s'\"]{4,}['\"]?"
        ),
    ),
    "money": (
        re.compile(
            r"(?:\$|€|£|CHF)\s?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?\b|"
            r"\b\d{1,3}(?:[,\s]\d{3})+(?:\.\d{2})?\s?(?:USD|EUR|CHF|GBP)\b",
            re.IGNORECASE,
        ),
    ),
    "date": (
        re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
        re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"),
        re.compile(
            r"\b(?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
            re.IGNORECASE,
        ),
    ),
}

_LLM_CATEGORY_HINTS: dict[str, str] = {
    "person": "person names",
    "org": "company and organisation names",
    "address": "physical addresses (streets, zip codes, office locations)",
    "password": "passwords and passphrases in prose",
    "project_name": "internal product or project codenames",
    "money": "specific monetary amounts not caught by regex",
    "date": "specific calendar dates not caught by regex",
}


@dataclass(frozen=True)
class PrivacyCategory:
    id: str
    label: str
    priority: int
    placeholder: str
    mode: str  # regex | llm | both
    enabled: bool
    description: str = ""

    def example_token(self, n: int = 1) -> str:
        return format_placeholder(self.placeholder, n)


def format_placeholder(prefix: str, n: int) -> str:
    return "{{" + f"{prefix}_{n}" + "}}"


def _shield_yaml_path() -> Path:
    return config.STORE_DIR / "privacy_shield.yaml"


def _ensure_store_file() -> Path:
    path = _shield_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        shutil.copy2(_DEFAULTS_PATH, path)
    return path


def _parse_categories(raw: Any) -> dict[str, PrivacyCategory]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, PrivacyCategory] = {}
    for cid, item in raw.items():
        if not isinstance(item, dict):
            continue
        ph = str(item.get("placeholder") or cid).strip().upper()
        mode = str(item.get("mode") or "llm").strip().lower()
        if mode not in ("regex", "llm", "both"):
            mode = "llm"
        out[str(cid)] = PrivacyCategory(
            id=str(cid),
            label=str(item.get("label") or cid),
            priority=int(item.get("priority") or 1),
            placeholder=ph,
            mode=mode,
            enabled=bool(item.get("enabled", True)),
            description=str(item.get("description") or ""),
        )
    return out


_cached: dict[str, PrivacyCategory] | None = None


def _bundled_categories() -> dict[str, PrivacyCategory]:
    raw = yaml.safe_load(_DEFAULTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    return _parse_categories(raw.get("categories"))


def _merge_with_bundled(store: dict[str, PrivacyCategory]) -> dict[str, PrivacyCategory]:
    """Keep user enabled flags; add missing categories and refresh labels from bundled defaults."""
    bundled = _bundled_categories()
    if not bundled:
        return store
    out: dict[str, PrivacyCategory] = {}
    for cid, default in bundled.items():
        if cid in store:
            s = store[cid]
            out[cid] = PrivacyCategory(
                id=cid,
                label=default.label,
                priority=default.priority,
                placeholder=default.placeholder,
                mode=default.mode,
                enabled=s.enabled,
                description=default.description,
            )
        else:
            out[cid] = default
    for cid, s in store.items():
        if cid not in out:
            out[cid] = s
    return out


def _sort_key(cat: PrivacyCategory) -> tuple[int, int, str]:
    try:
        order_ix = CATEGORY_DISPLAY_ORDER.index(cat.id)
    except ValueError:
        order_ix = 999
    return (cat.priority, order_ix, cat.id)


def categories_for_ui() -> list[PrivacyCategory]:
    return sorted(load_categories().values(), key=_sort_key)


def load_categories(*, reload: bool = False) -> dict[str, PrivacyCategory]:
    global _cached
    if _cached is not None and not reload:
        return _cached
    _ensure_store_file()
    data = yaml.safe_load(_shield_yaml_path().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {}
    store_cats = _parse_categories(data.get("categories"))
    bundled = _bundled_categories()
    cats = _merge_with_bundled(store_cats) if store_cats else bundled
    if not cats:
        cats = bundled
    _cached = cats
    return cats


def save_categories(categories: dict[str, PrivacyCategory]) -> None:
    path = _ensure_store_file()
    rows: dict[str, dict[str, Any]] = {}
    for cid, c in sorted(categories.items(), key=lambda x: (x[1].priority, x[0])):
        row: dict[str, Any] = {
            "label": c.label,
            "priority": c.priority,
            "placeholder": c.placeholder,
            "mode": c.mode,
            "enabled": c.enabled,
        }
        if c.description:
            row["description"] = c.description
        rows[cid] = row
    text = yaml.safe_dump(
        {"categories": rows},
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=88,
    )
    path.write_text(text, encoding="utf-8")
    load_categories(reload=True)


def enabled_categories() -> list[PrivacyCategory]:
    return sorted([c for c in load_categories().values() if c.enabled], key=_sort_key)


def regex_categories() -> list[PrivacyCategory]:
    return [c for c in enabled_categories() if c.mode in ("regex", "both") and c.id in _CATEGORY_REGEX]


def llm_categories() -> list[PrivacyCategory]:
    return [c for c in enabled_categories() if c.mode in ("llm", "both")]


_ENTITY_TYPE_ALIASES: dict[str, str] = {
    "person": "person",
    "name": "person",
    "human": "person",
    "org": "org",
    "organization": "org",
    "organisation": "org",
    "company": "org",
    "address": "address",
    "location": "address",
    "street": "address",
    "project_name": "project_name",
    "project": "project_name",
    "password": "password",
    "money": "money",
    "date": "date",
}


def placeholder_prefix_for_entity(type_key: str, placeholder_hint: str = "") -> str:
    """Map LLM entity type (or hint token) to configured placeholder prefix."""
    hint = (placeholder_hint or "").strip()
    m = re.match(r"\{\{([A-Z][A-Z0-9_]*?)_\d+\}\}", hint)
    if m and m.group(1) not in ("REDACTED",):
        return m.group(1)

    t = (type_key or "").strip().lower()
    cid = _ENTITY_TYPE_ALIASES.get(t, t)
    cats = load_categories()
    if cid in cats and cats[cid].enabled:
        return cats[cid].placeholder

    for c in llm_categories():
        if c.id == cid or c.placeholder.lower() == t:
            return c.placeholder
    return "PERSON"


def build_redact_system_prompt() -> str:
    llm_cats = llm_categories()
    lines = [
        "You redact sensitive text. Reply with JSON only, no markdown.",
        'Schema: {"redacted_text": string, "entities": [{"placeholder": string, "value": string, "type": string}]}.',
        "Never use {{REDACTED}} or other generic placeholders.",
        "Use placeholders exactly as listed (preserve them verbatim in redacted_text).",
        "List every redacted span in entities with its exact original value and type.",
    ]
    if llm_cats:
        lines.append("Replace these types:")
        for c in llm_cats:
            hint = _LLM_CATEGORY_HINTS.get(c.id, c.label.lower())
            lines.append(f"- {hint} → {c.example_token(1)} (increment n per occurrence)")
    regex_cats = regex_categories()
    if regex_cats:
        preserved = ", ".join(c.example_token(1) for c in regex_cats)
        lines.append(f"Do not alter tokens already present from regex pass: {preserved}.")
    return " ".join(lines)


def regex_redact_configured(text: str) -> tuple[str, dict[str, str]]:
    """Apply enabled regex categories; returns (text, placeholder→value)."""
    out = text
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}

    for cat in regex_categories():
        patterns = _CATEGORY_REGEX.get(cat.id, ())
        for pat in patterns:
            while True:
                m = pat.search(out)
                if not m:
                    break
                val = m.group(0)
                n = counters.get(cat.placeholder, 0) + 1
                counters[cat.placeholder] = n
                ph = format_placeholder(cat.placeholder, n)
                mapping[ph] = val
                out = out[: m.start()] + ph + out[m.end() :]
    return out, mapping
