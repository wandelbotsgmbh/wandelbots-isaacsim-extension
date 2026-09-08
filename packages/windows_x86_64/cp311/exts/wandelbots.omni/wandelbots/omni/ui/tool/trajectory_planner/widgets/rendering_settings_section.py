"""Collapsible rendering/visualization settings panel for trajectory planner.

Groups everything that only affects how the trajectory is *drawn* — colors,
velocity coloring, and the reference-frame/mounting-offset adjustments used to
correct the visualization when the robot is mounted on an external axis. None
of these affect the planned motion.
"""

from __future__ import annotations

import weakref
from typing import Callable

import carb
import carb.settings
import omni.ui as ui
import omni.usd
from pxr import UsdGeom

import wandelbots.omni.ui.colors as color_utils
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.wb_theme import TOOLTIP_RESET, build_tooltip
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.widgets.coordinates_input import (
    CoordinateInputFieldModel,
    CoordinatesInput,
)
from wandelbots.omni.ui.widgets.prim_picker import (
    PrimPicker,
    PrimPickerDialogProperties,
)
from wandelbots.omni.ui.widgets.section_divider import section_divider
from wandelbots.omni.utils.teaching import CARB_SETTINGS_PREFIX

_LABEL_WIDTH = 170
CARB_OVERLAY_COLOR = f"{CARB_SETTINGS_PREFIX}/overlay_color"


