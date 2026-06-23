from __future__ import annotations

from typing import Callable, Optional

import omni.ui as ui

from wandelbots.omni.reachability.reachability_service import ReachabilityResult
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.widgets.coordinates_input import (
    CoordinateInputFieldModel,
    CoordinatesInput,
)
from wandelbots.omni.utils.scene import SceneUtils

from .input_row import LABEL_WIDTH
from .pose_tree import PoseTree


_BUTTON_HEIGHT = 32


class CandidateDetail(ui.VStack):
    """Detail panel showing position, delta, status and poses for a selected candidate."""

    def __init__(
        self,
        on_move_clicked: Optional[Callable[[], None]] = None,
        on_pose_visibility_changed: Optional[Callable[[int, bool], None]] = None,
        on_pose_config_changed: Optional[Callable[[int, int], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("spacing", 0)
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._on_move_clicked = on_move_clicked
        self._on_pose_visibility_changed = on_pose_visibility_changed
        self._on_pose_config_changed = on_pose_config_changed
        self._content_frame: ui.Frame | None = None
        self._pose_tree: PoseTree | None = None

        self._build_empty()

    def update(
        self,
        pos: list[float] | None,
        result: ReachabilityResult | None,
        current_mm: list[float] | None,
        can_move: bool = False,
        target_prim_paths: list[str] | None = None,
        current_joint_positions: list[float] | None = None,
    ) -> None:
        if self._content_frame is None:
            return
        self._pose_tree = None
        self._content_frame.clear()
        with self._content_frame:
            if pos is None:
                self._build_placeholder()
                return
            self._build_content(
                pos,
                result,
                current_mm,
                can_move,
                target_prim_paths or [],
                current_joint_positions,
            )

    def _build_empty(self) -> None:
        with self:
            self._content_frame = ui.Frame(height=0)
            with self._content_frame:
                self._build_placeholder()

    def _build_placeholder(self) -> None:
        with ui.VStack(spacing=0):
            ui.Spacer(height=12)
            ui.Label(
                "Select a candidate in the viewport",
                alignment=ui.Alignment.CENTER,
                style={
                    "color": NOVAColor.TEXT_SECONDARY.color,
                    "font_size": 14,
                },
            )

    def _build_content(
        self,
        pos: list[float],
        result: ReachabilityResult | None,
        current_mm: list[float] | None,
        can_move: bool = False,
        target_prim_paths: list[str] | None = None,
        current_joint_positions: list[float] | None = None,
    ) -> None:
        has_any_reachable = result is not None and result.reachable_count > 0

        with ui.VStack(spacing=0):
            ui.Spacer(height=8)

            unit = SceneUtils.get_unit_label()

            with ui.HStack(height=24, spacing=4):
                ui.Spacer(width=8)
                ui.Label(
                    f"Position [{unit}]",
                    width=LABEL_WIDTH,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 14,
                    },
                )
                self._build_xyz_input(pos[0], pos[1], pos[2])
                ui.Spacer(width=8)

            if current_mm is not None:
                dx = pos[0] - current_mm[0]
                dy = pos[1] - current_mm[1]
                dz = pos[2] - current_mm[2]

                ui.Spacer(height=6)
                with ui.HStack(height=24, spacing=4):
                    ui.Spacer(width=8)
                    ui.Label(
                        f"Delta [{unit}]",
                        width=LABEL_WIDTH,
                        style={
                            "color": NOVAColor.TEXT_SECONDARY.color,
                            "font_size": 14,
                        },
                    )
                    self._build_xyz_input(dx, dy, dz)
                    ui.Spacer(width=8)

            ui.Spacer(height=6)
            with ui.HStack(height=_BUTTON_HEIGHT, spacing=4):
                ui.Spacer()
                ui.Button(
                    "Apply Transform",
                    width=110,
                    height=_BUTTON_HEIGHT,
                    enabled=can_move and has_any_reachable,
                    tooltip=(
                        "Move the motion group prim to this mounting position"
                        if can_move
                        else "No motion group prim selected"
                    ),
                    clicked_fn=lambda _self=self: (
                        _self._on_move_clicked() if _self._on_move_clicked else None
                    ),
                    style={
                        "Button": {
                            "background_color": NOVAColor.PRIMARY_MAIN.color,
                            "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                            "border_width": 0,
                        },
                        "Button:hovered": {
                            "background_color": NOVAColor.PRIMARY_LIGHT.color,
                            "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                        },
                        "Button:disabled": {
                            "background_color": NOVAColor.DIVIDER.color,
                            "color": NOVAColor.TEXT_SECONDARY.color,
                        },
                        "Tooltip": {"background_color": ui.color(0x1E1E1EFF)},
                    },
                )
                ui.Spacer(width=8)

            ui.Spacer(height=8)
            ui.Line(
                height=1,
                style={"border_width": 1, "color": NOVAColor.DIVIDER.color},
            )
            ui.Spacer(height=8)

            if result is None:
                status_word = "Pending"
                status_word_color = NOVAColor.TEXT_SECONDARY.color
                count_text = None
                count_color = NOVAColor.TEXT_SECONDARY.color
            elif result.error:
                status_word = f"Error: {result.error}"
                status_word_color = ui.color("#EF5350")
                count_text = None
                count_color = NOVAColor.TEXT_SECONDARY.color
            elif result.reachable:
                status_word = "Reachable"
                status_word_color = ui.color("#26A69A")
                count_text = f"{result.reachable_count}/{result.total_poses} poses"
                count_color = NOVAColor.TEXT_SECONDARY.color
            else:
                status_word = "Unreachable"
                status_word_color = ui.color("#EF5350")
                count_text = f"{result.reachable_count}/{result.total_poses} poses"
                count_color = ui.color("#EF5350")

            with ui.HStack(height=24, spacing=4):
                ui.Spacer(width=8)
                ui.Label(
                    "Status",
                    width=LABEL_WIDTH,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 14,
                    },
                )
                ui.Label(
                    status_word,
                    style={"color": status_word_color, "font_size": 14},
                )
                if count_text:
                    ui.Spacer()
                    ui.Label(
                        count_text,
                        alignment=ui.Alignment.RIGHT_CENTER,
                        style={"color": count_color, "font_size": 13},
                    )
                    ui.Spacer(width=8)

            # Pose tree (visibility + config selection per pose)
            has_solutions = (
                result is not None
                and result.all_joint_solutions is not None
                and target_prim_paths
            )

            if has_solutions:
                ui.Spacer(height=6)
                with ui.HStack(height=20, spacing=4):
                    ui.Spacer(width=8)
                    ui.Label(
                        "Poses",
                        style={
                            "color": NOVAColor.TEXT_SECONDARY.color,
                            "font_size": 13,
                        },
                    )
                self._pose_tree = PoseTree(
                    on_visibility_changed=self._on_pose_visibility_changed,
                    on_config_changed=self._on_pose_config_changed,
                )
                self._pose_tree.update(
                    target_prim_paths,
                    result.all_joint_solutions,
                    current_joint_positions,
                )
                ui.Spacer(height=4)

    @staticmethod
    def _build_xyz_input(x: float, y: float, z: float) -> None:
        labels = ["X", "Y", "Z"]
        values = [
            SceneUtils.millimeters_to_stage_value(x),
            SceneUtils.millimeters_to_stage_value(y),
            SceneUtils.millimeters_to_stage_value(z),
        ]
        fields = [
            CoordinateInputFieldModel(
                model=ui.SimpleFloatModel(val),
                label=lbl,
                tooltip=lbl,
            )
            for lbl, val in zip(labels, values)
        ]
        CoordinatesInput(fields=fields, readonly=True)
