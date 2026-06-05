"""OCR config normalization."""

from iterthink.ocr_settings import (
    DEFAULT_OCR_MODEL,
    DEFAULT_OLLAMA_OCR_MODEL,
    default_model_for_engine,
    normalize_ocr_engine,
    normalize_ocr_model,
)


def test_normalize_ocr_engine_defaults() -> None:
    assert normalize_ocr_engine(None) == "rapidocr"
    assert normalize_ocr_engine("ollama") == "ollama"
    assert normalize_ocr_engine("unknown") == "rapidocr"


def test_normalize_ocr_model_rapidocr() -> None:
    assert normalize_ocr_model("rapidocr", "ppocrv5_latin_mobile") == "ppocrv5_latin_mobile"
    assert normalize_ocr_model("rapidocr", "bad") == DEFAULT_OCR_MODEL


def test_normalize_ocr_model_ollama() -> None:
    assert normalize_ocr_model("ollama", "llava:13b") == "llava:13b"
    assert normalize_ocr_model("ollama", "") == DEFAULT_OLLAMA_OCR_MODEL


def test_default_model_for_engine() -> None:
    assert default_model_for_engine("rapidocr") == DEFAULT_OCR_MODEL
    assert default_model_for_engine("ollama") == DEFAULT_OLLAMA_OCR_MODEL
