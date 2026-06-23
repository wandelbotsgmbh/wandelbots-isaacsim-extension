from __future__ import annotations

import weakref
from typing import Callable, Optional

import omni.ui as ui
import omni.usd

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.utils.scene import SceneUtils
from .input_row import LABEL_WIDTH, build_input_row

_DEFAULT_SPACING_MM = 200.0
_DEFAULT_RANGE_MM = 600.0
_DEFAULT_OVERLAY_COLOR = [0.15, 0.65, 0.60, 0.25]


class GridSettings(ui.VStack):
    """Grid pattern configuration: spacing, range, axes, candidate count, show-only-valid."""

    def __init__(
        self,
        on_filter_changed: Optional[Callable[[], None]] = None,
        on_any_prim_changed: Optional[Callable[[bool], None]] = None,
        on_overlay_color_changed: Optional[Callable[[list[float]], None]] = None,
        initial_overlay_color: Optional[list[float]] = None,
        **kwargs,
    ):
        kwargs.setdefault("spacing", 6)
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._on_filter_changed = on_filter_changed
        self._on_any_prim_changed = on_any_prim_changed
        self._on_overlay_color_changed = on_overlay_color_changed
        self._overlay_color: list[float] = (
            list(initial_overlay_color)
            if initial_overlay_color
            else list(_DEFAULT_OVERLAY_COLOR)
        )
        self._overlay_color_widget: ui.ColorWidget | None = None

        self._grid_spacing_model = ui.SimpleFloatModel(0.0)
        self._grid_range_model = ui.SimpleFloatModel(0.0)
        self._axis_x_checkbox: ui.CheckBox | None = None
        self._axis_y_checkbox: ui.CheckBox | None = None
        self._axis_z_checkbox: ui.CheckBox | None = None
        self._show_only_valid_checkbox: ui.CheckBox | None = None
        self._any_prim_checkbox: ui.CheckBox | None = None
        self._count_label: ui.Label | None = None
        self._spacing_label: ui.Label | None = None
        self._range_label: ui.Label | None = None

        self._stage_event_sub = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(self._on_stage_event)
        )

        # Build immediately if a stage is already open
        if omni.usd.get_context().get_stage() is not None:
            self._rebuild()

    @property
    def overlay_color(self) -> list[float]:
        return list(self._overlay_color)

    @property
    def spacing(self) -> float:
        """Grid spacing in mm."""
        return SceneUtils.value_to_millimeters(self._grid_spacing_model.as_float)

    @property
    def range(self) -> float:
        """Grid range in mm."""
        return SceneUtils.value_to_millimeters(self._grid_range_model.as_float)

    @property
    def show_only_valid(self) -> bool:
        if self._show_only_valid_checkbox is None:
            return False
        return self._show_only_valid_checkbox.model.get_value_as_bool()

    @property
    def any_prim(self) -> bool:
        if self._any_prim_checkbox is None:
            return False
        return self._any_prim_checkbox.model.get_value_as_bool()

    def get_axes(self) -> tuple[bool, bool, bool]:
        return (
            self._axis_x_checkbox.model.get_value_as_bool()
            if self._axis_x_checkbox
            else True,
            self._axis_y_checkbox.model.get_value_as_bool()
            if self._axis_y_checkbox
            else True,
            self._axis_z_checkbox.model.get_value_as_bool()
            if self._axis_z_checkbox
            else True,
        )

    def _on_stage_event(self, event) -> None:
        if event.type == int(omni.usd.StageEventType.OPENED):
            stage = omni.usd.get_context().get_stage()
            if stage is not None:
                self._rebuild()

    def _rebuild(self) -> None:
        self._grid_spacing_model.set_value(
            SceneUtils.millimeters_to_stage_value(_DEFAULT_SPACING_MM)
        )
        self._grid_range_model.set_value(
            SceneUtils.millimeters_to_stage_value(_DEFAULT_RANGE_MM)
        )
        self.clear()
        self._build()
        self._subscribe_changes()

    def destroy(self) -> None:
        self._stage_event_sub = None
        super().destroy()

    def _format_count(self) -> str:
        from wandelbots.omni.ui.tool.mounting_assistant.grid_utils import (
            count_grid_points,
        )

        axes = self.get_axes()
        n = count_grid_points(self.spacing, self.range, axes)
        active = sum(axes)
        dim = ["", "1D", "2D", "3D"][active]
        return f"{n} ({dim} grid)"

    def _build(self) -> None:
        with self:
            ui.Spacer(height=4)

            unit = SceneUtils.get_unit_label()

            def _build_spacing():
                ui.FloatDrag(model=self._grid_spacing_model, min=0.001, step=0.01)

            self._spacing_label = build_input_row(
                f"Grid Spacing [{unit}]",
                tooltip="Distance between adjacent grid points along each enabled axis.",
                build_widget_fn=_build_spacing,
            )

            def _build_range():
                ui.FloatDrag(model=self._grid_range_model, min=0.001, step=0.05)

            self._range_label = build_input_row(
                f"Grid Range [{unit}]",
                tooltip="Maximum offset from center along each enabled axis (±).",
                build_widget_fn=_build_range,
            )

            def _build_axes():
                self._axis_x_checkbox = ui.CheckBox(width=16, height=16)
                self._axis_x_checkbox.model.set_value(True)
                ui.Spacer(width=4)
                ui.Label("X", width=14)
                ui.Spacer(width=8)
                self._axis_y_checkbox = ui.CheckBox(width=16, height=16)
                self._axis_y_checkbox.model.set_value(True)
                ui.Spacer(width=4)
                ui.Label("Y", width=14)
                ui.Spacer(width=8)
                self._axis_z_checkbox = ui.CheckBox(width=16, height=16)
                self._axis_z_checkbox.model.set_value(False)
                ui.Spacer(width=4)
                ui.Label("Z", width=14)

            build_input_row(
                "Axes",
                tooltip="Enable axes to vary. Disable an axis to keep the center coordinate fixed.",
                build_widget_fn=_build_axes,
            )

            with ui.HStack(height=22, spacing=8):
                ui.Spacer(width=5)
                ui.Label(
                    "Candidates",
                    width=LABEL_WIDTH,
                    style={"color": NOVAColor.TEXT_SECONDARY.color},
                )
                self._count_label = ui.Label(
                    self._format_count(),
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 14,
                    },
                )
                ui.Spacer(width=5)

            def _build_show_only_valid():
                self._show_only_valid_checkbox = ui.CheckBox(
                    width=16,
                    height=16,
                    tooltip="Hide unreachable candidates in the viewport.",
                )
                self._show_only_valid_checkbox.model.set_value(False)
                self._show_only_valid_checkbox.model.add_value_changed_fn(
                    lambda m, _self=self: _self._on_filter_toggle()
                )

            build_input_row(
                "Show only valid",
                height=22,
                build_widget_fn=_build_show_only_valid,
            )

            def _build_any_prim():
                self._any_prim_checkbox = ui.CheckBox(
                    width=16,
                    height=16,
                    tooltip="When enabled, any prim in the stage can be selected as a target pose (disables ghost object filter).",
                )
                self._any_prim_checkbox.model.set_value(False)
                self._any_prim_checkbox.model.add_value_changed_fn(
                    lambda m, _self=self: _self._on_any_prim_toggle(m)
                )

            build_input_row(
                "Any pose prim",
                height=22,
                tooltip="When enabled, any prim in the stage can be selected as a target pose.",
                build_widget_fn=_build_any_prim,
            )

            def _build_overlay_color():
                _weak = weakref.ref(self)

                def _on_edit_done(model, *_args):
                    inst = _weak()
                    if inst is None:
                        return
                    children = model.get_item_children()
                    inst._overlay_color = [
                        model.get_item_value_model(c).get_value_as_float()
                        for c in children
                    ]
                    if inst._on_overlay_color_changed:
                        inst._on_overlay_color_changed(inst._overlay_color)

                self._overlay_color_widget = ui.ColorWidget(
                    *self._overlay_color,
                    width=0,
                    tooltip="Color (RGBA) of the robot ghost overlay for the selected candidate.",
                )
                self._overlay_color_widget.model.add_end_edit_fn(_on_edit_done)

            build_input_row(
                "Overlay Color",
                height=26,
                tooltip="Color (RGBA) of the robot ghost overlay shown for the selected candidate.",
                build_widget_fn=_build_overlay_color,
            )

            ui.Spacer(height=4)

    def _subscribe_changes(self) -> None:
        def _update_count(model, _self=self):
            if _self._count_label:
                _self._count_label.text = _self._format_count()

        self._grid_spacing_model.add_value_changed_fn(_update_count)
        self._grid_range_model.add_value_changed_fn(_update_count)
        for cb in (self._axis_x_checkbox, self._axis_y_checkbox, self._axis_z_checkbox):
            if cb is not None:
                cb.model.add_value_changed_fn(_update_count)

    def _on_filter_toggle(self) -> None:
        if self._on_filter_changed:
            self._on_filter_changed()

    def _on_any_prim_toggle(self, model: ui.AbstractValueModel) -> None:
        if self._on_any_prim_changed:
            self._on_any_prim_changed(model.get_value_as_bool())
