"""Motion type column cell for the pose TreeView."""

from __future__ import annotations

from typing import Callable

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor

ROW_HEIGHT = 44

MOTION_TYPES = ["PathCartesianPTP", "PathLine", "PathJointPTP"]
MOTION_TYPE_LABELS = ["Cartesian PTP", "Linear", "Joint PTP"]


def build_motion_type_cell(
    motion_type: str,
    item_index: int,
    collision_free: bool,
    on_changed: Callable[[int], None] | None,
    widgets_out: list,
    subs_out: list,
) -> None:
    with ui.VStack(height=ROW_HEIGHT):
        ui.Spacer()
        with ui.HStack(spacing=4, height=0):
            if item_index == 0:
                ui.Label(
                    "START",
                    width=ui.Fraction(2),
                    alignment=ui.Alignment.CENTER,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 12,
                    },
                )
            elif collision_free:
                ui.Label(
                    "Collision Free",
                    width=ui.Fraction(2),
                    alignment=ui.Alignment.CENTER,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 12,
                    },
                )
            else:
                current_idx = (
                    MOTION_TYPES.index(motion_type)
                    if motion_type in MOTION_TYPES
                    else 0
                )
                combo = ui.ComboBox(
                    current_idx,
                    *MOTION_TYPE_LABELS,
                    width=ui.Fraction(3),
                )
                if on_changed:
                    sub = combo.model.subscribe_item_changed_fn(
                        lambda m, _, cb=on_changed: cb(
                            m.get_item_value_model().get_value_as_int()
                        )
                    )
                    subs_out.append(sub)
                widgets_out.append(combo)
        ui.Spacer()
