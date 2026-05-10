"""Impact analysis JSON coercion."""

from iterthink.impact_checks import ImpactCheck
from iterthink.services.impact_analysis_runner import _normalize_payload


def _chk_norm() -> ImpactCheck:
    return ImpactCheck(
        id="norm_compliance",
        label="Norm",
        system_prompt="s",
        user_template="{text}\n{context}",
    )


def _chk_consistency() -> ImpactCheck:
    return ImpactCheck(
        id="impact_consistency",
        label="Consistency",
        system_prompt="s",
        user_template="{text}\n{context}",
    )


def test_normalize_payload_full() -> None:
    raw = {
        "status": "stable",
        "comment": "ok",
        "explanation": "No conflict found.",
        "references": [{"document": "A.md", "paragraph": 2, "note": "aligns"}],
    }
    out, err = _normalize_payload(raw)
    assert err is None
    assert out is not None
    assert out["status"] == "stable"
    assert out["comment"] == "ok"
    assert out["details"]["explanation"] == "No conflict found."
    assert out["details"]["references"] == [{"document": "A.md", "paragraph": 2, "note": "aligns"}]


def test_normalize_payload_summary_alias() -> None:
    out, err = _normalize_payload(
        {
            "status": "changed",
            "summary": "short",
            "explanation": "longer",
            "references": [],
        }
    )
    assert err is None
    assert out["comment"] == "short"


def test_normalize_payload_empty_references() -> None:
    out, err = _normalize_payload(
        {"status": "risk", "comment": "x", "explanation": "y", "references": []}
    )
    assert err is None
    assert out["details"]["references"] == []


def test_normalize_payload_rejects_bad_status() -> None:
    out, err = _normalize_payload(
        {
            "status": "weird",
            "comment": "x",
            "explanation": "e",
            "references": [],
        }
    )
    assert out is None
    assert err is not None


def test_normalize_payload_requires_explanation() -> None:
    out, err = _normalize_payload({"status": "stable", "comment": "x", "references": []})
    assert out is None
    assert "explanation" in (err or "").lower()


def test_normalize_payload_reference_needs_paragraph() -> None:
    out, err = _normalize_payload(
        {
            "status": "stable",
            "comment": "x",
            "explanation": "e",
            "references": [{"document": "B.md"}],
        }
    )
    assert out is None
    assert err is not None


def test_normalize_payload_paragraph_one_based() -> None:
    out, err = _normalize_payload(
        {
            "status": "stable",
            "comment": "x",
            "explanation": "e",
            "references": [{"document": "B.md", "paragraph": 0}],
        }
    )
    assert out is None

    out2, err2 = _normalize_payload(
        {
            "status": "stable",
            "comment": "x",
            "explanation": "e",
            "references": [{"document": "B.md", "paragraph": 1}],
        }
    )
    assert err2 is None
    assert out2["details"]["references"][0]["paragraph"] == 1


def test_findings_norm_not_applicable() -> None:
    raw = {
        "paragraph_status": "not_applicable",
        "low_confidence": False,
        "not_applicable_reason": "Introductory text only.",
        "findings": [],
    }
    out, err = _normalize_payload(raw, check=_chk_norm())
    assert err is None and out is not None
    assert out["status"] == "not_applicable"
    assert "Introductory" in out["comment"]
    assert out["details"]["findings"] == []


def test_findings_norm_ok_finding() -> None:
    raw = {
        "paragraph_status": "ok",
        "low_confidence": False,
        "not_applicable_reason": None,
        "findings": [
            {
                "type": "ok",
                "severity": "info",
                "claim": "F90 wall",
                "norm_ref": "SIA 380/1",
                "expected": None,
                "found": None,
                "action": "No change needed.",
                "source_document": "norms.md",
                "source_excerpt": "F90 is acceptable.",
            }
        ],
    }
    out, err = _normalize_payload(raw, check=_chk_norm())
    assert err is None and out is not None
    assert out["status"] == "ok"
    assert out["details"]["findings"][0]["type"] == "ok"


def test_findings_norm_missing_ref_null_sources() -> None:
    raw = {
        "paragraph_status": "warning",
        "low_confidence": False,
        "not_applicable_reason": None,
        "findings": [
            {
                "type": "missing_ref",
                "severity": "warning",
                "claim": "Must insulate well",
                "norm_ref": "SIA 180",
                "expected": None,
                "found": None,
                "action": "Cite the applicable SIA clause.",
                "source_document": None,
                "source_excerpt": None,
            }
        ],
    }
    out, err = _normalize_payload(raw, check=_chk_norm())
    assert err is None and out is not None
    assert out["status"] == "warning"


def test_findings_norm_rejects_na_with_findings() -> None:
    raw = {
        "paragraph_status": "ok",
        "low_confidence": False,
        "not_applicable_reason": "x",
        "findings": [
            {
                "type": "ok",
                "severity": "info",
                "claim": "c",
                "action": "a",
                "source_document": "d.md",
                "source_excerpt": "q",
            }
        ],
    }
    out, err = _normalize_payload(raw, check=_chk_norm())
    assert out is None
    assert err is not None


def test_findings_consistency_contradiction() -> None:
    raw = {
        "paragraph_status": "error",
        "low_confidence": False,
        "not_applicable_reason": None,
        "findings": [
            {
                "type": "contradiction",
                "severity": "error",
                "claim": "12 cm",
                "this_states": "12 cm",
                "context_states": "10 cm",
                "source_document": "other.md",
                "source_excerpt": "minimum 10 cm",
                "action": "Reconcile thickness with other.md.",
            }
        ],
    }
    out, err = _normalize_payload(raw, check=_chk_consistency())
    assert err is None and out is not None
    assert out["status"] == "error"


def test_findings_consistency_wrong_severity() -> None:
    raw = {
        "paragraph_status": "ok",
        "low_confidence": False,
        "not_applicable_reason": None,
        "findings": [
            {
                "type": "ok",
                "severity": "warning",
                "claim": "x",
                "action": "y",
                "source_document": "d.md",
                "source_excerpt": "z",
            }
        ],
    }
    out, err = _normalize_payload(raw, check=_chk_consistency())
    assert out is None
    assert err is not None
