"""Settings → Import / OCR tab."""

from __future__ import annotations

from typing import Any, Callable

import flet as ft
import yaml

from iterthink import config
from iterthink.ai.ollama_models import classify_vision_models
from iterthink.ai.ollama_ocr import check_ollama_ocr_ready
from iterthink.ocr_settings import (
    RAPIDOCR_PRESET_OPTIONS,
    default_model_for_engine,
    normalize_ocr_engine,
    normalize_ocr_model,
)


def _ctrl_on_page(ctrl: ft.Control) -> bool:
    try:
        return ctrl.page is not None
    except RuntimeError:
        return False


def build_ocr_settings_tab(
    *,
    studio: Any,
    bootstrap_data: Callable[[], dict],
    on_saved: Callable[[], None] | None = None,
) -> ft.Container:
    _bd0 = bootstrap_data()

    _oe0 = _bd0.get("ocr_enabled", False)
    if not isinstance(_oe0, bool):
        _oe0 = False
    ocr_enabled_sw = ft.Switch(label="OCR enabled", value=_oe0)

    _eng0 = normalize_ocr_engine(
        _bd0.get("ocr_engine") if isinstance(_bd0.get("ocr_engine"), str) else None
    )
    engine_dd = ft.Dropdown(
        label="OCR engine",
        value=_eng0,
        options=[
            ft.dropdown.Option("rapidocr", "RapidOCR (local download)"),
            ft.dropdown.Option("ollama", "Ollama vision"),
        ],
        dense=True,
        width=280,
    )

    _mod0 = normalize_ocr_model(_eng0, _bd0.get("ocr_model") if isinstance(_bd0.get("ocr_model"), str) else None)

    rapidocr_model_dd = ft.Dropdown(
        label="RapidOCR model",
        value=_mod0 if _mod0 in {p.id for p in RAPIDOCR_PRESET_OPTIONS} else "ppocrv4_latin_mobile",
        options=[ft.dropdown.Option(p.id, p.label) for p in RAPIDOCR_PRESET_OPTIONS],
        dense=True,
        width=320,
        visible=_eng0 == "rapidocr",
    )

    ollama_model_tf = ft.TextField(
        label="Ollama vision model",
        hint_text="e.g. llava — host is set under Models",
        value=_mod0 if _eng0 == "ollama" else default_model_for_engine("ollama"),
        dense=True,
        expand=True,
        visible=_eng0 == "ollama",
    )

    ollama_status = ft.Text("", size=12, color=config.ON_SURFACE_VARIANT)

    def _sync_engine_controls() -> None:
        eng = normalize_ocr_engine(engine_dd.value)
        rapidocr_model_dd.visible = eng == "rapidocr"
        ollama_model_tf.visible = eng == "ollama"
        for c in (rapidocr_model_dd, ollama_model_tf):
            if _ctrl_on_page(c):
                c.update()

    def _on_engine_change(_e: ft.ControlEvent | None = None) -> None:
        _sync_engine_controls()

    engine_dd.on_change = _on_engine_change

    async def _refresh_ollama_status() -> None:
        if normalize_ocr_engine(engine_dd.value) != "ollama":
            ollama_status.value = ""
            if _ctrl_on_page(ollama_status):
                ollama_status.update()
            return
        model = (ollama_model_tf.value or "").strip() or default_model_for_engine("ollama")
        ok, msg = await check_ollama_ocr_ready(studio.ollama, model)
        ollama_status.value = msg if ok else f"Not ready: {msg}"
        if _ctrl_on_page(ollama_status):
            ollama_status.update()

    async def refresh_vision_models(_e: ft.ControlEvent | None = None) -> None:
        if normalize_ocr_engine(engine_dd.value) != "ollama":
            return
        try:
            names = await classify_vision_models(studio.ollama)
        except BaseException as ex:
            studio._snack(f"Could not list Ollama vision models: {ex}")
            await _refresh_ollama_status()
            return
        if names and not (ollama_model_tf.value or "").strip():
            ollama_model_tf.value = names[0]
            if _ctrl_on_page(ollama_model_tf):
                ollama_model_tf.update()
        await _refresh_ollama_status()

    async def save_ocr_settings(_e: ft.ControlEvent | None = None) -> None:
        try:
            data = bootstrap_data()
        except (OSError, yaml.YAMLError) as ex:
            studio._snack(f"Could not read app config: {ex}")
            return
        eng = normalize_ocr_engine(engine_dd.value)
        if eng == "rapidocr":
            model = normalize_ocr_model("rapidocr", rapidocr_model_dd.value)
        else:
            model = normalize_ocr_model("ollama", ollama_model_tf.value)
        data["ocr_enabled"] = bool(ocr_enabled_sw.value)
        data["ocr_engine"] = eng
        data["ocr_model"] = model
        try:
            config.write_bootstrap_yaml_text(
                yaml.safe_dump(
                    data,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                    width=88,
                )
            )
        except (OSError, ValueError, yaml.YAMLError) as ex:
            studio._snack(f"Could not save OCR settings: {ex}")
            return
        studio._snack("OCR settings saved.")
        if on_saved:
            on_saved()
        await _refresh_ollama_status()

    studio._ocr_settings_refresh_status = _refresh_ollama_status

    return ft.Container(
        content=ft.Column(
            [
                ft.Text("Import OCR", weight=ft.FontWeight.W_600, size=14),
                ocr_enabled_sw,
                engine_dd,
                rapidocr_model_dd,
                ollama_model_tf,
                ft.Row(
                    [
                        ft.OutlinedButton(
                            "Refresh Ollama vision list",
                            on_click=refresh_vision_models,
                        ),
                    ],
                    visible=_eng0 == "ollama",
                ),
                ollama_status,
                ft.FilledButton("Save import OCR", on_click=save_ocr_settings),
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
        ),
        padding=ft.padding.only(left=8, top=4, right=8, bottom=8),
    )
