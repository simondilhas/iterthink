"""Action cube for plan change regions (Review mode)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import flet as ft

from iterthink import config

from .components import (
    ACTION_RAIL_ICON_SIZE,
    action_rail_approve_icon_button,
    action_rail_icon_button_style,
    action_rail_reject_icon_button,
    build_action_rectangle,
)
from .constants import COMPARE_ACTION_GRID_CELL


@dataclass(frozen=True)
class PlanRegionActionHandlers:
    on_approve: Callable[[], None]
    on_reject: Callable[[], None]
    on_comment: Callable[[], None]
    on_act: Callable[[], None]


def build_plan_region_action_cube(handlers: PlanRegionActionHandlers) -> ft.Container:
    """2×2 action grid matching Review paragraph rows."""
    comment_btn = ft.IconButton(
        ft.Icons.CHAT_BUBBLE_OUTLINE,
        icon_size=ACTION_RAIL_ICON_SIZE,
        icon_color=config.ON_SURFACE_VARIANT,
        tooltip="Comment",
        style=action_rail_icon_button_style(),
        on_click=lambda _e: handlers.on_comment(),
    )
    act_btn = ft.IconButton(
        ft.Icons.PRECISION_MANUFACTURING,
        icon_size=ACTION_RAIL_ICON_SIZE,
        icon_color=config.ON_SURFACE_VARIANT,
        tooltip="Act",
        style=action_rail_icon_button_style(),
        on_click=lambda _e: handlers.on_act(),
    )
    return build_action_rectangle(
        top_left=action_rail_approve_icon_button(on_click=lambda _e: handlers.on_approve()),
        top_right=action_rail_reject_icon_button(on_click=lambda _e: handlers.on_reject()),
        bottom_left=comment_btn,
        bottom_right=act_btn,
        row_h=COMPARE_ACTION_GRID_CELL,
    )
