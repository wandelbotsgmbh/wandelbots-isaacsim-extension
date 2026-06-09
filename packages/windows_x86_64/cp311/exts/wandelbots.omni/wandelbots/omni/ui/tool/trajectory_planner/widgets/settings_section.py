"""Collapsible settings panel for trajectory planner parameters."""

from __future__ import annotations

import weakref
from typing import Callable

import carb
import carb.settings
import omni.ui as ui

import wandelbots.omni.ui.colors as color_utils
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import _TOOLTIP_SUB
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.utils.teaching import CARB_SETTINGS_PREFIX

_LABEL_WIDTH = 170
CARB_OVERLAY_COLOR = f"{CARB_SETTINGS_PREFIX}/overlay_color"
_ALGORITHM_OPTIONS = ["RRTConnectAlgorithm", "MidpointInsertionAlgorithm"]
_ALGORITHM_DISPLAY = ["RRTConnect", "MidpointInsertion"]


class SettingsSection:
    """Collapsible settings widget for live-update, colors, velocity, and payload."""

    def __init__(
        self,
        live_update: bool = False,
        overlay_color: list[float] | None = None,
        trajectory_color: list[float] | None = None,
        tcp_velocity: float = 500.0,
        tcp_acceleration: float = 2000.0,
        auto_blending: bool = False,
        blending_min_velocity_percent: int = 50,
        global_blending: dict | None = None,
        global_limits_override: dict | None = None,
        payload_name: str = "",
        payload_mass: float = 0.0,
        cf_algorithm: str = "RRTConnectAlgorithm",
        cf_max_iterations: int = 10000,
        move_to_start: bool = False,
        on_setting_changed: Callable[[str, object], None] | None = None,
    ) -> None:
        self.live_update = live_update
        self.overlay_color = overlay_color or self._read_overlay_color_from_carb()
        self.trajectory_color = trajectory_color or [0.808, 0.0, 0.345]
        self.tcp_velocity = tcp_velocity
        self.tcp_acceleration = tcp_acceleration
        self.auto_blending = auto_blending
        self.blending_min_velocity_percent = blending_min_velocity_percent
        self.global_blending = global_blending
        self.global_limits_override = global_limits_override
        self.payload_name = payload_name
        self.payload_mass = payload_mass
        self.cf_algorithm = cf_algorithm
        self.cf_max_iterations = cf_max_iterations
        self.move_to_start = move_to_start
        self._on_setting_changed = on_setting_changed
        self._live_update_checkbox: ui.CheckBox | None = None
        self._move_to_start_checkbox: ui.CheckBox | None = None
        self._algorithm_combo: ui.ComboBox | None = None
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
                self._build_overlay_color_row()
                self._build_trajectory_color_row()
                self._build_global_motion_settings_row()
                self._build_payload_name_row()
                self._build_float_row(
                    "Payload Mass [kg]",
                    self.payload_mass,
                    "Mass of the payload in kilograms.",
                    "payload_mass",
                )
                self._build_algorithm_row()
                self._build_int_row(
                    "CF Max Iterations",
                    self.cf_max_iterations,
                    "Maximum iterations for collision-free planning algorithm.",
                    "cf_max_iterations",
                )
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
                tooltip="Automatically re-run IK when poses are moved.",
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
        with ui.HStack(height=32, spacing=16):
            ui.Spacer(width=5)
            self._global_settings_button = ui.Button(
                "Global Motion Settings…",
                height=26,
                clicked_fn=lambda ws=weakref.ref(self): (
                    ws()._open_global_motion_settings() if ws() else None
                ),
                tooltip=(
                    "Configure global blending and TCP velocity/acceleration limits "
                    "applied to all motion commands (and collision-free planning)."
                ),
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
                **_TOOLTIP_SUB,
            }
        return {
            "Button": {"background_color": 0xFF292929},
            "Button:hovered": {"background_color": NOVAColor.BUTTON_HOVER.color},
            **_TOOLTIP_SUB,
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
                tooltip=tooltip,
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

    def _build_payload_name_row(self) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "Payload Name",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip="Name of the payload attached to the TCP.",
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

    def _build_algorithm_row(self) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                "CF Algorithm",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip="Path planning algorithm for collision-free segments.",
            )
            initial_idx = 0
            if self.cf_algorithm in _ALGORITHM_OPTIONS:
                initial_idx = _ALGORITHM_OPTIONS.index(self.cf_algorithm)
            self._algorithm_combo = ui.ComboBox(
                initial_idx, *_ALGORITHM_DISPLAY, height=22
            )
            self._algorithm_combo.model.add_item_changed_fn(
                lambda m, _item, ws=weakref.ref(self): (
                    ws()._on_algorithm_changed(m) if ws() else None
                )
            )
            ui.Spacer(width=5)

    def _on_algorithm_changed(self, model) -> None:
        idx = model.get_item_value_model().get_value_as_int()
        if 0 <= idx < len(_ALGORITHM_OPTIONS):
            self.cf_algorithm = _ALGORITHM_OPTIONS[idx]
            self._notify("cf_algorithm", self.cf_algorithm)

    def _build_int_row(self, label: str, value: int, tooltip: str, key: str) -> None:
        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label(
                label,
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip=tooltip,
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
                tooltip=(
                    "Before executing, plan and run a PTP move to the trajectory start position. "
                    "Without this the virtual robot is teleported there."
                ),
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

    @staticmethod
    def _read_overlay_color_from_carb() -> list[float]:
        settings = carb.settings.get_settings()
        hex_color = settings.get_as_string(CARB_OVERLAY_COLOR)
        if hex_color:
            rgba = color_utils.hex_to_float_array(hex_color)
            return rgba[:3]
        return [0.4, 1.0, 0.4]
