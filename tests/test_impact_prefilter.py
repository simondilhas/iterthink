"""Impact norm prefilter (LLM skip for structural non-content)."""

from iterthink.services.impact_prefilter import norm_compliance_skip_llm


def test_skip_horizontal_rule_paragraph() -> None:
    out = norm_compliance_skip_llm("---")
    assert out is not None
    assert out["status"] == "not_applicable"
    assert out["details"]["findings"] == []


def test_skip_heading_only_paragraph() -> None:
    out = norm_compliance_skip_llm("## 6. Umgebung")
    assert out is not None
    assert out["status"] == "not_applicable"


def test_no_skip_substantive_bullet() -> None:
    assert (
        norm_compliance_skip_llm(
            "- **Zufahrt:** Sickerfähige Beläge (Pflastersteine) zur Reduktion der Abwassergebühren."
        )
        is None
    )


def test_no_skip_mixed_heading_and_body() -> None:
    assert norm_compliance_skip_llm("## Titel\n\nMit Fliesstext darunter.") is None
