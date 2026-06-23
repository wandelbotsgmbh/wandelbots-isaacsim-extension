"""Reachability analysis window for testing robot model reach to TCP poses."""

from __future__ import annotations

import asyncio
import os
import weakref
from dataclasses import dataclass
from typing import Callable, Optional

import carb
import carb.settings
from omni.kit.app import SettingChangeSubscription
import omni.kit.actions.core
import omni.kit.menu.utils
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
import wandelbots_api_client.v2 as wb_v2
from omni.kit.async_engine import run_coroutine
from omni.usd import get_watcher
from pxr import Sdf, Usd, UsdGeom
import omni.client

from wandelbots.omni.constants import EXTENSION_ID, EXTENSION_WINDOW_MENU_ROOT
from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVACloudInstance, NOVAInstance
from wandelbots.omni.reachability.model_base_offsets import MODEL_BASE_OFFSETS
from wandelbots.omni.reachability.reachability_service import (
    ReachabilityResult,
    get_reachability_service,
)
from wandelbots.omni.ui.colors import NOVAColor, float_array_to_hex, hex_to_float_array
from wandelbots.omni.ui.tool.reachability.reachability_preview import (
    ReachabilityPreview,
)
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.ui.widgets.coordinates_input import (
    CoordinateInputFieldModel,
    CoordinatesInput,
)
from wandelbots.omni.ui.tool.reachability.collider_preview import ColliderPreview
from wandelbots.omni.ui.widgets.prim_picker import PrimPicker, MultiPrimPicker

WINDOW_MENU_ROOT = "Tools"
_LABEL_WIDTH = 100
_BUTTON_HEIGHT = 32
_ROW_HEIGHT = 40
_ALL_MANUFACTURERS = "All"
_ALL_REACHABILITY = "All"
_REACHABLE = "Reachable"
_UNREACHABLE = "Unreachable"
_REACHABILITY_OPTIONS = [_ALL_REACHABILITY, _REACHABLE, _UNREACHABLE]

CARB_REACHABILITY_PREVIEW_COLOR = (
    "/persistent/exts/wandelbots.omni/reachability/preview_color"
)
_DEFAULT_PREVIEW_COLOR = [0.4, 1.0, 0.4, 0.15]

# Maps lowercase API prefix → display name.
_PREFIX_TO_DISPLAY: dict[str, str] = {
    "abb": "ABB",
    "fanuc": "FANUC",
    "kuka": "KUKA",
    "universalrobots": "UR",
    "yaskawa": "Yaskawa",
}


def _extract_manufacturer(model_name: str) -> str:
    """Extract display manufacturer from 'MANUFACTURER_model' format."""
    prefix = model_name.split("_", 1)[0] if "_" in model_name else model_name
    return _PREFIX_TO_DISPLAY.get(prefix.lower(), prefix.title())


# -- TreeView model / delegate for results --------------------------------


class _ResultItem(ui.AbstractItem):
    def __init__(self, model_name: str):
        super().__init__()
        self.result: Optional[ReachabilityResult] = None
        self.model_name = ui.SimpleStringModel(model_name)
        self.status = ui.SimpleStringModel("\u2026 Pending")
        self.poses = ui.SimpleStringModel("")
        self._name = model_name
        self.is_calculating: bool = False

    def set_calculating(self) -> None:
        self.is_calculating = True
        self.result = None
        self.status.set_value("\u23f3 Calculating")
        self.poses.set_value("")

    def update(self, result: ReachabilityResult) -> None:
        self.is_calculating = False
        self.result = result
        if result.error:
            self.status.set_value("Error")
        elif result.reachable:
            self.status.set_value("\u2713 Reachable")
        else:
            self.status.set_value("\u2717 Not Reachable")
        self.poses.set_value(f"{result.reachable_count}/{result.total_poses}")


class _ResultModel(ui.AbstractItemModel):
    def __init__(self) -> None:
        super().__init__()
        self._all_items: list[_ResultItem] = []
        self._items: list[_ResultItem] = []  # filtered view
        self._search_text: str = ""
        self._manufacturer_filter: str = _ALL_MANUFACTURERS
        self._reachability_filter: str = _ALL_REACHABILITY

    def get_item_children(self, item: Optional[_ResultItem] = None):
        if item is None:
            return self._items
        return []

    def get_item_value_model_count(self, item: Optional[_ResultItem] = None) -> int:
        return 4  # icon, name, poses, action

    def get_item_value_model(
        self, item: Optional[_ResultItem] = None, column_id: int = 0
    ):
        if item is None:
            return ui.SimpleStringModel("")
        if column_id == 0:
            return item.status
        if column_id == 1:
            return item.model_name
        if column_id == 2:
            return item.poses
        return ui.SimpleStringModel("")

    def populate_models(self, model_names: list[str]) -> None:
        """Populate with model names in pending state."""
        self._all_items = [_ResultItem(name) for name in model_names]
        self._apply_filter()

    def update_item_by_name(self, model_name: str, result: ReachabilityResult) -> None:
        """Update a single item's result by model name and refresh view."""
        for item in self._all_items:
            if item._name == model_name:
                item.update(result)
                self._item_changed(item)
                return

    def set_item_calculating(self, model_name: str) -> None:
        """Mark a single item as currently calculating."""
        for item in self._all_items:
            if item._name == model_name:
                item.set_calculating()
                self._item_changed(item)
                return

    def get_manufacturers(self) -> list[str]:
        """Return sorted unique manufacturers from all items."""
        manufacturers = sorted(
            {_extract_manufacturer(item._name) for item in self._all_items}
        )
        return [_ALL_MANUFACTURERS] + manufacturers

    def set_filter(
        self,
        search_text: str = "",
        manufacturer: str = _ALL_MANUFACTURERS,
        reachability: str = _ALL_REACHABILITY,
    ) -> None:
        self._search_text = search_text.lower()
        self._manufacturer_filter = manufacturer
        self._reachability_filter = reachability
        self._apply_filter()

    def _apply_filter(self) -> None:
        filtered = self._all_items
        if self._manufacturer_filter != _ALL_MANUFACTURERS:
            filtered = [
                item
                for item in filtered
                if _extract_manufacturer(item._name) == self._manufacturer_filter
            ]
        if self._search_text:
            filtered = [
                item for item in filtered if self._search_text in item._name.lower()
            ]
        if self._reachability_filter == _REACHABLE:
            filtered = [
                item
                for item in filtered
                if item.result is not None and item.result.reachable
            ]
        elif self._reachability_filter == _UNREACHABLE:
            filtered = [
                item
                for item in filtered
                if item.result is not None and not item.result.reachable
            ]
        self._items = filtered
        self._item_changed(None)

    def clear(self) -> None:
        self._all_items = []
        self._items = []
        self._item_changed(None)

    def get_filtered_names(self) -> list[str]:
        """Return model names currently visible after filtering."""
        return [item._name for item in self._items]

    def has_items(self) -> bool:
        return len(self._all_items) > 0

    def reset_results(self) -> None:
        """Reset filtered items to pending state without clearing the table."""
        for item in self._items:
            item.result = None
            item.is_calculating = False
            item.status.set_value("\u2026 Pending")
            item.poses.set_value("")
        self._item_changed(None)


