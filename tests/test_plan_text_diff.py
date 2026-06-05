"""Tests for plan geometry diff (text change views)."""

from iterthink.services.plan_text_diff import PlanTextChangeView, diff_plan_geometry


def _page(page_num: int, lines: list[dict], *, w: float = 612.0, h: float = 792.0) -> dict:
    return {"page": page_num, "width": w, "height": h, "lines": lines}


def _line(text: str, bbox: list[float], size: float = 10.0) -> dict:
    return {"text": text, "bbox": bbox, "size": size}


def test_stable_match() -> None:
    geo = {
        "pages": [
            _page(1, [_line("A-101", [10.0, 10.0, 50.0, 22.0])]),
        ]
    }
    views = diff_plan_geometry(geo, geo)
    assert len(views) == 1
    assert views[0].kind == "stable"
    assert views[0].display_text == "A-101"
    assert views[0].pin_norm is None


def test_modified_text() -> None:
    base = {"pages": [_page(1, [_line("A-101", [10.0, 10.0, 50.0, 22.0])])]}
    cand = {"pages": [_page(1, [_line("A-102", [10.0, 10.0, 50.0, 22.0])])]}
    views = diff_plan_geometry(base, cand)
    assert len(views) == 1
    assert views[0].kind == "modified"
    assert views[0].old_text == "A-101"
    assert views[0].new_text == "A-102"
    assert views[0].pin_norm is not None


def test_added_and_removed() -> None:
    base = {
        "pages": [
            _page(1, [
                _line("Keep", [10.0, 10.0, 40.0, 22.0]),
                _line("Gone", [10.0, 30.0, 40.0, 42.0]),
            ]),
        ]
    }
    cand = {
        "pages": [
            _page(1, [
                _line("Keep", [10.0, 10.0, 40.0, 22.0]),
                _line("New", [10.0, 50.0, 40.0, 62.0]),
            ]),
        ]
    }
    views = diff_plan_geometry(base, cand)
    by_kind = {v.display_text: v for v in views}
    assert by_kind["Keep"].kind == "stable"
    assert by_kind["Gone"].kind == "removed"
    assert by_kind["New"].kind == "added"


def test_bbox_drift_tolerance() -> None:
    base = {"pages": [_page(1, [_line("Room", [10.0, 10.0, 50.0, 22.0])])]}
    cand = {"pages": [_page(1, [_line("Room", [12.0, 11.0, 52.0, 23.0])])]}
    views = diff_plan_geometry(base, cand)
    assert len(views) == 1
    assert views[0].kind == "stable"


def test_fuzzy_text_match_with_drift() -> None:
    base = {"pages": [_page(1, [_line("Label-A", [10.0, 10.0, 80.0, 22.0])])]}
    cand = {"pages": [_page(1, [_line("Label-B", [200.0, 400.0, 280.0, 412.0])])]}
    views = diff_plan_geometry(base, cand)
    kinds = {v.kind for v in views}
    assert "removed" in kinds or "modified" in kinds or "added" in kinds


def test_geometry_to_label_views() -> None:
    from iterthink.services.plan_text_diff import geometry_to_label_views

    geo = {
        "pages": [
            {
                "page": 1,
                "width": 612.0,
                "height": 792.0,
                "lines": [{"text": "Label", "bbox": [10.0, 10.0, 50.0, 22.0], "size": 10.0}],
            }
        ]
    }
    views = geometry_to_label_views(geo)
    assert len(views) == 1
    assert views[0].kind == "stable"
    assert views[0].pin_norm is None
