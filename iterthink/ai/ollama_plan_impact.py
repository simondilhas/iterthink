"""LLaVA vision assessment for plan change-region impact."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iterthink import config
from iterthink.ai.ollama_models import model_name_installed
from iterthink.ai.ollama_util import chat_response_text, ollama_error_message

_SYSTEM_PROMPT = (
    "You assess architectural plan revisions. Be concise and technical."
)

_USER_PROMPT = (
    "Compare these plan images for one changed area:\n"
    "1) before (baseline tight crop)\n"
    "2) after (candidate tight crop)\n"
    "3) surrounding context (2x area on candidate)\n\n"
    "Assess impact for architecture practice:\n"
    "- spatial: relocation, dimensions, circulation, room boundaries\n"
    "- structural: walls, openings, load-bearing hints visible in the crop\n"
    "- compliance: fire egress, accessibility, code-relevant deltas if inferable\n"
    "- adjacency: neighboring spaces, systems, or zones affected\n\n"
    "Write 3-6 sentences of impact narrative. No JSON, no bullet list."
)


async def assess_plan_region_impact_async(
    ollama: Any,
    *,
    crop_before: Path,
    crop_after: Path,
    context_crop: Path,
    model: str | None = None,
    text_hints: str = "",
) -> str:
    model_name = (model or config.PLAN_REGION_IMPACT_VISION_MODEL or "llava:13b").strip()
    user_content = _USER_PROMPT
    if (text_hints or "").strip():
        user_content = f"{_USER_PROMPT}\n\nDetected text changes:\n{text_hints.strip()}"
    images = [
        str(crop_before.resolve()),
        str(crop_after.resolve()),
        str(context_crop.resolve()),
    ]
    resp = await ollama.chat(
        model=model_name,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": user_content,
                "images": images,
            },
        ],
    )
    return chat_response_text(resp).strip()


async def check_plan_impact_vision_ready(
    ollama: Any,
    model: str | None = None,
) -> tuple[bool, str]:
    model_name = (model or config.PLAN_REGION_IMPACT_VISION_MODEL or "llava:13b").strip()
    try:
        lr = await ollama.list()
        names = sorted({str(m.model) for m in lr.models if getattr(m, "model", None)})
    except BaseException as ex:
        return False, f"Ollama not reachable: {ollama_error_message(ex)}"
    if not model_name_installed(names, model_name):
        return False, f"Model not installed: {model_name} (run: ollama pull {model_name})"
    return True, "Ready"
