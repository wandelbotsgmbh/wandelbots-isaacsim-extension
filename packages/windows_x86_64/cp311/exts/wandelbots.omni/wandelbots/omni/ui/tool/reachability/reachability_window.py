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
from wandelbots.omni.utils.locations import is_url, join_location, parent_location
from wandelbots.omni.utils.math import m_to_mm
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.teaching import GhostObjectUtils
from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVAInstance
from wandelbots.omni.reachability.model_base_offsets import MODEL_BASE_OFFSETS
from wandelbots.omni.reachability.reachability_service import (
    ReachabilityResult,
    ReachabilityService,
    ReachabilitySession,
    TargetPose,
    get_reachability_service,
)
from wandelbots.omni.ui.colors import NOVAColor, float_array_to_hex, hex_to_float_array
from wandelbots.omni.ui.manufacturers import (
    MANUFACTURERS,
    display_label,
    manufacturers_from_model_names,
    prefix_of_model_name,
)
from wandelbots.omni.ui.tool.reachability.reachability_preview import (
    ReachabilityPreview,
)
from wandelbots.omni.ui.utils import defer_call, get_icon
from wandelbots.omni.ui.wb_theme import (
    BUTTON_HEIGHT,
    BUTTON_PRIMARY_STYLE,
    BUTTON_STYLE,
    COMBOBOX_STYLE,
    CORNER_RADIUS,
    FIELD_STYLE,
    FONT_SIZE_SM,
    FORM_FIELD_WIDTH,
    FORM_HEADER_GAP,
    FORM_SIDE_MARGIN,
    PROGRESS_BAR_STYLE,
    SECTION_GAP,
    SPACING_MD,
    SPACING_SM,
    build_tooltip,
)
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.widgets.form_row import form_row
from wandelbots.omni.ui.widgets.icon_button import IconButton
from wandelbots.omni.ui.widgets.styled_checkbox import styled_checkbox
from wandelbots.omni.ui.widgets.coordinates_input import (
    CoordinateInputFieldModel,
    CoordinatesInput,
)
from wandelbots.omni.ui.tool.reachability.widgets import AttachToolWidget
from wandelbots.omni.ui.widgets.prim_picker import (
    MultiPrimPicker,
    PrimPickerDialogProperties,
)

WINDOW_MENU_ROOT = "Tools"
# Results-table row metrics (the setup form rows use the shared wb_theme
# metrics instead).
_BUTTON_HEIGHT = 32
_ROW_HEIGHT = 40
_ALL_MANUFACTURERS = "All"
_ALL_REACHABILITY = "All"
_REACHABLE = "Reachable"
_UNREACHABLE = "Unreachable"
_REACHABILITY_OPTIONS = [_ALL_REACHABILITY, _REACHABLE, _UNREACHABLE]
# Trailing inset of the per-pose collision setup combos, so they line up with
# the form rows' field column.
_POSE_SETUP_ROW_RIGHT_INSET = FORM_SIDE_MARGIN
# Default entry of the per-pose collision setup combo: no collision checking.
_NO_COLLISION_SETUP = "None"
# Height of the setup pane as a percentage of the window, draggable between
# the bounds; the results pane absorbs the rest.
_SETUP_PANE_DEFAULT_PERCENT = 40.0
_SETUP_PANE_MIN_PERCENT = 15.0
_SETUP_PANE_MAX_PERCENT = 85.0

CARB_REACHABILITY_PREVIEW_COLOR = (
    "/persistent/exts/wandelbots.omni/reachability/preview_color"
)
_DEFAULT_PREVIEW_COLOR = [0.4, 1.0, 0.4, 0.15]


def _extract_manufacturer(model_name: str) -> str:
    """Extract display manufacturer from 'MANUFACTURER_model' format."""
    return display_label(prefix_of_model_name(model_name))


# -- TreeView model / delegate for results --------------------------------


