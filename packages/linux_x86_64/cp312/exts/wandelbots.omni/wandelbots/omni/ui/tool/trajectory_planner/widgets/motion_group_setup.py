"""Motion group, TCP, and collision scene setup widget."""

from __future__ import annotations

import weakref
from typing import Callable

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine

import wandelbots.usd as wb_schema  # type: ignore
import wandelbots_api_client.v2 as wb_v2

from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    get_motion_group_configuration_from_prim,
)
from wandelbots.omni.manipulators.utils import get_scene_motion_group_prim_paths
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import ICON_BTN_STYLE
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.ui.widgets.prim_picker import (
    PrimPicker,
    PrimPickerDialogProperties,
)
from wandelbots.omni.ui.widgets.tcp_selector import TcpSelector
from wandelbots.omni.utils.api import ApiConfiguration, get_api_client_from_config

_LABEL_WIDTH = 170


class MotionGroupSetup:
    """Groups the Motion Group picker, TCP selector, and Collision Scene selector."""

    def __init__(
        self,
        on_motion_group_changed: Callable[[MotionGroupConfiguration | None], None]
        | None = None,
        on_tcp_changed: Callable[[str | None], None] | None = None,
        on_collision_setup_changed: Callable[[str | None], None] | None = None,
    ) -> None:
        self._on_motion_group_changed = on_motion_group_changed
        self._on_tcp_changed = on_tcp_changed
        self._on_collision_setup_changed = on_collision_setup_changed

        self._robot_prim = None
        self._mg_config: MotionGroupConfiguration | None = None
        self._robot_picker: PrimPicker | None = None
        self._robot_frame: ui.Frame | None = None

        self._tcp_selector: TcpSelector | None = None
        self._tcp_frame: ui.Frame | None = None
        self._pending_tcp_name: str | None = None

        self._collision_frame: ui.Frame | None = None
        self._collision_setups: list[str] = []
        self._selected_collision_setup: str | None = None
        self._collision_combo_sub = None

    @property
    def robot_prim(self):
        return self._robot_prim

    @property
    def mg_config(self) -> MotionGroupConfiguration | None:
        return self._mg_config

    @property
    def selected_tcp(self) -> str | None:
        if self._tcp_selector:
            return self._tcp_selector.selected_tcp
        return None

    @property
    def tcp_names(self) -> list[str]:
        if self._tcp_selector and self._tcp_selector._tcp_names_model:
            return self._tcp_selector._tcp_names_model.tcp_names
        return []

    @property
    def nova_tcps(self) -> dict:
        if self._tcp_selector:
            return self._tcp_selector._nova_tcps
        return {}

    @property
    def selected_collision_setup(self) -> str | None:
        return self._selected_collision_setup

    def get_api_configuration(self) -> ApiConfiguration | None:
        if not self._mg_config:
            return None
        return self._mg_config.motion_stream_configuration.get_api_configuration()

    def set_robot_prim(self, prim) -> None:
        self._robot_prim = prim
        if prim:
            self._mg_config = get_motion_group_configuration_from_prim(prim)
        else:
            self._mg_config = None

    def set_pending_tcp(self, tcp_name: str | None) -> None:
        self._pending_tcp_name = tcp_name

    def set_collision_setup(self, setup: str | None) -> None:
        self._selected_collision_setup = setup

    def build(self) -> None:
        stage = omni.usd.get_context().get_stage()

        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label("Motion Group", width=_LABEL_WIDTH)
            self._robot_frame = ui.Frame()
            with self._robot_frame:
                if stage:
                    self._robot_picker = PrimPicker(
                        stage=stage,
                        prim_picked_fn=self._on_robot_picked,
                        prim=self._robot_prim,
                        dialog_properties=PrimPickerDialogProperties(
                            title="Select Motion Group (MotionGroupAPI)",
                            filter_fn=lambda p: p.HasAPI(wb_schema.MotionGroupAPI),
                        ),
                    )
            ui.Spacer(width=5)

        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label("Default TCP", width=_LABEL_WIDTH)
            self._tcp_frame = ui.Frame()
            self._rebuild_tcp_row()
            ui.Spacer(width=5)

        with ui.HStack(height=26, spacing=16):
            ui.Spacer(width=5)
            ui.Label("Collision Scene", width=_LABEL_WIDTH)
            self._collision_frame = ui.Frame()
            self._rebuild_collision_row()
            ui.Spacer(width=5)

        self._auto_select_if_single()

    def _auto_select_if_single(self) -> None:
        """Auto-select the motion group if exactly one is present in the scene."""
        if self._robot_prim is not None:
            return
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        prim_paths = get_scene_motion_group_prim_paths(include_prims_without_api=False)
        if len(prim_paths) != 1:
            return
        prim = stage.GetPrimAtPath(prim_paths[0])
        if not prim or not prim.IsValid():
            return
        self._on_robot_picked(prim)
        # Refresh the PrimPicker to show the auto-selected prim
        if self._robot_frame:
            self._robot_frame.clear()
            with self._robot_frame:
                self._robot_picker = PrimPicker(
                    stage=stage,
                    prim_picked_fn=self._on_robot_picked,
                    prim=self._robot_prim,
                    dialog_properties=PrimPickerDialogProperties(
                        title="Select Motion Group (MotionGroupAPI)",
                        filter_fn=lambda p: p.HasAPI(wb_schema.MotionGroupAPI),
                    ),
                )

    def _on_robot_picked(self, prim) -> None:
        self._robot_prim = prim
        self._mg_config = None
        self._tcp_selector = None

        if prim is None:
            self._rebuild_tcp_row()
            self._rebuild_collision_row()
            if self._on_motion_group_changed:
                self._on_motion_group_changed(None)
            return

        config = get_motion_group_configuration_from_prim(prim)
        if config is None:
            nm.post_notification(
                "Prim does not have MotionGroupAPI.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            self._rebuild_tcp_row()
            self._rebuild_collision_row()
            return

        self._mg_config = config
        self._rebuild_tcp_row()
        self._rebuild_collision_row()
        if self._on_motion_group_changed:
            self._on_motion_group_changed(config)

    def _rebuild_tcp_row(self) -> None:
        if self._tcp_frame is None:
            return
        self._tcp_frame.clear()
        self._tcp_selector = None

        if not self._mg_config:
            with self._tcp_frame:
                ui.Label(
                    "Select a motion group first",
                    style={"color": NOVAColor.TEXT_DISABLED.color},
                )
            return

        msc = self._mg_config.motion_stream_configuration
        api_config = msc.get_api_configuration()

        with self._tcp_frame:
            self._tcp_selector = TcpSelector(
                api_configuration=api_config,
                cell=msc.cell,
                controller=msc.controller,
                motion_group=msc.motion_group,
                selected_tcp=self._pending_tcp_name,
                select_first_tcp_fallback=True,
                tcp_changed_fn=self._on_tcp_selection_changed,
            )

    def _on_tcp_selection_changed(self, tcp_name: str) -> None:
        self._pending_tcp_name = tcp_name
        if self._on_tcp_changed:
            self._on_tcp_changed(tcp_name)

    def _rebuild_collision_row(self) -> None:
        if self._collision_frame is None:
            return
        self._collision_frame.clear()
        self._collision_combo_sub = None

        if not self._mg_config:
            with self._collision_frame:
                ui.Label(
                    "Select a motion group first",
                    style={"color": NOVAColor.TEXT_DISABLED.color},
                )
            return

        run_coroutine(self._fetch_collision_setups())

    async def _fetch_collision_setups(self) -> None:
        api_config = self.get_api_configuration()
        if not api_config or not self._mg_config:
            return
        msc = self._mg_config.motion_stream_configuration
        try:
            async with get_api_client_from_config(api_config) as api:
                self._collision_setups = await wb_v2.StoreCollisionSetupsApi(
                    api
                ).list_stored_collision_setups_keys(cell=msc.cell)
        except Exception as exc:
            carb.log_warn(f"Failed to fetch collision setups: {exc}")
            self._collision_setups = []
        self._build_collision_ui()

    def _build_collision_ui(self) -> None:
        if self._collision_frame is None:
            return
        self._collision_frame.clear()
        self._collision_combo_sub = None

        with self._collision_frame:
            if not self._collision_setups:
                with ui.HStack(spacing=16):
                    ui.Label(
                        "None available",
                        style={"color": NOVAColor.TEXT_DISABLED.color},
                    )
                    ui.Button(
                        "Open Collision Setup",
                        width=150,
                        height=22,
                        tooltip="Open the Collision Setup window to create one.",
                        clicked_fn=lambda: self._open_collision_setup_window(),
                        style={
                            "background_color": 0xFF292929,
                            "font_size": 12,
                            ":hovered": {
                                "background_color": NOVAColor.BUTTON_HOVER.color
                            },
                        },
                    )
                    ui.Button(
                        "",
                        width=22,
                        height=22,
                        image_url=get_icon("refresh.svg"),
                        image_width=14,
                        image_height=14,
                        tooltip="Refresh collision scenes",
                        clicked_fn=lambda ws=weakref.ref(self): (
                            run_coroutine(ws()._fetch_collision_setups())
                            if ws()
                            else None
                        ),
                        style={
                            "background_color": 0x00000000,
                            ":hovered": {
                                "background_color": NOVAColor.BUTTON_HOVER.color
                            },
                        },
                    )
                return

            with ui.HStack(spacing=4):
                labels = ["None"] + self._collision_setups
                current_idx = 0
                if self._selected_collision_setup in self._collision_setups:
                    current_idx = (
                        self._collision_setups.index(self._selected_collision_setup) + 1
                    )

                combo = ui.ComboBox(current_idx, *labels)
                self._collision_combo_sub = combo.model.subscribe_item_changed_fn(
                    lambda m, _, ws=weakref.ref(self): (
                        ws()._on_collision_setup_selected(
                            m.get_item_value_model().get_value_as_int()
                        )
                        if ws()
                        else None
                    )
                )
                ui.Button(
                    "",
                    width=22,
                    height=22,
                    image_url=get_icon("refresh.svg"),
                    image_width=14,
                    image_height=14,
                    tooltip="Refresh collision scenes",
                    clicked_fn=lambda ws=weakref.ref(self): (
                        run_coroutine(ws()._fetch_collision_setups()) if ws() else None
                    ),
                    style=ICON_BTN_STYLE,
                )

    def _on_collision_setup_selected(self, idx: int) -> None:
        new_value = None if idx == 0 else self._collision_setups[idx - 1]
        if new_value == self._selected_collision_setup:
            return
        self._selected_collision_setup = new_value
        if self._on_collision_setup_changed:
            self._on_collision_setup_changed(self._selected_collision_setup)

    @staticmethod
    def _open_collision_setup_window() -> None:
        window = ui.Workspace.get_window("Collision Setup")
        if window:
            window.visible = True
        else:
            carb.log_warn("Collision Setup window not found.")
