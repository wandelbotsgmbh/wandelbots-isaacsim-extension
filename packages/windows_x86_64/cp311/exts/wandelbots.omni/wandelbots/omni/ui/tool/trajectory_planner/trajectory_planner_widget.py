"""Trajectory planner widget - builds UI, serializes state, provides public API."""

from __future__ import annotations

import weakref
from typing import Callable

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd

import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.manipulators.utils import get_link_0_from_motion_group_prim
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.tool.trajectory_planner.events import TrajectoryPlannerEvents
from wandelbots.omni.ui.tool.trajectory_planner.execution_orchestrator import (
    ExecutionOrchestrator,
    ExecutionState,
)
from wandelbots.omni.ui.tool.trajectory_planner.ik_manager import IKManager
from wandelbots.omni.ui.tool.trajectory_planner.planning_orchestrator import (
    PlanningOrchestrator,
)
from wandelbots.omni.ui.tool.trajectory_planner.pose_list_manager import PoseListManager
from wandelbots.omni.ui.tool.trajectory_planner.pose_tree_widget import (
    PoseDelegate,
    PoseItem,
    PoseModel,
)
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_controller import (
    TrajectoryPlannerController,
)
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_preview import (
    TrajectoryPlannerPreview,
)
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
    PlannedTrajectoryConfig,
    PoseConfig,
    TrajectoryPlannerConfig,
)
from wandelbots.omni.ui.tool.trajectory_planner.widgets.motion_group_setup import (
    MotionGroupSetup,
)
from wandelbots.omni.ui.tool.trajectory_planner.widgets.trajectory_controls import (
    TrajectoryControls,
)
from wandelbots.omni.ui.tool.trajectory_planner.widgets.progress_status_bar import (
    ProgressStatusBar,
)
from wandelbots.omni.ui.tool.trajectory_planner.widgets.settings_section import (
    SettingsSection,
)
from wandelbots.omni.ui.styles import TOOLTIP_STYLE, _TOOLTIP_SUB
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.utils.api import ApiConfiguration
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.teaching import make_ghost_tcp_matcher


