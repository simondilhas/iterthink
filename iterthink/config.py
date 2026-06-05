"""Bootstrap YAML (paths, theme, Ollama defaults) plus load/refresh."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULTS_DIR = _PACKAGE_DIR / "defaults"
APP_SYMBOL_PNG = _PACKAGE_DIR / "assets" / "fav.png"
APP_SYMBOL_SVG = _PACKAGE_DIR / "assets" / "fav.svg"


def app_config_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "iterthink"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "iterthink"
        return home / "AppData" / "Roaming" / "iterthink"
    xdg = os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))
    return Path(xdg) / "iterthink"


APP_CONFIG_PATH = app_config_dir() / "config.yaml"

# Filled by refresh()
DOCUMENTS: Path = Path.home() / "Documents"
STORE_DIR: Path = DOCUMENTS / ".iterthink"
STORE_DB_PATH: Path = STORE_DIR / "store.sqlite3"
RAG_DB_PATH: Path = STORE_DIR / "store.rag.sqlite3"
IMPORT_ASSETS_DIR: Path = DOCUMENTS / "iterthink_import_assets"
DEFAULT_OLLAMA_MODEL: str = "llama3:8B"
OLLAMA_HOST: str | None = None

APPEARANCE: str = "dark"
IS_LIGHT: bool = False

PAGE_BG: str = "#0F0F12"
PRIMARY_COLOR: str = "#B8A8D4"
HIGHLIGHT: str = "#D2C4E8"
ON_PRIMARY: str = "#141416"
ON_SURFACE: str = "#F2EEF8"
ON_SURFACE_SOFT: str = "#9A9AA8"
ON_SURFACE_VARIANT: str = "#A8A6B4"
OUTLINE: str = "#3E3E4A"
SUCCESS: str = "#C8E4C4"

SURFACE: str = "#18181C"
SURFACE_VARIANT: str = "#1E1E24"
SIDEBAR_SURFACE: str = "#1E1E24"
CHAT_SYSTEM: str = "You are a prose editor focusing on intent and clarity."
SELECTION_OVERLAY: str = "#88B8A8D4"

STARTUP_DAILY_LOG: bool = True
NEW_NOTE_NAME_TEMPLATE: str = "unnamed-{n}.md"
RAG_SYSTEM: bool = False
RAG_SEARCH_ENABLED: bool = False
RAG_INDEX_ON_STARTUP: bool = False
RAG_OVERLAP_CHARS: int = 200
RAG_RERANKER_ENABLED: bool = False
RAG_RERANKER_MODEL: str = "Xenova/ms-marco-MiniLM-L-6-v2"
RAG_CONTEXT_MAX_CHARS: int = 2400
PRIVACY_SHIELD_ENABLED: bool = True
PRIVACY_SHIELD_HF_REPO: str = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
PRIVACY_SHIELD_HF_FILE: str = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
PRIVACY_SHIELD_CACHE_NAME: str = "qwen-2.5-1.5b.gguf"
PRIVACY_SHIELD_REINJECT: bool = True
PRIVACY_SHIELD_SHOW_MASKED_IN_CHAT: bool = False
PRIVACY_SHIELD_CHUNK_MAX_CHARS: int = 2800
PRIVACY_SHIELD_CHUNK_OVERLAP_PARAGRAPHS: int = 1
TOKEN_COST_PERIOD: str = "year"
PLAN_PDF_IMPORT_ENABLED: bool = False
FOCUS_SELECTION_REVIEW_ACTIONS_ENABLED: bool = False
OCR_ENABLED: bool = False
OCR_ENGINE: str = "rapidocr"
OCR_MODEL: str = "ppocrv4_latin_mobile"


def _bundled_defaults_dict() -> dict[str, Any]:
    path = _DEFAULTS_DIR / "config.yaml"
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid bundled config: {path}")
    return data


def _coerce_rag_system_value(raw: Any) -> bool:
    """Normalize bootstrap ``rag_system`` to bool (unknown shapes default to True)."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return raw != 0
    if isinstance(raw, str):
        s = raw.strip().lower()
        return False if s in ("false", "no", "0", "off") else True
    return True


