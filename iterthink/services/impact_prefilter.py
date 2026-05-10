"""Cheap pre-checks for Impact analysis: skip LLM when paragraph is structurally not norm-checkable."""

from __future__ import annotations

import re


def synthetic_norm_not_applicable(reason: str) -> dict:
    """Payload shape matching ``_normalize_findings_envelope`` output for DB + UI."""
    r = reason.strip()
    comment = r[:200] + ("…" if len(r) > 200 else "")
    return {
        "status": "not_applicable",
        "comment": comment,
        "details": {
            "low_confidence": False,
            "not_applicable_reason": r,
            "findings": [],
        },
    }


def norm_compliance_skip_llm(paragraph: str) -> dict | None:
    """If the paragraph cannot contain norm-checkable claims, return a synthetic result; else None."""
    t = paragraph.strip()
    if not t:
        return None

    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if not lines:
        return synthetic_norm_not_applicable("Leerer Absatz.")

    # Horizontal rules / decoration-only blocks (---, ***, ___).
    if all(re.fullmatch(r"[\s\-_*]{3,}", ln) for ln in lines):
        return synthetic_norm_not_applicable(
            "Nur Markdown-Trennlinie oder Dekoration; keine technischen Aussagen."
        )

    # Every non-empty line is a markdown heading (## …) with no body text.
    heading = re.compile(r"^#{1,6}\s+\S")
    if all(heading.match(ln) for ln in lines):
        return synthetic_norm_not_applicable("Nur Überschriften; keine prüfbaren technischen Festlegungen.")

    return None
