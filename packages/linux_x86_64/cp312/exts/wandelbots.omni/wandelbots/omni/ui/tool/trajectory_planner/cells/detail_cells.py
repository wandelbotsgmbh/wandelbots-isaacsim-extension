"""Detail row cells for expanded TreeView items (TCP pose + Joint config)."""

from __future__ import annotations

from typing import Callable

import omni.ui as ui
import omni.usd

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import TOOLTIP_STYLE, ICON_BTN_STYLE
from wandelbots.omni.ui.tool.ghost_teaching.widgets.joint_config_selector import (
    JointConfigSelector,
)
from wandelbots.omni.utils.kinematics import joint_config_signs

_DETAIL_ROW_HEIGHT = 28


def _copy_to_clipboard(text: str) -> None:
    try:
        import omni.kit.clipboard

        omni.kit.clipboard.copy(text)
    except Exception:
        pass


def build_tcp_detail(pose: WSPose, tcp_label: str) -> None:
    from wandelbots.omni.ui.utils import get_icon

    values = pose.pose
    pose_text = (
        f"({values[0]:.1f}, {values[1]:.1f}, {values[2]:.1f}, "
        f"{values[3]:.3f}, {values[4]:.3f}, {values[5]:.3f})"
    )
    pose_tooltip = (
        f"x={values[0]:.1f} [mm], y={values[1]:.1f} [mm], z={values[2]:.1f} [mm], "
        f"rx={values[3]:.3f} [rad], ry={values[4]:.3f} [rad], rz={values[5]:.3f} [rad]"
    )
    with ui.VStack(spacing=2):
        with ui.HStack(height=_DETAIL_ROW_HEIGHT, spacing=4, style=TOOLTIP_STYLE):
            ui.Label(
                f"TCP: {tcp_label}",
                alignment=ui.Alignment.LEFT_CENTER,
                style={
                    "color": NOVAColor.TEXT_SECONDARY.color,
                    "font_size": 12,
                },
            )
        with ui.HStack(height=_DETAIL_ROW_HEIGHT, spacing=4, style=TOOLTIP_STYLE):
            ui.Label(
                pose_text,
                alignment=ui.Alignment.LEFT_CENTER,
                elided_text=True,
                tooltip=pose_tooltip,
                tooltip_offset_y=14,
                style={
                    "color": NOVAColor.TEXT_SECONDARY.color,
                    "font_size": 12,
                },
            )
            ui.Button(
                "",
                width=_DETAIL_ROW_HEIGHT,
                height=_DETAIL_ROW_HEIGHT,
                image_url=get_icon("copy.svg"),
                image_width=12,
                image_height=12,
                tooltip="Copy pose to clipboard",
                clicked_fn=lambda t=pose_text: _copy_to_clipboard(t),
                style=ICON_BTN_STYLE,
            )


def build_joint_config_detail(
    joint_configs: list,
    selected_config_idx: int,
    ik_loading: bool,
) -> None:
    from wandelbots.omni.ui.utils import get_icon

    with ui.HStack(height=_DETAIL_ROW_HEIGHT, spacing=4, style=TOOLTIP_STYLE):
        if ik_loading:
            ui.Label(
                "Calculating IK...",
                alignment=ui.Alignment.LEFT_CENTER,
                style={
                    "color": NOVAColor.TEXT_DISABLED.color,
                    "font_size": 12,
                },
            )
        elif not joint_configs:
            ui.Label(
                "No IK Solution",
                alignment=ui.Alignment.LEFT_CENTER,
                style={
                    "color": NOVAColor.TEXT_DISABLED.color,
                    "font_size": 12,
                },
            )
        else:
            cfg = joint_configs[selected_config_idx]
            text = f"[{', '.join(f'{v:.2f}' for v in cfg)}]"
            ui.Label(
                text,
                alignment=ui.Alignment.LEFT_CENTER,
                elided_text=True,
                tooltip=text,
                tooltip_offset_y=14,
                style={
                    "color": NOVAColor.TEXT_SECONDARY.color,
                    "font_size": 12,
                },
            )
            ui.Button(
                "",
                width=_DETAIL_ROW_HEIGHT,
                height=_DETAIL_ROW_HEIGHT,
                image_url=get_icon("copy.svg"),
                image_width=12,
                image_height=12,
                tooltip="Copy joint config to clipboard",
                clicked_fn=lambda t=text: _copy_to_clipboard(t),
                style=ICON_BTN_STYLE,
            )


def build_joint_config_selector(
    joint_configs: list,
    selected_config_idx: int,
    is_ghost_object: bool,
    ik_loading: bool,
    prim_path: str,
    on_config_changed: Callable[[int], None] | None,
    widgets_out: list,
    subs_out: list,
    row_height: int = 44,
) -> None:
    with ui.VStack(height=row_height):
        ui.Spacer()
        with ui.HStack(height=0, spacing=4):
            if is_ghost_object:
                _build_ghost_config_selector(
                    prim_path,
                    joint_configs,
                    selected_config_idx,
                    on_config_changed,
                    widgets_out,
                )
            elif ik_loading:
                ui.Label(
                    "...",
                    alignment=ui.Alignment.CENTER,
                    style={
                        "color": NOVAColor.TEXT_DISABLED.color,
                        "font_size": 12,
                    },
                )
            elif not joint_configs:
                ui.Label(
                    "-",
                    alignment=ui.Alignment.CENTER,
                    style={
                        "color": NOVAColor.TEXT_DISABLED.color,
                        "font_size": 12,
                    },
                )
            else:
                config_labels = [
                    f"{i + 1} {joint_config_signs(cfg, None)}"
                    for i, cfg in enumerate(joint_configs)
                ]
                clamped_idx = max(0, min(selected_config_idx, len(config_labels) - 1))
                combo = ui.ComboBox(
                    clamped_idx,
                    *config_labels,
                    width=ui.Fraction(2),
                )
                if on_config_changed:
                    sub = combo.model.subscribe_item_changed_fn(
                        lambda m, _, cb=on_config_changed: cb(
                            m.get_item_value_model().get_value_as_int()
                        )
                    )
                    subs_out.append(sub)
                widgets_out.append(combo)
        ui.Spacer()


def _build_ghost_config_selector(
    prim_path: str,
    joint_configs: list,
    selected_config_idx: int,
    on_config_changed: Callable[[int], None] | None,
    widgets_out: list,
) -> None:
    stage = omni.usd.get_context().get_stage()
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        ui.Label(
            "Invalid",
            width=80,
            alignment=ui.Alignment.CENTER,
            style={"color": NOVAColor.TEXT_DISABLED.color, "font_size": 12},
        )
        return
    selector = JointConfigSelector(
        ghost_object_prim=prim,
        initial_joint_configs=joint_configs,
        joint_config_changed_fn=on_config_changed,
        write_to_prim=False,
        selected_index=selected_config_idx if joint_configs else None,
    )
    widgets_out.append(selector)
