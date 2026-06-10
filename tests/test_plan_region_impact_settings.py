"""Plan region impact vision model config normalization."""

from iterthink.ocr_settings import (
    DEFAULT_PLAN_REGION_IMPACT_VISION_MODEL,
    normalize_plan_region_impact_vision_model,
)


def test_normalize_plan_region_impact_vision_model_default() -> None:
    assert normalize_plan_region_impact_vision_model(None) == DEFAULT_PLAN_REGION_IMPACT_VISION_MODEL
    assert normalize_plan_region_impact_vision_model("") == DEFAULT_PLAN_REGION_IMPACT_VISION_MODEL
    assert normalize_plan_region_impact_vision_model("   ") == DEFAULT_PLAN_REGION_IMPACT_VISION_MODEL


def test_normalize_plan_region_impact_vision_model_passthrough() -> None:
    assert normalize_plan_region_impact_vision_model("llava:7b") == "llava:7b"
    assert normalize_plan_region_impact_vision_model("  llava:13b  ") == "llava:13b"
