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
DEFAULT_OLLAMA_MODEL: str = "llama3:8B"
DEFAULT_OLLAMA_EMBED_MODEL: str = "nomic-embed-text-v2-moe"
OLLAMA_HOST: str | None = None

APPEARANCE: str = "dark"
IS_LIGHT: bool = False

PAGE_BG: str = "#230F33"
PRIMARY_COLOR: str = "#B38FC1"
HIGHLIGHT: str = "#B38FC1"
ON_PRIMARY: str = "#FFFFFF"
ON_SURFACE: str = "#FFFFFF"
ON_SURFACE_SOFT: str = "#B8CEE8"
ON_SURFACE_VARIANT: str = "#959799"
OUTLINE: str = "#959799"
SUCCESS: str = "#C8E4C4"

SURFACE: str = "#230F33"
SURFACE_VARIANT: str = "#1A0A26"
SIDEBAR_SURFACE: str = "#1A0A26"
CHAT_SYSTEM: str = "You are a prose editor focusing on intent and clarity."
SELECTION_OVERLAY: str = "#88B38FC1"

STARTUP_DAILY_LOG: bool = True
NEW_NOTE_NAME_TEMPLATE: str = "unnamed-{n}.md"


def _bundled_defaults_dict() -> dict[str, Any]:
    path = _DEFAULTS_DIR / "config.yaml"
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid bundled config: {path}")
    return data


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
    global DOCUMENTS, STORE_DIR, STORE_DB_PATH
    global DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_EMBED_MODEL, OLLAMA_HOST
    global APPEARANCE, IS_LIGHT
    global PAGE_BG, PRIMARY_COLOR, HIGHLIGHT, ON_PRIMARY, ON_SURFACE, ON_SURFACE_SOFT
    global ON_SURFACE_VARIANT, OUTLINE, SUCCESS
    global SURFACE, SURFACE_VARIANT, SIDEBAR_SURFACE, CHAT_SYSTEM, SELECTION_OVERLAY
    global STARTUP_DAILY_LOG, NEW_NOTE_NAME_TEMPLATE

    merged = _merged_config()
    DOCUMENTS = _as_path("documents_root", merged)
    STORE_DIR = _as_path("store_dir", merged)
    STORE_DB_PATH = STORE_DIR / "store.sqlite3"

    dm = merged.get("default_ollama_model")
    em = merged.get("default_ollama_embed_model")
    if not isinstance(dm, str) or not dm.strip():
        raise ValueError("default_ollama_model must be a non-empty string.")
    if not isinstance(em, str) or not em.strip():
        raise ValueError("default_ollama_embed_model must be a non-empty string.")

    DEFAULT_OLLAMA_MODEL = (os.environ.get("OLLAMA_MODEL") or dm).strip()
    DEFAULT_OLLAMA_EMBED_MODEL = (os.environ.get("OLLAMA_EMBED_MODEL") or em).strip()

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

    PAGE_BG = _tok("page_background", "#230F33" if not IS_LIGHT else "#FFFFFF")
    SURFACE = _tok("surface", "#230F33" if not IS_LIGHT else "#FFFFFF")
    SURFACE_VARIANT = _tok("sidebar_surface", "#1A0A26" if not IS_LIGHT else "#F8F9FA")
    SIDEBAR_SURFACE = _tok("sidebar_surface", "#1A0A26" if not IS_LIGHT else "#F8F9FA")
    PRIMARY_COLOR = _tok("primary", "#B38FC1" if not IS_LIGHT else "#312F8A")
    HIGHLIGHT = _tok("highlight", PRIMARY_COLOR)
    ON_PRIMARY = _tok("on_primary", "#FFFFFF")
    ON_SURFACE = _tok("on_surface", "#FFFFFF" if not IS_LIGHT else "#230F33")
    ON_SURFACE_SOFT = _tok("on_surface_soft", "#B8CEE8" if not IS_LIGHT else "#230F33")
    ON_SURFACE_VARIANT = _tok("on_sidebar_surface", "#959799")
    OUTLINE = _tok("outline", "#959799")
    SUCCESS = _tok("success", "#C8E4C4")
    SELECTION_OVERLAY = _tok("selection_overlay", "#88B38FC1" if not IS_LIGHT else "#55312F8A")

    cs = merged.get("chat_system")
    if isinstance(cs, str) and cs.strip():
        CHAT_SYSTEM = cs.strip()
    else:
        fb = _bundled_defaults_dict().get("chat_system")
        CHAT_SYSTEM = fb.strip() if isinstance(fb, str) and fb.strip() else "You are a prose editor focusing on intent and clarity."

    sdl = merged.get("startup_daily_log", True)
    STARTUP_DAILY_LOG = bool(sdl) if isinstance(sdl, bool) else True

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
