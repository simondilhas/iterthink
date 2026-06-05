"""Local ONNX OCR via RapidOCR (lazy model download)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iterthink import config
from iterthink.ocr_settings import DEFAULT_OCR_MODEL
from iterthink.persistence import store_db

_engine: Any = None
_engine_preset: str | None = None


def ocr_models_root() -> Path:
    return config.STORE_DIR / "embedded_models" / "ocr"


def _rapidocr_params(preset: str) -> dict[str, Any]:
    from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion

    base = {
        "Det.engine_type": EngineType.ONNXRUNTIME,
        "Det.model_type": ModelType.MOBILE,
        "Rec.engine_type": EngineType.ONNXRUNTIME,
        "Rec.model_type": ModelType.MOBILE,
    }
    if preset == "ppocrv5_latin_mobile":
        return {
            **base,
            "Det.lang_type": LangDet.CH,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Rec.lang_type": LangRec.LATIN,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
        }
    if preset == "ppocrv4_mobile_ch":
        return {
            **base,
            "Det.lang_type": LangDet.CH,
            "Det.ocr_version": OCRVersion.PPOCRV4,
            "Rec.lang_type": LangRec.CH,
            "Rec.ocr_version": OCRVersion.PPOCRV4,
        }
    # ppocrv4_latin_mobile (default)
    return {
        **base,
        "Det.lang_type": LangDet.EN,
        "Det.ocr_version": OCRVersion.PPOCRV4,
        "Rec.lang_type": LangRec.LATIN,
        "Rec.ocr_version": OCRVersion.PPOCRV4,
    }


def _get_engine() -> Any:
    global _engine, _engine_preset
    preset = config.OCR_MODEL if config.OCR_ENGINE == "rapidocr" else DEFAULT_OCR_MODEL
    if _engine is not None and _engine_preset == preset:
        return _engine
    from rapidocr import RapidOCR

    store_db.ensure_store_dir()
    root = ocr_models_root()
    root.mkdir(parents=True, exist_ok=True)
    _engine = RapidOCR(params=_rapidocr_params(preset))
    _engine_preset = preset
    return _engine


def prepare_runtime_ocr_model_sync() -> None:
    """Download ONNX weights if missing (constructs RapidOCR once)."""
    _get_engine()


def _text_from_result(result: Any) -> str:
    if result is None:
        return ""
    txts = getattr(result, "txts", None)
    if txts:
        return "\n".join(str(t).strip() for t in txts if str(t).strip())
    if isinstance(result, (list, tuple)):
        lines: list[str] = []
        for item in result:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                t = str(item[1]).strip()
                if t:
                    lines.append(t)
        return "\n".join(lines)
    return str(result).strip()


def ocr_image_sync(image: Path | Any) -> str:
    """OCR a PIL image or filesystem path; returns plain text."""
    engine = _get_engine()
    result = engine(str(image) if isinstance(image, Path) else image)
    if isinstance(result, tuple) and result:
        result = result[0]
    return _text_from_result(result)
