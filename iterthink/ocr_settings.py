"""OCR engine presets and config normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OcrEngine = Literal["rapidocr", "ollama"]

DEFAULT_OCR_ENGINE: OcrEngine = "rapidocr"
DEFAULT_OCR_MODEL = "ppocrv4_latin_mobile"
DEFAULT_OLLAMA_OCR_MODEL = "llava"

RAPIDOCR_PRESETS = frozenset(
    {
        "ppocrv4_latin_mobile",
        "ppocrv5_latin_mobile",
        "ppocrv4_mobile_ch",
    }
)


@dataclass(frozen=True)
class RapidOcrPreset:
    id: str
    label: str


RAPIDOCR_PRESET_OPTIONS: tuple[RapidOcrPreset, ...] = (
    RapidOcrPreset("ppocrv4_latin_mobile", "PP-OCRv4 Latin (EN/DE/FR/IT)"),
    RapidOcrPreset("ppocrv5_latin_mobile", "PP-OCRv5 Latin (EN/DE/FR/IT)"),
    RapidOcrPreset("ppocrv4_mobile_ch", "PP-OCRv4 Chinese/English"),
)


def normalize_ocr_engine(raw: str | None) -> OcrEngine:
    s = (raw or "").strip().lower()
    if s == "ollama":
        return "ollama"
    return "rapidocr"


def default_model_for_engine(engine: OcrEngine) -> str:
    if engine == "ollama":
        return DEFAULT_OLLAMA_OCR_MODEL
    return DEFAULT_OCR_MODEL


def normalize_ocr_model(engine: OcrEngine, raw: str | None) -> str:
    s = (raw or "").strip()
    if engine == "rapidocr":
        if s in RAPIDOCR_PRESETS:
            return s
        return DEFAULT_OCR_MODEL
    if s:
        return s
    return DEFAULT_OLLAMA_OCR_MODEL


def is_image_import_extension(ext: str | None) -> bool:
    return ext in IMAGE_IMPORT_EXTENSIONS if ext else False


IMAGE_IMPORT_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})
