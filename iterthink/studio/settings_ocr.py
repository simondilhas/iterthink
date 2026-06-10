"""Settings → Import tab (OCR + document-function classification)."""

from __future__ import annotations

from typing import Any, Callable

import flet as ft
import yaml

from iterthink import config
from iterthink.ai.ollama_models import classify_installed_models, classify_vision_models
from iterthink.ai.ollama_ocr import check_ollama_ocr_ready
from iterthink.ai.ollama_plan_impact import check_plan_impact_vision_ready
from iterthink.import_classification_settings import normalize_import_classification_tier
from iterthink.ocr_settings import (
    RAPIDOCR_PRESET_OPTIONS,
    default_model_for_engine,
    normalize_ocr_engine,
    normalize_ocr_model,
    normalize_plan_region_impact_vision_model,
)
from iterthink.studio.llm_backend import (
    build_llm_tier_tabs,
    ki_tier_index_from_change_event,
    sync_llm_tier_tab_icons,
)
from iterthink.studio.util import KI_TIER_LOCAL, KI_TIERS, normalize_ki_tier


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

    _prim0 = _bd0.get("plan_region_impact_vision_model")
    if not isinstance(_prim0, str) or not _prim0.strip():
        _prim0 = config.PLAN_REGION_IMPACT_VISION_MODEL
    _prim0 = normalize_plan_region_impact_vision_model(_prim0)
    plan_impact_model_dd = ft.Dropdown(
        label="Region impact vision model",
        options=[],
        value=_prim0,
        dense=True,
        expand=True,
    )
    plan_impact_status = ft.Text("", size=12, color=config.ON_SURFACE_VARIANT)

    _icllm0 = _bd0.get("import_classification_llm_enabled", False)
    if not isinstance(_icllm0, bool):
        _icllm0 = False
    import_llm_sw = ft.Switch(
        label="LLM assist for document function",
        value=_icllm0,
        tooltip="When unsure, send a short excerpt to the model below (off = path/KBOB only).",
    )

    _ict0 = normalize_import_classification_tier(
        _bd0.get("import_classification_tier")
        if isinstance(_bd0.get("import_classification_tier"), str)
        else None
    )
    _icm0 = _bd0.get("import_classification_model")
    if not isinstance(_icm0, str):
        _icm0 = ""

    tier_ix = KI_TIERS.index(_ict0) if _ict0 in KI_TIERS else 0
    selected_tier: dict[str, str] = {"value": _ict0}

    def _on_tier_change(e: ft.ControlEvent) -> None:
        ix = ki_tier_index_from_change_event(e, fallback=tier_ix)
        if ix is None:
            return
        selected_tier["value"] = KI_TIERS[ix]
        sync_llm_tier_tab_icons(class_tier_tabs, selected_index=ix)
        _sync_class_model_controls()

    class_tier_tabs = build_llm_tier_tabs(
        selected_index=tier_ix,
        on_change=_on_tier_change,
        icon_size=18,
        tab_bar_height=40.0,
    )

    class_local_model_dd = ft.Dropdown(
        label="Local model",
        options=[],
        value=None,
        dense=True,
        expand=True,
        visible=_ict0 == KI_TIER_LOCAL,
    )

    class_company_model_tf = ft.TextField(
        label="Office model",
        value=studio.company_openai_model or "",
        dense=True,
        expand=True,
        visible=_ict0 == "company",
    )

    class_cloud_model_tf = ft.TextField(
        label="Cloud model",
        value=(
            studio.cloud_anthropic_model
            if studio.cloud_vendor == "anthropic"
            else studio.cloud_openai_model
            if studio.cloud_vendor == "openai"
            else studio.cloud_google_model
        )
        or "",
        dense=True,
        expand=True,
        visible=_ict0 == "cloud",
    )

    class_model_status = ft.Text("", size=12, color=config.ON_SURFACE_VARIANT)

    def _sync_class_model_controls() -> None:
        t = normalize_import_classification_tier(selected_tier["value"])
        class_local_model_dd.visible = t == KI_TIER_LOCAL
        class_company_model_tf.visible = t == "company"
        class_cloud_model_tf.visible = t == "cloud"
        for c in (class_local_model_dd, class_company_model_tf, class_cloud_model_tf):
            if _ctrl_on_page(c):
                c.update()

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

    async def _refresh_plan_impact_status() -> None:
        model = normalize_plan_region_impact_vision_model(plan_impact_model_dd.value)
        ok, msg = await check_plan_impact_vision_ready(studio.ollama, model)
        plan_impact_status.value = msg if ok else f"Not ready: {msg}"
        if _ctrl_on_page(plan_impact_status):
            plan_impact_status.update()

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

    async def refresh_plan_impact_vision_models(_e: ft.ControlEvent | None = None) -> None:
        try:
            names = await classify_vision_models(studio.ollama)
        except BaseException as ex:
            plan_impact_status.value = str(ex)
            if _ctrl_on_page(plan_impact_status):
                plan_impact_status.update()
            studio._snack(f"Could not list Ollama vision models: {ex}")
            return
        cur = normalize_plan_region_impact_vision_model(plan_impact_model_dd.value or _prim0)
        opts = list(names)
        if cur not in opts:
            opts.insert(0, cur)
        plan_impact_model_dd.options = [ft.dropdown.Option(n) for n in opts]
        if cur in opts:
            plan_impact_model_dd.value = cur
        elif opts:
            plan_impact_model_dd.value = opts[0]
        if _ctrl_on_page(plan_impact_model_dd):
            plan_impact_model_dd.update()
        await _refresh_plan_impact_status()

    async def refresh_class_local_models(_e: ft.ControlEvent | None = None) -> None:
        try:
            chat_opts, _ = await classify_installed_models(studio.ollama)
        except BaseException as ex:
            class_model_status.value = str(ex)
            if _ctrl_on_page(class_model_status):
                class_model_status.update()
            return
        class_local_model_dd.options = [ft.dropdown.Option(n) for n in chat_opts]
        cur = (_icm0 or config.DEFAULT_OLLAMA_MODEL).strip()
        if cur in chat_opts:
            class_local_model_dd.value = cur
        elif chat_opts:
            class_local_model_dd.value = chat_opts[0]
        class_model_status.value = f"{len(chat_opts)} local model(s)" if chat_opts else "No local models"
        if _ctrl_on_page(class_local_model_dd):
            class_local_model_dd.update()
            class_model_status.update()

    async def save_import_settings(_e: ft.ControlEvent | None = None) -> None:
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
        data["import_classification_llm_enabled"] = bool(import_llm_sw.value)
        data["import_classification_tier"] = normalize_import_classification_tier(selected_tier["value"])
        tier = data["import_classification_tier"]
        if tier == KI_TIER_LOCAL:
            data["import_classification_model"] = (class_local_model_dd.value or "").strip()
        elif tier == "company":
            data["import_classification_model"] = (class_company_model_tf.value or "").strip()
        else:
            data["import_classification_model"] = (class_cloud_model_tf.value or "").strip()
        data["plan_region_impact_vision_model"] = normalize_plan_region_impact_vision_model(
            plan_impact_model_dd.value
        )
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
            config.refresh()
        except (OSError, ValueError, yaml.YAMLError) as ex:
            studio._snack(f"Could not save import settings: {ex}")
            return
        studio._snack("Import settings saved.")
        if on_saved:
            on_saved()
        await _refresh_ollama_status()
        await _refresh_plan_impact_status()

    studio._ocr_settings_refresh_status = _refresh_ollama_status

    async def _init_import_tab() -> None:
        await refresh_class_local_models()
        await refresh_plan_impact_vision_models()

    studio.page.run_task(_init_import_tab)

    return ft.Container(
        content=ft.Column(
            [
                ft.Text("Document function", weight=ft.FontWeight.W_600, size=14),
                import_llm_sw,
                class_tier_tabs,
                class_local_model_dd,
                class_company_model_tf,
                class_cloud_model_tf,
                ft.Row(
                    [
                        ft.OutlinedButton(
                            "Refresh local models",
                            on_click=refresh_class_local_models,
                        ),
                    ],
                ),
                class_model_status,
                ft.Divider(height=1),
                ft.Text("Plan review", weight=ft.FontWeight.W_600, size=14),
                plan_impact_model_dd,
                ft.Row(
                    [
                        ft.OutlinedButton(
                            "Refresh Ollama vision list",
                            on_click=refresh_plan_impact_vision_models,
                        ),
                    ],
                ),
                plan_impact_status,
                ft.Divider(height=1),
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
                ft.FilledButton("Save import settings", on_click=save_import_settings),
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
        ),
        padding=ft.padding.only(left=8, top=4, right=8, bottom=8),
    )
