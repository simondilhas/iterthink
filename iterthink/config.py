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
FEDORA_BLUE: str = "#007BFF"
SURFACE: str = "#1E1E1E"
SURFACE_VARIANT: str = "#2D2D2D"
SIDEBAR_SURFACE: str = "#2A2D32"
CHAT_SYSTEM: str = "You are a prose editor focusing on intent and clarity."
SELECTION_OVERLAY: str = "#59007BFF"


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


def refresh() -> None:
    """Reload bootstrap YAML into module-level settings (paths, colors, defaults)."""
    global DOCUMENTS, STORE_DIR, STORE_DB_PATH
    global DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_EMBED_MODEL, OLLAMA_HOST
    global FEDORA_BLUE, SURFACE, SURFACE_VARIANT, SIDEBAR_SURFACE, CHAT_SYSTEM, SELECTION_OVERLAY

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

    def _str_key(k: str, fallback: str) -> str:
        v = merged.get(k, fallback)
        return v if isinstance(v, str) and v else fallback

    FEDORA_BLUE = _str_key("higlight_color", "#007BFF")
    SURFACE = _str_key("surface", "#1E1E1E")
    SURFACE_VARIANT = _str_key("surface_variant", "#2D2D2D")
    SIDEBAR_SURFACE = _str_key("sidebar_surface", "#2A2D32")
    SELECTION_OVERLAY = _str_key("selection_overlay", "#59007BFF")
    cs = merged.get("chat_system")
    if isinstance(cs, str) and cs.strip():
        CHAT_SYSTEM = cs.strip()
    else:
        fb = _bundled_defaults_dict().get("chat_system")
        CHAT_SYSTEM = fb.strip() if isinstance(fb, str) and fb.strip() else "You are a prose editor focusing on intent and clarity."


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
