from __future__ import annotations

import os
from typing import Callable, Optional

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.utils.kinematics import (
    joint_config_signs,
    sort_joint_configs_by_proximity,
)

_ROW_HEIGHT = 26


class PoseTree(ui.VStack):
    """Flat list of target poses with visibility toggles and joint-config dropdowns."""

    def __init__(
        self,
        on_visibility_changed: Optional[Callable[[int, bool], None]] = None,
        on_config_changed: Optional[Callable[[int, int], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("spacing", 2)
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._on_visibility_changed = on_visibility_changed
        self._on_config_changed = on_config_changed

        # Strong refs to prevent GC while widget is alive
        self._checkboxes: list[ui.CheckBox] = []
        self._combo_frames: list[ui.Frame] = []
        self._combo_subs: list = []

    def update(
        self,
        target_prim_paths: list[str],
        all_joint_solutions: Optional[list[list[list[float]]]],
        current_joint_positions: Optional[list[float]] = None,
    ) -> None:
        """Rebuild the list for a newly selected candidate."""
        self._checkboxes.clear()
        self._combo_frames.clear()
        self._combo_subs.clear()
        self.clear()

        if not target_prim_paths or all_joint_solutions is None:
            return

        with self:
            for pose_idx, path in enumerate(target_prim_paths):
                solutions: list[list[float]] = (
                    all_joint_solutions[pose_idx]
                    if pose_idx < len(all_joint_solutions)
                    else []
                )
                reachable = len(solutions) > 0
                label = os.path.basename(path) if path else f"Pose {pose_idx + 1}"

                # Determine the best default config index using current robot joints
                if reachable and current_joint_positions:
                    sorted_configs = sort_joint_configs_by_proximity(
                        solutions, current_joint_positions
                    )
                    best_config = sorted_configs[0]
                    default_idx = solutions.index(best_config)
                else:
                    default_idx = 0

                with ui.HStack(height=_ROW_HEIGHT, spacing=4):
                    ui.Spacer(width=4)
                    # Visibility checkbox — unchecked for unreachable poses
                    with ui.VStack(width=16):
                        ui.Spacer(height=5)
                        cb = ui.CheckBox(
                            width=16,
                            height=16,
                            tooltip="Toggle pose visibility in the viewport.",
                        )
                    cb.model.set_value(reachable)
                    cb.enabled = reachable
                    cb.model.add_value_changed_fn(
                        lambda m, idx=pose_idx, _self=self: (
                            _self._on_visibility_changed(idx, m.get_value_as_bool())
                            if _self._on_visibility_changed
                            else None
                        )
                    )
                    self._checkboxes.append(cb)
                    ui.Spacer(width=4)
                    ui.Label(
                        label,
                        width=ui.Fraction(1),
                        tooltip=path,
                        style={
                            "color": (
                                NOVAColor.TEXT_PRIMARY.color
                                if reachable
                                else NOVAColor.TEXT_SECONDARY.color
                            ),
                            "font_size": 13,
                        },
                    )
                    # Joint-config selector — same Frame width as ghost teaching
                    frame = ui.Frame(width=ui.Pixel(85))
                    self._combo_frames.append(frame)
                    with frame:
                        if reachable:
                            config_labels = [
                                f"{i + 1} {joint_config_signs(solutions[i])}"
                                for i in range(len(solutions))
                            ]
                            combo = ui.ComboBox(default_idx, *config_labels)
                            sub = combo.model.add_item_changed_fn(
                                lambda m, _, idx=pose_idx, _self=self: (
                                    _self._on_config_changed(
                                        idx, m.get_item_value_model().as_int
                                    )
                                    if _self._on_config_changed
                                    else None
                                )
                            )
                            self._combo_subs.append(sub)
                        else:
                            ui.Label(
                                "No IK",
                                style={
                                    "color": NOVAColor.TEXT_SECONDARY.color,
                                    "font_size": 12,
                                },
                            )
                    ui.Spacer(width=4)