def _ensure_bootstrap_file() -> None:
    APP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not APP_CONFIG_PATH.is_file():
        shutil.copy(_DEFAULTS_DIR / "config.yaml", APP_CONFIG_PATH)


def _merged_config() -> dict[str, Any]:
    base = _bundled_defaults_dict()
    _ensure_bootstrap_file()
    user_raw = APP_CONFIG_PATH.read_text(encoding="utf-8")
    user = yaml.safe_load(user_raw)
    if user is None:
        user = {}
    if not isinstance(user, dict):
        raise ValueError("App config must be a YAML mapping at the top level.")
    merged = {**base, **user}
    return merged


def _as_path(key: str, merged: dict[str, Any]) -> Path:
    v = merged.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"Missing or invalid {key!r} in config.")
    return Path(v).expanduser().resolve()


def _branch_dict(themes: Any, name: str) -> dict[str, Any]:
    if not isinstance(themes, dict):
        return {}
    b = themes.get(name)
    return b if isinstance(b, dict) else {}


def _merged_theme_variants(merged: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    base = _bundled_defaults_dict()
    base_t = base.get("theme")
    merged_t = merged.get("theme")
    dark = {**_branch_dict(base_t, "dark"), **_branch_dict(merged_t, "dark")}
    light = {**_branch_dict(base_t, "light"), **_branch_dict(merged_t, "light")}
    return dark, light


def _legacy_dark_palette_overrides(merged: dict[str, Any]) -> dict[str, str]:
    """Map legacy flat color keys into dark theme tokens (user files pre-theme block)."""
    out: dict[str, str] = {}
    hc = merged.get("higlight_color")
    if isinstance(hc, str) and hc.strip():
        v = hc.strip()
        out["primary"] = v
        out["highlight"] = v
    hl = merged.get("highlight_color")
    if isinstance(hl, str) and hl.strip():
        out["highlight"] = hl.strip()
    for src, dest in (
        ("surface", "surface"),
        ("sidebar_surface", "sidebar_surface"),
        ("sidebar_surface", "sidebar_surface"),
        ("selection_overlay", "selection_overlay"),
    ):
        x = merged.get(src)
        if isinstance(x, str) and x.strip():
            out[dest] = x.strip()
    return out


def refresh() -> None:
    """Reload bootstrap YAML into module-level settings (paths, colors, defaults)."""
    global DOCUMENTS, STORE_DIR, STORE_DB_PATH, RAG_DB_PATH, IMPORT_ASSETS_DIR
    global DEFAULT_OLLAMA_MODEL, OLLAMA_HOST
    global APPEARANCE, IS_LIGHT
    global PAGE_BG, PRIMARY_COLOR, HIGHLIGHT, ON_PRIMARY, ON_SURFACE, ON_SURFACE_SOFT
    global ON_SURFACE_VARIANT, OUTLINE, SUCCESS
    global SURFACE, SURFACE_VARIANT, SIDEBAR_SURFACE, CHAT_SYSTEM, SELECTION_OVERLAY
    global STARTUP_DAILY_LOG, NEW_NOTE_NAME_TEMPLATE, RAG_SYSTEM, RAG_SEARCH_ENABLED
    global RAG_INDEX_ON_STARTUP, RAG_OVERLAP_CHARS, RAG_RERANKER_ENABLED, RAG_RERANKER_MODEL
    global RAG_CONTEXT_MAX_CHARS
    global PRIVACY_SHIELD_ENABLED, PRIVACY_SHIELD_HF_REPO, PRIVACY_SHIELD_HF_FILE
    global PRIVACY_SHIELD_CACHE_NAME, PRIVACY_SHIELD_REINJECT, PRIVACY_SHIELD_SHOW_MASKED_IN_CHAT
    global PRIVACY_SHIELD_CHUNK_MAX_CHARS, PRIVACY_SHIELD_CHUNK_OVERLAP_PARAGRAPHS
    global TOKEN_COST_PERIOD
    global PLAN_PDF_IMPORT_ENABLED, FOCUS_SELECTION_REVIEW_ACTIONS_ENABLED
    global OCR_ENABLED, OCR_ENGINE, OCR_MODEL

    merged = _merged_config()
    DOCUMENTS = _as_path("documents_root", merged)
    STORE_DIR = _as_path("store_dir", merged)
    STORE_DB_PATH = STORE_DIR / "store.sqlite3"
    RAG_DB_PATH = STORE_DIR / "store.rag.sqlite3"
    IMPORT_ASSETS_DIR = DOCUMENTS / "iterthink_import_assets"

    dm = merged.get("default_ollama_model")
    if not isinstance(dm, str) or not dm.strip():
        raise ValueError("default_ollama_model must be a non-empty string.")

    DEFAULT_OLLAMA_MODEL = (os.environ.get("OLLAMA_MODEL") or dm).strip()

    host_env = os.environ.get("OLLAMA_HOST")
    if host_env is not None and str(host_env).strip() != "":
        OLLAMA_HOST = str(host_env).strip()
    else:
        hy = merged.get("ollama_host")
        if hy is None or (isinstance(hy, str) and hy.strip() == ""):
            OLLAMA_HOST = None
        elif isinstance(hy, str):
            OLLAMA_HOST = hy.strip()
        else:
            OLLAMA_HOST = None

    dark_m, light_m = _merged_theme_variants(merged)
    dark_m = {**dark_m, **_legacy_dark_palette_overrides(merged)}

    raw_ap = merged.get("appearance", "dark")
    ap = raw_ap.strip().lower() if isinstance(raw_ap, str) and raw_ap.strip() else "dark"
    if ap not in ("dark", "light"):
        ap = "dark"
    APPEARANCE = ap
    IS_LIGHT = ap == "light"
    pal = light_m if IS_LIGHT else dark_m

    def _tok(key: str, fallback: str) -> str:
        v = pal.get(key, fallback)
        return v.strip() if isinstance(v, str) and v.strip() else fallback

    PAGE_BG = _tok("page_background", "#0F0F12" if not IS_LIGHT else "#FFFFFF")
    SURFACE = _tok("surface", "#18181C" if not IS_LIGHT else "#FFFFFF")
    SURFACE_VARIANT = _tok("sidebar_surface", "#1E1E24" if not IS_LIGHT else "#F6F6F8")
    SIDEBAR_SURFACE = _tok("sidebar_surface", "#1E1E24" if not IS_LIGHT else "#F6F6F8")
    PRIMARY_COLOR = _tok("primary", "#B8A8D4" if not IS_LIGHT else "#2E2C6E")
    HIGHLIGHT = _tok("highlight", "#D2C4E8" if not IS_LIGHT else "#8B76B8")
    ON_PRIMARY = _tok("on_primary", "#141416" if not IS_LIGHT else "#FFFFFF")
    ON_SURFACE = _tok("on_surface", "#F2EEF8" if not IS_LIGHT else "#1A1A24")
    ON_SURFACE_SOFT = _tok("on_surface_soft", "#9A9AA8" if not IS_LIGHT else "#5A5A68")
    ON_SURFACE_VARIANT = _tok("on_sidebar_surface", "#A8A6B4" if not IS_LIGHT else "#6B6B78")
    OUTLINE = _tok("outline", "#3E3E4A" if not IS_LIGHT else "#C6C6D2")
    SUCCESS = _tok("success", "#C8E4C4")
    SELECTION_OVERLAY = _tok("selection_overlay", "#88B8A8D4" if not IS_LIGHT else "#552E2C6E")

    cs = merged.get("chat_system")
    if isinstance(cs, str) and cs.strip():
        CHAT_SYSTEM = cs.strip()
    else:
        fb = _bundled_defaults_dict().get("chat_system")
        CHAT_SYSTEM = fb.strip() if isinstance(fb, str) and fb.strip() else "You are a prose editor focusing on intent and clarity."

    sdl = merged.get("startup_daily_log", True)
    STARTUP_DAILY_LOG = bool(sdl) if isinstance(sdl, bool) else True

    _bd_rag = _bundled_defaults_dict()
    _bundled_rag = _bd_rag.get("rag_system", False)
    rs = merged.get("rag_system", _bundled_rag)
    if rs is None:
        rs = _bundled_rag
    RAG_SYSTEM = _coerce_rag_system_value(rs)

    rse = merged.get("rag_search_enabled", _bd_rag.get("rag_search_enabled", False))
    RAG_SEARCH_ENABLED = bool(rse) if isinstance(rse, bool) else False

    rio = merged.get("rag_index_on_startup", _bd_rag.get("rag_index_on_startup", True))
    RAG_INDEX_ON_STARTUP = bool(rio) if isinstance(rio, bool) else True

    roc = merged.get("rag_overlap_chars", _bd_rag.get("rag_overlap_chars", 200))
    try:
        RAG_OVERLAP_CHARS = max(0, int(roc))
    except (TypeError, ValueError):
        RAG_OVERLAP_CHARS = 200

    rre = merged.get("rag_reranker_enabled", _bd_rag.get("rag_reranker_enabled", True))
    RAG_RERANKER_ENABLED = bool(rre) if isinstance(rre, bool) else True

    rrm = merged.get("rag_reranker_model", _bd_rag.get("rag_reranker_model", "Xenova/ms-marco-MiniLM-L-6-v2"))
    RAG_RERANKER_MODEL = (
        rrm.strip()
        if isinstance(rrm, str) and rrm.strip()
        else "Xenova/ms-marco-MiniLM-L-6-v2"
    )

    rcm = merged.get("rag_context_max_chars", _bd_rag.get("rag_context_max_chars", 2400))
    try:
        RAG_CONTEXT_MAX_CHARS = max(200, int(rcm))
    except (TypeError, ValueError):
        RAG_CONTEXT_MAX_CHARS = 2400

    nt = merged.get("new_note_name_template")
    if isinstance(nt, str) and nt.strip() and nt.count("{n}") == 1:
        NEW_NOTE_NAME_TEMPLATE = nt.strip()
    else:
        fb_nt = _bundled_defaults_dict().get("new_note_name_template")
        NEW_NOTE_NAME_TEMPLATE = (
            fb_nt.strip()
            if isinstance(fb_nt, str) and fb_nt.strip() and fb_nt.count("{n}") == 1
            else "unnamed-{n}.md"
        )

    _bd = _bundled_defaults_dict()
    pse = merged.get("privacy_shield_enabled", _bd.get("privacy_shield_enabled", True))
    PRIVACY_SHIELD_ENABLED = bool(pse) if isinstance(pse, bool) else True

    phr = merged.get("privacy_shield_hf_repo", _bd.get("privacy_shield_hf_repo", "Qwen/Qwen2.5-1.5B-Instruct-GGUF"))
    PRIVACY_SHIELD_HF_REPO = (
        phr.strip()
        if isinstance(phr, str) and phr.strip()
        else "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
    )

    phf = merged.get("privacy_shield_hf_file", _bd.get("privacy_shield_hf_file", "qwen2.5-1.5b-instruct-q4_k_m.gguf"))
    PRIVACY_SHIELD_HF_FILE = (
        phf.strip()
        if isinstance(phf, str) and phf.strip()
        else "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    )

    pcn = merged.get("privacy_shield_cache_name", _bd.get("privacy_shield_cache_name", "qwen-2.5-1.5b.gguf"))
    PRIVACY_SHIELD_CACHE_NAME = (
        pcn.strip()
        if isinstance(pcn, str) and pcn.strip()
        else "qwen-2.5-1.5b.gguf"
    )

    psr = merged.get("privacy_shield_reinject", _bd.get("privacy_shield_reinject", True))
    PRIVACY_SHIELD_REINJECT = bool(psr) if isinstance(psr, bool) else True

    psm = merged.get("privacy_shield_show_masked_in_chat", _bd.get("privacy_shield_show_masked_in_chat", False))
    PRIVACY_SHIELD_SHOW_MASKED_IN_CHAT = bool(psm) if isinstance(psm, bool) else False

    psc = merged.get("privacy_shield_chunk_max_chars", _bd.get("privacy_shield_chunk_max_chars", 2800))
    try:
        PRIVACY_SHIELD_CHUNK_MAX_CHARS = max(500, int(psc))
    except (TypeError, ValueError):
        PRIVACY_SHIELD_CHUNK_MAX_CHARS = 2800

    pso = merged.get(
        "privacy_shield_chunk_overlap_paragraphs",
        _bd.get("privacy_shield_chunk_overlap_paragraphs", 1),
    )
    try:
        PRIVACY_SHIELD_CHUNK_OVERLAP_PARAGRAPHS = max(0, min(5, int(pso)))
    except (TypeError, ValueError):
        PRIVACY_SHIELD_CHUNK_OVERLAP_PARAGRAPHS = 1

    tcp = merged.get("token_cost_period", _bd.get("token_cost_period", "year"))
    if isinstance(tcp, str) and tcp.strip().lower() in ("day", "month", "year"):
        TOKEN_COST_PERIOD = tcp.strip().lower()
    else:
        TOKEN_COST_PERIOD = "year"

    from iterthink.ocr_settings import normalize_ocr_engine, normalize_ocr_model

    ppie = merged.get("plan_pdf_import_enabled", _bd.get("plan_pdf_import_enabled", False))
    PLAN_PDF_IMPORT_ENABLED = bool(ppie) if isinstance(ppie, bool) else False

    fsra = merged.get(
        "focus_selection_review_actions_enabled",
        _bd.get("focus_selection_review_actions_enabled", False),
    )
    FOCUS_SELECTION_REVIEW_ACTIONS_ENABLED = bool(fsra) if isinstance(fsra, bool) else False

    oce = merged.get("ocr_enabled", _bd.get("ocr_enabled", False))
    OCR_ENABLED = bool(oce) if isinstance(oce, bool) else False

    oeng = merged.get("ocr_engine", _bd.get("ocr_engine", "rapidocr"))
    engine = normalize_ocr_engine(oeng if isinstance(oeng, str) else None)
    OCR_ENGINE = engine

    om = merged.get("ocr_model", _bd.get("ocr_model", "ppocrv4_latin_mobile"))
    OCR_MODEL = normalize_ocr_model(engine, om if isinstance(om, str) else None)


def read_bootstrap_yaml_text() -> str:
    _ensure_bootstrap_file()
    return APP_CONFIG_PATH.read_text(encoding="utf-8")


def write_bootstrap_yaml_text(text: str) -> None:
    APP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict):
        raise ValueError("Config must be a YAML mapping.")
    APP_CONFIG_PATH.write_text(text.rstrip() + "\n", encoding="utf-8")
    refresh()


def merge_bootstrap_paths(*, documents_root: str, store_dir: str) -> None:
    _ensure_bootstrap_file()
    data = yaml.safe_load(APP_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {}
    data["documents_root"] = documents_root.strip()
    data["store_dir"] = store_dir.strip()
    dumped = yaml.safe_dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=88,
    )
    APP_CONFIG_PATH.write_text(dumped, encoding="utf-8")
    refresh()


_ensure_bootstrap_file()
refresh()
