"""Mounting Assistant tool window for finding optimal robot mounting positions."""

from __future__ import annotations

import asyncio
import weakref
from typing import TYPE_CHECKING, Optional

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from omni.usd import get_watcher
from pxr import Gf, Sdf, Usd, UsdGeom

from wandelbots.omni.utils.scene import SceneUtils

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.manipulators.motion_group import is_prim_motion_group
from wandelbots.omni.manipulators.utils import (
    get_link_0_from_motion_group_prim,
    get_motion_group_current_joint_positions,
)
from wandelbots.omni.utils.teaching import GhostObjectUtils
from wandelbots.omni.reachability.reachability_service import (
    ReachabilityResult,
    get_reachability_service,
)
from wandelbots.omni.ui.colors import NOVAColor, float_array_to_hex, hex_to_float_array
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.tool.mounting_assistant.grid_utils import generate_grid_pattern
from wandelbots.omni.ui.tool.mounting_assistant.mounting_preview import (
    CALCULATING,
    ERROR,
    HIDDEN,
    MountingPreview,
    PENDING,
    REACHABLE,
    UNREACHABLE,
)
from wandelbots.omni.ui.tool.reachability.reachability_preview import (
    ReachabilityPreview,
)

if TYPE_CHECKING:
    from wandelbots.omni.ui.tool.mounting_assistant.widgets import (
        CandidateDetail,
        GridSettings,
        PrimPickersSection,
    )

_BUTTON_HEIGHT = 32

CARB_MOUNTING_OVERLAY_COLOR = (
    "/persistent/exts/wandelbots.omni/mounting_assistant/overlay_color"
)
_DEFAULT_OVERLAY_COLOR = [0.15, 0.65, 0.60, 0.25]