class _ResultDelegate(ui.AbstractItemDelegate):
    def __init__(self, download_fn: Callable[[str], None]) -> None:
        super().__init__()
        self._download_fn = download_fn
        self._buttons: list[ui.Button] = []  # prevent GC

    def build_branch(self, model, item, column_id, level, expanded):
        pass

    @staticmethod
    def _row_background_color(
        item: _ResultItem,
    ) -> int | None:
        """Return 20%-alpha background color based on state."""
        if item.is_calculating:
            return ui.color("#FFB30030")
        result = item.result
        if result is None:
            return None
        if result.error or not result.reachable:
            return ui.color("#EF535030")
        return ui.color("#26A69A30")

    def build_widget(self, model, item, column_id, level, expanded):
        if item is None:
            return
        result: Optional[ReachabilityResult] = item.result
        bg = self._row_background_color(item)

        with ui.ZStack(height=_ROW_HEIGHT):
            if bg is not None:
                ui.Rectangle(style={"background_color": bg, "border_radius": 2})

            with ui.Frame(style={"margin": 4}):
                if column_id == 0:
                    if item.is_calculating:
                        icon = get_icon("pending.svg")
                        icon_color = ui.color("#FFB300")
                    elif result is None:
                        icon = get_icon("pending.svg")
                        icon_color = NOVAColor.PRIMARY_CONTRAST_TEXT.color
                    elif result.error or not result.reachable:
                        icon = get_icon("cross_circle.svg")
                        icon_color = NOVAColor.PRIMARY_CONTRAST_TEXT.color
                    else:
                        icon = get_icon("checkmark_circle.svg")
                        icon_color = NOVAColor.PRIMARY_CONTRAST_TEXT.color
                    ui.Image(
                        icon,
                        width=28,
                        height=28,
                        style={"color": icon_color},
                    )
                elif column_id == 1:
                    ui.Label(
                        item._name,
                        alignment=ui.Alignment.LEFT_CENTER,
                        style={"font_size": 16},
                    )
                elif column_id == 2:
                    poses_text = (
                        f"{result.reachable_count}/{result.total_poses}"
                        if result
                        else ""
                    )
                    ui.Label(
                        poses_text,
                        alignment=ui.Alignment.LEFT_CENTER,
                        style={
                            "color": NOVAColor.TEXT_SECONDARY.color,
                            "font_size": 16,
                        },
                    )
                elif column_id == 3:
                    if result is not None:
                        is_reachable = result.reachable and not result.error
                        if is_reachable:
                            name = result.model_name
                            btn = ui.Button(
                                "Add to Scene",
                                width=100,
                                height=_BUTTON_HEIGHT,
                                clicked_fn=lambda n=name: self._download_fn(n),
                            )
                            self._buttons.append(btn)

    def build_header(self, column_id):
        headers = ["", "Model", "Poses", ""]
        ui.Label(
            headers[column_id] if column_id < len(headers) else "",
            alignment=ui.Alignment.LEFT_CENTER,
            style={"color": NOVAColor.TEXT_SECONDARY.color, "font_size": 15},
        )


# -- Window ----------------------------------------------------------------