class TrajectoryPlannerWidget:
    """Orchestrates sub-components for a single trajectory planner skill."""

    def __init__(
        self,
        name: str,
        on_delete: Callable[["TrajectoryPlannerWidget"], None],
        on_selection_changed: Callable[
            ["TrajectoryPlannerWidget", PoseItem | None], None
        ],
    ) -> None:
        self._name = name
        self._on_delete = on_delete

        # UI elements rebuilt on each _rebuild()
        self._frame: ui.Frame | None = None
        self._collapsed: bool = False
        self._poses_collapsed: bool = False
        self._skill_section: CollapsibleSection | None = None
        self._tree_view: ui.TreeView | None = None
        self._live_update_btn: ui.Button | None = None
        self._edit_mode_btn: ui.Button | None = None
        self._add_poses_btn: ui.Button | None = None
        self._poses_section: CollapsibleSection | None = None
        self._widget_visible: bool = False

        # Shared event bus - one instance per skill
        self._events = TrajectoryPlannerEvents()

        # Data model
        self._pose_model = PoseModel()

        # Delegate wires directly to pose_list / events (no widget callbacks)
        self._pose_delegate = PoseDelegate(
            visibility_fn=lambda item: self._pose_list.toggle_visibility(item),
            remove_fn=lambda item: self._pose_list.remove_pose(item),
            copy_fn=lambda item: self._pose_list.copy_pose(item),
            move_up_fn=lambda item: self._pose_list.move_up(item),
            move_down_fn=lambda item: self._pose_list.move_down(item),
            motion_type_changed_fn=lambda item, mt: (
                self._events.motion_type_changed.emit(item, mt)
            ),
            joint_config_changed_fn=lambda item, idx: (
                self._events.inline_config_changed.emit(item, idx)
            ),
            refresh_ik_fn=lambda item: self._ik_manager.refresh_ik_for_pose(item),
            settings_fn=lambda item: self._events.pose_settings_clicked.emit(item),
            get_selected_tcp=lambda: self._mg_setup.selected_tcp,
            go_to_fn=lambda item: self._events.go_to_requested.emit(item),
        )

        # Sub-components - emit through the events bus instead of callbacks
        self._mg_setup = MotionGroupSetup(
            on_motion_group_changed=self._events.motion_group_changed.emit,
            on_tcp_changed=self._events.tcp_changed.emit,
            on_collision_setup_changed=self._events.collision_setup_changed.emit,
            get_plan_collision_free=lambda: self._settings.plan_collision_free,
            on_plan_collision_free_changed=lambda v: self._events.setting_changed.emit(
                "plan_collision_free", v
            ),
        )

        self._settings = SettingsSection(
            on_setting_changed=self._events.setting_changed.emit,
        )

        self._progress = ProgressStatusBar(name=name)

        self._controls = TrajectoryControls(
            on_calculate_iks=self._events.calculate_iks_requested.emit,
            on_plan=self._events.plan_requested.emit,
            on_execute=self._events.execute_toggle_requested.emit,
            on_replan=self._events.replan_requested.emit,
            on_force_stop=self._events.force_stop_requested.emit,
            on_start_from_here=self._events.start_from_here_requested.emit,
        )

        self._pose_list = PoseListManager(
            pose_model=self._pose_model,
            get_pose_relative_to_mg=self._get_pose_relative_to_mg,
            events=self._events,
            get_nova_tcps=lambda: self._mg_setup.nova_tcps,
        )

        self._ik_manager = IKManager(
            pose_model=self._pose_model,
            get_api_config=self._get_api_configuration,
            get_stream_params=self._get_stream_params,
            get_selected_tcp=lambda: self._mg_setup.selected_tcp,
            get_collision_setup=lambda: self._mg_setup.selected_collision_setup,
            events=self._events,
            get_tcp_for_item=self._get_tcp_for_pose_item,
        )

        self._planner = PlanningOrchestrator(
            pose_model=self._pose_model,
            get_api_config=self._get_api_configuration,
            get_stream_params=self._get_stream_params,
            get_mg_prim_path=lambda: (
                self._mg_setup.mg_config.prim_path if self._mg_setup.mg_config else None
            ),
            get_selected_tcp=lambda: self._mg_setup.selected_tcp,
            get_collision_setup=lambda: self._mg_setup.selected_collision_setup,
            get_settings=self._get_planning_settings,
            events=self._events,
            get_tcp_for_item=self._get_tcp_for_pose_item,
        )
        self._planner.set_skill_name(name)

        self._executor = ExecutionOrchestrator(
            get_api_config=self._get_api_configuration,
            get_stream_params=self._get_stream_params,
            get_selected_tcp=lambda: (
                self._planner.planned_tcp or self._mg_setup.selected_tcp
            ),
            get_move_to_start=lambda: self._settings.move_to_start,
            events=self._events,
        )

        self._preview = TrajectoryPlannerPreview()

        # Controller owns all coordination logic
        self._controller = TrajectoryPlannerController(
            events=self._events,
            pose_model=self._pose_model,
            pose_delegate=self._pose_delegate,
            mg_setup=self._mg_setup,
            settings=self._settings,
            controls=self._controls,
            progress=self._progress,
            preview=self._preview,
            planner=self._planner,
            ik_manager=self._ik_manager,
            executor=self._executor,
            pose_list=self._pose_list,
            on_selection_changed=lambda item, ws=weakref.ref(self): (
                on_selection_changed(ws(), item) if ws() else None
            ),
            rebuild_fn=lambda ws=weakref.ref(self): ws()._rebuild() if ws() else None,
            update_poses_title_fn=lambda ws=weakref.ref(self): (
                ws()._update_poses_section_title() if ws() else None
            ),
            get_pose_relative_to_mg=self._get_pose_relative_to_mg,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def pose_model(self) -> PoseModel:
        return self._pose_model

    @property
    def tree_view(self) -> ui.TreeView | None:
        return self._tree_view

    @property
    def selected_tcp(self) -> str | None:
        return self._mg_setup.selected_tcp

    def refresh_trajectory(self) -> None:
        """Redraw this skill's restored trajectory curve (Refresh action)."""
        self._controller.refresh_trajectory()

    def plan(self) -> None:
        """Trigger planning for this skill (same as pressing Plan)."""
        self._events.plan_requested.emit()

    def get_api_context(self):
        """Return (ApiConfiguration, cell) if a motion group is connected, else None."""
        api_config = self._mg_setup.get_api_configuration()
        mg = self._mg_setup.mg_config
        if not api_config or not mg:
            return None
        try:
            return api_config, mg.motion_stream_configuration.cell
        except Exception:
            return None

    @property
    def start_joint_position(self) -> list[float] | None:
        items = self._pose_model.items
        if items and items[0].selected_joint_config:
            return items[0].selected_joint_config
        return None

    def build(self, parent_frame: ui.Frame) -> None:
        self._frame = parent_frame
        self._rebuild()

    def set_visible(self, visible: bool) -> None:
        if visible == self._widget_visible:
            return
        self._widget_visible = visible

    def select_by_prim_path(self, prim_path: str) -> bool:
        return self._controller.select_by_prim_path(prim_path)

    def clear_selection(self) -> None:
        self._controller.clear_selection()

    def to_config(self) -> TrajectoryPlannerConfig:
        poses = [
            PoseConfig(
                prim_path=item.prim_path,
                motion_type=item.motion_type,
                selected_joint_config=item.selected_joint_config,
                joint_configs=item.joint_configs,
                selected_config_idx=item.selected_config_idx,
                is_ghost_object=item.is_ghost_object,
                tcp_name=item.tcp_name,
                blending=item.blending,
                limits_override=item.limits_override,
            )
            for item in self._pose_model.items
        ]
        planned_trajectory = None
        jt = self._planner.planned_joint_trajectory
        if jt:
            planned_trajectory = PlannedTrajectoryConfig(
                joint_positions=jt.joint_positions,
                locations=jt.locations,
                times=jt.times or [],
                collision_free=self._mg_setup.selected_collision_setup is not None,
            )
        return TrajectoryPlannerConfig(
            name=self._name,
            robot_prim_path=(
                self._mg_setup.robot_prim.GetPath().pathString
                if self._mg_setup.robot_prim
                else None
            ),
            tcp_name=self._mg_setup.selected_tcp,
            collision_setup=self._mg_setup.selected_collision_setup,
            poses=poses,
            live_update=self._settings.live_update,
            overlay_color=list(self._settings.overlay_color),
            trajectory_color=list(self._settings.trajectory_color),
            velocity_coloring=self._settings.velocity_coloring,
            tcp_velocity=self._settings.tcp_velocity,
            tcp_acceleration=self._settings.tcp_acceleration,
            auto_blending=self._settings.auto_blending,
            blending_min_velocity_percent=self._settings.blending_min_velocity_percent,
            global_blending=self._settings.global_blending,
            global_limits_override=self._settings.global_limits_override,
            payload_name=self._settings.payload_name,
            payload_mass=self._settings.payload_mass,
            cf_algorithm=self._settings.cf_algorithm,
            cf_max_iterations=self._settings.cf_max_iterations,
            plan_collision_free=self._settings.plan_collision_free,
            move_to_start=self._settings.move_to_start,
            collapsed=self._collapsed,
            poses_collapsed=self._poses_collapsed,
            planned_trajectory=planned_trajectory,
        )

    def apply_config(self, config: TrajectoryPlannerConfig) -> None:
        self._name = config.name
        self._collapsed = config.collapsed
        self._poses_collapsed = config.poses_collapsed

        self._settings.live_update = config.live_update
        self._settings.overlay_color = list(config.overlay_color)
        self._settings.trajectory_color = list(config.trajectory_color)
        self._settings.velocity_coloring = config.velocity_coloring
        self._settings.tcp_velocity = config.tcp_velocity
        self._settings.tcp_acceleration = config.tcp_acceleration
        self._settings.auto_blending = config.auto_blending
        self._settings.blending_min_velocity_percent = (
            config.blending_min_velocity_percent
        )
        self._settings.global_blending = config.global_blending
        self._settings.global_limits_override = config.global_limits_override
        self._settings.payload_name = config.payload_name
        self._settings.payload_mass = config.payload_mass
        self._settings.cf_algorithm = config.cf_algorithm
        self._settings.cf_max_iterations = config.cf_max_iterations
        self._settings.plan_collision_free = config.plan_collision_free
        self._settings.move_to_start = config.move_to_start

        self._mg_setup.set_collision_setup(config.collision_setup)
        # Collision-free is an independent planning mode now, not implied by the
        # presence of a collision scene.
        plan_cf = config.plan_collision_free
        self._pose_delegate.collision_free = plan_cf
        self._pose_model.collision_free = plan_cf

        robot_resolved = False
        if config.robot_prim_path:
            stage = omni.usd.get_context().get_stage()
            if stage:
                prim = stage.GetPrimAtPath(config.robot_prim_path)
                if prim.IsValid():
                    self._mg_setup.set_robot_prim(prim)
                    robot_resolved = True
        if robot_resolved:
            # Notify the controller of the restored motion group so it refreshes
            # the tree + selectors on the next rebuild without wiping restored
            # state (see TrajectoryPlannerController.begin_restore).
            self._controller.begin_restore()

        missing_prims = False
        if config.poses and self._mg_setup.robot_prim:
            stage = omni.usd.get_context().get_stage()
            if stage:
                link_0 = get_link_0_from_motion_group_prim(self._mg_setup.robot_prim)
                reference_path = (
                    str(link_0.GetPath())
                    if link_0
                    else self._mg_setup.robot_prim.GetPath().pathString
                )
                for pose_cfg in config.poses:
                    prim = stage.GetPrimAtPath(pose_cfg.prim_path)
                    if not prim.IsValid():
                        missing_prims = True
                        continue
                    pose = PrimUtils.get_relative_prim_pose(
                        prim_path_a=reference_path,
                        prim_path_b=pose_cfg.prim_path,
                        rotation_type="cartesian",
                    )
                    item = self._pose_model.add_pose(
                        prim_path=pose_cfg.prim_path,
                        name=prim.GetName(),
                        pose=pose,
                        is_ghost_object=pose_cfg.is_ghost_object,
                    )
                    item.motion_type = pose_cfg.motion_type
                    item.joint_configs = pose_cfg.joint_configs
                    item.selected_config_idx = pose_cfg.selected_config_idx
                    item.tcp_name = getattr(pose_cfg, "tcp_name", None)
                    item.blending = pose_cfg.blending
                    item.limits_override = pose_cfg.limits_override
                    if item.joint_configs:
                        item.reachable = True
                    self._controller.setup_watcher_for_prim(pose_cfg.prim_path)
        elif config.poses and not self._mg_setup.robot_prim:
            missing_prims = True

        if missing_prims:
            carb.log_warn(
                f"Loaded skill '{config.name}': some prims "
                f"(motion group / poses) were not found in the current stage."
            )
            nm.post_notification(
                f"Loaded '{config.name}', but some prims were not found in the "
                f"current stage; those poses were skipped.",
                duration=6.0,
                status=nm.NotificationStatus.WARNING,
            )

        if config.planned_trajectory and config.planned_trajectory.joint_positions:
            pt = config.planned_trajectory
            self._planner.restore_trajectory(
                wb_v2_models.JointTrajectory(
                    joint_positions=pt.joint_positions,
                    locations=pt.locations,
                    times=pt.times if pt.times else None,
                )
            )

        self._mg_setup.set_pending_tcp(config.tcp_name)

    def destroy(self) -> None:
        self._controller.destroy()
        self._preview.hide()
        self._preview.destroy()
        self._ik_manager.destroy()
        self._planner.destroy()
        self._executor.destroy()
        self._pose_model.clear()
        self._tree_view = None
        self._frame = None
        self._skill_section = None

    def _rebuild(self) -> None:
        if self._frame is None:
            return
        if self._executor.state != ExecutionState.IDLE:
            return
        if self._mg_setup.selected_tcp:
            self._mg_setup.set_pending_tcp(self._mg_setup.selected_tcp)

        prev_state = (
            self._progress.visible,
            self._progress.value,
            self._progress.hint_text,
            self._progress.hint_visible,
        )

        self._frame.clear()
        with self._frame:
            with ui.VStack(spacing=0, height=0, style=TOOLTIP_STYLE):
                self._skill_section = CollapsibleSection(
                    title=self._name,
                    collapsed=self._collapsed,
                    build_header_fn=self._build_header_buttons,
                    on_collapsed_changed=lambda c: setattr(self, "_collapsed", c),
                )
                with self._skill_section.body:
                    with ui.ZStack():
                        ui.Rectangle(
                            style={
                                "background_color": NOVAColor.COLLAPSIBLE_SECTION_BODY.color,
                                "border_radius": 2,
                            },
                        )
                        with ui.HStack():
                            ui.Spacer(width=4)
                            with ui.VStack(spacing=0, height=0):
                                self._build_body()
                            ui.Spacer(width=4)

        self._progress.restore_state(*prev_state)

        self._controller.tree_view = self._tree_view
        self._controller.on_rebuild()

    def _build_header_buttons(self, section: CollapsibleSection) -> None:
        with ui.VStack(width=0):
            ui.Spacer()
            ui.Button(
                "",
                width=22,
                height=22,
                image_url=get_icon("close.svg"),
                image_width=16,
                image_height=16,
                tooltip="Delete skill",
                clicked_fn=lambda ws=weakref.ref(self): (
                    ws()._on_delete(ws()) if ws() else None
                ),
                style={
                    "Button": {
                        "margin": 0,
                        "padding": 0,
                        "background_color": 0x00000000,
                    },
                    "Button:hovered": {
                        "background_color": NOVAColor.BUTTON_HOVER.color
                    },
                    **_TOOLTIP_SUB,
                },
            )
            ui.Spacer()

    def _build_body(self) -> None:
        with ui.VStack(spacing=4, height=0):
            ui.Spacer(height=4)

            self._mg_setup.build()

            ui.Line(
                height=1,
                style={"border_width": 1, "color": NOVAColor.DIVIDER.color},
            )

            self._poses_section = CollapsibleSection(
                title=f"Poses ({len(self._pose_model.items)})",
                collapsed=self._poses_collapsed,
                build_header_fn=lambda section, ws=weakref.ref(self): (
                    ws()._build_poses_header_buttons() if ws() else None
                ),
                on_collapsed_changed=lambda collapsed, ws=weakref.ref(self): (
                    ws()._on_poses_collapsed_changed(collapsed) if ws() else None
                ),
            )
            with self._poses_section.body:
                self._tree_view = ui.TreeView(
                    self._pose_model,
                    delegate=self._pose_delegate,
                    root_visible=False,
                    header_visible=False,
                    height=0,
                    columns_resizable=False,
                    selection_changed_fn=lambda sel, ws=weakref.ref(self): (
                        ws()._controller._on_tree_selection_changed(sel)
                        if ws()
                        else None
                    ),
                    column_widths=[
                        ui.Fraction(3),
                        ui.Fraction(1),
                        ui.Pixel(108),
                    ],
                    style={
                        "TreeView.Item": {
                            "margin": 0,
                            "background_color": NOVAColor.TREEVIEW_BACKGROUND.color,
                        },
                        "TreeView.Row": {
                            "margin": 0,
                            "background_color": NOVAColor.TREEVIEW_BACKGROUND.color,
                        },
                        "TreeView": {
                            "background_color": NOVAColor.TREEVIEW_BACKGROUND.color
                        },
                        "TreeView.Item:selected": {
                            "background_color": NOVAColor.TREEVIEW_SELECTED.color,
                            "border_color": 0x00000000,
                            "border_width": 0,
                        },
                        "TreeView.Row:selected": {
                            "background_color": NOVAColor.TREEVIEW_SELECTED.color,
                            "border_color": 0x00000000,
                            "border_width": 0,
                        },
                        "TreeView.Item:focused": {
                            "border_color": 0x00000000,
                            "border_width": 0,
                        },
                        "TreeView.Row:focused": {
                            "border_color": 0x00000000,
                            "border_width": 0,
                        },
                        "TreeView.Item:hovered": {
                            "background_color": NOVAColor.TREEVIEW_HOVERED.color
                        },
                        "TreeView.Row:hovered": {
                            "background_color": NOVAColor.TREEVIEW_HOVERED.color
                        },
                        **TOOLTIP_STYLE,
                    },
                )

            self._settings.build()
            with ui.VStack(spacing=0, height=0):
                self._controls.build(
                    live_update_widget_fn=self._build_live_update_controls
                )
                self._progress.build()

    def _build_live_update_controls(self) -> None:
        """Build live-update link/unlink toggle, called inside the controls row."""
        active = self._settings.live_update
        self._live_update_btn = ui.Button(
            "",
            width=30,
            height=ui.Fraction(1),
            image_url=get_icon("link.svg" if active else "unlink.svg"),
            tooltip="Apply changes automatically: re-plan when poses are moved.",
            clicked_fn=lambda ws=weakref.ref(self): (
                ws()._on_live_update_toggled(not ws()._settings.live_update)
                if ws()
                else None
            ),
            style={
                "Button": {
                    "background_color": NOVAColor.PRIMARY_MAIN.color
                    if active
                    else 0xFF292929,
                    "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color
                    if active
                    else NOVAColor.TEXT_PRIMARY.color,
                },
                "Button:hovered": {
                    "background_color": NOVAColor.PRIMARY_LIGHT.color
                    if active
                    else NOVAColor.BUTTON_HOVER.color
                },
                **_TOOLTIP_SUB,
            },
        )

    def _on_live_update_toggled(self, enabled: bool) -> None:
        self._settings.live_update = enabled
        self._settings._notify("live_update", enabled)
        if self._live_update_btn:
            self._live_update_btn.image_url = get_icon(
                "link.svg" if enabled else "unlink.svg"
            )
            self._live_update_btn.set_style(
                {
                    "Button": {
                        "background_color": NOVAColor.PRIMARY_MAIN.color
                        if enabled
                        else 0xFF292929,
                        "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color
                        if enabled
                        else NOVAColor.TEXT_PRIMARY.color,
                    },
                    "Button:hovered": {
                        "background_color": NOVAColor.PRIMARY_LIGHT.color
                        if enabled
                        else NOVAColor.BUTTON_HOVER.color
                    },
                    **_TOOLTIP_SUB,
                }
            )

    def _get_api_configuration(self) -> ApiConfiguration | None:
        return self._mg_setup.get_api_configuration()

    def _get_tcp_for_pose_item(self, item: PoseItem) -> str | None:
        if item.tcp_name:
            return item.tcp_name
        if item.is_ghost_object:
            nova_tcps = self._mg_setup.nova_tcps
            if nova_tcps:
                stage = omni.usd.get_context().get_stage()
                if stage:
                    prim = stage.GetPrimAtPath(item.prim_path)
                    if prim and prim.IsValid():
                        matched = make_ghost_tcp_matcher(prim)(nova_tcps)
                        if matched:
                            item.tcp_name = matched
                            return matched
        return self._mg_setup.selected_tcp

    def _get_stream_params(self) -> tuple[str, str, str] | None:
        mg = self._mg_setup.mg_config
        if not mg:
            return None
        msc = mg.motion_stream_configuration
        return msc.cell, msc.controller, msc.motion_group

    def _get_planning_settings(self) -> dict:
        # TCP velocity/acceleration are now edited via the Global Motion Settings
        # modal (as a limits override). Prefer the override's values; fall back to
        # the robot's auto-limit defaults so behaviour matches the old inline fields.
        from wandelbots.omni.ui.tool.trajectory_planner.widgets.motion_settings_dialog import (
            limits_from_dict,
        )

        override = limits_from_dict(self._settings.global_limits_override)
        tcp_velocity = getattr(override, "tcp_velocity_limit", None) or (
            self._settings.tcp_velocity if self._settings.tcp_velocity > 0 else None
        )
        tcp_acceleration = getattr(override, "tcp_acceleration_limit", None) or (
            self._settings.tcp_acceleration
            if self._settings.tcp_acceleration > 0
            else None
        )
        return {
            "tcp_velocity": tcp_velocity,
            "tcp_acceleration": tcp_acceleration,
            "auto_blending": self._settings.auto_blending,
            "blending_min_velocity_percent": self._settings.blending_min_velocity_percent,
            "global_blending": self._settings.global_blending,
            "global_limits_override": self._settings.global_limits_override,
            "payload_name": self._settings.payload_name
            if self._settings.payload_name
            else None,
            "payload_mass": self._settings.payload_mass
            if self._settings.payload_mass > 0
            else None,
            "cf_algorithm": self._settings.cf_algorithm,
            "cf_max_iterations": self._settings.cf_max_iterations,
            "plan_collision_free": self._settings.plan_collision_free,
            "velocity_coloring": self._settings.velocity_coloring,
        }

    def _get_pose_relative_to_mg(self, prim_path: str, stage=None) -> WSPose:
        if stage is None:
            stage = omni.usd.get_context().get_stage()
        mg = self._mg_setup.mg_config
        if mg and mg.prim_path:
            mg_prim = stage.GetPrimAtPath(mg.prim_path)
            link_0 = get_link_0_from_motion_group_prim(mg_prim)
            reference_path = str(link_0.GetPath()) if link_0 else mg.prim_path
            return PrimUtils.get_relative_prim_pose(
                prim_path_a=reference_path,
                prim_path_b=prim_path,
                rotation_type="cartesian",
            )
        return PrimUtils.get_prim_pose(
            prim_path=prim_path,
            coordinate_system="world",
            rotation_type="cartesian",
            stage=stage,
        )

    def _build_poses_header_buttons(self) -> None:
        self._add_poses_btn = ui.Button(
            "Add poses",
            width=70,
            height=22,
            tooltip="Pick GhostObjects or Pose prims to add as trajectory poses",
            clicked_fn=lambda ws=weakref.ref(self): (
                ws()._pose_list.pick_poses() if ws() else None
            ),
            style={
                "Button": {
                    "background_color": NOVAColor.PRIMARY_MAIN.color,
                    "font_size": 12,
                    "border_radius": 4,
                },
                "Button:hovered": {"background_color": NOVAColor.PRIMARY_LIGHT.color},
                **_TOOLTIP_SUB,
            },
        )
        self._edit_mode_btn = ui.Button(
            "Done" if self._controller.edit_mode else "Edit",
            width=50,
            height=22,
            tooltip="Toggle edit mode (reorder, hide, delete)",
            clicked_fn=lambda ws=weakref.ref(self): (
                ws()._toggle_edit_mode() if ws() else None
            ),
            style={
                "Button": {
                    "background_color": 0xFF292929,
                    "font_size": 12,
                    "border_radius": 4,
                },
                "Button:hovered": {"background_color": NOVAColor.BUTTON_HOVER.color},
                **_TOOLTIP_SUB,
            },
        )

    def _on_poses_collapsed_changed(self, collapsed: bool) -> None:
        self._poses_collapsed = collapsed

    def _toggle_edit_mode(self) -> None:
        self._controller.edit_mode = not self._controller.edit_mode
        self._pose_delegate.edit_mode = self._controller.edit_mode
        if self._edit_mode_btn:
            self._edit_mode_btn.text = "Done" if self._controller.edit_mode else "Edit"
        self._controller.refresh_tree_view()

    def _update_poses_section_title(self) -> None:
        if self._poses_section:
            self._poses_section.title = f"Poses ({len(self._pose_model.items)})"