class MountingAssistantWindow:
    """Window for finding optimal robot mounting positions via grid IK analysis."""

    _singleton: "MountingAssistantWindow | None" = None

    def __init__(self) -> None:
        if MountingAssistantWindow._singleton is not None:
            try:
                MountingAssistantWindow._singleton.destroy()
            except Exception as exc:
                carb.log_warn(
                    f"Error destroying previous MountingAssistantWindow: {exc}"
                )
        MountingAssistantWindow._singleton = self

        self._stage = omni.usd.get_context().get_stage()
        self._motion_group_prim: Usd.Prim | None = None
        self._center_prim_path: str | None = None
        self._target_prim_paths: list[str] = []

        self._watch_subs: list = []
        self._debounce_task: asyncio.Task | None = None
        self._debounce_delay: float = 0.5

        self._pickers_section: PrimPickersSection | None = None
        self._grid_settings: GridSettings | None = None
        self._candidate_detail: CandidateDetail | None = None
        self._settings_frame: CollapsibleSection | None = None
        self._detail_collapsable: CollapsibleSection | None = None
        self._overlay_color: list[float] = self._load_overlay_color()

        self._generate_button: ui.Button | None = None
        self._abort_button: ui.Button | None = None
        self._status_label: ui.Label | None = None
        self._progress_bar: ui.ProgressBar | None = None
        self._progress_model = ui.SimpleFloatModel(0.0)

        # Candidate data
        self._candidate_positions: list[list[float]] = []
        self._candidate_results: list[Optional[ReachabilityResult]] = []
        self._candidate_statuses: list[int] = []
        self._candidate_center_mm: list[float] | None = None
        self._selected_idx: int | None = None

        self._preview = MountingPreview()
        self._preview.set_on_select(
            lambda idx, ws=weakref.ref(self): (
                ws()._on_sphere_selected(idx) if ws() else None
            )
        )
        self._analysis_task: asyncio.Task | None = None
        self._is_analyzing: bool = False

        self._robot_preview = ReachabilityPreview(
            frame_name="mounting_assistant_robot_preview"
        )
        self._robot_preview_task: asyncio.Task | None = None

        self._window = ui.Window(
            "Mounting Assistant",
            width=600,
            height=600,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        self._window.visible = False
        self._window.deferred_dock_in(
            "Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE
        )
        self._window.set_visibility_changed_fn(self._on_visibility_changed)
        self._build_ui()

        self._stage_event_sub = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(self._on_stage_event)
        )

    def destroy(self) -> None:
        self._stage_event_sub = None
        self._teardown_watchers()
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            self._debounce_task = None
        if self._analysis_task is not None:
            self._analysis_task.cancel()
            self._analysis_task = None
        self._preview.destroy()
        self._robot_preview.destroy()
        if self._robot_preview_task is not None:
            self._robot_preview_task.cancel()
            self._robot_preview_task = None
        if self._pickers_section is not None:
            self._pickers_section.destroy()
            self._pickers_section = None
        if self._grid_settings is not None:
            self._grid_settings.destroy()
            self._grid_settings = None
        if self._window:
            self._window.set_visibility_changed_fn(None)
            self._window.visible = False
        self._window = None
        if MountingAssistantWindow._singleton is self:
            MountingAssistantWindow._singleton = None

    def __del__(self) -> None:
        self.destroy()

    @property
    def window(self) -> ui.Window:
        return self._window

    def _on_visibility_changed(self, visible: bool) -> None:
        if not visible:
            self._preview.clear()
            self._robot_preview.clear()
        else:
            if self._candidate_positions:
                self._preview.restore_candidates(
                    self._candidate_positions,
                    self._candidate_statuses,
                    self._selected_idx,
                    center_mm=self._candidate_center_mm,
                )
                self._refresh_robot_preview()
            elif self._motion_group_prim is None:
                self._auto_select_motion_group()

    def _on_stage_event(self, event) -> None:
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._stage = omni.usd.get_context().get_stage()
            self._reset()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._stage = None
            self._reset()

    def _reset(self) -> None:
        if self._analysis_task is not None and not self._analysis_task.done():
            self._analysis_task.cancel()
        self._analysis_task = None
        self._is_analyzing = False
        self._teardown_watchers()
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            self._debounce_task = None
        self._motion_group_prim = None
        self._center_prim_path = None
        self._target_prim_paths = []
        self._candidate_statuses = []
        self._candidate_results = []
        self._candidate_positions = []
        self._candidate_center_mm = None
        self._selected_idx = None
        self._preview.clear()
        self._robot_preview.clear()
        self._set_status("")
        self._set_progress(0, 0)
        if self._pickers_section is not None:
            self._pickers_section.set_stage(self._stage)
            self._pickers_section.clear()
        self._rebuild_target_prim_list()
        self._refresh_generate_button()

    def _build_ui(self) -> None:
        with self._window.frame:
            with ui.VStack(spacing=0):
                with ui.ScrollingFrame(
                    vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                    horizontal_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                ):
                    with ui.VStack(spacing=0, height=0):
                        self._build_pickers_section()
                        self._build_action_row()
                        self._build_settings_section()
                        ui.Line(
                            height=1,
                            style={
                                "border_width": 1,
                                "color": NOVAColor.DIVIDER.color,
                            },
                        )
                        self._build_detail_section()
                self._build_status_bar()

    def _build_pickers_section(self) -> None:
        from .widgets.prim_pickers_section import PrimPickersSection

        self._pickers_section = PrimPickersSection(
            stage=self._stage,
            on_motion_group_picked=lambda prim, ws=weakref.ref(self): (
                ws()._on_motion_group_picked(prim) if ws() else None
            ),
            on_center_picked=lambda prim, ws=weakref.ref(self): (
                ws()._on_center_picked(prim) if ws() else None
            ),
            on_targets_picked=lambda prims, ws=weakref.ref(self): (
                ws()._on_targets_picked(prims) if ws() else None
            ),
            motion_group_filter_fn=is_prim_motion_group,
            pose_filter_fn=lambda p, ws=weakref.ref(self): (
                ws()._pose_filter(p) if ws() else False
            ),
        )

    def _build_action_row(self) -> None:
        with ui.HStack(height=40, spacing=8):
            ui.Spacer(width=5)
            ui.Spacer()
            self._generate_button = ui.Button(
                "Analyze",
                width=180,
                height=_BUTTON_HEIGHT + 2,
                enabled=False,
                tooltip="Generate the grid pattern and run IK reachability for all candidates.",
                clicked_fn=lambda ws=weakref.ref(self): (
                    ws()._on_generate_clicked() if ws() else None
                ),
                style={
                    "Button": {
                        "background_color": NOVAColor.PRIMARY_MAIN.color,
                        "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                        "font_size": 15,
                    },
                    "Button:hovered": {
                        "background_color": NOVAColor.PRIMARY_LIGHT.color,
                        "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                    },
                    "Button:disabled": {
                        "background_color": NOVAColor.DIVIDER.color,
                        "color": NOVAColor.TEXT_SECONDARY.color,
                    },
                    "Tooltip": {"background_color": ui.color(0x1E1E1EFF)},
                },
            )
            self._abort_button = ui.Button(
                "Abort",
                width=180,
                height=_BUTTON_HEIGHT + 2,
                visible=False,
                tooltip="Stop the ongoing analysis.",
                clicked_fn=lambda ws=weakref.ref(self): (
                    ws()._on_abort_clicked() if ws() else None
                ),
                style={
                    "Button": {
                        "background_color": ui.color("#EF5350"),
                        "font_size": 15,
                    },
                    "Tooltip": {"background_color": ui.color(0x1E1E1EFF)},
                },
            )
            ui.Button(
                "Clear",
                width=70,
                height=_BUTTON_HEIGHT + 2,
                tooltip="Clear all candidates and remove the viewport overlay.",
                clicked_fn=lambda ws=weakref.ref(self): (
                    ws()._on_clear() if ws() else None
                ),
            )
            ui.Spacer(width=5)

    def _build_settings_section(self) -> None:
        from .widgets.grid_settings import GridSettings

        self._settings_frame = CollapsibleSection("Settings", collapsed=True)
        with self._settings_frame.body:
            with ui.HStack():
                ui.Spacer(width=10)
                with ui.Frame(margin=5):
                    self._grid_settings = GridSettings(
                        on_filter_changed=lambda ws=weakref.ref(self): (
                            ws()._apply_display_filter() if ws() else None
                        ),
                        on_any_prim_changed=lambda enabled, ws=weakref.ref(self): (
                            ws()._on_any_prim_changed(enabled) if ws() else None
                        ),
                        on_overlay_color_changed=lambda color, ws=weakref.ref(self): (
                            ws()._on_overlay_color_changed(color) if ws() else None
                        ),
                        initial_overlay_color=self._overlay_color,
                    )

    def _build_detail_section(self) -> None:
        from .widgets.candidate_detail import CandidateDetail

        self._detail_collapsable = CollapsibleSection(
            "Candidate Details", collapsed=True
        )
        with self._detail_collapsable.body:
            with ui.HStack():
                ui.Spacer(width=10)
                with ui.Frame(margin=5):
                    self._candidate_detail = CandidateDetail(
                        on_move_clicked=lambda ws=weakref.ref(self): (
                            ws()._on_move_robot_clicked() if ws() else None
                        ),
                        on_pose_visibility_changed=lambda pose_idx, visible, ws=weakref.ref(self): (
                            ws()._on_pose_visibility_changed(pose_idx, visible)
                            if ws()
                            else None
                        ),
                        on_pose_config_changed=lambda pose_idx, config_idx, ws=weakref.ref(self): (
                            ws()._on_pose_config_changed(pose_idx, config_idx)
                            if ws()
                            else None
                        ),
                    )

    def _build_status_bar(self) -> None:
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

    def _on_sphere_selected(self, idx: int | None) -> None:
        self._selected_idx = idx
        self._update_detail_panel()
        self._refresh_robot_preview()

    def _on_overlay_color_changed(self, color: list[float]) -> None:
        self._overlay_color = color
        self._save_overlay_color(color)
        self._robot_preview.update_color(color)

    @staticmethod
    def _load_overlay_color() -> list[float]:
        settings = carb.settings.get_settings()
        hex_color = settings.get_as_string(CARB_MOUNTING_OVERLAY_COLOR)
        if hex_color:
            return hex_to_float_array(hex_color)
        return list(_DEFAULT_OVERLAY_COLOR)

    @staticmethod
    def _save_overlay_color(color: list[float]) -> None:
        settings = carb.settings.get_settings()
        settings.set_string(CARB_MOUNTING_OVERLAY_COLOR, float_array_to_hex(color))

    def _refresh_robot_preview(self) -> None:
        if self._robot_preview_task is not None:
            self._robot_preview_task.cancel()
            self._robot_preview_task = None
        idx = self._selected_idx
        if idx is None or idx >= len(self._candidate_results):
            self._robot_preview.clear()
            return
        result = self._candidate_results[idx]
        if result is None or not result.joint_solutions:
            self._robot_preview.clear()
            return
        pos_mm = self._candidate_positions[idx]
        mounting_pose = [pos_mm[0], pos_mm[1], pos_mm[2], 0.0, 0.0, 0.0]
        self._robot_preview_task = run_coroutine(
            self._robot_preview.show_preview(
                result,
                mounting_pose=mounting_pose,
                color=self._overlay_color,
                force=True,
                motion_group_prim=self._motion_group_prim,
            )
        )

    def _update_detail_panel(self) -> None:
        idx = self._selected_idx
        if idx is None or idx >= len(self._candidate_positions):
            if self._detail_collapsable:
                self._detail_collapsable.collapsed = True
            if self._candidate_detail:
                self._candidate_detail.update(None, None, None)
            return

        pos = self._candidate_positions[idx]
        result: Optional[ReachabilityResult] = (
            self._candidate_results[idx] if idx < len(self._candidate_results) else None
        )
        current_mm = self._get_motion_group_world_pos_mm()
        can_move = self._motion_group_prim is not None
        current_joints = (
            get_motion_group_current_joint_positions(self._motion_group_prim)
            if self._motion_group_prim is not None
            else None
        )

        if self._candidate_detail:
            self._candidate_detail.update(
                pos,
                result,
                current_mm,
                can_move,
                self._target_prim_paths,
                current_joints,
            )
        if self._detail_collapsable:
            self._detail_collapsable.collapsed = False

    def _get_motion_group_world_pos_mm(self) -> list[float] | None:
        """Return the world position of the motion group prim in mm, or None."""
        if self._motion_group_prim is None:
            return None
        try:
            xform_cache = UsdGeom.XformCache()
            world_mat = xform_cache.GetLocalToWorldTransform(self._motion_group_prim)
            t = world_mat.ExtractTranslation()
            unit_to_mm = 1000.0 / SceneUtils.get_stage_units()
            return [t[0] * unit_to_mm, t[1] * unit_to_mm, t[2] * unit_to_mm]
        except Exception:
            return None

    def _on_pose_visibility_changed(self, pose_idx: int, visible: bool) -> None:
        self._robot_preview.set_pose_visible(pose_idx, visible)

    def _on_pose_config_changed(self, pose_idx: int, config_idx: int) -> None:
        idx = self._selected_idx
        if idx is None or idx >= len(self._candidate_results):
            return
        result = self._candidate_results[idx]
        if result is None or result.all_joint_solutions is None:
            return
        if pose_idx >= len(result.all_joint_solutions):
            return
        solutions = result.all_joint_solutions[pose_idx]
        if config_idx >= len(solutions):
            return
        self._robot_preview.update_pose_joint_config(pose_idx, solutions[config_idx])

    def _on_move_robot_clicked(self) -> None:
        idx = self._selected_idx
        if idx is None or idx >= len(self._candidate_positions):
            return
        if self._motion_group_prim is None:
            carb.log_warn("No motion group prim selected")
            return

        target_center_mm = self._candidate_positions[idx]
        unit_factor = SceneUtils.get_stage_units() / 1000.0

        xform_cache = UsdGeom.XformCache()
        center_prim = self._stage.GetPrimAtPath(self._center_prim_path)
        center_world = xform_cache.GetLocalToWorldTransform(
            center_prim
        ).ExtractTranslation()
        mg_world = xform_cache.GetLocalToWorldTransform(
            self._motion_group_prim
        ).ExtractTranslation()

        # Offset from motion group prim to center prim (in stage units)
        offset = center_world - mg_world

        # Desired world position of motion group prim
        target_world = Gf.Vec3d(
            target_center_mm[0] * unit_factor - offset[0],
            target_center_mm[1] * unit_factor - offset[1],
            target_center_mm[2] * unit_factor - offset[2],
        )

        # Convert to motion group prim's parent local space
        parent = self._motion_group_prim.GetParent()
        if parent and parent.IsValid() and not parent.IsPseudoRoot():
            parent_world = xform_cache.GetLocalToWorldTransform(parent)
            local_pos = parent_world.GetInverse().Transform(target_world)
        else:
            local_pos = target_world

        try:
            xformable = UsdGeom.Xformable(self._motion_group_prim)
            ops = xformable.GetOrderedXformOps()
            translate_op = next(
                (op for op in ops if op.GetOpType() == UsdGeom.XformOp.TypeTranslate),
                None,
            )
            if translate_op is None:
                translate_op = xformable.AddTranslateOp(
                    precision=UsdGeom.XformOp.PrecisionDouble
                )
            translate_op.Set(local_pos)
        except Exception as exc:
            carb.log_error(f"Failed to move prim: {exc}")

    def _resolve_center_prim(self, motion_group_prim: Usd.Prim) -> Usd.Prim:
        """Resolve best center prim: link_0/base > link_0 > motion group prim."""
        link_0_prim = get_link_0_from_motion_group_prim(
            motion_group_prim, fallback_to_motion_group=False
        )
        if link_0_prim is not None:
            base_path = link_0_prim.GetPath().AppendPath("base")
            base_prim = link_0_prim.GetStage().GetPrimAtPath(base_path)
            if base_prim.IsValid():
                return base_prim
            return link_0_prim
        return motion_group_prim

    def _on_motion_group_picked(self, prim: Usd.Prim | None) -> None:
        self._motion_group_prim = prim
        if prim is not None and self._center_prim_path is None:
            center_prim = self._resolve_center_prim(prim)
            self._center_prim_path = center_prim.GetPath().pathString
            if self._pickers_section is not None:
                self._pickers_section.set_center_prim(center_prim)
        self._setup_watchers()
        self._refresh_generate_button()

    def _auto_select_motion_group(self) -> None:
        """Auto-select the motion group if exactly one exists in the scene."""
        if self._stage is None:
            return
        motion_groups = [
            prim for prim in self._stage.Traverse() if is_prim_motion_group(prim)
        ]
        if len(motion_groups) == 1:
            prim = motion_groups[0]
            if self._pickers_section is not None:
                self._pickers_section.set_motion_group_prim(prim)
            self._on_motion_group_picked(prim)

    def _on_center_picked(self, prim: Usd.Prim | None) -> None:
        self._center_prim_path = prim.GetPath().pathString if prim else None
        self._setup_watchers()
        self._refresh_generate_button()

    def _on_targets_picked(self, prims: list[Usd.Prim]) -> None:
        self._target_prim_paths = [p.GetPath().pathString for p in prims]
        self._setup_watchers()
        self._rebuild_target_prim_list()
        self._refresh_generate_button()

    def _on_any_prim_changed(self, enabled: bool) -> None:
        pass  # filter is resolved dynamically in _pose_filter

    def _pose_filter(self, prim) -> bool:
        if self._grid_settings is not None and self._grid_settings.any_prim:
            return True
        if GhostObjectUtils.is_ghost_object(prim):
            return True
        try:
            custom_data = prim.GetCustomDataByKey("wandelbots")
            if custom_data and custom_data.get("type") == "POSE":
                return True
        except Exception:
            pass
        return False

    def _rebuild_target_prim_list(self) -> None:
        if self._pickers_section is not None:
            self._pickers_section.rebuild_target_list(self._target_prim_paths)

    def _refresh_generate_button(self) -> None:
        if self._generate_button:
            self._generate_button.enabled = (
                self._motion_group_prim is not None
                and self._center_prim_path is not None
                and bool(self._target_prim_paths)
                and not self._is_analyzing
            )
            self._generate_button.visible = not self._is_analyzing
        if self._abort_button:
            self._abort_button.visible = self._is_analyzing

    def _apply_display_filter(self) -> None:
        """Update viewport visibility based on the show-only-valid toggle."""
        show_only = (
            self._grid_settings is not None and self._grid_settings.show_only_valid
        )
        for idx, status in enumerate(self._candidate_statuses):
            if status in (PENDING, CALCULATING):
                continue
            display = status if (not show_only or status == REACHABLE) else HIDDEN
            self._preview.update_candidate(idx, display)

    def _setup_watchers(self) -> None:
        self._teardown_watchers()
        paths: list[str] = []
        if self._center_prim_path:
            paths.append(self._center_prim_path)
        if (
            self._motion_group_prim is not None
            and self._motion_group_prim.GetPath().pathString not in paths
        ):
            paths.append(self._motion_group_prim.GetPath().pathString)
        paths.extend(p for p in self._target_prim_paths if p not in paths)
        watcher = get_watcher()
        for path in paths:
            sub = watcher.subscribe_to_change_info_path(
                Sdf.Path(path),
                lambda changed_path=None, ws=weakref.ref(self): (
                    ws()._on_watched_prim_changed(changed_path) if ws() else None
                ),
            )
            self._watch_subs.append(sub)

    def _teardown_watchers(self) -> None:
        self._watch_subs.clear()

    def _on_watched_prim_changed(self, path: Sdf.Path = None) -> None:
        if path is not None:
            path_str = path.pathString
            if not (
                path_str.endswith(":translate")
                or path_str.endswith(":rotate")
                or path_str.endswith(":orient")
                or path_str.endswith(".xformOp:transform")
            ):
                return
        if self._is_analyzing:
            return
        if not self._candidate_positions:
            return
        if self._debounce_task is not None:
            self._debounce_task.cancel()
        self._debounce_task = run_coroutine(self._debounced_analyze())

    async def _debounced_analyze(self) -> None:
        await asyncio.sleep(self._debounce_delay)
        self._debounce_task = None
        self._on_generate_clicked()

    def _set_status(self, message: str) -> None:
        if self._status_label:
            self._status_label.text = message

    def _set_progress(self, current: int, total: int) -> None:
        if self._progress_bar:
            self._progress_bar.visible = total > 0
            self._progress_model.set_value(current / total if total > 0 else 0.0)

    def _on_abort_clicked(self) -> None:
        if self._analysis_task is not None and not self._analysis_task.done():
            self._analysis_task.cancel()
        self._set_status("Aborted.")
        self._robot_preview.clear()

    def _on_clear(self) -> None:
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            self._debounce_task = None
        if self._analysis_task is not None and not self._analysis_task.done():
            self._analysis_task.cancel()
        self._analysis_task = None
        self._is_analyzing = False
        self._candidate_statuses = []
        self._candidate_results = []
        self._candidate_positions = []
        self._candidate_center_mm = None
        self._selected_idx = None
        self._preview.clear()
        self._robot_preview.clear()
        self._update_detail_panel()
        self._set_status("")
        self._set_progress(0, 0)
        self._refresh_generate_button()

    def _on_generate_clicked(self) -> None:
        if self._is_analyzing:
            return
        self._is_analyzing = True
        self._refresh_generate_button()
        if self._analysis_task is not None and not self._analysis_task.done():
            self._analysis_task.cancel()
        self._preview.clear()
        self._robot_preview.clear()
        self._set_status("Preparing...")
        self._analysis_task = run_coroutine(self._run_analysis())

    async def _run_analysis(self) -> None:
        service = get_reachability_service()
        try:
            center_pose = service.extract_mounting_pose_from_prim(
                self._center_prim_path
            )
            center_mm = center_pose.pose[:3]

            target_poses = service.extract_target_poses_from_prims(
                self._target_prim_paths
            )

            spacing_mm = max(1.0, self._grid_settings.spacing)
            range_mm = max(spacing_mm, self._grid_settings.range)
            axes = self._grid_settings.get_axes()

            candidates = generate_grid_pattern(center_mm, spacing_mm, range_mm, axes)
            if not candidates:
                nm.post_notification(
                    "No candidates generated. Check parameters.",
                    duration=4.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return

            self._candidate_positions = candidates
            self._candidate_results = [None] * len(candidates)
            self._candidate_statuses = [PENDING] * len(candidates)
            self._candidate_center_mm = list(center_mm)
            self._selected_idx = None
            self._update_detail_panel()
            self._preview.set_candidates(candidates, center_mm=list(center_mm))

            self._set_status("Connecting to NOVA...")
            (
                session,
                model_name,
                _tcp_offset,
            ) = await service.prepare_mounting_session_from_prim(
                self._motion_group_prim, target_poses
            )

            async with session:
                carb.log_info(f"Mounting assistant: using model '{model_name}'")

                self._set_progress(0, len(candidates))
                self._set_status(
                    f"Analyzing {len(candidates)} candidate(s) for model '{model_name}'..."
                )

                # Mark all candidates as calculating upfront
                for idx in range(len(candidates)):
                    self._preview.update_candidate(idx, CALCULATING)

                completed = 0
                reachable = 0
                _CONCURRENCY = 1

                async def _check_one(idx: int, pos_mm: list[float]) -> None:
                    nonlocal completed, reachable
                    mounting_pose = WSPose(
                        pose=[pos_mm[0], pos_mm[1], pos_mm[2], 0.0, 0.0, 0.0]
                    )
                    result = await service.check_single_model(
                        session, model_name, mounting_pose
                    )
                    self._candidate_results[idx] = result
                    is_ok = result.reachable and not result.error
                    status = (
                        REACHABLE if is_ok else (ERROR if result.error else UNREACHABLE)
                    )
                    self._candidate_statuses[idx] = status
                    self._apply_display_filter()
                    if idx == self._selected_idx:
                        self._update_detail_panel()
                        self._refresh_robot_preview()
                    completed += 1
                    if is_ok:
                        reachable += 1
                    self._set_progress(completed, len(candidates))
                    self._set_status(
                        f"Checked {completed}/{len(candidates)}: {reachable} reachable so far"
                    )

                pairs = list(enumerate(candidates))
                for batch_start in range(0, len(pairs), _CONCURRENCY):
                    batch = pairs[batch_start : batch_start + _CONCURRENCY]
                    await asyncio.gather(
                        *[_check_one(idx, pos_mm) for idx, pos_mm in batch],
                        return_exceptions=True,
                    )

                self._set_progress(0, 0)
                self._set_status(
                    f"Done: {reachable}/{len(candidates)} positions can reach all "
                    f"{len(target_poses)} target(s)"
                )

        except Exception as exc:
            carb.log_error(f"Mounting analysis failed: {exc}")
            nm.post_notification(
                f"Analysis failed: {exc}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            self._set_status(f"Error: {exc}")
        finally:
            self._is_analyzing = False
            self._refresh_generate_button()
