"""Edit buttons cell for the pose TreeView (move up/down, visibility, remove)."""

from __future__ import annotations

from typing import Callable

import omni.ui as ui

from wandelbots.omni.ui.styles import ICON_BTN_STYLE
from wandelbots.omni.ui.utils import get_icon

ROW_HEIGHT = 44

_ICON_BTN_STYLE = ICON_BTN_STYLE


def build_edit_buttons_cell(
    item_index: int,
    item_count: int,
    is_visible: bool,
    on_move_up: Callable[[], None],
    on_move_down: Callable[[], None],
    on_toggle_visibility: Callable[[], None],
    on_remove: Callable[[], None],
    widgets_out: list,
) -> None:
    with ui.VStack(height=ROW_HEIGHT):
        ui.Spacer()
        with ui.HStack(spacing=2, height=0):
            btn_up = ui.Button(
                "",
                width=24,
                height=24,
                image_url=get_icon("arrow_up.svg"),
                image_width=16,
                image_height=16,
                tooltip="Move up",
                clicked_fn=on_move_up,
                style=_ICON_BTN_STYLE,
                enabled=item_index > 0,
            )
            widgets_out.append(btn_up)
            btn_down = ui.Button(
                "",
                width=24,
                height=24,
                image_url=get_icon("arrow_down.svg"),
                image_width=16,
                image_height=16,
                tooltip="Move down",
                clicked_fn=on_move_down,
                style=_ICON_BTN_STYLE,
                enabled=item_index < item_count - 1,
            )
            widgets_out.append(btn_down)
            icon_name = "eye_open.svg" if is_visible else "eye_closed.svg"
            btn_vis = ui.Button(
                "",
                width=24,
                height=24,
                image_url=get_icon(icon_name),
                image_width=16,
                image_height=16,
                tooltip="Toggle visibility",
                clicked_fn=on_toggle_visibility,
                style=_ICON_BTN_STYLE,
            )
            widgets_out.append(btn_vis)
            btn_remove = ui.Button(
                "",
                width=24,
                height=24,
                image_url=get_icon("close.svg"),
                image_width=16,
                image_height=16,
                tooltip="Remove from list",
                clicked_fn=on_remove,
                style=_ICON_BTN_STYLE,
            )
            widgets_out.append(btn_remove)
        ui.Spacer()