class ReachabilityWindow:
    """Window for analyzing which robot models can reach selected TCP poses."""

    _singleton: "ReachabilityWindow | None" = None

    def __init__(self) -> None:
        # Destroy any previous instance (e.g. from hot-reload)
        if ReachabilityWindow._singleton is not None:
            try:
                ReachabilityWindow._singleton.destroy()
            except Exception as exc:
                carb.log_warn(f"Error destroying previous ReachabilityWindow: {exc}")
        ReachabilityWindow._singleton = self

        self._instances: list[NOVAInstance] = []
        self._selected_instance_idx: int = 0
        self._instance_combo_sub = None
        self._instance_frame: ui.Frame | None = None
        self._consider_colliders: bool = False
        self._display_colliders: bool = False
        self._swept_colliders: dict | None = None
        self._sweep_radius_model: ui.SimpleFloatModel = ui.SimpleFloatModel(0.0)
        self._sweep_radius_sub = None
        self._collider_preview = ColliderPreview()
        self._collider_color: list[float] = [1.0, 0.5, 0.2, 0.3]
        self._collider_color_widget: ui.ColorWidget | None = None
        self._analysis_task = None
        self._is_analyzing: bool = False

        self._stage = omni.usd.get_context().get_stage()
        self._mounting_prim_path: str | None = None
        self._target_prim_paths: list[str] = []
        self._mounting_picker: PrimPicker | None = None
        self._target_picker: MultiPrimPicker | None = None
        self._target_prim_list_frame: ui.Frame | None = None
        self._tcp_models: list[ui.SimpleFloatModel] = [
            ui.SimpleFloatModel(0.0) for _ in range(6)
        ]
        # Snapshot of the mounting pose used for the last analysis run.
        # Used consistently for preview and Add-to-Scene so they match IK.
        self._analysis_mounting_pose: WSPose | None = None

        self._result_model = _ResultModel()
        self._result_delegate = _ResultDelegate(download_fn=self._download_robot)

        # Watch mode
        self._watch_enabled: bool = False
        self._watch_subs: list = []  # USD watcher subscriptions
        self._debounce_task: asyncio.Task | None = None
        self._debounce_delay: float = 0.5  # seconds

        self._status_label: ui.Label | None = None
        self._analyze_button: ui.Button | None = None
        self._watch_checkbox: ui.CheckBox | None = None
        self._tree_view: ui.TreeView | None = None
        self._search_field: ui.StringField | None = None
        self._manufacturer_combo_sub = None
        self._filter_frame: ui.Frame | None = None
        self._reachability_frame: ui.Frame | None = None
        self._reachability_combo: ui.ComboBox | None = None
        self._reachability_combo_sub = None
        self._progress_bar: ui.ProgressBar | None = None
        self._progress_model: ui.SimpleFloatModel = ui.SimpleFloatModel(0.0)
        self._preview = ReachabilityPreview()
        self._preview_task: asyncio.Task | None = None
        self._preview_color: list[float] = self._load_preview_color()
        self._preview_color_widget: ui.ColorWidget | None = None

        def _on_color_setting_changed(
            value,
            change_type: carb.settings.ChangeEventType,
            weak_self=weakref.ref(self),
        ):
            inst = weak_self()
            if not inst:
                return
            if change_type == carb.settings.ChangeEventType.CHANGED:
                inst._preview_color = inst._load_preview_color()
                inst._preview.update_color(inst._preview_color)

        self._color_setting_sub = SettingChangeSubscription(
            CARB_REACHABILITY_PREVIEW_COLOR,
            _on_color_setting_changed,
        )

        self._window = ui.Window(
            "Robot Reachability Analysis",
            width=700,
            height=650,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        self._window.visible = False
        self._window.deferred_dock_in(
            "Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE
        )
        self._window.set_visibility_changed_fn(self._on_visibility_changed)
        self._build_ui()

        # Reset when a new stage is opened or the current one is closed.
        self._stage_event_sub = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(self._on_stage_event)
        )

    def destroy(self) -> None:
        """Tear down all resources. Safe to call multiple times."""
        self._stage_event_sub = None
        self._teardown_watchers()
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            self._debounce_task = None
        if self._analysis_task is not None:
            self._analysis_task.cancel()
            self._analysis_task = None
        if self._preview_task is not None:
            self._preview_task.cancel()
            self._preview_task = None
        self._preview.destroy()
        self._collider_preview.destroy()
        self._sweep_radius_sub = None
        self._color_setting_sub = None
        if self._window:
            self._window.set_visibility_changed_fn(None)
            self._window.visible = False
        self._window = None
        if ReachabilityWindow._singleton is self:
            ReachabilityWindow._singleton = None

    def __del__(self) -> None:
        self.destroy()

    def _on_visibility_changed(self, visible: bool) -> None:
        if not visible:
            self._preview.clear()

    def _on_stage_event(self, event) -> None:
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._stage = omni.usd.get_context().get_stage()
            self._reset()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._stage = None
            self._reset()

    def _reset(self) -> None:
        """Reset the window to its initial state."""
        # Cancel any running tasks
        if self._analysis_task is not None and not self._analysis_task.done():
            self._analysis_task.cancel()
        self._analysis_task = None
        self._is_analyzing = False
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            self._debounce_task = None

        self._teardown_watchers()
        if self._watch_checkbox is not None:
            self._watch_checkbox.model.set_value(False)
        self._watch_enabled = False

        self._preview.clear()
        if self._preview_task is not None and not self._preview_task.done():
            self._preview_task.cancel()
        self._preview_task = None

        # Clear inputs
        self._mounting_prim_path = None
        self._target_prim_paths = []
        self._analysis_mounting_pose = None
        self._consider_colliders = False
        self._display_colliders = False
        self._swept_colliders = None
        self._collider_preview.clear()
        if self._mounting_picker is not None:
            self._mounting_picker._stage = self._stage
            self._mounting_picker._clear()
        if self._target_picker is not None:
            self._target_picker._stage = self._stage
            self._target_picker._clear()
        self._rebuild_target_prim_list()

        # Clear results
        self._result_model.clear()
        self._set_status("")
        self._set_progress(0, 0)

        if self._analyze_button:
            self._analyze_button.enabled = False

    @property
    def window(self) -> ui.Window:
        return self._window

    # -- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        with self._window.frame:
            with ui.VStack(spacing=0):
                # ---- Setup (40%) ----
                with ui.VStack(height=ui.Percent(40), spacing=0):
                    with ui.ScrollingFrame(
                        vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                        horizontal_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                    ):
                        with ui.VStack(spacing=6, height=0):
                            ui.Spacer(height=4)
                            # Instance
                            with ui.HStack(height=26, spacing=8):
                                ui.Spacer(width=5)
                                ui.Label(
                                    "Instance",
                                    width=_LABEL_WIDTH,
                                    tooltip="Select the NOVA instance you want to download your robot from.",
                                )
                                self._instance_frame = ui.Frame()
                                ui.Spacer(width=5)
                            # Robot Base
                            with ui.HStack(height=26, spacing=8):
                                ui.Spacer(width=5)
                                ui.Label(
                                    "Robot Base",
                                    width=_LABEL_WIDTH,
                                    tooltip="Select the prim that defines the robot's mounting position in the scene.",
                                )
                                self._mounting_picker = PrimPicker(
                                    stage=self._stage,
                                    prim_picked_fn=lambda prim, ws=weakref.ref(self): (
                                        ws()._on_mounting_prim_picked(prim)
                                        if ws()
                                        else None
                                    ),
                                )
                                ui.Spacer(width=5)
                            # Target Poses
                            with ui.HStack(height=26, spacing=8):
                                ui.Spacer(width=5)
                                ui.Label(
                                    "Target Poses",
                                    width=_LABEL_WIDTH,
                                    tooltip="Select one or more prims that define the target poses.",
                                )
                                self._target_picker = MultiPrimPicker(
                                    stage=self._stage,
                                    prims_picked_fn=lambda prims, ws=weakref.ref(self): (
                                        ws()._on_targets_picked(prims) if ws() else None
                                    ),
                                )
                                ui.Spacer(width=5)
                            # Target Poses list (collapsable)
                            with ui.HStack(height=0, spacing=8):
                                ui.Spacer(width=5 + 8 + _LABEL_WIDTH)
                                with ui.CollapsableFrame(
                                    "Selected Target Poses",
                                    height=0,
                                    collapsed=True,
                                ):
                                    self._target_prim_list_frame = ui.Frame(height=0)
                                ui.Spacer(width=5)
                            # TCP Offset
                            with ui.HStack(height=26, spacing=8):
                                ui.Spacer(width=5)
                                ui.Label(
                                    "TCP Offset",
                                    width=_LABEL_WIDTH,
                                    tooltip="Optional offset from the robot flange to the tool center point (TCP).",
                                )
                                pos_fields = [
                                    CoordinateInputFieldModel(
                                        model=self._tcp_models[i],
                                        label=["X", "Y", "Z"][i],
                                        tooltip=["X (mm)", "Y (mm)", "Z (mm)"][i],
                                        step=0.1,
                                    )
                                    for i in range(3)
                                ]
                                CoordinatesInput(fields=pos_fields)
                                ui.Spacer(width=5)
                            with ui.HStack(height=26, spacing=8):
                                ui.Spacer(width=5)
                                ui.Spacer(width=_LABEL_WIDTH)
                                rot_fields = [
                                    CoordinateInputFieldModel(
                                        model=self._tcp_models[i + 3],
                                        label=["RX", "RY", "RZ"][i],
                                        tooltip=["RX (rad)", "RY (rad)", "RZ (rad)"][i],
                                        step=0.1,
                                    )
                                    for i in range(3)
                                ]
                                CoordinatesInput(fields=rot_fields)
                                ui.Spacer(width=5)
                            # -- Overlay settings --
                            with ui.CollapsableFrame(
                                "Overlay", height=0, collapsed=True
                            ):
                                with ui.HStack():
                                    ui.Spacer(width=20)
                                    with ui.Frame(margin=5):
                                        with ui.VStack(spacing=8, width=ui.Fraction(1)):
                                            with ui.HStack(height=26, spacing=8):
                                                ui.Label(
                                                    "Preview Color",
                                                    width=ui.Fraction(1),
                                                    tooltip="Color used to overlay robot configuration for reachable poses in the viewport.",
                                                )
                                                ui.Spacer(width=ui.Fraction(1))
                                                self._preview_color_widget = (
                                                    ui.ColorWidget(
                                                        *self._preview_color[:3],
                                                        width=26,
                                                        height=26,
                                                        style={
                                                            "border_radius": 4,
                                                        },
                                                    )
                                                )

                                                _weak_self_color = weakref.ref(self)

                                                def _color_edit_done(model, *_args):
                                                    instance = _weak_self_color()
                                                    if instance is not None:
                                                        instance._on_preview_color_changed()

                                                self._preview_color_widget.model.add_end_edit_fn(
                                                    _color_edit_done
                                                )
                                            with ui.HStack(height=26, spacing=8):
                                                ui.Label(
                                                    "Live Update",
                                                    width=ui.Fraction(1),
                                                    tooltip="Automatically re-run analysis when the Robot Base or Target Poses are moved.",
                                                )
                                                ui.Spacer(width=ui.Fraction(1))
                                                self._watch_checkbox = ui.CheckBox(
                                                    width=20,
                                                )
                                                self._watch_checkbox.model.set_value(
                                                    False
                                                )
                                                self._watch_checkbox.model.add_value_changed_fn(
                                                    lambda m, ws=weakref.proxy(self): (
                                                        ws._on_watch_toggled(
                                                            m.get_value_as_bool()
                                                        )
                                                    )
                                                )

                            # -- Collision settings --
                            with ui.CollapsableFrame(
                                "Collision", height=0, collapsed=True
                            ):
                                with ui.HStack():
                                    ui.Spacer(width=20)
                                    with ui.Frame(margin=5):
                                        with ui.VStack(spacing=8, width=ui.Fraction(1)):
                                            with ui.HStack(height=26, spacing=8):
                                                ui.Label(
                                                    "Sweep Radius",
                                                    width=ui.Fraction(1),
                                                    tooltip=(
                                                        "Sphere sweep radius around the Robot Base "
                                                        "prim to discover scene colliders (meters)."
                                                    ),
                                                )
                                                ui.FloatDrag(
                                                    model=self._sweep_radius_model,
                                                    min=0,
                                                    step=0.1,
                                                    width=ui.Pixel(120),
                                                )
                                                ui.Label("m", width=20)
                                            with ui.HStack(height=26, spacing=8):
                                                ui.Label(
                                                    "Consider Colliders",
                                                    width=ui.Fraction(1),
                                                    tooltip=(
                                                        "When enabled, scene colliders within the "
                                                        "sweep radius are included in the IK check."
                                                    ),
                                                )
                                                ui.Spacer(width=ui.Fraction(1))
                                                self._consider_colliders_cb = (
                                                    ui.CheckBox(
                                                        width=20,
                                                    )
                                                )
                                                self._consider_colliders_cb.model.set_value(
                                                    False
                                                )
                                                self._consider_colliders_cb.model.add_value_changed_fn(
                                                    lambda m, ws=weakref.proxy(self): (
                                                        ws._on_consider_colliders_toggled(
                                                            m.get_value_as_bool()
                                                        )
                                                    )
                                                )
                                            with ui.HStack(height=26, spacing=8):
                                                ui.Label(
                                                    "Display Colliders",
                                                    width=ui.Fraction(1),
                                                    tooltip=(
                                                        "Show the sweep sphere and discovered "
                                                        "colliders in the viewport."
                                                    ),
                                                )
                                                ui.Spacer(width=ui.Fraction(1))
                                                self._display_colliders_cb = (
                                                    ui.CheckBox(
                                                        width=20,
                                                    )
                                                )
                                                self._display_colliders_cb.model.set_value(
                                                    False
                                                )
                                                self._display_colliders_cb.model.add_value_changed_fn(
                                                    lambda m, ws=weakref.proxy(self): (
                                                        ws._on_display_colliders_toggled(
                                                            m.get_value_as_bool()
                                                        )
                                                    )
                                                )
                                            with ui.HStack(height=26, spacing=8):
                                                ui.Label(
                                                    "Collider Color",
                                                    width=ui.Fraction(1),
                                                    tooltip="Color used to visualize discovered scene colliders in the viewport.",
                                                )
                                                ui.Spacer(width=ui.Fraction(1))
                                                self._collider_color_widget = (
                                                    ui.ColorWidget(
                                                        *self._collider_color[:3],
                                                        width=26,
                                                        height=26,
                                                        style={
                                                            "border_radius": 4,
                                                        },
                                                    )
                                                )

                                                _weak_self_cc = weakref.ref(self)

                                                def _collider_color_edit_done(
                                                    model, *_args
                                                ):
                                                    instance = _weak_self_cc()
                                                    if instance is not None:
                                                        instance._on_collider_color_changed()

                                                self._collider_color_widget.model.add_end_edit_fn(
                                                    _collider_color_edit_done
                                                )
                            # Analyze
                            with ui.HStack(
                                height=40, spacing=8, alignment=ui.Alignment.CENTER
                            ):
                                ui.Spacer(width=5)
                                ui.Spacer()
                                self._analyze_button = ui.Button(
                                    "Analyze Reachability",
                                    width=200,
                                    height=34,
                                    enabled=False,
                                    tooltip="Run reachability analysis for all selected robot models.",
                                    clicked_fn=lambda ws=weakref.proxy(self): (
                                        ws._on_analyze_clicked()
                                    ),
                                    style={
                                        "background_color": NOVAColor.PRIMARY_MAIN.color,
                                        "font_size": 15,
                                        ":disabled": {
                                            "background_color": NOVAColor.DIVIDER.color,
                                            "color": NOVAColor.TEXT_SECONDARY.color,
                                        },
                                    },
                                )
                                ui.Button(
                                    "Clear Preview",
                                    width=100,
                                    height=34,
                                    tooltip="Remove the reachability overlay from the viewport.",
                                    clicked_fn=lambda ws=weakref.proxy(self): (
                                        ws._preview.clear()
                                    ),
                                )
                                ui.Spacer(width=5)
                            ui.Spacer(height=4)

                # Divider
                ui.Line(
                    height=1,
                    style={"border_width": 1, "color": NOVAColor.DIVIDER.color},
                )

                # ---- Filter bar ----
                with ui.ZStack(height=30):
                    ui.Rectangle(
                        style={
                            "background_color": ui.color("#FFFFFF1A"),
                            "border_radius": 0,
                        }
                    )
                    with ui.VStack(spacing=4):
                        ui.Spacer(height=2)
                        with ui.HStack(height=22, spacing=0):
                            ui.Spacer(width=8)
                            with ui.ZStack(width=ui.Fraction(1), height=22):
                                self._search_field = ui.StringField(
                                    height=22,
                                    style={
                                        "background_color": ui.color("#1A1A1A"),
                                        "border_radius": 4,
                                        "color": NOVAColor.TEXT_SECONDARY.color,
                                    },
                                )
                                self._search_placeholder = ui.Label(
                                    "Filter robot models by string ...",
                                    style={
                                        "color": ui.color("#666666"),
                                        "margin_width": 6,
                                    },
                                    alignment=ui.Alignment.LEFT_CENTER,
                                )

                                def _on_search_changed(
                                    m,
                                    ws=weakref.ref(self),
                                ):
                                    inst = ws()
                                    if not inst:
                                        return
                                    inst._search_placeholder.visible = (
                                        m.get_value_as_string() == ""
                                    )
                                    inst._on_filter_changed()

                                self._search_field.model.add_value_changed_fn(
                                    _on_search_changed
                                )
                            ui.Spacer(width=6)
                            self._filter_frame = ui.Frame(width=130)
                            ui.Spacer(width=6)
                            self._reachability_frame = ui.Frame(width=110)
                            with self._reachability_frame:
                                self._reachability_combo = ui.ComboBox(
                                    0, *_REACHABILITY_OPTIONS
                                )
                                self._reachability_combo_sub = self._reachability_combo.model.subscribe_item_changed_fn(
                                    lambda m, _, ws=weakref.ref(self): (
                                        ws()._on_filter_changed() if ws() else None
                                    )
                                )
                            ui.Spacer(width=8)
                        ui.Spacer(height=2)

                # ---- Results TreeView (60%) ----
                with ui.ScrollingFrame(
                    vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                    horizontal_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                    height=ui.Fraction(1),
                ):
                    self._tree_view = ui.TreeView(
                        self._result_model,
                        delegate=self._result_delegate,
                        root_visible=False,
                        header_visible=True,
                        height=0,
                        selection_changed_fn=lambda selection, ws=weakref.ref(self): (
                            ws()._on_selection_changed(selection) if ws() else None
                        ),
                        column_widths=[
                            ui.Pixel(36),
                            ui.Fraction(4),
                            ui.Fraction(1),
                            ui.Pixel(110),
                        ],
                        style={
                            "TreeView.Item": {"margin": 1},
                            "TreeView.Row": {"margin": 1},
                            "TreeView": {"background_color": 0x00000000},
                        },
                    )

                # ---- Progress + Status below TreeView ----
                self._progress_bar = ui.ProgressBar(
                    model=self._progress_model,
                    height=1,
                    style={
                        "color": NOVAColor.PRIMARY_MAIN.color,
                        "background_color": NOVAColor.DIVIDER.color,
                        "border_radius": 1,
                        "font_size": 0,
                    },
                )
                self._progress_bar.visible = False
                with ui.HStack(height=20):
                    ui.Spacer(width=6)
                    self._status_label = ui.Label(
                        "",
                        style={
                            "color": NOVAColor.TEXT_SECONDARY.color,
                            "font_size": 12,
                        },
                    )

        self._refresh_instances()
        self._rebuild_instance_row()
        self._rebuild_manufacturer_combo()

        self._sweep_radius_sub = self._sweep_radius_model.add_value_changed_fn(
            lambda m, ws=weakref.ref(self): (
                ws()._on_sweep_radius_changed() if ws() else None
            )
        )

    def _refresh_analyze_button(self) -> None:
        if self._analyze_button:
            self._analyze_button.enabled = bool(self._mounting_prim_path) and bool(
                self._target_prim_paths
            )

    def _rebuild_target_prim_list(self) -> None:
        if self._target_prim_list_frame is None:
            return
        self._target_prim_list_frame.clear()
        with self._target_prim_list_frame:
            with ui.VStack(spacing=2, height=0):
                ui.Spacer(height=2)
                for path in self._target_prim_paths:
                    ui.Label(
                        path,
                        height=20,
                        style={
                            "color": NOVAColor.TEXT_SECONDARY.color,
                            "font_size": 14,
                        },
                    )
                if not self._target_prim_paths:
                    ui.Label(
                        "No prims selected.",
                        height=20,
                        style={
                            "color": NOVAColor.TEXT_SECONDARY.color,
                            "font_size": 14,
                        },
                    )
                ui.Spacer(height=2)

    # -- Callbacks ---------------------------------------------------------

    def _on_mounting_prim_picked(self, prim) -> None:
        if prim is None:
            self._mounting_prim_path = None
            carb.log_info("Robot Base cleared")
        else:
            self._mounting_prim_path = prim.GetPath().pathString
            carb.log_info(f"Mounting position set to: {self._mounting_prim_path}")

        self._refresh_analyze_button()
        if self._watch_enabled:
            self._setup_watchers()
        if self._display_colliders:
            self._refresh_collider_preview()

    def _on_targets_picked(self, prims: list[Usd.Prim]) -> None:
        self._target_prim_paths = [prim.GetPath().pathString for prim in prims]
        carb.log_info(f"{len(self._target_prim_paths)} target prim(s) selected")

        self._refresh_analyze_button()
        self._rebuild_target_prim_list()
        if self._watch_enabled:
            self._setup_watchers()

    def _on_watch_toggled(self, enabled: bool) -> None:
        self._watch_enabled = enabled
        if enabled:
            self._setup_watchers()
        else:
            self._teardown_watchers()

    def _on_consider_colliders_toggled(self, enabled: bool) -> None:
        self._consider_colliders = enabled
        if not enabled:
            self._swept_colliders = None
            self._collider_preview.clear()

    def _on_display_colliders_toggled(self, enabled: bool) -> None:
        self._display_colliders = enabled
        if enabled:
            if not self._mounting_prim_path:
                nm.post_notification(
                    "Select a Robot Base prim first to display colliders.",
                    duration=4.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return
            self._refresh_collider_preview()
        else:
            self._collider_preview.clear()

    def _on_sweep_radius_changed(self) -> None:
        if self._display_colliders:
            self._refresh_collider_preview()

    def _refresh_collider_preview(self) -> None:
        """Run a sphere sweep and display the sphere + colliders in the viewport."""
        self._collider_preview.clear()

        radius = self._sweep_radius_model.as_float if self._sweep_radius_model else 0.0
        if radius <= 0.0 or not self._mounting_prim_path:
            return

        run_coroutine(self._refresh_collider_preview_async(radius))

    async def _refresh_collider_preview_async(self, radius: float) -> None:
        service = get_reachability_service()
        try:
            pose = PrimUtils.get_prim_pose(
                prim_path=self._mounting_prim_path,
                coordinate_system="world",
                rotation_type="cartesian",
                stage=omni.usd.get_context().get_stage(),
            )
            center_mm = (pose.pose[0], pose.pose[1], pose.pose[2])

            colliders = await service.sweep_colliders_around_prim(
                self._mounting_prim_path, radius
            )
            self._swept_colliders = colliders
            if self._watch_enabled:
                self._setup_watchers()
            nm.post_notification(
                f"Sphere sweep found {len(colliders)} collider(s)",
                duration=3.0,
                status=nm.NotificationStatus.INFO,
            )
            self._collider_preview.show(
                center_mm, radius, colliders, self._collider_color
            )
        except Exception as exc:
            carb.log_warn(f"Collider preview failed: {exc}")
            nm.post_notification(
                f"Collider preview failed: {exc}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )

    def _setup_watchers(self) -> None:
        """Subscribe to transform changes on all watched prims."""
        self._teardown_watchers()
        watcher = get_watcher()
        paths_to_watch: list[str] = []
        if self._mounting_prim_path:
            paths_to_watch.append(self._mounting_prim_path)
        paths_to_watch.extend(self._target_prim_paths)

        if self._consider_colliders and self._swept_colliders:
            stage = omni.usd.get_context().get_stage()
            for key in self._swept_colliders:
                prim_path = (
                    key.rsplit("/", 1)[0] if key[-1].isdigit() and "/" in key else key
                )
                if prim_path not in paths_to_watch:
                    if stage and stage.GetPrimAtPath(prim_path).IsValid():
                        paths_to_watch.append(prim_path)
                parent_path = Sdf.Path(prim_path).GetParentPath().pathString
                if parent_path and parent_path not in paths_to_watch:
                    if stage and stage.GetPrimAtPath(parent_path).IsValid():
                        paths_to_watch.append(parent_path)

        for prim_path in paths_to_watch:
            sub = watcher.subscribe_to_change_info_path(
                Sdf.Path(prim_path),
                lambda path=None, ws=weakref.ref(self): (
                    ws()._on_watched_prim_changed(path) if ws() else None
                ),
            )
            self._watch_subs.append(sub)

        if paths_to_watch:
            carb.log_info(
                f"Watching {len(paths_to_watch)} prim(s) for transform changes"
            )

    def _teardown_watchers(self) -> None:
        self._watch_subs.clear()

    def _on_watched_prim_changed(self, path: Sdf.Path = None) -> None:
        if path is not None:
            path_str = path.pathString
            if not (
                path_str.endswith(":translate")
                or path_str.endswith(":rotate")
                or path_str.endswith(":orient")
                or path_str.endswith(":scale")
                or path_str.endswith(".xformOp:transform")
            ):
                return
        if self._is_analyzing:
            return
        # Debounce: restart the timer on each change so the analysis
        # only triggers once the user finishes dragging.
        if self._debounce_task is not None:
            self._debounce_task.cancel()
        self._debounce_task = run_coroutine(self._debounced_analyze())

    async def _debounced_analyze(self) -> None:
        await asyncio.sleep(self._debounce_delay)
        self._debounce_task = None
        self._on_analyze_clicked()

    def _on_analyze_clicked(self) -> None:
        if self._is_analyzing:
            return

        instance = self._get_instance()
        if instance is None:
            nm.post_notification(
                "No NOVA instance connected. Please connect to NOVA first.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        if not self._target_prim_paths:
            nm.post_notification(
                "No targets set. Select prims and click 'Set from Selection'.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        self._is_analyzing = True
        if self._analyze_button:
            self._analyze_button.enabled = False

        # Cancel any previous analysis that may still be running
        if self._analysis_task is not None and not self._analysis_task.done():
            self._analysis_task.cancel()

        self._preview.clear()
        self._set_status("Extracting poses...")
        self._analysis_task = run_coroutine(self._run_analysis())

    # -- Rebuild helpers ---------------------------------------------------

    def _get_instance(self) -> NOVAInstance | None:
        """Return the currently selected NOVA instance, or None."""
        if not self._instances:
            return None
        idx = min(self._selected_instance_idx, len(self._instances) - 1)
        return self._instances[idx]

    def _refresh_instances(self) -> None:
        api = get_instances_api()
        self._instances = [
            inst
            for instances in api.get_cloud_instances().values()
            for inst in instances
        ] + api.get_custom_instances()

    def _rebuild_instance_row(self) -> None:
        if self._instance_frame is None:
            return
        self._instance_combo_sub = None
        self._instance_frame.clear()
        with self._instance_frame:
            if not self._instances:
                ui.Label(
                    "No instances available",
                    style={"color": NOVAColor.TEXT_SECONDARY.color},
                )
                return
            names = [inst.display_name for inst in self._instances]
            idx = min(self._selected_instance_idx, len(names) - 1)
            combo = ui.ComboBox(idx, *names)

            def _on_instance_changed(
                model: ui.AbstractItemModel, _, ws=weakref.ref(self)
            ) -> None:
                w = ws()
                if w is None:
                    return
                new_idx = model.get_item_value_model().as_int
                if new_idx == w._selected_instance_idx:
                    return
                w._selected_instance_idx = new_idx

            self._instance_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_instance_changed
            )

    def _set_status(self, message: str) -> None:
        if self._status_label:
            self._status_label.text = message

    def _set_progress(self, current: int, total: int) -> None:
        if self._progress_bar:
            self._progress_bar.visible = total > 0
            self._progress_model.set_value(current / total if total > 0 else 0.0)

    def _on_filter_changed(self) -> None:
        search_text = ""
        if self._search_field:
            search_text = self._search_field.model.get_value_as_string()
        reachability = _ALL_REACHABILITY
        if self._reachability_combo:
            idx = self._reachability_combo.model.get_item_value_model().as_int
            reachability = (
                _REACHABILITY_OPTIONS[idx]
                if idx < len(_REACHABILITY_OPTIONS)
                else _ALL_REACHABILITY
            )
        self._result_model.set_filter(
            search_text=search_text,
            manufacturer=self._result_model._manufacturer_filter,
            reachability=reachability,
        )

    def _rebuild_manufacturer_combo(self) -> None:
        if self._filter_frame is None:
            return
        self._manufacturer_combo_sub = None
        self._filter_frame.clear()
        manufacturers = self._result_model.get_manufacturers()
        with self._filter_frame:
            combo = ui.ComboBox(0, *manufacturers)

            def _on_manufacturer_changed(
                model: ui.AbstractItemModel,
                _,
                ws=weakref.ref(self),
                names=manufacturers,
            ) -> None:
                w = ws()
                if w is None:
                    return
                idx = model.get_item_value_model().as_int
                selected = names[idx] if idx < len(names) else _ALL_MANUFACTURERS
                w._result_model.set_filter(
                    search_text=w._result_model._search_text,
                    manufacturer=selected,
                    reachability=w._result_model._reachability_filter,
                )

            self._manufacturer_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_manufacturer_changed
            )

    def _on_selection_changed(self, selection: list[_ResultItem]) -> None:
        if self._preview_task is not None:
            self._preview_task.cancel()
            self._preview_task = None

        if not selection:
            self._preview.clear()
            return

        item: _ResultItem = selection[0]
        result = item.result
        if result is None or not result.joint_solutions:
            self._preview.clear()
            return

        instance = self._get_instance()
        if instance is None:
            return

        mounting_pose = (
            self._analysis_mounting_pose.pose if self._analysis_mounting_pose else None
        )

        self._preview_task = run_coroutine(
            self._preview.show_preview(
                result, instance, mounting_pose, self._preview_color
            )
        )

    def _refresh_selected_preview(self) -> None:
        """Re-render the preview for the currently selected tree item."""
        if self._tree_view is None:
            return
        selection = self._tree_view.selection
        if not selection:
            return
        item: _ResultItem = selection[0]
        result = item.result
        if result is None or not result.joint_solutions:
            return
        instance = self._get_instance()
        if instance is None:
            return
        mounting_pose = (
            self._analysis_mounting_pose.pose if self._analysis_mounting_pose else None
        )
        if self._preview_task is not None:
            self._preview_task.cancel()
        self._preview_task = run_coroutine(
            self._preview.show_preview(
                result, instance, mounting_pose, self._preview_color, force=True
            )
        )

    def _on_preview_color_changed(self) -> None:
        if self._preview_color_widget is None:
            return
        model = self._preview_color_widget.model
        children = model.get_item_children()
        self._preview_color = [
            model.get_item_value_model(children[0]).get_value_as_float(),
            model.get_item_value_model(children[1]).get_value_as_float(),
            model.get_item_value_model(children[2]).get_value_as_float(),
            self._preview_color[3],
        ]
        self._save_preview_color(self._preview_color)

    def _on_collider_color_changed(self) -> None:
        if self._collider_color_widget is None:
            return
        model = self._collider_color_widget.model
        children = model.get_item_children()
        self._collider_color = [
            model.get_item_value_model(children[0]).get_value_as_float(),
            model.get_item_value_model(children[1]).get_value_as_float(),
            model.get_item_value_model(children[2]).get_value_as_float(),
            self._collider_color[3],
        ]
        if self._display_colliders:
            self._refresh_collider_preview()

    @staticmethod
    def _load_preview_color() -> list[float]:
        settings = carb.settings.get_settings()
        hex_color = settings.get_as_string(CARB_REACHABILITY_PREVIEW_COLOR)
        if hex_color:
            return hex_to_float_array(hex_color)
        return list(_DEFAULT_PREVIEW_COLOR)

    @staticmethod
    def _save_preview_color(color: list[float]) -> None:
        settings = carb.settings.get_settings()
        settings.set_string(CARB_REACHABILITY_PREVIEW_COLOR, float_array_to_hex(color))

    # -- Analysis ----------------------------------------------------------

    async def _run_analysis(self) -> None:
        service = get_reachability_service()
        try:
            mounting_pose = None
            if self._mounting_prim_path:
                try:
                    mounting_pose = service.extract_mounting_pose_from_prim(
                        self._mounting_prim_path
                    )
                except ValueError as exc:
                    nm.post_notification(
                        f"Invalid mounting prim: {exc}",
                        duration=5.0,
                        status=nm.NotificationStatus.WARNING,
                    )
                    return

            self._analysis_mounting_pose = mounting_pose

            try:
                target_poses = service.extract_target_poses_from_prims(
                    self._target_prim_paths
                )
            except ValueError as exc:
                nm.post_notification(
                    str(exc), duration=5.0, status=nm.NotificationStatus.WARNING
                )
                return

            tcp_values = [m.as_float for m in self._tcp_models]
            tcp_offset = None
            if any(v != 0.0 for v in tcp_values):
                tcp_offset = WSPose(pose=tcp_values)

            instance = self._get_instance()
            if instance is None:
                nm.post_notification(
                    "No NOVA instance connected. Please connect to NOVA first.",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return

            self._set_status(f"Fetching models from '{instance.display_name}'...")

            swept_colliders = None
            if self._consider_colliders and self._mounting_prim_path:
                sweep_radius = (
                    self._sweep_radius_model.as_float
                    if self._sweep_radius_model
                    else 0.0
                )
                if sweep_radius > 0.0:
                    try:
                        self._set_status("Sweeping scene colliders...")
                        swept_colliders = await service.sweep_colliders_around_prim(
                            self._mounting_prim_path, sweep_radius
                        )
                        self._swept_colliders = swept_colliders
                        if self._watch_enabled:
                            self._setup_watchers()
                        nm.post_notification(
                            f"Sphere sweep found {len(swept_colliders)} collider(s)",
                            duration=3.0,
                            status=nm.NotificationStatus.INFO,
                        )
                        carb.log_info(
                            f"Sphere sweep found {len(swept_colliders)} collider(s)"
                        )
                        if self._display_colliders and mounting_pose:
                            center_mm = (
                                mounting_pose.pose[0],
                                mounting_pose.pose[1],
                                mounting_pose.pose[2],
                            )
                            self._collider_preview.show(
                                center_mm,
                                sweep_radius,
                                swept_colliders,
                                self._collider_color,
                            )
                    except Exception as exc:
                        carb.log_warn(f"Sphere sweep failed: {exc}")
                        nm.post_notification(
                            f"Scene sweep failed (is the timeline playing?): {exc}",
                            duration=5.0,
                            status=nm.NotificationStatus.WARNING,
                        )

            session, all_models = await service.prepare_session(
                instance,
                target_poses,
                mounting_pose,
                tcp_offset,
                static_colliders=swept_colliders,
            )

            async with session:
                # If the table already has models, run only the filtered subset;
                # otherwise populate with all models from the API first.
                if not self._result_model.has_items():
                    self._result_model.populate_models(all_models)
                    self._rebuild_manufacturer_combo()

                # Always run only the filtered (visible) subset.
                available = set(all_models)
                run_models = [
                    n for n in self._result_model.get_filtered_names() if n in available
                ]
                # Reset only the filtered items to pending
                self._result_model.reset_results()

                carb.log_info(
                    f"Running analysis for {len(run_models)} filtered model(s) "
                    f"out of {len(all_models)} available"
                )

                self._set_progress(0, len(run_models))
                self._set_status(
                    f"Analyzing {len(run_models)} model(s) against "
                    f"{len(target_poses)} target(s)..."
                )

                # Check each model one-by-one and update the UI incrementally
                reachable = 0
                for idx, model_name in enumerate(run_models):
                    self._result_model.set_item_calculating(model_name)
                    result = await service.check_single_model(session, model_name)
                    self._result_model.update_item_by_name(model_name, result)
                    if result.reachable:
                        reachable += 1
                    self._set_progress(idx + 1, len(run_models))
                    self._set_status(
                        f"Checked {idx + 1}/{len(run_models)}: {reachable} reachable so far"
                    )

                self._set_progress(0, 0)
                mounting_str = (
                    f" | Base: ({', '.join(f'{v:.0f}' for v in mounting_pose.pose[:3])})"
                    if mounting_pose
                    else ""
                )
                self._set_status(
                    f"Done: {reachable}/{len(run_models)} models can reach all "
                    f"{len(target_poses)} target(s){mounting_str}"
                )

                # Refresh the preview for the currently selected robot
                self._refresh_selected_preview()

        except Exception as exc:
            carb.log_error(f"Reachability analysis failed: {exc}")
            nm.post_notification(
                f"Analysis failed: {exc}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            self._set_status(f"Error: {exc}")

        finally:
            self._is_analyzing = False
            self._refresh_analyze_button()

    # -- Download ----------------------------------------------------------

    def _download_robot(self, model_name: str) -> None:
        run_coroutine(self._download_robot_async(model_name))

    async def _download_robot_async(self, model_name: str) -> None:
        if not self._instances:
            nm.post_notification(
                "No instance available for download",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        instance = self._get_instance()
        if instance is None:
            nm.post_notification(
                "No NOVA instance connected. Please connect to NOVA first.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        stage_url = omni.usd.get_context().get_stage_url() or ""
        if stage_url and "://" in stage_url:
            stage_dir = stage_url.rsplit("/", 1)[0] if "/" in stage_url else stage_url
            download_path = f"{stage_dir}/assets/robots/{model_name}.usd"
        elif stage_url:
            stage_dir = os.path.dirname(stage_url)
            download_path = os.path.join(
                stage_dir, "assets", "robots", f"{model_name}.usd"
            )
        else:
            result = await omni.usd.get_context().save_stage_with_callback_async(
                lambda r, e: None
            )
            if not result:
                nm.post_notification(
                    "Please save the scene before adding robots.",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return
            stage_url = omni.usd.get_context().get_stage_url() or ""
            if not stage_url:
                nm.post_notification(
                    "Scene was not saved. Please save it first.",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return
            if "://" in stage_url:
                stage_dir = (
                    stage_url.rsplit("/", 1)[0] if "/" in stage_url else stage_url
                )
                download_path = f"{stage_dir}/assets/robots/{model_name}.usd"
            else:
                stage_dir = os.path.dirname(stage_url)
                download_path = os.path.join(
                    stage_dir, "assets", "robots", f"{model_name}.usd"
                )

        api_client = self._make_api_client(instance)
        if api_client is None:
            nm.post_notification(
                f"Cannot connect to instance '{instance.display_name}'",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        try:
            self._set_status(f"Downloading '{model_name}'...")
            models_api = wb_v2.MotionGroupModelsApi(api_client)
            usd_bytes: bytearray = await models_api.get_motion_group_usd_model(
                motion_group_model=model_name
            )

            is_nucleus = "://" in download_path
            if is_nucleus:
                write_result = await omni.client.write_file_async(
                    download_path, bytes(usd_bytes)
                )
                if write_result != omni.client.Result.OK:
                    raise RuntimeError(
                        f"omni.client.write_file_async failed: {write_result}"
                    )
            else:
                parent_dir = os.path.dirname(download_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with open(download_path, "wb") as f:
                    f.write(usd_bytes)

            stage = omni.usd.get_context().get_stage()
            if stage is None:
                return

            safe_name = (
                model_name
                if Sdf.Path.IsValidIdentifier(model_name)
                else model_name.replace("-", "_").replace(" ", "_")
            )
            parent_path = (
                Sdf.Path(self._mounting_prim_path)
                if self._mounting_prim_path
                else Sdf.Path("/World")
            )
            robot_prim_path = parent_path.AppendChild(safe_name)
            xform = UsdGeom.Xform.Define(stage, robot_prim_path)
            xform.GetPrim().GetPayloads().AddPayload(download_path)

            # Place the robot under the Robot Base prim.
            # Since it's a child, the parent transform already positions it.
            # Only apply the model-specific kinematic base offset along Z.
            base_offset = MODEL_BASE_OFFSETS.get(model_name, 0.0)
            if base_offset != 0.0:
                # Offset is in meters, WSPose expects mm
                offset_pose = WSPose(pose=[0, 0, base_offset * 1000.0, 0, 0, 0])
                PrimUtils.set_prim_pose(
                    robot_prim_path.pathString,
                    offset_pose,
                    stage,
                )

            self._set_status(f"Downloaded '{model_name}'")
            nm.post_notification(
                f"Robot '{model_name}' downloaded and imported",
                duration=3.0,
                status=nm.NotificationStatus.INFO,
            )

        except Exception as exc:
            carb.log_error(f"Failed to download model '{model_name}': {exc}")
            nm.post_notification(
                f"Download failed: {exc}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )

        finally:
            try:
                await api_client.close()
            except Exception as exc:
                carb.log_warn(f"Error closing API client: {exc}")

    def _make_api_client(self, instance: NOVAInstance) -> wb_v2.ApiClient | None:
        if isinstance(instance, NOVACloudInstance):
            token = get_instances_api().get_auth_token_from_host(instance.host)
            return instance.create_api_client(token=token)
        return instance.create_api_client()


# -- Registration ----------------------------------------------------------


@dataclass
class ReachabilityWindowSubscription:
    reachability_window: ReachabilityWindow = None
    menu_subscriptions: list = None

    def __del__(self):
        if self.reachability_window:
            self.reachability_window.destroy()
            self.reachability_window = None
        if self.menu_subscriptions:
            omni.kit.menu.utils.remove_menu_items(
                self.menu_subscriptions, WINDOW_MENU_ROOT
            )


def register_reachability_window():
    reachability_window = ReachabilityWindow()

    def toggle_visibility():
        reachability_window.window.visible = not reachability_window.window.visible

    def _is_visible(
        window_ref: Callable[[], ReachabilityWindow | None] = weakref.ref(
            reachability_window
        ),
    ):
        return window_ref().window.visible if window_ref() else False

    ext_id = EXTENSION_ID
    name = "Reachability"
    action_name = "toggle_reachability_window"
    action_unique = f"{ext_id}_{name}_{action_name}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(ext_id, action_unique)
    action_registry.register_action(
        ext_id,
        action_unique,
        toggle_visibility,
        display_name=name,
        tag="MenuItem",
    )

    return ReachabilityWindowSubscription(
        reachability_window,
        omni.kit.menu.utils.add_menu_items(
            [
                omni.kit.menu.utils.MenuItemDescription(
                    name=EXTENSION_WINDOW_MENU_ROOT,
                    sub_menu=[
                        omni.kit.menu.utils.MenuItemDescription(
                            name=name,
                            onclick_action=(ext_id, action_unique),
                            ticked_fn=_is_visible,
                        )
                    ],
                )
            ],
            WINDOW_MENU_ROOT,
        ),
    )
