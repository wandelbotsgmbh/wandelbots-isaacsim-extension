"""Prim picker section widget for the mounting assistant."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.widgets.prim_picker import (
    MultiPrimPicker,
    PrimPicker,
    PrimPickerDialogProperties,
)

if TYPE_CHECKING:
    from pxr import Usd


class PrimPickersSection:
    """Motion Group, Robot Base, and Target Poses pickers with a collapsable target list."""

    def __init__(
        self,
        stage: "Usd.Stage | None",
        on_motion_group_picked: Callable[["Usd.Prim | None"], None],
        on_center_picked: Callable[["Usd.Prim | None"], None],
        on_targets_picked: Callable[[list["Usd.Prim"]], None],
        motion_group_filter_fn: Optional[Callable[["Usd.Prim"], bool]] = None,
        pose_filter_fn: Optional[Callable[["Usd.Prim"], bool]] = None,
    ) -> None:
        self._target_prim_list_row: ui.HStack | None = None
        self._target_prim_list_frame: ui.Frame | None = None

        self.motion_group_picker: PrimPicker
        self.center_picker: PrimPicker
        self.target_picker: MultiPrimPicker

        with ui.VStack(height=0, spacing=6):
            ui.Spacer(height=4)
            with ui.HStack(height=26, spacing=8):
                ui.Spacer(width=5)
                ui.Label(
                    "Motion Group",
                    width=110,
                    tooltip="Select the installed motion group prim whose model will be used for IK checks.",
                )
                self.motion_group_picker = PrimPicker(
                    stage=stage,
                    prim_picked_fn=on_motion_group_picked,
                    dialog_properties=PrimPickerDialogProperties(
                        title="Select Motion Group",
                        filter_fn=motion_group_filter_fn,
                    ),
                )
                ui.Spacer(width=5)
            with ui.HStack(height=26, spacing=8):
                ui.Spacer(width=5)
                ui.Label(
                    "Robot Base",
                    width=110,
                    tooltip="Select the prim that defines the center of the search pattern.",
                )
                self.center_picker = PrimPicker(
                    stage=stage,
                    prim_picked_fn=on_center_picked,
                )
                ui.Spacer(width=5)
            with ui.HStack(height=26, spacing=8):
                ui.Spacer(width=5)
                ui.Label(
                    "Target Poses",
                    width=110,
                    tooltip="Select prims that define the target TCP poses all candidates must reach.",
                )
                self.target_picker = MultiPrimPicker(
                    stage=stage,
                    prims_picked_fn=on_targets_picked,
                    dialog_properties=PrimPickerDialogProperties(
                        title="Select Target Poses",
                        filter_fn=pose_filter_fn,
                    ),
                )
                ui.Spacer(width=5)
            self._target_prim_list_row = ui.HStack(height=0, spacing=8, visible=False)
            with self._target_prim_list_row:
                ui.Spacer(width=5 + 8 + 110)
                with ui.CollapsableFrame(
                    "Selected Target Poses",
                    height=0,
                    collapsed=True,
                ):
                    self._target_prim_list_frame = ui.Frame(height=0)
                ui.Spacer(width=5)
            ui.Line(
                height=1,
                style={
                    "border_width": 1,
                    "color": NOVAColor.DIVIDER.color,
                },
            )

    def set_stage(self, stage: "Usd.Stage | None") -> None:
        """Update the USD stage on all pickers (e.g. after stage open/close)."""
        self.motion_group_picker._stage = stage
        self.center_picker._stage = stage
        self.target_picker._stage = stage

    def clear(self) -> None:
        """Clear displayed picker values without emitting picker callbacks."""
        self.set_motion_group_prim(None)
        self.set_center_prim(None)
        self.target_picker._prims = []
        self.target_picker._deferred_build_ui()

    def set_motion_group_prim(self, prim: "Usd.Prim | None") -> None:
        """Update the displayed motion group prim without triggering the callback."""
        self.motion_group_picker._prim = prim
        self.motion_group_picker._deferred_build_ui()

    def set_center_prim(self, prim: "Usd.Prim | None") -> None:
        """Update the displayed center prim without triggering the callback."""
        self.center_picker._prim = prim
        self.center_picker._deferred_build_ui()

    def clear_targets(self) -> None:
        """Clear the selected target prims."""
        self.target_picker._clear()

    def rebuild_target_list(self, paths: list[str]) -> None:
        """Rebuild the collapsable target prim path list."""
        if self._target_prim_list_row is not None:
            self._target_prim_list_row.visible = bool(paths)
        if self._target_prim_list_frame is None:
            return
        self._target_prim_list_frame.clear()
        with self._target_prim_list_frame:
            with ui.ScrollingFrame(
                height=min(len(paths) * 22 + 4, 110),
                horizontal_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
            ):
                with ui.VStack(spacing=2, height=0):
                    ui.Spacer(height=2)
                    for path in paths:
                        ui.Label(
                            path,
                            height=20,
                            style={
                                "color": NOVAColor.TEXT_SECONDARY.color,
                                "font_size": 14,
                            },
                        )
                    ui.Spacer(height=2)

    def destroy(self) -> None:
        self._target_prim_list_frame = None
        self._target_prim_list_row = None
