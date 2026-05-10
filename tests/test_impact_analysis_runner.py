"""Impact analysis JSON coercion."""

from iterthink.services.impact_analysis_runner import _normalize_payload


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
