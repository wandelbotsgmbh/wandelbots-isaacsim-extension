"""Collapsible settings panel for trajectory planner parameters."""

from __future__ import annotations

import weakref
from typing import Callable

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.utils.kinematics import clamp_step_size
from wandelbots.omni.ui.wb_theme import TOOLTIP_RESET, build_tooltip
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection

_LABEL_WIDTH = 170

#: What the field shows, and accepts, for "let the algorithm decide".
_ADAPTIVE_TEXT = "Auto"


def _parsed_number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


class SettingsSection:
    """Collapsible settings widget for live-update, colors, velocity, and payload."""

    def __init__(
        self,
        live_update: bool = False,
        tcp_velocity: float = 500.0,
        tcp_acceleration: float = 2000.0,
        auto_blending: bool = False,
        blending_min_velocity_percent: int = 50,
        global_blending: dict | None = None,
        global_limits_override: dict | None = None,
        payload_name: str = "",
        payload_mass: float = 0.0,
        cf_max_iterations: int = 10000,
        cf_step_size: float | None = None,
        plan_collision_free: bool = False,
        move_to_start: bool = False,
        on_setting_changed: Callable[[str, object], None] | None = None,
    ) -> None:
        self.live_update = live_update
        self.tcp_velocity = tcp_velocity
        self.tcp_acceleration = tcp_acceleration
        self.auto_blending = auto_blending
        self.blending_min_velocity_percent = blending_min_velocity_percent
        self.global_blending = global_blending
        self.global_limits_override = global_limits_override
        self.payload_name = payload_name
        self.payload_mass = payload_mass
        self.cf_max_iterations = cf_max_iterations
        self.cf_step_size = cf_step_size
        self.plan_collision_free = plan_collision_free
        self.move_to_start = move_to_start
        self._on_setting_changed = on_setting_changed
        self._live_update_checkbox: ui.CheckBox | None = None
        self._move_to_start_checkbox: ui.CheckBox | None = None
        self._cf_step_size_field: ui.StringField | None = None
        self._global_settings_button: ui.Button | None = None
        self._collision_free: bool = False
        self._motion_group_limits: dict | None = None

    def build(self) -> None:
        self._settings_section = CollapsibleSection(
            "Settings",
            collapsed=True,
        )
        with self._settings_section.body:
            with ui.VStack(spacing=4):
                ui.Spacer(height=4)
                self._build_global_motion_settings_row()
                self._build_payload_name_row()
                self._build_float_row(
                    "Payload Mass [kg]",
                    self.payload_mass,
                    "Mass of the payload in kilograms.",
                    "payload_mass",
                )
                self._build_int_row(
                    "CF Max Iterations",
                    self.cf_max_iterations,
                    "Maximum iterations for collision-free planning algorithm.",
                    "cf_max_iterations",
                )
                self._build_cf_step_size_row()
                self._build_move_to_start_row()

    def set_tcp_limits(
        self, velocity: float | None, acceleration: float | None
    ) -> None:
        """Store the robot's auto TCP velocity/acceleration (used as defaults).

        These are now edited via the Global Motion Settings modal, not inline; the
        modal's reference values come from set_motion_group_limits().
        """
        if velocity is not None and velocity > 0:
            self.tcp_velocity = velocity
        if acceleration is not None and acceleration > 0:
            self.tcp_acceleration = acceleration

    def set_collision_free(self, collision_free: bool) -> None:
        """Track planning mode. Global motion settings stay available in both modes."""
        self._collision_free = collision_free

    def set_motion_group_limits(self, limits: dict | None) -> None:
        """Store motion group limits for display in the override dialog."""
        self._motion_group_limits = limits

    def _notify(self, key: str, value: object) -> None:
        if self._on_setting_changed:
            self._on_setting_changed(key, value)

    def _build_live_update_row(self) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Live Update",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip_fn=lambda: build_tooltip(
                    "Automatically re-run IK when poses are moved."
                ),
                style=TOOLTIP_RESET,
            )
            with ui.VStack(width=20):
                ui.Spacer()
                self._live_update_checkbox = ui.CheckBox(
                    width=20,
                    height=20,
                    style={
                        "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                        "background_color": 0xFF1A1A1A,
                        "font_size": 14,
                    },
                )
                ui.Spacer()
            self._live_update_checkbox.model.set_value(self.live_update)
            self._live_update_checkbox.model.add_value_changed_fn(
                lambda m, ws=weakref.ref(self): (
                    ws()._on_live_update_toggled(m.get_value_as_bool())
                    if ws()
                    else None
                )
            )
            ui.Spacer(width=5)

    def _on_live_update_toggled(self, enabled: bool) -> None:
        self.live_update = enabled
        self._notify("live_update", enabled)

    def _build_global_motion_settings_row(self) -> None:
        has_overrides = (
            self.global_blending is not None or self.global_limits_override is not None
        )
        tooltip = (
            "Configure global blending and TCP velocity/acceleration limits "
            "applied to all motion commands (and collision-free planning)."
        )
        with ui.HStack(height=32, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Motion Settings",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip_fn=lambda t=tooltip: build_tooltip(t),
                style=TOOLTIP_RESET,
            )
            ui.Spacer()
            self._global_settings_button = ui.Button(
                "Open Motion Settings",
                width=180,
                height=26,
                clicked_fn=lambda ws=weakref.ref(self): (
                    ws()._open_global_motion_settings() if ws() else None
                ),
                tooltip_fn=lambda t=tooltip: build_tooltip(t),
                style=self._global_settings_button_style(has_overrides),
            )
            ui.Spacer(width=5)

    def _global_settings_button_style(self, has_overrides: bool) -> dict:
        """Highlight the button when a global blending/limits override is set."""
        if has_overrides:
            return {
                "Button": {
                    "background_color": 0xFF292929,
                    "color": NOVAColor.PRIMARY_MAIN.color,
                    "border_width": 1,
                    "border_color": NOVAColor.PRIMARY_MAIN.color,
                    "border_radius": 4,
                },
                "Button:hovered": {"background_color": NOVAColor.BUTTON_HOVER.color},
                **TOOLTIP_RESET,
            }
        return {
            "Button": {"background_color": 0xFF292929},
            "Button:hovered": {"background_color": NOVAColor.BUTTON_HOVER.color},
            **TOOLTIP_RESET,
        }

    def _refresh_global_settings_button(self) -> None:
        if self._global_settings_button is not None:
            has_overrides = (
                self.global_blending is not None
                or self.global_limits_override is not None
            )
            self._global_settings_button.set_style(
                self._global_settings_button_style(has_overrides)
            )

    def _open_global_motion_settings(self) -> None:
        from wandelbots.omni.ui.tool.trajectory_planner.widgets.motion_settings_dialog import (
            MotionSettingsDialog,
            blending_from_dict,
            limits_from_dict,
        )

        MotionSettingsDialog(
            title="Global Motion Settings",
            blending=blending_from_dict(self.global_blending),
            limits_override=limits_from_dict(self.global_limits_override),
            on_apply=self._on_global_motion_settings_applied,
            motion_group_limits=self._motion_group_limits,
        )

    def _on_global_motion_settings_applied(self, blending, limits_override) -> None:
        from wandelbots.omni.ui.tool.trajectory_planner.widgets.motion_settings_dialog import (
            blending_to_dict,
            limits_to_dict,
        )

        self.global_blending = blending_to_dict(blending)
        self.global_limits_override = limits_to_dict(limits_override)
        self._refresh_global_settings_button()
        self._notify("global_blending", self.global_blending)
        self._notify("global_limits_override", self.global_limits_override)

    def _build_float_row(
        self,
        label: str,
        value: float,
        tooltip: str,
        key: str,
        model_attr: str | None = None,
    ) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                label,
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip_fn=lambda t=tooltip: build_tooltip(t),
                style=TOOLTIP_RESET,
            )
            field = ui.FloatField(height=22, alignment=ui.Alignment.CENTER)
            field.model.set_value(value)
            if model_attr:
                setattr(self, model_attr, field.model)
            field.model.add_value_changed_fn(
                lambda m, k=key, ws=weakref.ref(self): (
                    ws()._on_float_changed(k, m.get_value_as_float()) if ws() else None
                )
            )
            ui.Spacer(width=5)

    def _on_float_changed(self, key: str, value: float) -> None:
        clamped = max(0.0, value)
        setattr(self, key, clamped)
        self._notify(key, clamped)

    def _build_cf_step_size_row(self) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "CF Step Size",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip_fn=lambda: build_tooltip(
                    "Largest step, in joint space, that collision-free planning "
                    "extends its search by. Smaller steps follow narrow passages "
                    "more closely and plan more slowly. Clear the field, or type "
                    f"{_ADAPTIVE_TEXT}, to leave the step size to the algorithm."
                ),
                style=TOOLTIP_RESET,
            )
            # A number field cannot show "no value", so the step size is edited
            # as text: that keeps adaptive and fixed apart on one control.
            self._cf_step_size_field = ui.StringField(height=22)
            self._cf_step_size_field.model.set_value(self._cf_step_size_text())
            self._cf_step_size_field.model.add_end_edit_fn(
                lambda m, ws=weakref.ref(self): (
                    ws()._on_cf_step_size_edited(m.get_value_as_string())
                    if ws()
                    else None
                )
            )
            ui.Spacer(width=5)

    def _cf_step_size_text(self) -> str:
        if self.cf_step_size is None:
            return _ADAPTIVE_TEXT
        return f"{self.cf_step_size:g}"

    def _on_cf_step_size_edited(self, text: str) -> None:
        entered = text.strip()
        if not entered or entered.casefold() == _ADAPTIVE_TEXT.casefold():
            self.cf_step_size = None
        else:
            step_size = clamp_step_size(_parsed_number(entered))
            # Text that is not a step size the planner can use leaves the
            # setting as it was, and the line below puts the old value back in
            # the field.
            if step_size is not None:
                self.cf_step_size = step_size
        if self._cf_step_size_field is not None:
            self._cf_step_size_field.model.set_value(self._cf_step_size_text())
        self._notify("cf_step_size", self.cf_step_size)

    def _build_payload_name_row(self) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Payload Name",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip_fn=lambda: build_tooltip(
                    "Name of the payload attached to the TCP."
                ),
                style=TOOLTIP_RESET,
            )
            field = ui.StringField(height=22)
            field.model.set_value(self.payload_name)
            field.model.add_value_changed_fn(
                lambda m, ws=weakref.ref(self): (
                    ws()._on_payload_name_changed(m.get_value_as_string())
                    if ws()
                    else None
                )
            )
            ui.Spacer(width=5)

    def _on_payload_name_changed(self, value: str) -> None:
        self.payload_name = value.strip()
        self._notify("payload_name", self.payload_name)

    def _build_int_row(self, label: str, value: int, tooltip: str, key: str) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                label,
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip_fn=lambda t=tooltip: build_tooltip(t),
                style=TOOLTIP_RESET,
            )
            field = ui.IntField(height=22, alignment=ui.Alignment.CENTER)
            field.model.set_value(value)
            field.model.add_value_changed_fn(
                lambda m, k=key, ws=weakref.ref(self): (
                    ws()._on_int_changed(k, m.get_value_as_int()) if ws() else None
                )
            )
            ui.Spacer(width=5)

    def _on_int_changed(self, key: str, value: int) -> None:
        clamped = max(1, value)
        setattr(self, key, clamped)
        self._notify(key, clamped)

    def _build_move_to_start_row(self) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Move to Start",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip_fn=lambda: build_tooltip(
                    "Before executing, plan and run a PTP move to the trajectory start position. "
                    "Without this the virtual robot is teleported there."
                ),
                style=TOOLTIP_RESET,
            )
            with ui.VStack(width=20):
                ui.Spacer()
                self._move_to_start_checkbox = ui.CheckBox(
                    width=20,
                    height=20,
                    style={
                        "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                        "background_color": 0xFF1A1A1A,
                        "font_size": 14,
                    },
                )
                ui.Spacer()
            self._move_to_start_checkbox.model.set_value(self.move_to_start)
            self._move_to_start_checkbox.model.add_value_changed_fn(
                lambda m, ws=weakref.ref(self): (
                    ws()._on_move_to_start_toggled(m.get_value_as_bool())
                    if ws()
                    else None
                )
            )
            ui.Spacer(width=5)

    def _on_move_to_start_toggled(self, enabled: bool) -> None:
        self.move_to_start = enabled
        self._notify("move_to_start", enabled)