class RenderingSettingsSection:
    """Collapsible settings widget for trajectory colors and visualization-only
    pose adjustments (reference frame, mounting offset)."""

    def __init__(
        self,
        overlay_color: list[float] | None = None,
        trajectory_color: list[float] | None = None,
        velocity_coloring: bool = False,
        tcp_velocity: float = 500.0,
        reference_frame_path: str | None = None,
        mounting_offset: list[float] | None = None,
        mounting_rotation: list[float] | None = None,
        on_setting_changed: Callable[[str, object], None] | None = None,
    ) -> None:
        self.overlay_color = overlay_color or self._read_overlay_color_from_carb()
        self.trajectory_color = trajectory_color or [0.808, 0.0, 0.345]
        self.velocity_coloring = velocity_coloring
        self.tcp_velocity = tcp_velocity
        self.reference_frame_path = reference_frame_path
        self.mounting_offset = (
            [float(v) for v in mounting_offset]
            if mounting_offset and len(mounting_offset) == 3
            else [0.0, 0.0, 0.0]
        )
        self.mounting_rotation = (
            [float(v) for v in mounting_rotation]
            if mounting_rotation and len(mounting_rotation) == 3
            else [0.0, 0.0, 0.0]
        )
        self._on_setting_changed = on_setting_changed

        self._velocity_coloring_checkbox: ui.CheckBox | None = None
        self._velocity_legend: ui.VStack | None = None
        self._velocity_legend_max_label: ui.Label | None = None
        self._reference_frame_picker: PrimPicker | None = None
        self._reference_frame_frame: ui.Frame | None = None
        self._mounting_offset_frame: ui.Frame | None = None
        self._mounting_rotation_frame: ui.Frame | None = None

        # Persistent X/Y/Z models so set_mounting_offset()/set_mounting_rotation()
        # can update the fields after they've been built.
        self._mounting_offset_models: list[ui.SimpleFloatModel] = [
            ui.SimpleFloatModel(v / 1000.0) for v in self.mounting_offset
        ]
        for axis_idx, model in enumerate(self._mounting_offset_models):
            model.add_value_changed_fn(
                lambda m, i=axis_idx, ws=weakref.ref(self): (
                    ws()._on_offset_changed(i, m.get_value_as_float()) if ws() else None
                )
            )
        self._mounting_rotation_models: list[ui.SimpleFloatModel] = [
            ui.SimpleFloatModel(v) for v in self.mounting_rotation
        ]
        for axis_idx, model in enumerate(self._mounting_rotation_models):
            model.add_value_changed_fn(
                lambda m, i=axis_idx, ws=weakref.ref(self): (
                    ws()._on_rotation_changed(i, m.get_value_as_float())
                    if ws()
                    else None
                )
            )

    def build(self) -> None:
        self._section = CollapsibleSection(
            "Rendering Settings",
            collapsed=True,
        )
        with self._section.body:
            with ui.VStack(spacing=4):
                ui.Spacer(height=4)
                self._build_overlay_color_row()
                self._build_trajectory_color_row()
                self._build_velocity_coloring_row()
                self._build_velocity_legend_row()
                ui.Spacer(height=4)
                section_divider()
                self._build_reference_frame_row()
                self._build_mounting_offset_row()
                self._build_mounting_rotation_row()

    def set_tcp_velocity(self, velocity: float | None) -> None:
        """Store the robot's auto TCP velocity (used as the legend's max label)."""
        if velocity is not None and velocity > 0:
            self.tcp_velocity = velocity
        if self._velocity_legend_max_label:
            self._velocity_legend_max_label.text = self._velocity_legend_max_text()

    def set_reference_frame(self, path: str | None) -> None:
        self.reference_frame_path = path or None

    def set_mounting_offset(self, offset: list[float] | None) -> None:
        if offset and len(offset) == 3:
            self.mounting_offset = [float(v) for v in offset]
            for model, value in zip(self._mounting_offset_models, self.mounting_offset):
                model.set_value(value / 1000.0)

    def set_mounting_rotation(self, rotation: list[float] | None) -> None:
        if rotation and len(rotation) == 3:
            self.mounting_rotation = [float(v) for v in rotation]
            for model, value in zip(
                self._mounting_rotation_models, self.mounting_rotation
            ):
                model.set_value(value)

    def _notify(self, key: str, value: object) -> None:
        if self._on_setting_changed:
            self._on_setting_changed(key, value)

    def _build_overlay_color_row(self) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Overlay Color",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
            )
            widget = ui.ColorWidget(*self.overlay_color, width=26, height=26)
            widget.model.add_end_edit_fn(
                lambda m, _item, ws=weakref.ref(self): (
                    ws()._on_overlay_color_changed(m) if ws() else None
                )
            )
            ui.Spacer(width=5)

    def _on_overlay_color_changed(self, model) -> None:
        items = model.get_item_children()
        self.overlay_color = [
            model.get_item_value_model(items[i]).get_value_as_float() for i in range(3)
        ]
        hex_color = color_utils.float_array_to_hex(self.overlay_color + [0.3])
        settings = carb.settings.get_settings()
        settings.set(CARB_OVERLAY_COLOR, hex_color)
        self._notify("overlay_color", self.overlay_color)

    def _build_trajectory_color_row(self) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Trajectory Color",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
            )
            widget = ui.ColorWidget(*self.trajectory_color, width=26, height=26)
            widget.model.add_end_edit_fn(
                lambda m, _item, ws=weakref.ref(self): (
                    ws()._on_trajectory_color_changed(m) if ws() else None
                )
            )
            ui.Spacer(width=5)

    def _on_trajectory_color_changed(self, model) -> None:
        items = model.get_item_children()
        self.trajectory_color = [
            model.get_item_value_model(items[i]).get_value_as_float() for i in range(3)
        ]
        self._notify("trajectory_color", self.trajectory_color)

    def _build_velocity_coloring_row(self) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Velocity Coloring",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip_fn=lambda: build_tooltip(
                    "Color the trajectory curve by TCP speed (green = fast, "
                    "red = slow). When off, the solid Trajectory Color is used."
                ),
                style=TOOLTIP_RESET,
            )
            with ui.VStack(width=20):
                ui.Spacer()
                self._velocity_coloring_checkbox = ui.CheckBox(
                    width=20,
                    height=20,
                    style={
                        "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                        "background_color": 0xFF1A1A1A,
                        "font_size": 14,
                    },
                )
                ui.Spacer()
            self._velocity_coloring_checkbox.model.set_value(self.velocity_coloring)
            self._velocity_coloring_checkbox.model.add_value_changed_fn(
                lambda m, ws=weakref.ref(self): (
                    ws()._on_velocity_coloring_toggled(m.get_value_as_bool())
                    if ws()
                    else None
                )
            )
            ui.Spacer(width=5)

    def _build_velocity_legend_row(self) -> None:
        # A red(slow)->green(fast) gradient matching the trajectory velocity overlay
        # (see planning_orchestrator._speeds_to_colors). Visible only when coloring is on.
        self._velocity_legend = ui.VStack(
            spacing=2, height=0, visible=self.velocity_coloring
        )
        # Align the spectrum's left edge with the checkbox column (label width +
        # the row spacing), matching the velocity-coloring row layout above.
        left_margin = 5 + _LABEL_WIDTH + 16
        with self._velocity_legend:
            with ui.HStack(height=10):
                ui.Spacer(width=left_margin)
                steps = 16
                with ui.HStack(spacing=0):
                    for i in range(steps):
                        t = i / (steps - 1)
                        ui.Rectangle(
                            style={"background_color": ui.color(1.0 - t, t, 0.0)}
                        )
                ui.Spacer(width=5)
            with ui.HStack(height=14):
                ui.Spacer(width=left_margin)
                ui.Label(
                    "0",
                    alignment=ui.Alignment.LEFT_CENTER,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 11,
                    },
                )
                self._velocity_legend_max_label = ui.Label(
                    self._velocity_legend_max_text(),
                    alignment=ui.Alignment.RIGHT_CENTER,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 11,
                    },
                )
                ui.Spacer(width=5)

    def _velocity_legend_max_text(self) -> str:
        if self.tcp_velocity and self.tcp_velocity > 0:
            return f"{self.tcp_velocity:.0f} mm/s"
        return "max"

    def _on_velocity_coloring_toggled(self, enabled: bool) -> None:
        self.velocity_coloring = enabled
        if self._velocity_legend:
            self._velocity_legend.visible = enabled
        self._notify("velocity_coloring", enabled)

    def _build_reference_frame_row(self) -> None:
        stage = omni.usd.get_context().get_stage()
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Reference Frame",
                width=_LABEL_WIDTH,
                style=TOOLTIP_RESET,
                tooltip_fn=lambda t=("Prim the trajectory is drawn relative to (it follows this prim's transform). Leave empty to use the robot base. Visualization only — the planned motion is unchanged."): (
                    build_tooltip(t)
                ),
            )
            self._reference_frame_frame = ui.Frame()
            with self._reference_frame_frame:
                if stage:
                    ref_prim = (
                        stage.GetPrimAtPath(self.reference_frame_path)
                        if self.reference_frame_path
                        else None
                    )
                    self._reference_frame_picker = PrimPicker(
                        stage=stage,
                        prim_picked_fn=self._on_reference_frame_picked,
                        prim=ref_prim if (ref_prim and ref_prim.IsValid()) else None,
                        dialog_properties=PrimPickerDialogProperties(
                            title="Select Reference Frame",
                            filter_fn=lambda p: bool(UsdGeom.Xformable(p)),
                        ),
                    )
            ui.Spacer(width=5)

    def _on_reference_frame_picked(self, prim) -> None:
        self.reference_frame_path = (
            prim.GetPath().pathString if (prim and prim.IsValid()) else None
        )

    def _build_mounting_offset_row(self) -> None:
        # spacing=16 + Spacer(5) gives the label the same left margin as the rows
        # above (Overlay Color, Trajectory Color, ...).
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Mounting Offset (m)",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                style=TOOLTIP_RESET,
                tooltip_fn=lambda t=("Translation XYZ (m) to correct the trajectory's placement, e.g. when the robot is mounted on an external axis. Visualization only — the planned motion is unchanged."): (
                    build_tooltip(t)
                ),
            )
            self._mounting_offset_frame = ui.Frame()
            self._rebuild_mounting_offset_row()
            ui.Spacer(width=5)

    def _rebuild_mounting_offset_row(self) -> None:
        """Draw the inline colored X/Y/Z offset fields."""
        if self._mounting_offset_frame is None:
            return
        self._mounting_offset_frame.clear()
        with self._mounting_offset_frame:
            fields = [
                CoordinateInputFieldModel(
                    model=self._mounting_offset_models[i],
                    label=axis,
                    tooltip=f"{axis} offset (m)",
                )
                for i, axis in enumerate(("X", "Y", "Z"))
            ]
            CoordinatesInput(fields=fields)

    def _on_offset_changed(self, axis_idx: int, value: float) -> None:
        if 0 <= axis_idx < len(self.mounting_offset):
            # The field displays metres; the offset is stored/consumed as mm.
            self.mounting_offset[axis_idx] = value * 1000.0

    def _build_mounting_rotation_row(self) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Mounting Rotation (deg)",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                style=TOOLTIP_RESET,
                tooltip_fn=lambda t=("Rotation XYZ (degrees, extrinsic) to correct the trajectory's orientation, e.g. when the robot's mount is rotated relative to the reference frame. Visualization only — the planned motion is unchanged."): (
                    build_tooltip(t)
                ),
            )
            self._mounting_rotation_frame = ui.Frame()
            self._rebuild_mounting_rotation_row()
            ui.Spacer(width=5)

    def _rebuild_mounting_rotation_row(self) -> None:
        """Draw the inline colored X/Y/Z rotation fields."""
        if self._mounting_rotation_frame is None:
            return
        self._mounting_rotation_frame.clear()
        with self._mounting_rotation_frame:
            fields = [
                CoordinateInputFieldModel(
                    model=self._mounting_rotation_models[i],
                    label=axis,
                    tooltip=f"{axis} rotation (deg)",
                    step=1.0,
                )
                for i, axis in enumerate(("X", "Y", "Z"))
            ]
            CoordinatesInput(fields=fields)

    def _on_rotation_changed(self, axis_idx: int, value: float) -> None:
        if 0 <= axis_idx < len(self.mounting_rotation):
            self.mounting_rotation[axis_idx] = value

    @staticmethod
    def _read_overlay_color_from_carb() -> list[float]:
        settings = carb.settings.get_settings()
        hex_color = settings.get_as_string(CARB_OVERLAY_COLOR)
        if hex_color:
            rgba = color_utils.hex_to_float_array(hex_color)
            return rgba[:3]
        return [0.4, 1.0, 0.4]
