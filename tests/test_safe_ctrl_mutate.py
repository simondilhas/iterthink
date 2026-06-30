"""Tests for Flet frozen-control mutation helpers."""

from __future__ import annotations

from unittest.mock import patch

import flet as ft

from iterthink.studio.util import safe_ctrl_mutate


def test_safe_ctrl_mutate_unfreezes_and_updates() -> None:
    ctrl = ft.Container(opacity=0.0)
    ctrl._frozen = True  # type: ignore[attr-defined]

    with patch("iterthink.studio.util.ctrl_on_page", return_value=True), patch.object(
        ctrl, "update"
    ) as mock_update:
        safe_ctrl_mutate(ctrl, lambda c: setattr(c, "opacity", 1.0))

    assert ctrl.opacity == 1.0
    mock_update.assert_called_once()
    assert getattr(ctrl, "_frozen", False) is True
