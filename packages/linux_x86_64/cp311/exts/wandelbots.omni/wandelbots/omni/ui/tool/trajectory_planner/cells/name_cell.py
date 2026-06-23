"""Name column cell for the pose TreeView."""

from __future__ import annotations

from typing import Callable

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import _TOOLTIP_SUB
from wandelbots.omni.ui.utils import get_icon

ROW_HEIGHT = 44


def build_name_cell(
    name: str,
    is_ghost_object: bool,
    has_overrides: bool = False,
    on_settings_clicked: Callable[[], None] | None = None,
    on_go_to_clicked: Callable[[], None] | None = None,
    cycle_time_s: float | None = None,
    reachable: bool | None = None,
) -> None:
    with ui.VStack(height=ROW_HEIGHT):
        ui.Spacer()
        with ui.HStack(spacing=4, height=0):
            icon = "ghost.svg" if is_ghost_object else "gizmo.svg"
            with ui.VStack(width=15):
                ui.Spacer()
                ui.Image(
                    get_icon(icon),
                    width=15,
                    height=15,
                    style={"color": NOVAColor.PRIMARY_CONTRAST_TEXT.color},
                )
                ui.Spacer()
            ui.Spacer(width=4)
            ui.Label(
                name,
                alignment=ui.Alignment.LEFT_CENTER,
                elided_text=True,
                tooltip=name,
                tooltip_offset_y=18,
                width=ui.Fraction(2),
                style={
                    "color": NOVAColor.TEXT_PRIMARY.color,
                    "font_size": 14,
                },
            )
            if cycle_time_s is not None:
                ui.Label(
                    f"{cycle_time_s:.1f}s",
                    width=0,
                    alignment=ui.Alignment.RIGHT_CENTER,
                    tooltip=f"Segment cycle time: {cycle_time_s:.3f} s",
                    tooltip_offset_y=18,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 11,
                    },
                )
            if on_go_to_clicked:
                ui.Button(
                    "Go to",
                    width=0,
                    height=24,
                    tooltip="Move the virtual robot to this pose's joint position.",
                    clicked_fn=on_go_to_clicked,
                    style={
                        "Button": {
                            "background_color": NOVAColor.PRIMARY_MAIN.color,
                            "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                            "font_size": 12,
                            "border_radius": 4,
                            "padding": 6,
                        },
                        "Button:hovered": {
                            "background_color": NOVAColor.PRIMARY_LIGHT.color,
                        },
                        **_TOOLTIP_SUB,
                    },
                )
            if on_settings_clicked:
                ui.Button(
                    "",
                    width=20,
                    height=20,
                    image_url=get_icon("speed.svg"),
                    image_width=14,
                    image_height=14,
                    tooltip="Motion command settings (blending / limits)",
                    clicked_fn=on_settings_clicked,
                    style={
                        "Button": {
                            "background_color": 0x00000000,
                            "color": (
                                NOVAColor.PRIMARY_MAIN.color
                                if has_overrides
                                else NOVAColor.TEXT_SECONDARY.color
                            ),
                        },
                        "Button:hovered": {
                            "background_color": NOVAColor.BUTTON_HOVER.color
                        },
                        **_TOOLTIP_SUB,
                    },
                )
        ui.Spacer()
