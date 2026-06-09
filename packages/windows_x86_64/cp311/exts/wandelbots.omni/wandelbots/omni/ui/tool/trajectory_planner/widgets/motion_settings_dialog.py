"""Modal dialog for editing blending and limits override on a motion command."""

from __future__ import annotations

import copy
import weakref
from typing import Callable

import omni.ui as ui
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import _TOOLTIP_SUB

_LABEL_WIDTH = 200
_BLENDING_TYPES = ["none", "auto", "position"]
_BLENDING_LABELS = ["None", "Auto", "Position"]
_SPACE_OPTIONS = ["JOINT", "CARTESIAN"]


def blending_from_dict(d: dict | None) -> wb_v2_models.MotionCommandBlending | None:
    """Reconstruct a MotionCommandBlending from its serialized dict."""
    if not d:
        return None
    return wb_v2_models.MotionCommandBlending.from_dict(d)


def limits_from_dict(d: dict | None) -> wb_v2_models.LimitsOverride | None:
    """Reconstruct a LimitsOverride from its serialized dict."""
    if not d:
        return None
    return wb_v2_models.LimitsOverride.from_dict(d)


def blending_to_dict(b: wb_v2_models.MotionCommandBlending | None) -> dict | None:
    """Serialize a MotionCommandBlending to a dict for storage."""
    if not b:
        return None
    return b.to_dict()


def limits_to_dict(lo: wb_v2_models.LimitsOverride | None) -> dict | None:
    """Serialize a LimitsOverride to a dict for storage."""
    if not lo:
        return None
    return lo.to_dict()


def _get_blending_type(blending: wb_v2_models.MotionCommandBlending | None) -> str:
    if not blending or not blending.actual_instance:
        return "none"
    if isinstance(blending.actual_instance, wb_v2_models.BlendingAuto):
        return "auto"
    if isinstance(blending.actual_instance, wb_v2_models.BlendingPosition):
        return "position"
    return "none"