class _ResultItem(ui.AbstractItem):
    def __init__(self, model_name: str):
        super().__init__()
        self.result: Optional[ReachabilityResult] = None
        self.model_name = ui.SimpleStringModel(model_name)
        self.status = ui.SimpleStringModel("\u2026 Pending")
        self.poses = ui.SimpleStringModel("")
        self.name = model_name
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
        # Pre-analysis manufacturer choices; the static fallback until the
        # selected instance's model catalog has been fetched.
        self._known_manufacturers: list[str] = list(MANUFACTURERS)

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
            if item.name == model_name:
                item.update(result)
                self._refresh_item(item)
                return

    def set_item_calculating(self, model_name: str) -> None:
        """Mark a single item as currently calculating."""
        for item in self._all_items:
            if item.name == model_name:
                item.set_calculating()
                self._refresh_item(item)
                return

    def _refresh_item(self, item: _ResultItem) -> None:
        """Notify the view of an item change, re-filtering when the new state
        moves the item in or out of the current reachability facet."""
        should_be_visible = self._matches_structural_filters(
            item.name
        ) and self._passes_reachability_filter(item)
        if should_be_visible != (item in self._items):
            self._apply_filter()
        else:
            self._item_changed(item)

    def get_manufacturers(self) -> list[str]:
        """Return sorted unique manufacturers from all items.

        Before the first analysis there are no items yet, so fall back to the
        known manufacturers: that lets the user restrict already the first run
        to a manufacturer instead of having to analyze everything once.
        """
        if self._all_items:
            manufacturers = sorted(
                {_extract_manufacturer(item.name) for item in self._all_items}
            )
        else:
            manufacturers = sorted(self._known_manufacturers)
        return [_ALL_MANUFACTURERS] + manufacturers

    def set_known_manufacturers(self, labels: list[str]) -> None:
        """Replace the pre-analysis manufacturer choices (fetched from the
        selected instance's model catalog)."""
        self._known_manufacturers = labels

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

    def _matches_structural_filters(self, name: str) -> bool:
        """Search and manufacturer facets; unlike the reachability facet they
        describe the model itself, not its (possibly missing) result."""
        if (
            self._manufacturer_filter != _ALL_MANUFACTURERS
            and _extract_manufacturer(name) != self._manufacturer_filter
        ):
            return False
        return not self._search_text or self._search_text in name.lower()

    def get_analysis_names(self) -> list[str]:
        """Model names the next analysis run should test.

        Applies only the structural facets: the reachability facet classifies
        existing results and must not shrink the set of models to test.
        """
        return [
            item.name
            for item in self._all_items
            if self._matches_structural_filters(item.name)
        ]

    def _passes_reachability_filter(self, item: _ResultItem) -> bool:
        if self._reachability_filter == _REACHABLE:
            return item.result is not None and item.result.reachable
        if self._reachability_filter == _UNREACHABLE:
            return item.result is not None and not item.result.reachable
        return True

    def _apply_filter(self) -> None:
        self._items = [
            item
            for item in self._all_items
            if self._matches_structural_filters(item.name)
            and self._passes_reachability_filter(item)
        ]
        self._item_changed(None)

    def clear(self) -> None:
        self._all_items = []
        self._items = []
        self._item_changed(None)

    def get_filtered_names(self) -> list[str]:
        """Return model names currently visible after filtering."""
        return [item.name for item in self._items]

    def has_items(self) -> bool:
        return len(self._all_items) > 0

    def reset_results(self, names: list[str] | None = None) -> None:
        """Reset items to pending state without clearing the table.

        Only the given names are reset (default: the currently visible items).
        The view is re-filtered afterwards, since dropping results changes
        membership under the reachability facet.
        """
        targets = (
            set(names) if names is not None else {item.name for item in self._items}
        )
        for item in self._all_items:
            if item.name in targets:
                item.result = None
                item.is_calculating = False
                item.status.set_value("\u2026 Pending")
                item.poses.set_value("")
        self._apply_filter()

    def reset_calculating(self) -> None:
        """Reset any item stuck in the calculating state back to pending.

        Used when an analysis is cancelled mid-flight, so the row being
        checked doesn't show a permanent "Calculating" spinner.
        """
        changed = False
        for item in self._all_items:
            if item.is_calculating:
                item.is_calculating = False
                item.result = None
                item.status.set_value("\u2026 Pending")
                item.poses.set_value("")
                changed = True
        if changed:
            self._apply_filter()


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
        background_color = self._row_background_color(item)

        with ui.ZStack(height=_ROW_HEIGHT):
            if background_color is not None:
                ui.Rectangle(
                    style={"background_color": background_color, "border_radius": 2}
                )

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
                        item.name,
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
                                style=BUTTON_STYLE,
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
        self._selected_instance_index: int = 0
        self._instance_combo_sub = None
        self._instance_frame: ui.Frame | None = None
        self._analysis_task = None
        self._is_analyzing: bool = False

        self._stage = omni.usd.get_context().get_stage()
        # Candidate robot mounting positions; a target counts as reachable if
        # any base reaches it (results are the union over all bases).
        self._mounting_prim_paths: list[str] = []
        self._target_prim_paths: list[str] = []
        self._mounting_picker: MultiPrimPicker | None = None
        self._target_picker: MultiPrimPicker | None = None
        self._base_prim_list_frame: ui.Frame | None = None
        self._target_prim_list_frame: ui.Frame | None = None
        # Per-pose collision setup CHOICES: target prim path -> NOVA-stored
        # setup name, or None for an explicit "no collision checking". A
        # path absent from the dict follows _default_collision_setup - see
        # _effective_pose_collision_setup.
        self._pose_collision_setup: dict[str, Optional[str]] = {}
        # Default NOVA-stored setup applied to every pose without an
        # explicit per-pose choice; None = no collision checking by default.
        self._default_collision_setup: Optional[str] = None
        self._default_setup_frame: ui.Frame | None = None
        self._default_setup_combo_sub = None
        # Cache of collision setup names stored on NOVA for the current
        # instance, used to populate the default and per-pose dropdowns.
        self._collision_setup_names: list[str] = []
        self._collision_setup_row_subs: list = []
        self._tcp_models: list[ui.SimpleFloatModel] = [
            ui.SimpleFloatModel(0.0) for _ in range(6)
        ]
        self._attach_tool_widget: AttachToolWidget | None = None
        # Snapshot of the mounting pose used for the last single-base analysis
        # run; None for multi-base runs, where each result carries the winning
        # base per pose. Used by preview and Add-to-Scene so they match IK.
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
        # Setup/results split: the setup pane's percentage height, adjusted by
        # dragging the divider between the two panes.
        self._setup_area_percent: float = _SETUP_PANE_DEFAULT_PERCENT
        self._panes_container: ui.VStack | None = None
        self._setup_container: ui.VStack | None = None
        self._splitter_line: ui.Line | None = None
        self._splitter_drag_active: bool = False
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
            window = weak_self()
            if not window:
                return
            if change_type == carb.settings.ChangeEventType.CHANGED:
                window._preview_color = window._load_preview_color()
                window._preview.update_color(window._preview_color)

        self._color_setting_sub = SettingChangeSubscription(
            CARB_REACHABILITY_PREVIEW_COLOR,
            _on_color_setting_changed,
        )

        self._window = ui.Window(
            "Reachability Analysis",
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
        else:
            # The build-time instance fetch is a snapshot from extension
            # startup; re-fetch on every open (silently) so instances
            # added/removed via Connect-to-NOVA since then show up without a
            # manual refresh. Also re-seeds the instance-scoped dropdown data
            # (collision setup names, manufacturers).
            run_coroutine(self._refresh_instances_async(notify=False))
        # The menu tick (ticked_fn) is only re-evaluated on a menu refresh, so
        # closing the window via its title-bar X would leave the tick stale.
        omni.kit.menu.utils.refresh_menu_items(WINDOW_MENU_ROOT)

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
        self._mounting_prim_paths = []
        self._target_prim_paths = []
        self._pose_collision_setup = {}
        self._analysis_mounting_pose = None
        for picker in (self._mounting_picker, self._target_picker):
            if picker is not None:
                picker.set_stage(self._stage)
                picker.clear()
        if self._attach_tool_widget is not None:
            self._attach_tool_widget.clear()
        self._rebuild_base_prim_list()
        self._rebuild_target_prim_list()

        # Clear results
        self._result_model.clear()
        self._set_status("")
        self._set_progress(0, 0)

        self._refresh_analyze_button()

    @property
    def window(self) -> ui.Window:
        return self._window

    # -- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        # RootFrame ground (see instances/main_window.py): styled through a
        # type-name override, not a flat background_color, because a flat color
        # cascades into every descendant and overrides the scoped Button
        # styles the themed widgets depend on.
        self._window.frame.style_type_name_override = "RootFrame"
        self._window.frame.style = {
            "RootFrame": {"background_color": NOVAColor.LAYER_BASE.color}
        }
        with self._window.frame:
            self._panes_container = ui.VStack(spacing=0)
            with self._panes_container:
                self._build_setup_pane()
                self._build_splitter()
                self._build_filter_bar()
                self._build_results_pane()
                self._build_status_row()

        self._refresh_instances()
        self._rebuild_instance_row()
        self._rebuild_default_setup_row()
        self._refresh_instance_data()
        self._rebuild_manufacturer_combo()

    def _build_setup_pane(self) -> None:
        """The upper pane, whose height the splitter below it adjusts. The
        results pane absorbs whatever height is left."""
        self._setup_container = ui.VStack(
            height=ui.Percent(self._setup_area_percent), spacing=0
        )
        with self._setup_container:
            with ui.ScrollingFrame(
                vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                horizontal_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
            ):
                # The section cards sit inside the scroll container so they
                # share one right edge with the scrollbar gutter.
                with ui.HStack():
                    ui.Spacer(width=SPACING_MD)
                    with ui.VStack(spacing=SECTION_GAP, height=0):
                        ui.Spacer(height=SPACING_SM)
                        self._build_instance_section()
                        self._build_bases_section()
                        self._build_targets_section()
                        self._build_tool_section()
                        self._build_settings_section()
                        self._build_action_row()
                        ui.Spacer(height=SPACING_SM)
                    ui.Spacer(width=SPACING_MD)

    def _build_results_pane(self) -> None:
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

    def _build_status_row(self) -> None:
        self._progress_bar = ui.ProgressBar(
            model=self._progress_model,
            height=4,
            style=PROGRESS_BAR_STYLE,
        )
        self._progress_bar.visible = False
        with ui.HStack(height=20):
            ui.Spacer(width=SPACING_MD)
            self._status_label = ui.Label(
                "",
                style={
                    "color": NOVAColor.TEXT_SECONDARY.color,
                    "font_size": FONT_SIZE_SM,
                },
            )

    def _build_instance_section(self) -> None:
        section = CollapsibleSection("Instance", collapsed=False)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            with form_row(
                "Instance",
                tooltip=(
                    "Select the NOVA instance you want to download your robot from."
                ),
            ):
                with ui.HStack(height=20, spacing=SPACING_SM):
                    self._instance_frame = ui.Frame(width=ui.Fraction(1))
                    IconButton(
                        icon="refresh.svg",
                        tooltip="Refetch all available NOVA instances.",
                        clicked_fn=lambda ws=weakref.ref(self): (
                            ws()._on_refresh_instances_clicked() if ws() else None
                        ),
                    )
            with form_row(
                "Collision Setup",
                tooltip=(
                    "Default NOVA-stored collision setup applied to every "
                    "target pose. Individual poses can override it in the "
                    "Target Poses section."
                ),
            ):
                self._default_setup_frame = ui.Frame(height=0)
            ui.Spacer(height=SPACING_SM)

    def _rebuild_default_setup_row(self) -> None:
        """Fill the default collision setup combo with the current
        instance's stored setup names."""
        frame = self._default_setup_frame
        if frame is None:
            return
        self._default_setup_combo_sub = None
        # A default from a previously selected instance may not exist on
        # the current one; fall back to no collision checking.
        if (
            self._default_collision_setup
            and self._default_collision_setup not in self._collision_setup_names
        ):
            self._default_collision_setup = None
        options = [_NO_COLLISION_SETUP] + self._collision_setup_names
        selected_index = (
            options.index(self._default_collision_setup)
            if self._default_collision_setup
            else 0
        )
        frame.clear()
        with frame:
            combo = ui.ComboBox(
                selected_index,
                *options,
                height=20,
                style=COMBOBOX_STYLE,
            )

            def _on_default_changed(
                model, _, options=options, ws=weakref.ref(self)
            ) -> None:
                window = ws()
                if window is None:
                    return
                index = model.get_item_value_model().as_int
                window._default_collision_setup = (
                    options[index] if 0 < index < len(options) else None
                )
                # Poses without an explicit per-pose choice follow the
                # default; refresh their rows (deferred: containers must
                # not be rebuilt during the combo's own event pass).
                defer_call(window._rebuild_target_prim_list)

            self._default_setup_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_default_changed
            )

    def _effective_pose_collision_setup(self, path: str) -> Optional[str]:
        """The setup checked for a pose: its explicit per-pose choice
        (which may be an explicit None = no collision checking), or the
        default when the user never touched the pose's own dropdown."""
        if path in self._pose_collision_setup:
            return self._pose_collision_setup[path]
        return self._default_collision_setup

    def _build_bases_section(self) -> None:
        """Robot base picker plus the selected-prim list, one section."""
        section = CollapsibleSection("Robot Bases", collapsed=False)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            with form_row(
                "Prims",
                tooltip=(
                    "Select one or more prims as candidate robot mounting "
                    "positions. A target counts as reachable if any base "
                    "reaches it."
                ),
            ):
                self._mounting_picker = MultiPrimPicker(
                    stage=self._stage,
                    prims_picked_fn=lambda prims, ws=weakref.ref(self): (
                        ws()._on_mounting_prims_picked(prims) if ws() else None
                    ),
                    # The list below the row shows the selected paths; the
                    # picker's own "N prim(s) selected" summary is redundant.
                    show_selection_summary=False,
                )
            self._base_prim_list_frame = ui.Frame(height=0)
            ui.Spacer(height=SPACING_SM)

    def _build_targets_section(self) -> None:
        """Target pose picker plus the per-pose list (each row carrying its
        collision setup dropdown), one section."""
        section = CollapsibleSection("Target Poses", collapsed=False)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            with form_row(
                "Prims",
                tooltip=(
                    "Select one or more pose prims (ghost objects or "
                    "type:POSE prims) that define the target poses."
                ),
            ):
                self._target_picker = MultiPrimPicker(
                    stage=self._stage,
                    prims_picked_fn=lambda prims, ws=weakref.ref(self): (
                        ws()._on_targets_picked(prims) if ws() else None
                    ),
                    dialog_properties=PrimPickerDialogProperties(
                        title="Select Target Poses",
                        # A plain staticmethod reference, not a
                        # weakref-wrapped closure like the other picker
                        # callbacks - it holds no reference to `self`.
                        filter_fn=self._pose_filter,
                    ),
                    # The per-pose list below shows the selected paths; the
                    # picker's own "N prim(s) selected" summary is redundant.
                    show_selection_summary=False,
                )
            self._target_prim_list_frame = ui.Frame(height=0)
            ui.Spacer(height=SPACING_SM)

    def _build_tool_section(self) -> None:
        section = CollapsibleSection("Tool", collapsed=False)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            self._build_tool_rows()
            ui.Spacer(height=SPACING_SM)

    def _build_tool_rows(self) -> None:
        """TCP offset and tool rows; body of the "Tool" section."""
        tcp_tooltip = (
            "Optional offset from the robot flange to the tool center point (TCP)."
        )
        with form_row("TCP Position [mm]", tooltip=tcp_tooltip):
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
        with form_row("TCP Rotation [rad]", tooltip=tcp_tooltip):
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
        # Attach Tool (optional; auto-fills the TCP offset above from the
        # tool's own TCP if it has one, renders the tool mesh in the
        # preview, and contributes the tool's convex hulls to the
        # collision check)
        with form_row(
            "File",
            tooltip=(
                "Optional: attach a tool USD file. It is shown mounted on "
                "whichever model's flange is being tested, its convex "
                "hulls ride the flange during collision checking (a "
                "chosen collision setup contributes only its static "
                "colliders), and it pre-fills the TCP offset above if the "
                "tool contains a TCP prim."
            ),
        ):
            self._attach_tool_widget = AttachToolWidget(
                tcp_offset_picked_fn=self._on_tool_tcp_offset_picked,
            )
        # The TCP picker renders as its own form row ("TCP" in the label
        # column) directly below, only while an attached tool carries
        # several TCPs.
        self._attach_tool_widget.set_tcp_row_frame(ui.Frame(height=0))

    def _build_settings_section(self) -> None:
        section = CollapsibleSection("Settings", collapsed=True)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            with form_row(
                "Preview Color",
                tooltip=(
                    "Color used to overlay robot configuration for reachable "
                    "poses in the viewport."
                ),
            ):
                with ui.HStack(height=26):
                    self._preview_color_widget = ui.ColorWidget(
                        *self._preview_color[:3],
                        width=26,
                        height=26,
                        style={"border_radius": CORNER_RADIUS},
                    )
                    ui.Spacer()

            _weak_self_color = weakref.ref(self)

            def _color_edit_done(model, *_args):
                instance = _weak_self_color()
                if instance is not None:
                    instance._on_preview_color_changed()

            self._preview_color_widget.model.add_end_edit_fn(_color_edit_done)

            with form_row(
                "Live Update",
                tooltip=(
                    "Automatically re-run analysis when the Robot Base or "
                    "Target Poses are moved."
                ),
            ):
                with ui.HStack(height=18):
                    self._watch_checkbox = styled_checkbox(width=18, height=18)
                    ui.Spacer()
            self._watch_checkbox.model.set_value(False)
            self._watch_checkbox.model.add_value_changed_fn(
                lambda m, ws=weakref.proxy(self): ws._on_watch_toggled(
                    m.get_value_as_bool()
                )
            )
            ui.Spacer(height=SPACING_SM)

    def _build_action_row(self) -> None:
        with ui.HStack(height=BUTTON_HEIGHT, spacing=SPACING_MD):
            ui.Spacer(width=ui.Fraction(1))
            ui.Button(
                "Clear Preview",
                width=0,
                height=BUTTON_HEIGHT,
                tooltip="Remove the reachability overlay from the viewport.",
                clicked_fn=lambda ws=weakref.proxy(self): ws._preview.clear(),
                style=BUTTON_STYLE,
            )
            # Text and tooltip are swapped in place by _refresh_analyze_button
            # ("Analyze Reachability" <-> "Cancel"), so the width stays fixed
            # instead of content-hugging.
            self._analyze_button = ui.Button(
                "Analyze Reachability",
                width=200,
                height=BUTTON_HEIGHT,
                enabled=False,
                tooltip="Run reachability analysis for all selected robot models.",
                clicked_fn=lambda ws=weakref.proxy(self): ws._on_analyze_clicked(),
                style={
                    **BUTTON_PRIMARY_STYLE,
                    "Button:disabled": {
                        "background_color": (NOVAColor.ACTION_DISABLED_BACKGROUND.color)
                    },
                },
            )
            ui.Spacer(width=FORM_SIDE_MARGIN)

    def _build_filter_bar(self) -> None:
        with ui.ZStack(height=30):
            ui.Rectangle(
                style={
                    "background_color": NOVAColor.SURFACE_OVERLAY.color,
                    "border_radius": 0,
                }
            )
            with ui.VStack(spacing=SPACING_SM):
                ui.Spacer(height=2)
                with ui.HStack(height=22, spacing=0):
                    ui.Spacer(width=SPACING_MD)
                    with ui.ZStack(width=ui.Fraction(1), height=22):
                        self._search_field = ui.StringField(
                            height=22,
                            style=FIELD_STYLE,
                        )
                        self._search_placeholder = ui.Label(
                            "Filter robot models by string ...",
                            style={
                                "color": NOVAColor.TEXT_DISABLED.color,
                                "margin_width": 6,
                            },
                            alignment=ui.Alignment.LEFT_CENTER,
                        )

                        def _on_search_changed(
                            m,
                            ws=weakref.ref(self),
                        ):
                            window = ws()
                            if not window:
                                return
                            window._search_placeholder.visible = (
                                m.get_value_as_string() == ""
                            )
                            window._on_filter_changed()

                        self._search_field.model.add_value_changed_fn(
                            _on_search_changed
                        )
                    ui.Spacer(width=SPACING_SM)
                    self._filter_frame = ui.Frame(width=130)
                    ui.Spacer(width=SPACING_SM)
                    self._reachability_frame = ui.Frame(width=110)
                    with self._reachability_frame:
                        self._reachability_combo = ui.ComboBox(
                            0,
                            *_REACHABILITY_OPTIONS,
                            style=COMBOBOX_STYLE,
                        )
                        self._reachability_combo_sub = (
                            self._reachability_combo.model.subscribe_item_changed_fn(
                                lambda m, _, ws=weakref.ref(self): (
                                    ws()._on_filter_changed() if ws() else None
                                )
                            )
                        )
                    ui.Spacer(width=SPACING_MD)
                ui.Spacer(height=2)

    def _build_splitter(self) -> None:
        """Draggable divider between the setup pane and the results pane.

        Dragging it vertically re-sizes the setup pane's percentage height
        (clamped to the _SETUP_PANE_*_PERCENT bounds); the results pane's
        Fraction(1) scroll area absorbs the remainder. omni.ui delivers
        mouse-moved events to the pressed widget for the whole drag, so the
        handle keeps receiving updates even when the cursor leaves its 8px
        hit area.
        """
        with ui.ZStack(height=8):
            hit_area = ui.Rectangle(
                style={"background_color": NOVAColor.SURFACE_TRANSPARENT.color}
            )
            with ui.VStack(spacing=0):
                ui.Spacer()
                self._splitter_line = ui.Line(
                    height=1,
                    style={"border_width": 1, "color": NOVAColor.DIVIDER.color},
                )
                ui.Spacer()

        def _pressed(x, y, button, *_args, ws=weakref.ref(self)):
            window = ws()
            if window is not None and button == 0:
                window._splitter_drag_active = True

        def _released(x, y, button, *_args, ws=weakref.ref(self)):
            window = ws()
            if window is not None and button == 0:
                window._splitter_drag_active = False

        def _moved(x, y, *_args, ws=weakref.ref(self)):
            window = ws()
            if window is not None and window._splitter_drag_active:
                window._on_splitter_dragged(y)

        def _hovered(hovered, ws=weakref.ref(self)):
            window = ws()
            if window is None or window._splitter_line is None:
                return
            # Brighten the hairline while hovered so the divider reads as
            # grabbable.
            color = NOVAColor.PRIMARY_MAIN.color if hovered else NOVAColor.DIVIDER.color
            window._splitter_line.style = {"border_width": 1, "color": color}

        hit_area.set_mouse_pressed_fn(_pressed)
        hit_area.set_mouse_released_fn(_released)
        hit_area.set_mouse_moved_fn(_moved)
        hit_area.set_mouse_hovered_fn(_hovered)

    def _on_splitter_dragged(self, screen_y: float) -> None:
        container = self._panes_container
        setup = self._setup_container
        if container is None or setup is None:
            return
        total = container.computed_height
        if total <= 0:
            return
        # Mouse coordinates arrive in screen space; convert to a percentage
        # of the pane container's height.
        percent = (screen_y - container.screen_position_y) / total * 100.0
        percent = max(_SETUP_PANE_MIN_PERCENT, min(_SETUP_PANE_MAX_PERCENT, percent))
        self._setup_area_percent = percent
        setup.height = ui.Percent(percent)

    def _refresh_analyze_button(self) -> None:
        if not self._analyze_button:
            return
        if self._is_analyzing:
            self._analyze_button.text = "Cancel"
            self._analyze_button.tooltip = "Cancel the running reachability analysis."
            self._analyze_button.enabled = True
        else:
            self._analyze_button.text = "Analyze Reachability"
            self._analyze_button.tooltip = (
                "Run reachability analysis for all selected robot models."
            )
            self._analyze_button.enabled = bool(self._mounting_prim_paths) and bool(
                self._target_prim_paths
            )

    def _rebuild_target_prim_list(self) -> None:
        """Fill the target pose list with one row per prim, each carrying its
        own collision-setup dropdown (default: None - no collision
        checking)."""
        # Drop overrides for paths that are no longer selected so a stale
        # entry doesn't silently reapply if the same path is picked again
        # under a different pose set later.
        selected = set(self._target_prim_paths)
        for stale_path in [p for p in self._pose_collision_setup if p not in selected]:
            del self._pose_collision_setup[stale_path]

        frame = self._target_prim_list_frame
        self._collision_setup_row_subs = []
        if frame is None:
            return
        frame.clear()
        with frame:
            # Leading inset so the path rows share the form rows' left edge
            # (the "Prims" label above sits FORM_SIDE_MARGIN in).
            with ui.HStack(spacing=0):
                ui.Spacer(width=FORM_SIDE_MARGIN)
                self._build_target_prim_rows()

    def _build_target_prim_rows(self) -> None:
        with ui.VStack(spacing=2, height=0):
            ui.Spacer(height=2)
            if self._target_prim_paths:
                ui.Label(
                    "Define the collision setup to apply to each target pose.",
                    height=20,
                    word_wrap=True,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 12,
                    },
                )
            for path in self._target_prim_paths:
                options = [_NO_COLLISION_SETUP] + self._collision_setup_names
                effective = self._effective_pose_collision_setup(path)
                if effective and effective not in options:
                    options.append(effective)
                selected_index = options.index(effective) if effective else 0

                # spacing=0 so the trailing alignment inset isn't padded
                # by an extra inter-child gap; the label/combo gap is an
                # explicit spacer instead.
                with ui.HStack(height=20, spacing=0):
                    ui.Label(
                        path,
                        style={
                            "color": NOVAColor.TEXT_SECONDARY.color,
                            "font_size": 14,
                        },
                    )
                    ui.Spacer(width=6)
                    combo = ui.ComboBox(
                        selected_index,
                        *options,
                        width=ui.Pixel(FORM_FIELD_WIDTH),
                        style=COMBOBOX_STYLE,
                    )
                    ui.Spacer(width=_POSE_SETUP_ROW_RIGHT_INSET)

                    def _on_row_changed(
                        model,
                        _,
                        path=path,
                        options=options,
                        ws=weakref.ref(self),
                    ):
                        window = ws()
                        if window is None:
                            return
                        index = model.get_item_value_model().as_int
                        window._pose_collision_setup[path] = (
                            options[index] if 0 < index < len(options) else None
                        )

                    self._collision_setup_row_subs.append(
                        combo.model.subscribe_item_changed_fn(_on_row_changed)
                    )
            ui.Spacer(height=2)

    def _rebuild_base_prim_list(self) -> None:
        self._rebuild_prim_list(self._base_prim_list_frame, self._mounting_prim_paths)

    @staticmethod
    def _rebuild_prim_list(frame: Optional[ui.Frame], paths: list[str]) -> None:
        """Fill a collapsable selection list with one prim path per row."""
        if frame is None:
            return
        frame.clear()
        with frame:
            # Leading inset so the path rows share the form rows' left edge
            # (the "Prims" label above sits FORM_SIDE_MARGIN in).
            with ui.HStack(spacing=0):
                ui.Spacer(width=FORM_SIDE_MARGIN)
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
                    if not paths:
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

    def _on_mounting_prims_picked(self, prims: list[Usd.Prim]) -> None:
        self._mounting_prim_paths = [prim.GetPath().pathString for prim in prims]
        carb.log_info(f"{len(self._mounting_prim_paths)} robot base prim(s) selected")

        self._refresh_analyze_button()
        self._rebuild_base_prim_list()
        if self._watch_enabled:
            self._setup_watchers()

    def _on_targets_picked(self, prims: list[Usd.Prim]) -> None:
        self._target_prim_paths = [prim.GetPath().pathString for prim in prims]
        carb.log_info(f"{len(self._target_prim_paths)} target prim(s) selected")

        self._refresh_analyze_button()
        self._rebuild_target_prim_list()
        if self._watch_enabled:
            self._setup_watchers()

    @staticmethod
    def _pose_filter(prim: Usd.Prim) -> bool:
        """Restrict the Target Poses picker to actual pose prims: ghost
        objects (GhostObjectAPI) or prims tagged type=POSE, the same check
        the trajectory planner uses (PoseListManager.is_pose_prim)."""
        if GhostObjectUtils.is_ghost_object(prim):
            return True
        try:
            custom_data = prim.GetCustomDataByKey("wandelbots")
            if custom_data and custom_data.get("type") == "POSE":
                return True
        except Exception as exc:
            carb.log_warn(f"Failed to check custom data for pose: {exc}")
        return False

    def _on_tool_tcp_offset_picked(self, offset: list[float]) -> None:
        """Pre-fill the manual TCP offset fields from an attached tool's own
        TCP prim. The fields remain fully hand-editable afterward."""
        for model, value in zip(self._tcp_models, offset):
            model.set_value(value)

    def _on_watch_toggled(self, enabled: bool) -> None:
        self._watch_enabled = enabled
        if enabled:
            self._setup_watchers()
        else:
            self._teardown_watchers()

    def _setup_watchers(self) -> None:
        """Subscribe to transform changes on all watched prims."""
        self._teardown_watchers()
        watcher = get_watcher()
        paths_to_watch: list[str] = []
        paths_to_watch.extend(self._mounting_prim_paths)
        paths_to_watch.extend(self._target_prim_paths)

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
        # Only auto-trigger a fresh run; never cancel one the user started
        # manually in the meantime (that's the button click's job).
        if self._is_analyzing:
            return
        self._on_analyze_clicked()

    def _on_analyze_clicked(self) -> None:
        if self._is_analyzing:
            carb.log_info(
                "Analyze clicked while already analyzing - cancelling current task."
            )
            if self._analysis_task is not None and not self._analysis_task.done():
                self._analysis_task.cancel()
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
        self._refresh_analyze_button()

        self._preview.clear()
        self._set_status("Extracting poses...")
        self._analysis_task = run_coroutine(self._run_analysis())

    # -- Rebuild helpers ---------------------------------------------------

    def _get_instance(self) -> NOVAInstance | None:
        """Return the currently selected NOVA instance, or None."""
        if not self._instances:
            return None
        index = min(self._selected_instance_index, len(self._instances) - 1)
        return self._instances[index]

    def _refresh_instances(self) -> None:
        api = get_instances_api()
        self._instances = [
            instance
            for instances in api.get_cloud_instances().values()
            for instance in instances
        ] + api.get_custom_instances()

    def _on_refresh_instances_clicked(self) -> None:
        run_coroutine(self._refresh_instances_async())

    async def _refresh_instances_async(self, notify: bool = True) -> None:
        """Refetch all available NOVA instances without blocking the UI.

        The cloud instance lookup is a synchronous portal HTTP call (up to
        10s timeout per auth config), so it runs in a worker thread.
        ``notify=False`` skips the result toast - used by the silent
        refresh on window open, where an unsolicited notification would be
        noise.
        """
        selected = self._get_instance()
        api = get_instances_api()
        try:
            cloud, custom = await asyncio.to_thread(
                lambda: (api.get_cloud_instances(), api.get_custom_instances())
            )
        except Exception as exc:
            carb.log_warn(f"Failed to refresh NOVA instances: {exc}")
            nm.post_notification(
                f"Failed to refresh NOVA instances: {exc}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        self._instances = [
            instance for instances in cloud.values() for instance in instances
        ] + custom
        # Keep the previous selection if that instance is still available.
        if selected is not None:
            for index, instance in enumerate(self._instances):
                if instance.host == selected.host:
                    self._selected_instance_index = index
                    break
            else:
                # The previously selected instance is gone; the fallback is
                # effectively an instance switch.
                self._selected_instance_index = 0
                self._on_instance_switched()
        self._rebuild_instance_row()
        self._refresh_instance_data()
        if notify:
            nm.post_notification(
                f"Found {len(self._instances)} NOVA instance(s)",
                duration=3.0,
                status=nm.NotificationStatus.INFO,
            )

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
            names = [instance.display_name for instance in self._instances]
            index = min(self._selected_instance_index, len(names) - 1)
            combo = ui.ComboBox(
                index,
                *names,
                height=20,
                style=COMBOBOX_STYLE,
                tooltip_fn=lambda: build_tooltip(
                    "Select the NOVA instance to run the analysis against."
                ),
            )

            def _on_instance_changed(
                model: ui.AbstractItemModel, _, ws=weakref.ref(self)
            ) -> None:
                window = ws()
                if window is None:
                    return
                new_index = model.get_item_value_model().as_int
                if new_index == window._selected_instance_index:
                    return
                window._selected_instance_index = new_index
                window._on_instance_switched()
                window._refresh_instance_data()

            self._instance_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_instance_changed
            )

    def _is_selected_instance(self, instance: NOVAInstance) -> bool:
        """Whether `instance` is still the selected one - the staleness
        check for instance-scoped async refreshes, compared by host (the
        identity key _refresh_instances_async also matches on)."""
        current = self._get_instance()
        return current is not None and current.host == instance.host

    def _on_instance_switched(self) -> None:
        """Drop results tied to the previous instance.

        Model availability differs per instance: a table kept from the old
        instance would pin the next run to the old model set (see
        _run_analysis, which only populates the table when it's empty), so
        models that exist only on the new instance would never appear. A
        run still in flight is checking against the old instance and is
        cancelled.
        """
        if self._analysis_task is not None and not self._analysis_task.done():
            self._analysis_task.cancel()
        self._result_model.clear()
        # Back to the pre-analysis manufacturer choices until the new
        # instance's catalog (or first run) replaces them.
        self._rebuild_manufacturer_combo()
        self._preview.clear()
        self._set_status("")
        self._set_progress(0, 0)

    def _refresh_instance_data(self) -> None:
        """Refetch the instance-scoped dropdown data for the currently
        selected instance: the NOVA-stored collision setup names (rebuilding
        the target pose rows so their override dropdowns reflect the new
        options) and the manufacturers available in its model catalog."""
        instance = self._get_instance()
        if instance is None:
            return
        run_coroutine(self._refresh_collision_setup_names_async(instance))
        run_coroutine(self._refresh_known_manufacturers_async(instance))

    async def _refresh_collision_setup_names_async(
        self, instance: NOVAInstance
    ) -> None:
        service = get_reachability_service()
        names = await service.list_collision_setup_names(instance)
        if not self._is_selected_instance(instance):
            # The user switched instances while this request was in flight;
            # the result belongs to the previous instance - drop it (the
            # switch dispatched its own fresh refresh).
            return
        if names is None:
            # Transient failure (list_collision_setup_names distinguishes it
            # from a genuinely empty store): keep the previous names and
            # every explicit per-pose choice rather than erasing user state
            # over an outage; a later refresh reconciles.
            nm.post_notification(
                "Could not fetch the collision setups stored on "
                f"'{instance.display_name}'; keeping the previous list.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return
        self._collision_setup_names = names
        # Explicit per-pose choices referencing setups that don't exist on
        # this instance would silently run without collision checking
        # (_resolve_stored_collision_setups drops unresolvable names), so
        # prune them - the rows fall back to the default. Explicit None
        # choices ("no collision checking") are instance-independent and
        # stay.
        available = set(self._collision_setup_names)
        stale = [
            path
            for path, name in self._pose_collision_setup.items()
            if name is not None and name not in available
        ]
        for path in stale:
            del self._pose_collision_setup[path]
        if stale:
            carb.log_info(
                f"Dropped {len(stale)} per-pose collision setup choice(s) "
                "not stored on the selected instance."
            )
        self._rebuild_default_setup_row()
        self._rebuild_target_prim_list()

    async def _refresh_known_manufacturers_async(self, instance: NOVAInstance) -> None:
        """Seed the manufacturer filter from the instance's model catalog so
        already the first analysis can be restricted to a manufacturer the
        instance actually offers (the static fallback only covers the
        manufacturers known at build time)."""
        models = await get_reachability_service().fetch_model_names(instance)
        # A response that arrives after the user switched instances belongs
        # to the previous instance and must not seed the new one's filter.
        if not self._is_selected_instance(instance):
            return
        # After the first analysis the actual result items drive the list.
        if not models or self._result_model.has_items():
            return
        self._result_model.set_known_manufacturers(
            list(manufacturers_from_model_names(models))
        )
        self._rebuild_manufacturer_combo()

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
            index = self._reachability_combo.model.get_item_value_model().as_int
            reachability = (
                _REACHABILITY_OPTIONS[index]
                if index < len(_REACHABILITY_OPTIONS)
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
        # Keep the current choice across rebuilds (the first analysis replaces
        # the seeded manufacturer list with the ones actually available).
        current = self._result_model._manufacturer_filter
        try:
            selected_index = manufacturers.index(current)
        except ValueError:
            selected_index = 0
            if current != _ALL_MANUFACTURERS:
                self._result_model.set_filter(
                    search_text=self._result_model._search_text,
                    manufacturer=_ALL_MANUFACTURERS,
                    reachability=self._result_model._reachability_filter,
                )
        with self._filter_frame:
            combo = ui.ComboBox(
                selected_index,
                *manufacturers,
                style=COMBOBOX_STYLE,
                tooltip_fn=lambda: build_tooltip(
                    "Filter results by manufacturer. Also limits which models "
                    "the next analysis run tests."
                ),
            )

            def _on_manufacturer_changed(
                model: ui.AbstractItemModel,
                _,
                ws=weakref.ref(self),
                names=manufacturers,
            ) -> None:
                window = ws()
                if window is None:
                    return
                index = model.get_item_value_model().as_int
                selected = names[index] if index < len(names) else _ALL_MANUFACTURERS
                window._result_model.set_filter(
                    search_text=window._result_model._search_text,
                    manufacturer=selected,
                    reachability=window._result_model._reachability_filter,
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
            mounting_poses = self._collect_mounting_poses(service)
            if mounting_poses is None:
                return
            # Single-base runs keep the pose snapshot for preview/Add-to-Scene;
            # multi-base results carry their winning base per pose instead.
            self._analysis_mounting_pose = (
                mounting_poses[0] if len(mounting_poses) == 1 else None
            )

            targets = self._collect_targets(service)
            if targets is None:
                return

            instance = self._get_instance()
            if instance is None:
                nm.post_notification(
                    "No NOVA instance connected. Please connect to NOVA first.",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return

            self._set_status(f"Fetching models from '{instance.display_name}'...")
            session, all_models = await service.prepare_session(
                instance,
                targets,
                self._analysis_mounting_pose,
                self._tcp_offset(),
                tool_mesh_vertices=self._tool_mesh_vertices(),
                tool_collider_hulls=self._tool_collider_hulls(),
            )
            async with session:
                await self._analyze_models(
                    service, session, all_models, mounting_poses, len(targets)
                )

        except asyncio.CancelledError:
            carb.log_info("Reachability analysis cancelled by user.")
            self._result_model.reset_calculating()
            self._set_progress(0, 0)
            self._set_status("Cancelled")

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

    def _collect_mounting_poses(
        self, service: ReachabilityService
    ) -> Optional[list[WSPose]]:
        """World poses of the selected robot bases, or None when one of them
        cannot be read (the run is abandoned rather than silently shortened)."""
        mounting_poses: list[WSPose] = []
        for mounting_prim_path in self._mounting_prim_paths:
            try:
                mounting_poses.append(
                    service.extract_mounting_pose_from_prim(mounting_prim_path)
                )
            except ValueError as exc:
                nm.post_notification(
                    f"Invalid mounting prim: {exc}",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return None
        return mounting_poses

    def _collect_targets(
        self, service: ReachabilityService
    ) -> Optional[list[TargetPose]]:
        """Target poses paired with the collision setup chosen for each.

        A prim that fails pose extraction shifts the indices, so the chosen
        setups are dropped for that run instead of being applied to the wrong
        pose.
        """
        try:
            poses = service.extract_target_poses_from_prims(self._target_prim_paths)
        except ValueError as exc:
            nm.post_notification(
                str(exc), duration=5.0, status=nm.NotificationStatus.WARNING
            )
            return None
        if len(poses) != len(self._target_prim_paths):
            if self._pose_collision_setup or self._default_collision_setup:
                carb.log_warn(
                    "Some target prims failed pose extraction; skipping "
                    "per-pose collision setups for this run."
                )
            return [TargetPose(pose=pose) for pose in poses]
        return [
            TargetPose(
                pose=pose,
                collision_setup_name=self._effective_pose_collision_setup(path),
            )
            for pose, path in zip(poses, self._target_prim_paths)
        ]

    def _tcp_offset(self) -> Optional[WSPose]:
        """The manual TCP offset, or None when every field is zero."""
        values = [model.as_float for model in self._tcp_models]
        if not any(value != 0.0 for value in values):
            return None
        return WSPose(pose=values)

    def _tool_mesh_vertices(self) -> Optional[list[tuple[float, float, float]]]:
        if self._attach_tool_widget is None:
            return None
        return self._attach_tool_widget.tool_mesh_vertices

    def _tool_collider_hulls(
        self,
    ) -> Optional[list[list[tuple[float, float, float]]]]:
        if self._attach_tool_widget is None:
            return None
        return self._attach_tool_widget.tool_collider_hulls

    def _prepare_result_table(self, all_models: list[str]) -> list[str]:
        """The models this run tests, with their previous results dropped.

        The table is filled on the first run; later runs re-check only what
        the search and manufacturer filters still show.
        """
        if not self._result_model.has_items():
            self._result_model.populate_models(all_models)
            self._rebuild_manufacturer_combo()
        available = set(all_models)
        run_models = [
            name
            for name in self._result_model.get_analysis_names()
            if name in available
        ]
        # Also drops results of models currently hidden by the reachability
        # facet but about to be re-analyzed.
        self._result_model.reset_results(run_models)
        carb.log_info(
            f"Running analysis for {len(run_models)} filtered model(s) "
            f"out of {len(all_models)} available"
        )
        return run_models

    async def _analyze_models(
        self,
        service: ReachabilityService,
        session: ReachabilitySession,
        all_models: list[str],
        mounting_poses: list[WSPose],
        target_count: int,
    ) -> None:
        """Check every model in the run set and fill the table as results
        arrive. With several bases the reachable poses are unioned over them.
        """
        run_models = self._prepare_result_table(all_models)
        self._set_progress(0, len(run_models))
        self._set_status(
            f"Analyzing {len(run_models)} model(s) against {target_count} "
            f"target(s) from {len(mounting_poses)} base(s)..."
        )

        reachable = 0
        for index, model_name in enumerate(run_models):
            self._result_model.set_item_calculating(model_name)
            result = await service.check_single_model_multi_base(
                session, model_name, mounting_poses
            )
            self._result_model.update_item_by_name(model_name, result)
            if result.reachable:
                reachable += 1
            self._set_progress(index + 1, len(run_models))
            self._set_status(
                f"Checked {index + 1}/{len(run_models)}: {reachable} reachable so far"
            )

        self._set_progress(0, 0)
        self._set_status(
            f"Done: {reachable}/{len(run_models)} models can reach all "
            f"{target_count} target(s){self._base_summary(mounting_poses)}"
        )
        self._refresh_selected_preview()

    def _base_summary(self, mounting_poses: list[WSPose]) -> str:
        """Trailing status text naming the base(s) the run used."""
        pose = self._analysis_mounting_pose
        if pose is not None:
            position = ", ".join(f"{value:.0f}" for value in pose.pose[:3])
            return f" | Base: ({position})"
        if mounting_poses:
            return f" | {len(mounting_poses)} bases"
        return ""

    # -- Download ----------------------------------------------------------

    def _download_robot(self, model_name: str) -> None:
        run_coroutine(self._download_robot_async(model_name))

    async def _download_robot_async(self, model_name: str) -> None:
        instance = self._get_instance()
        if instance is None:
            nm.post_notification(
                "No NOVA instance connected. Please connect to NOVA first.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        download_path = await self._resolve_download_path(model_name)
        if download_path is None:
            return

        api_client = get_instances_api().create_api_client_for_instance(instance)
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
            await self._write_usd_file(download_path, usd_bytes)
            self._add_robot_to_stage(model_name, download_path)

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

    async def _resolve_download_path(self, model_name: str) -> Optional[str]:
        """Where the model is written: assets/robots next to the scene. The
        path is relative to the scene, so an unsaved scene is saved first."""
        stage_url = omni.usd.get_context().get_stage_url() or ""
        if not stage_url:
            stage_url = await self._save_stage_for_download()
        if not stage_url:
            return None
        assets_directory = join_location(parent_location(stage_url), "assets")
        robots_directory = join_location(assets_directory, "robots")
        return join_location(robots_directory, f"{model_name}.usd")

    @staticmethod
    async def _save_stage_for_download() -> str:
        """Save the scene so it has a location, and return that location."""
        saved = await omni.usd.get_context().save_stage_with_callback_async(
            lambda result, error: None
        )
        if not saved:
            nm.post_notification(
                "Please save the scene before adding robots.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return ""
        stage_url = omni.usd.get_context().get_stage_url() or ""
        if not stage_url:
            nm.post_notification(
                "Scene was not saved. Please save it first.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
        return stage_url

    @staticmethod
    async def _write_usd_file(download_path: str, usd_bytes: bytearray) -> None:
        if is_url(download_path):
            write_result = await omni.client.write_file_async(
                download_path, bytes(usd_bytes)
            )
            if write_result != omni.client.Result.OK:
                raise RuntimeError(
                    f"omni.client.write_file_async failed: {write_result}"
                )
            return
        parent_directory = parent_location(download_path)
        if parent_directory:
            os.makedirs(parent_directory, exist_ok=True)
        with open(download_path, "wb") as usd_file:
            usd_file.write(usd_bytes)

    def _add_robot_to_stage(self, model_name: str, download_path: str) -> None:
        """Payload the downloaded model under the first selected robot base."""
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return

        safe_name = (
            model_name
            if Sdf.Path.IsValidIdentifier(model_name)
            else model_name.replace("-", "_").replace(" ", "_")
        )
        # With several candidate bases the import destination is ambiguous;
        # place the robot under the first selected base.
        parent_path = (
            Sdf.Path(self._mounting_prim_paths[0])
            if self._mounting_prim_paths
            else Sdf.Path("/World")
        )
        robot_prim_path = parent_path.AppendChild(safe_name)
        xform = UsdGeom.Xform.Define(stage, robot_prim_path)
        xform.GetPrim().GetPayloads().AddPayload(download_path)

        base_offset = MODEL_BASE_OFFSETS.get(model_name, 0.0)
        if base_offset == 0.0:
            return
        # The parent base prim already positions the robot; only the model's
        # own kinematic base offset (meters) is applied on top.
        PrimUtils.set_prim_pose(
            robot_prim_path.pathString,
            WSPose(pose=[0, 0, m_to_mm(base_offset), 0, 0, 0]),
            stage,
        )


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
    name = "Reachability Analysis"
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
