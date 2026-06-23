"""Window for converting one or more prims into ghost objects.

Opened from ``Create -> Wandelbots NOVA -> Convert to Ghost Object``. The user
picks a scene motion group and one of that motion group's TCP sources; on confirm
a ghost object is created for each selected prim so the chosen TCP lands on that
prim. The non-UI work lives in :mod:`convert_pose_service`.
"""

from __future__ import annotations

import weakref

import omni.kit.app
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from pxr import Sdf, Usd

from wandelbots.omni.datatypes import TCPSource
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.create_context_menu.pose.convert_pose_service import (
    ConvertPoseService,
)
from wandelbots.omni.ui.utils import defer_call
from wandelbots.omni.usd import SchemaUtils

_LABEL_WIDTH = 140
_DISABLED_STYLE = {"color": NOVAColor.TEXT_DISABLED.color}

_WINDOW_HEIGHT = 240


class ConvertPoseWindow:
    def __init__(self) -> None:
        self._pose_prim_paths: list[str] = []

        self._motion_group_paths: list[str] = []
        self._selected_mg_idx: int = 0

        self._tcp_sources: list[TCPSource] = []
        self._selected_tcp_idx: int = 0

        self._description_frame: ui.Frame | None = None
        self._mg_frame: ui.Frame | None = None
        self._tcp_frame: ui.Frame | None = None

        self._mg_combo_sub = None
        self._tcp_combo_sub = None

        self._action_frame: ui.Frame | None = None
        self._convert_button: ui.Button | None = None
        self._progress_bar: ui.ProgressBar | None = None
        self._progress_label: ui.Label | None = None
        self._converting: bool = False

        self.window = ui.Window(
            "Convert Poses to Ghost Objects",
            width=460,
            height=_WINDOW_HEIGHT,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR | ui.WINDOW_FLAGS_NO_COLLAPSE,
        )
        self.window.visible = False
        self._build_ui()

    # -- lifecycle ---------------------------------------------------------

    def open(self, payload: dict | None = None) -> None:
        self._pose_prim_paths = ConvertPoseService.resolve_convertible_prim_paths(
            payload
        )
        if not self._pose_prim_paths:
            nm.post_notification(
                "Select one or more prims (Xforms) to convert.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        self._selected_mg_idx = 0
        self._selected_tcp_idx = 0
        self._converting = False
        self._build_action_buttons()

        self._refresh_motion_groups()

        self._rebuild_mg_row()
        self._refresh_tcp_sources()
        self._rebuild_tcp_row()
        self._rebuild_description()
        self._refresh_convert_enabled()

        self.window.visible = True
        self.window.focus()

    # -- data --------------------------------------------------------------

    def _refresh_motion_groups(self) -> None:
        self._motion_group_paths = ConvertPoseService.list_motion_group_paths()

    def _refresh_tcp_sources(self) -> None:
        self._selected_tcp_idx = 0
        if not self._motion_group_paths:
            self._tcp_sources = []
            return
        mg_path = self._motion_group_paths[
            min(self._selected_mg_idx, len(self._motion_group_paths) - 1)
        ]
        self._tcp_sources = ConvertPoseService.list_tcp_sources(mg_path)
        run_coroutine(self._auto_select_tcp())

    async def _auto_select_tcp(self) -> None:
        """Pre-select the TCP configured for the motion group in NOVA, if any."""
        match_idx = await ConvertPoseService.match_nova_tcp_index(
            self._selected_mg_prim(), self._tcp_sources
        )
        if match_idx is not None and match_idx != self._selected_tcp_idx:
            self._selected_tcp_idx = match_idx
            defer_call(self._rebuild_tcp_row)

    def _selected_mg_prim(self) -> Usd.Prim | None:
        if not self._motion_group_paths:
            return None
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return None
        path = self._motion_group_paths[
            min(self._selected_mg_idx, len(self._motion_group_paths) - 1)
        ]
        prim = stage.GetPrimAtPath(path)
        return prim if (prim and prim.IsValid()) else None

    def _refresh_convert_enabled(self) -> None:
        """Enable Convert only when the selected motion group exposes a TCP source
        to build the ghost from; the MG/TCP rows already explain when it can't."""
        if self._convert_button is not None:
            self._convert_button.enabled = bool(self._tcp_sources)

    # -- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        with self.window.frame:
            with ui.HStack():
                ui.Spacer(width=10)
                with ui.VStack(spacing=8):
                    ui.Spacer(height=4)
                    self._description_frame = ui.Frame(height=0)
                    ui.Spacer(height=4)

                    with ui.HStack(height=24):
                        ui.Label(
                            "Motion Group",
                            width=_LABEL_WIDTH,
                            tooltip="Scene robot (MotionGroupAPI) whose tool is cloned",
                        )
                        self._mg_frame = ui.Frame()

                    with ui.HStack(height=24):
                        ui.Label(
                            "TCP",
                            width=_LABEL_WIDTH,
                            tooltip="TCP source the ghost object is aligned to",
                        )
                        self._tcp_frame = ui.Frame()

                    ui.Spacer(height=4)

                    self._action_frame = ui.Frame(height=0)
                ui.Spacer(width=10)

    def _build_action_buttons(self) -> None:
        """Show the Cancel / Convert buttons (right-aligned) in the action area."""
        if self._action_frame is None:
            return
        self._progress_bar = None
        self._progress_label = None
        self._action_frame.clear()
        with self._action_frame:
            with ui.HStack(height=28, spacing=8):
                ui.Spacer()
                ui.Button(
                    "Cancel",
                    width=100,
                    clicked_fn=lambda ws=weakref.proxy(self): ws._on_cancel(),
                )
                self._convert_button = ui.Button(
                    "Convert",
                    width=100,
                    clicked_fn=lambda ws=weakref.proxy(self): ws._on_confirm(),
                    enabled=bool(self._tcp_sources),
                    style={
                        "Button": {
                            "background_color": NOVAColor.PRIMARY_MAIN.color,
                        },
                        "Button:hovered": {
                            "background_color": NOVAColor.PRIMARY_LIGHT.color,
                        },
                    },
                )

    def _build_action_progress(self) -> None:
        """Replace the buttons with a compact progress bar and a status label."""
        if self._action_frame is None:
            return
        self._convert_button = None
        self._action_frame.clear()
        with self._action_frame:
            with ui.VStack(spacing=4):
                # font_size 1 collapses the built-in percentage text so only the
                # primary-coloured fill shows.
                self._progress_bar = ui.ProgressBar(
                    height=4,
                    style={
                        "color": NOVAColor.PRIMARY_MAIN.color,
                        "background_color": NOVAColor.PROGRESS_BAR_BACKGROUND.color,
                        "secondary_color": NOVAColor.PROGRESS_BAR_BACKGROUND.color,
                        "border_radius": 2,
                        "font_size": 1,
                    },
                )
                self._progress_bar.model.set_value(0.0)
                self._progress_label = ui.Label(
                    "",
                    height=0,
                    style={"color": NOVAColor.TEXT_SECONDARY.color, "font_size": 13},
                )

    def _rebuild_description(self) -> None:
        if self._description_frame is None:
            return
        self._description_frame.clear()
        with self._description_frame:
            ui.Label(
                "Create a ghost object at each selected prim for the chosen motion "
                "group and TCP.",
                word_wrap=True,
                style={"color": NOVAColor.TEXT_SECONDARY.color},
                height=0,
            )

    def _rebuild_mg_row(self) -> None:
        if self._mg_frame is None:
            return
        self._mg_combo_sub = None
        self._mg_frame.clear()
        with self._mg_frame:
            if not self._motion_group_paths:
                ui.Label("No motion group found in scene", style=_DISABLED_STYLE)
                return
            names = [Sdf.Path(p).name or p for p in self._motion_group_paths]
            idx = min(self._selected_mg_idx, len(names) - 1)
            combo = ui.ComboBox(idx, *names)

            def _on_changed(model: ui.AbstractItemModel, _, ws=weakref.proxy(self)):
                new_idx = model.get_item_value_model().as_int
                if new_idx == ws._selected_mg_idx:
                    return
                ws._selected_mg_idx = new_idx
                ws._refresh_tcp_sources()
                ws._rebuild_tcp_row()
                ws._refresh_convert_enabled()

            self._mg_combo_sub = combo.model.subscribe_item_changed_fn(_on_changed)

    def _rebuild_tcp_row(self) -> None:
        if self._tcp_frame is None:
            return
        self._tcp_combo_sub = None
        self._tcp_frame.clear()
        with self._tcp_frame:
            if not self._motion_group_paths:
                ui.Label("Select a motion group first", style=_DISABLED_STYLE)
                return
            if not self._tcp_sources:
                ui.Label("No TCP found for this motion group", style=_DISABLED_STYLE)
                return
            names = [source.name for source in self._tcp_sources]
            idx = min(self._selected_tcp_idx, len(names) - 1)
            combo = ui.ComboBox(idx, *names)

            def _on_changed(model: ui.AbstractItemModel, _, ws=weakref.proxy(self)):
                ws._selected_tcp_idx = model.get_item_value_model().as_int

            self._tcp_combo_sub = combo.model.subscribe_item_changed_fn(_on_changed)

    # -- actions -----------------------------------------------------------

    def _on_cancel(self) -> None:
        self.window.visible = False

    def _on_confirm(self) -> None:
        if self._converting:
            return

        stage = omni.usd.get_context().get_stage()
        if stage is None or not self._pose_prim_paths:
            return

        if not self._tcp_sources:
            nm.post_notification(
                "The selected motion group has no TCP source to build a ghost object.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        tcp_source = self._tcp_sources[
            min(self._selected_tcp_idx, len(self._tcp_sources) - 1)
        ]
        tcp_prim = stage.GetPrimAtPath(tcp_source.prim_path)
        if not tcp_prim or not tcp_prim.IsValid():
            nm.post_notification(
                f"TCP prim '{tcp_source.prim_path}' is no longer valid.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return
        tool_prim = SchemaUtils.find_parent_tool(tcp_prim)
        if tool_prim is None:
            nm.post_notification(
                "Could not find the tool the selected TCP belongs to.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        # Swap the buttons for a progress bar and convert one ghost at a time.
        self._converting = True
        self._build_action_progress()
        run_coroutine(self._run_conversion(stage, tcp_prim, tool_prim))

    def _update_progress(self, ghost_name: str, done: int, total: int) -> None:
        if self._progress_bar is not None:
            self._progress_bar.model.set_value(done / total if total else 0.0)
        if self._progress_label is not None:
            self._progress_label.text = (
                f"Converting {ghost_name}  ·  {done} / {total} converted"
            )

    async def _run_conversion(
        self,
        stage: Usd.Stage,
        tcp_prim: Usd.Prim,
        tool_prim: Usd.Prim,
    ) -> None:
        """Create the ghost objects one at a time, updating the progress bar between
        each so the UI stays responsive."""
        total = len(self._pose_prim_paths)
        created = 0
        failed: list[str] = []
        try:
            for pose_path in self._pose_prim_paths:
                pose_name = Sdf.Path(pose_path).name or pose_path
                self._update_progress(f"{pose_name}_go", created, total)
                # Yield first so the label/bar render before the (blocking) USD work.
                await omni.kit.app.get_app().next_update_async()

                if ConvertPoseService.create_ghost_for_pose(
                    stage, pose_path, tcp_prim, tool_prim
                ):
                    created += 1
                else:
                    failed.append(pose_name)
                self._update_progress(f"{pose_name}_go", created, total)
        finally:
            self._converting = False

        if created == 0:
            nm.post_notification(
                "Failed to create any ghost objects.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            self.window.visible = False
            return
        if failed:
            nm.post_notification(
                f"Created {created} ghost object(s); "
                f"{len(failed)} failed: {', '.join(failed)}.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )

        self.window.visible = False

    def __del__(self) -> None:
        self.window.visible = False