class MotionSettingsDialog:
    """Modal window for per-pose or global blending/limits override editing."""

    def __init__(
        self,
        title: str = "Motion Command Settings",
        blending: wb_v2_models.MotionCommandBlending | None = None,
        limits_override: wb_v2_models.LimitsOverride | None = None,
        on_apply: (
            Callable[
                [
                    wb_v2_models.MotionCommandBlending | None,
                    wb_v2_models.LimitsOverride | None,
                ],
                None,
            ]
            | None
        ) = None,
        motion_group_limits: dict | None = None,
        tcp_names: list[str] | None = None,
        current_tcp: str | None = None,
        on_tcp_changed: Callable[[str | None], None] | None = None,
    ) -> None:
        self._blending = copy.deepcopy(blending)
        self._limits = copy.deepcopy(limits_override) or wb_v2_models.LimitsOverride()
        self._blending_type = _get_blending_type(self._blending)
        self._on_apply_cb = on_apply
        self._mg_limits = motion_group_limits or {}
        self._tcp_names = tcp_names or []
        self._selected_tcp = current_tcp
        self._on_tcp_changed = on_tcp_changed
        self._window: ui.Window | None = None
        self._blending_type_combo: ui.ComboBox | None = None
        self._blending_stack: ui.VStack | None = None
        # Editable state for auto blending
        self._auto_min_vel: int = 50
        if (
            self._blending_type == "auto"
            and self._blending
            and self._blending.actual_instance
        ):
            self._auto_min_vel = (
                self._blending.actual_instance.min_velocity_in_percent or 50
            )
        # Editable state for position blending
        self._position_blending = (
            copy.deepcopy(self._blending.actual_instance)
            if self._blending_type == "position" and self._blending
            else wb_v2_models.BlendingPosition()
        )
        self._show(title)

    def _show(self, title: str) -> None:
        self._window = ui.Window(
            title,
            width=450,
            height=680,
            flags=ui.WINDOW_FLAGS_NO_COLLAPSE
            | ui.WINDOW_FLAGS_MODAL
            | ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        self._window.visible = True
        with self._window.frame:
            with ui.VStack(spacing=0):
                with ui.ScrollingFrame(
                    height=ui.Fraction(1),
                    vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                    horizontal_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                ):
                    with ui.VStack(spacing=0, height=0):
                        ui.Spacer(height=12)
                        with ui.HStack():
                            ui.Spacer(width=16)
                            with ui.VStack(spacing=8):
                                self._build_tcp_section()
                                ui.Spacer(height=4)
                                ui.Line(
                                    height=1,
                                    style={
                                        "color": NOVAColor.DIVIDER.color,
                                        "border_width": 0.5,
                                    },
                                )
                                ui.Spacer(height=4)
                                self._build_blending_section()
                                ui.Spacer(height=4)
                                ui.Line(
                                    height=1,
                                    style={
                                        "color": NOVAColor.DIVIDER.color,
                                        "border_width": 0.5,
                                    },
                                )
                                ui.Spacer(height=4)
                                self._build_limits_section()
                            ui.Spacer(width=16)
                        ui.Spacer(height=16)
                self._build_buttons()
                ui.Spacer(height=12)

    def _build_tcp_section(self) -> None:
        ui.Label(
            "TCP",
            style={
                "color": NOVAColor.TEXT_PRIMARY.color,
                "font_size": 15,
                "font_weight": "bold",
            },
            height=24,
        )
        ui.Spacer(height=4)
        with ui.HStack(height=26, spacing=8):
            ui.Spacer(width=5)
            ui.Label(
                "TCP",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip="TCP used for IK and planning of this pose. (default) uses the global TCP.",
            )
            if self._tcp_names:
                display_names = ["(default)"] + self._tcp_names
                selected_idx = 0
                if self._selected_tcp in self._tcp_names:
                    selected_idx = self._tcp_names.index(self._selected_tcp) + 1
                combo = ui.ComboBox(selected_idx, *display_names, height=22)
                combo.model.add_item_changed_fn(
                    lambda m, _, ws=weakref.ref(self): (
                        ws()._on_tcp_combo_changed(
                            m.get_item_value_model().get_value_as_int()
                        )
                        if ws()
                        else None
                    )
                )
            else:
                ui.Label(
                    "(no TCPs available)",
                    alignment=ui.Alignment.LEFT_CENTER,
                    style={"color": NOVAColor.TEXT_DISABLED.color},
                )
            ui.Spacer(width=5)

    def _on_tcp_combo_changed(self, idx: int) -> None:
        if idx == 0:
            self._selected_tcp = None
        elif 1 <= idx <= len(self._tcp_names):
            self._selected_tcp = self._tcp_names[idx - 1]

    def _build_blending_section(self) -> None:
        ui.Label(
            "Blending",
            style={
                "color": NOVAColor.TEXT_PRIMARY.color,
                "font_size": 15,
                "font_weight": "bold",
            },
            height=24,
        )
        ui.Spacer(height=4)
        initial_idx = (
            _BLENDING_TYPES.index(self._blending_type)
            if self._blending_type in _BLENDING_TYPES
            else 0
        )
        with ui.HStack(height=26, spacing=8):
            ui.Spacer(width=5)
            ui.Label("Type", width=_LABEL_WIDTH, alignment=ui.Alignment.LEFT_CENTER)
            self._blending_type_combo = ui.ComboBox(
                initial_idx, *_BLENDING_LABELS, height=22
            )
            self._blending_type_combo.model.add_item_changed_fn(
                lambda m, _, ws=weakref.ref(self): (
                    ws()._on_blending_type_changed(
                        m.get_item_value_model().get_value_as_int()
                    )
                    if ws()
                    else None
                )
            )
            ui.Spacer(width=5)

        self._blending_stack = ui.VStack(spacing=4)
        with self._blending_stack:
            self._rebuild_blending_fields()

    def _rebuild_blending_fields(self) -> None:
        if self._blending_type == "auto":
            self._build_int_field(
                "Min Velocity [%]",
                self._auto_min_vel,
                "Minimum velocity percentage (0-100) for auto blending.",
                self._on_auto_min_vel_changed,
            )
        elif self._blending_type == "position":
            pb = self._position_blending
            self._build_optional_float(
                "Position Zone Radius [mm]",
                pb.position_zone_radius,
                "Max radius around target where TCP path can be altered.",
                lambda v: setattr(self._position_blending, "position_zone_radius", v),
            )
            self._build_optional_float(
                "Position Zone [%]",
                pb.position_zone_percentage,
                "Max blending percentage based on trajectory length.",
                lambda v: setattr(
                    self._position_blending, "position_zone_percentage", v
                ),
            )
            self._build_optional_float(
                "Orientation Zone Radius [rad]",
                pb.orientation_zone_radius,
                "Max radius for orientation blending.",
                lambda v: setattr(
                    self._position_blending, "orientation_zone_radius", v
                ),
            )
            self._build_optional_float(
                "Orientation Zone [%]",
                pb.orientation_zone_percentage,
                "Max blending percentage for orientation.",
                lambda v: setattr(
                    self._position_blending, "orientation_zone_percentage", v
                ),
            )
            self._build_optional_float(
                "Joints Zone Radius [rad]",
                pb.joints_zone_radius,
                "Max radius for joint space blending.",
                lambda v: setattr(self._position_blending, "joints_zone_radius", v),
            )
            self._build_optional_float(
                "Joints Zone [%]",
                pb.joints_zone_percentage,
                "Max blending percentage for joint space.",
                lambda v: setattr(self._position_blending, "joints_zone_percentage", v),
            )
            space_idx = (
                _SPACE_OPTIONS.index(pb.space.value)
                if pb.space and pb.space.value in _SPACE_OPTIONS
                else 0
            )
            with ui.HStack(height=26, spacing=8):
                ui.Spacer(width=5)
                ui.Label(
                    "Space",
                    width=_LABEL_WIDTH,
                    alignment=ui.Alignment.LEFT_CENTER,
                    tooltip="Defines the space in which blending is performed.",
                )
                combo = ui.ComboBox(space_idx, *_SPACE_OPTIONS, height=22)
                combo.model.add_item_changed_fn(
                    lambda m, _, ws=weakref.ref(self): (
                        ws()._on_space_changed(
                            m.get_item_value_model().get_value_as_int()
                        )
                        if ws()
                        else None
                    )
                )
                ui.Spacer(width=5)

    def _build_limits_section(self) -> None:
        ui.Label(
            "Limits Override",
            style={
                "color": NOVAColor.TEXT_PRIMARY.color,
                "font_size": 15,
                "font_weight": "bold",
            },
            height=24,
        )
        if self._mg_limits:
            self._build_current_limits_info()
            ui.Spacer(height=6)
        ui.Spacer(height=4)

        tcp_vel_default = (
            self._mg_limits.get("tcp_velocity") if self._mg_limits else None
        )
        tcp_acc_default = (
            self._mg_limits.get("tcp_acceleration") if self._mg_limits else None
        )
        tcp_orient_vel_default = (
            self._mg_limits.get("tcp_orientation_velocity") if self._mg_limits else None
        )
        tcp_orient_acc_default = (
            self._mg_limits.get("tcp_orientation_acceleration")
            if self._mg_limits
            else None
        )

        self._build_optional_float(
            "TCP Velocity [mm/s]",
            self._limits.tcp_velocity_limit
            if self._limits.tcp_velocity_limit
            else tcp_vel_default,
            "Maximum TCP velocity limit.",
            lambda v: setattr(self._limits, "tcp_velocity_limit", v),
        )
        self._build_optional_float(
            "TCP Acceleration [mm/s²]",
            self._limits.tcp_acceleration_limit
            if self._limits.tcp_acceleration_limit
            else tcp_acc_default,
            "Maximum TCP acceleration limit.",
            lambda v: setattr(self._limits, "tcp_acceleration_limit", v),
        )
        self._build_optional_float(
            "TCP Orientation Velocity [rad/s]",
            self._limits.tcp_orientation_velocity_limit
            if self._limits.tcp_orientation_velocity_limit
            else tcp_orient_vel_default,
            "Maximum TCP rotation velocity.",
            lambda v: setattr(self._limits, "tcp_orientation_velocity_limit", v),
        )
        self._build_optional_float(
            "TCP Orientation Accel [rad/s²]",
            self._limits.tcp_orientation_acceleration_limit
            if self._limits.tcp_orientation_acceleration_limit
            else tcp_orient_acc_default,
            "Maximum TCP rotation acceleration.",
            lambda v: setattr(self._limits, "tcp_orientation_acceleration_limit", v),
        )

    def _build_current_limits_info(self) -> None:
        """Show current motion group limits as read-only reference."""
        with ui.CollapsableFrame(
            "Current Motion Group Limits",
            height=0,
            collapsed=True,
            style={
                "CollapsableFrame": {
                    "background_color": 0x1A000000,
                    "border_radius": 4,
                    "margin": 0,
                    "padding": 6,
                },
                "CollapsableFrame:hovered": {"background_color": 0x22000000},
            },
        ):
            with ui.VStack(spacing=2):
                tcp_vel = self._mg_limits.get("tcp_velocity")
                tcp_acc = self._mg_limits.get("tcp_acceleration")
                tcp_orient_vel = self._mg_limits.get("tcp_orientation_velocity")
                tcp_orient_acc = self._mg_limits.get("tcp_orientation_acceleration")

                if tcp_vel is not None:
                    self._build_info_row("TCP Velocity", f"{tcp_vel:.1f} mm/s")
                if tcp_acc is not None:
                    self._build_info_row("TCP Acceleration", f"{tcp_acc:.1f} mm/s²")
                if tcp_orient_vel is not None:
                    self._build_info_row(
                        "TCP Orient. Velocity", f"{tcp_orient_vel:.3f} rad/s"
                    )
                if tcp_orient_acc is not None:
                    self._build_info_row(
                        "TCP Orient. Accel.", f"{tcp_orient_acc:.3f} rad/s²"
                    )

                joint_vel = self._mg_limits.get("joint_velocity_limits")
                joint_acc = self._mg_limits.get("joint_acceleration_limits")
                if joint_vel:
                    vals = ", ".join(
                        f"{v:.2f}" if v is not None else "–" for v in joint_vel
                    )
                    self._build_info_row("Joint Velocities", f"[{vals}] rad/s")
                if joint_acc:
                    vals = ", ".join(
                        f"{v:.2f}" if v is not None else "–" for v in joint_acc
                    )
                    self._build_info_row("Joint Accelerations", f"[{vals}] rad/s²")

    def _build_info_row(self, label: str, value: str) -> None:
        with ui.HStack(height=18, spacing=4):
            ui.Spacer(width=4)
            ui.Label(
                label,
                width=_LABEL_WIDTH - 40,
                alignment=ui.Alignment.LEFT_CENTER,
                style={"color": NOVAColor.TEXT_SECONDARY.color, "font_size": 12},
            )
            ui.Label(
                value,
                alignment=ui.Alignment.LEFT_CENTER,
                style={"color": NOVAColor.TEXT_PRIMARY.color, "font_size": 12},
            )

    def _build_buttons(self) -> None:
        with ui.HStack(height=36, spacing=8):
            ui.Spacer(width=16)
            ui.Button(
                "Reset to defaults",
                width=130,
                height=30,
                clicked_fn=self._on_reset_defaults,
                tooltip="Reset to auto blending defaults",
                style={
                    "Button": {
                        "background_color": NOVAColor.SECONDARY_TONAL.color,
                        "border_radius": 4,
                    },
                    "Button:hovered": {
                        "background_color": NOVAColor.BUTTON_HOVER.color
                    },
                    **_TOOLTIP_SUB,
                },
            )
            ui.Spacer()
            ui.Button(
                "Cancel",
                width=80,
                height=30,
                clicked_fn=self._on_cancel,
                style={
                    "background_color": NOVAColor.SECONDARY_TONAL.color,
                    "border_radius": 4,
                    ":hovered": {"background_color": NOVAColor.BUTTON_HOVER.color},
                },
            )
            ui.Button(
                "Apply",
                width=80,
                height=30,
                clicked_fn=self._on_apply_clicked,
                style={
                    "background_color": NOVAColor.PRIMARY_MAIN.color,
                    "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                    "border_radius": 4,
                    ":hovered": {"background_color": NOVAColor.PRIMARY_LIGHT.color},
                },
            )
            ui.Spacer(width=16)

    def _build_int_field(
        self, label: str, value: int, tooltip: str, on_changed: Callable
    ) -> None:
        with ui.HStack(height=26, spacing=8):
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
                lambda m, cb=on_changed: cb(m.get_value_as_int())
            )
            ui.Spacer(width=5)

    def _build_optional_float(
        self,
        label: str,
        value: float | None,
        tooltip: str,
        on_changed: Callable,
    ) -> None:
        with ui.HStack(height=26, spacing=8):
            ui.Spacer(width=5)
            ui.Label(
                label,
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_CENTER,
                tooltip=tooltip,
            )
            field = ui.FloatField(height=22, alignment=ui.Alignment.CENTER)
            if value is not None:
                field.model.set_value(value)
            else:
                field.model.set_value(0.0)
            field.model.add_value_changed_fn(
                lambda m, cb=on_changed: cb(
                    m.get_value_as_float() if m.get_value_as_float() != 0.0 else None
                )
            )
            ui.Spacer(width=5)

    def _on_blending_type_changed(self, idx: int) -> None:
        if 0 <= idx < len(_BLENDING_TYPES):
            self._blending_type = _BLENDING_TYPES[idx]
            if self._blending_stack:
                self._blending_stack.clear()
                with self._blending_stack:
                    self._rebuild_blending_fields()

    def _on_auto_min_vel_changed(self, value: int) -> None:
        self._auto_min_vel = max(0, min(100, value))

    def _on_space_changed(self, idx: int) -> None:
        if 0 <= idx < len(_SPACE_OPTIONS):
            self._position_blending.space = wb_v2_models.BlendingSpace(
                _SPACE_OPTIONS[idx]
            )

    def _build_result_blending(self) -> wb_v2_models.MotionCommandBlending | None:
        if self._blending_type == "auto":
            return wb_v2_models.MotionCommandBlending(
                wb_v2_models.BlendingAuto(min_velocity_in_percent=self._auto_min_vel)
            )
        elif self._blending_type == "position":
            return wb_v2_models.MotionCommandBlending(self._position_blending)
        return None

    def _build_result_limits(self) -> wb_v2_models.LimitsOverride | None:
        if _has_any_limit(self._limits):
            return self._limits
        return None

    def _on_reset_defaults(self) -> None:
        """Reset to auto blending defaults and apply."""
        default_blending = wb_v2_models.MotionCommandBlending(
            wb_v2_models.BlendingAuto(min_velocity_in_percent=50)
        )
        if self._on_apply_cb:
            self._on_apply_cb(default_blending, None)
        self._close()

    def _on_apply_clicked(self) -> None:
        blending = self._build_result_blending()
        limits = self._build_result_limits()
        if self._on_tcp_changed:
            self._on_tcp_changed(self._selected_tcp)
        if self._on_apply_cb:
            self._on_apply_cb(blending, limits)
        self._close()

    def _on_cancel(self) -> None:
        self._close()

    def _close(self) -> None:
        if self._window:
            self._window.visible = False
            self._window = None


def _has_any_limit(limits: wb_v2_models.LimitsOverride) -> bool:
    return any(
        [
            limits.tcp_velocity_limit,
            limits.tcp_acceleration_limit,
            limits.tcp_orientation_velocity_limit,
            limits.tcp_orientation_acceleration_limit,
            limits.joint_velocity_limits,
            limits.joint_acceleration_limits,
        ]
    )
