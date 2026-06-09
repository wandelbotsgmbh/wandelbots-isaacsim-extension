"""Coordination logic for a single trajectory planner skill instance."""

from __future__ import annotations

import asyncio
import weakref
from typing import Callable

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from omni.usd import get_watcher
from pxr import Sdf

import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models

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
    PoseDetailItem,
    PoseItem,
    PoseModel,
)
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_preview import (
    TrajectoryPlannerPreview,
)
from wandelbots.omni.ui.tool.trajectory_planner.widgets.motion_group_setup import (
    MotionGroupSetup,
)
from wandelbots.omni.ui.tool.trajectory_planner.widgets.progress_status_bar import (
    ProgressStatusBar,
)
from wandelbots.omni.ui.tool.trajectory_planner.widgets.settings_section import (
    SettingsSection,
)
from wandelbots.omni.ui.tool.trajectory_planner.widgets.trajectory_controls import (
    TrajectoryControls,
)
from wandelbots.omni.ui.utils import defer_call
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.utils.teaching import make_ghost_tcp_matcher

_DEBOUNCE_DELAY = 0.3


class TrajectoryPlannerController:
    """Wires events to actions for one trajectory-planner skill instance."""

    def __init__(
        self,
        *,
        events: TrajectoryPlannerEvents,
        pose_model: PoseModel,
        pose_delegate: PoseDelegate,
        mg_setup: MotionGroupSetup,
        settings: SettingsSection,
        controls: TrajectoryControls,
        progress: ProgressStatusBar,
        preview: TrajectoryPlannerPreview,
        planner: PlanningOrchestrator,
        ik_manager: IKManager,
        executor: ExecutionOrchestrator,
        pose_list: PoseListManager,
        on_selection_changed: Callable[[PoseItem | None], None],
        rebuild_fn: Callable[[], None],
        update_poses_title_fn: Callable[[], None],
        get_pose_relative_to_mg: Callable[..., object],
    ) -> None:
        self._events = events
        self._pose_model = pose_model
        self._pose_delegate = pose_delegate
        self._mg_setup = mg_setup
        self._settings = settings
        self._controls = controls
        self._progress = progress
        self._preview = preview
        self._planner = planner
        self._ik_manager = ik_manager
        self._executor = executor
        self._pose_list = pose_list
        self._on_selection_changed = on_selection_changed
        self._rebuild_fn = rebuild_fn
        self._update_poses_title_fn = update_poses_title_fn
        self._get_pose_relative_to_mg = get_pose_relative_to_mg

        self._selected_pose_item: PoseItem | None = None
        self._syncing_selection: bool = False
        self._debounce_task: asyncio.Task | None = None
        self._watch_subs: list = []
        self._stage_event_sub = None
        self._cached_tool_colliders: dict | None = None
        self._cached_collision_setup_name: str | None = None
        self._motion_group_limits: dict | None = None
        self._edit_mode: bool = False
        self._planned_waypoint_count: int | None = None

        self.tree_view: ui.TreeView | None = None

        self._subscribe()

    def _subscribe(self) -> None:
        ev = self._events
        ev.motion_group_changed.connect(self._on_motion_group_changed)
        ev.tcp_changed.connect(self._on_tcp_changed)
        ev.collision_setup_changed.connect(self._on_collision_setup_changed)
        ev.setting_changed.connect(self._on_setting_changed)
        ev.calculate_iks_requested.connect(self._on_calculate_iks)
        ev.plan_requested.connect(self._on_plan)
        ev.replan_requested.connect(self._on_replan)
        ev.execute_toggle_requested.connect(self._on_execute_toggle)
        ev.force_stop_requested.connect(self._on_force_stop)
        ev.pose_added.connect(self._on_pose_added)
        ev.pose_removed.connect(self._on_pose_removed)
        ev.poses_reordered.connect(self._on_poses_reordered)
        ev.motion_type_changed.connect(self._on_motion_type_changed)
        ev.inline_config_changed.connect(self._on_inline_config_changed)
        ev.pose_settings_clicked.connect(self._on_pose_settings_clicked)
        ev.ik_progress.connect(self._on_ik_progress)
        ev.ik_complete.connect(self._on_ik_complete)
        ev.reachability_complete.connect(self._on_reachability_complete)
        ev.plan_started.connect(self._on_plan_started)
        ev.plan_progress.connect(self._on_plan_progress)
        ev.plan_complete.connect(self._on_plan_complete)
        ev.plan_failed.connect(self._on_plan_failed)
        ev.plan_stored.connect(self._on_plan_stored)
        ev.execution_started.connect(self._on_execution_started)
        ev.execution_paused.connect(self._on_execution_paused)
        ev.execution_progress.connect(self._on_execution_progress)
        ev.execution_joint_update.connect(self._on_execution_joint_update)
        ev.execution_location.connect(self._on_execution_location)
        ev.execution_complete.connect(self._on_execution_done)
        ev.execution_cancelled.connect(self._on_execution_done)
        ev.execution_failed.connect(lambda _: self._on_execution_done())

    @property
    def edit_mode(self) -> bool:
        return self._edit_mode

    @edit_mode.setter
    def edit_mode(self, value: bool) -> None:
        self._edit_mode = value

    @property
    def selected_pose_item(self) -> PoseItem | None:
        return self._selected_pose_item

    def on_rebuild(self) -> None:
        """Call after every widget._rebuild() to refresh controller-owned state."""
        if self._mg_setup.mg_config:
            cs = self._mg_setup.selected_collision_setup
            if cs and self._cached_collision_setup_name != cs:
                self._fetch_tool_colliders_for_setup()
        self._update_controls()

    def refresh_tree_view(self) -> None:
        if not self.tree_view or self._executor.state != ExecutionState.IDLE:
            return
        self._pose_delegate._widgets.clear()
        self._pose_delegate._subs.clear()
        self.tree_view.dirty_widgets()

    def select_by_prim_path(self, prim_path: str) -> bool:
        if self._syncing_selection:
            return False
        item = self._pose_model.get_item_by_path(prim_path)
        if item is None or self.tree_view is None:
            return False
        self._syncing_selection = True
        self.tree_view.selection = [item]
        self._syncing_selection = False
        return True

    def clear_selection(self) -> None:
        if self.tree_view:
            self._syncing_selection = True
            self.tree_view.selection = []
            self._syncing_selection = False

    def setup_watcher_for_prim(self, prim_path: str) -> None:
        watcher = get_watcher()
        sub = watcher.subscribe_to_change_info_path(
            Sdf.Path(prim_path),
            lambda path=None, p=prim_path, ws=weakref.ref(self): (
                ws()._on_prim_changed(p) if ws() else None
            ),
        )
        self._watch_subs.append(sub)
        if self._stage_event_sub is None:
            usd_context = omni.usd.get_context()
            self._stage_event_sub = (
                usd_context.get_stage_event_stream().create_subscription_to_pop(
                    self._on_stage_event,
                    name="TrajectoryPlannerController.stage_events",
                )
            )

    def teardown_watchers(self) -> None:
        self._watch_subs.clear()
        self._stage_event_sub = None
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            self._debounce_task = None

    def destroy(self) -> None:
        self.teardown_watchers()
        self._events.clear_all()

    def _on_motion_group_changed(self, config) -> None:
        self._recalculate_all_poses()
        self.refresh_tree_view()
        for item in self._pose_model.items:
            item.joint_configs = []
            item.selected_config_idx = 0
        self._preview.hide()
        self._planner.invalidate()
        self._controls.set_trajectory_planned(False)
        self._update_controls()
        if config:
            run_coroutine(self._fetch_and_apply_auto_limits())
            if self._pose_model.items:
                self._ik_manager.refresh_all_ik()

    def _on_tcp_changed(self, tcp_name: str | None) -> None:
        self._resolve_ghost_tcp_names()
        self._planner.invalidate()
        self._preview.hide()
        self._controls.set_trajectory_planned(False)
        self._update_controls()
        if self._pose_model.items:
            self._ik_manager.refresh_all_ik()

    def _resolve_ghost_tcp_names(self) -> None:
        """Retroactively set tcp_name on ghost items using offset matching."""
        nova_tcps = self._mg_setup.nova_tcps
        if not nova_tcps:
            return
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        for item in self._pose_model.items:
            if not item.is_ghost_object or item.tcp_name:
                continue
            prim = stage.GetPrimAtPath(item.prim_path)
            if not prim or not prim.IsValid():
                continue
            matched = make_ghost_tcp_matcher(prim)(nova_tcps)
            if matched:
                item.tcp_name = matched
        self._pose_model.notify_item_changed(None)

    async def _fetch_and_apply_auto_limits(self) -> None:
        api_config = self._mg_setup.get_api_configuration()
        mg = self._mg_setup.mg_config
        if not api_config or not mg:
            return
        msc = mg.motion_stream_configuration
        cell, controller, motion_group = msc.cell, msc.controller, msc.motion_group
        try:
            async with get_api_client_from_config(api_config) as api_client:
                mg_api = wb_v2.MotionGroupApi(api_client)
                description = await mg_api.get_motion_group_description(
                    cell=cell,
                    controller=controller,
                    motion_group=motion_group,
                )
            auto_limits = getattr(
                getattr(description, "operation_limits", None), "auto_limits", None
            )
            if auto_limits and auto_limits.tcp:
                self._settings.set_tcp_limits(
                    velocity=auto_limits.tcp.velocity,
                    acceleration=auto_limits.tcp.acceleration,
                )
                self._motion_group_limits = {
                    "tcp_velocity": auto_limits.tcp.velocity,
                    "tcp_acceleration": auto_limits.tcp.acceleration,
                    "tcp_orientation_velocity": getattr(
                        auto_limits.tcp, "orientation_velocity", None
                    ),
                    "tcp_orientation_acceleration": getattr(
                        auto_limits.tcp, "orientation_acceleration", None
                    ),
                }
            if auto_limits and auto_limits.joints:
                joint_vel = []
                joint_acc = []
                for j in auto_limits.joints:
                    joint_vel.append(getattr(j, "velocity", None))
                    joint_acc.append(getattr(j, "acceleration", None))
                if not self._motion_group_limits:
                    self._motion_group_limits = {}
                self._motion_group_limits["joint_velocity_limits"] = joint_vel
                self._motion_group_limits["joint_acceleration_limits"] = joint_acc
            self._settings.set_motion_group_limits(self._motion_group_limits)
        except Exception as exc:
            carb.log_info(f"Could not fetch auto_limits for settings defaults: {exc}")

    def _on_collision_setup_changed(self, setup: str | None) -> None:
        self._pose_delegate.collision_free = setup is not None
        self._pose_model.collision_free = setup is not None
        self._controls.set_collision_free(setup is not None)
        self._settings.set_collision_free(setup is not None)
        self._planner.invalidate()
        self._fetch_tool_colliders_for_setup()
        defer_call(self._rebuild_fn)

    def _on_setting_changed(self, key: str, value) -> None:
        if key == "live_update" and value:
            self._trigger_live_update()
        elif key == "overlay_color":
            pass
        elif key == "trajectory_color":
            if self._planner.planned_joint_trajectory:
                self._planner.update_trajectory_color(self._settings.trajectory_color)
        else:
            self._on_plan_invalidated()

    def _on_pose_added(self, item: PoseItem) -> None:
        self.setup_watcher_for_prim(item.prim_path)
        self._planner.invalidate()
        self._ik_manager.fetch_ik_for_pose(item, silent=True)
        self._update_poses_title_fn()
        self._update_controls()

    def _on_pose_removed(self, item: PoseItem) -> None:
        # item is already removed from pose_model by PoseListManager
        self._planner.invalidate()
        defer_call(self._rebuild_fn)

    def _on_poses_reordered(self, item: PoseItem) -> None:
        # move has already happened in PoseListManager
        self._planner.invalidate()
        self._selected_pose_item = item
        defer_call(lambda: self._rebuild_and_reselect(item))
        if self._settings.live_update:
            self._trigger_live_update()

    def _on_motion_type_changed(self, item: PoseItem, motion_type: str) -> None:
        self._planner.invalidate()
        self._pose_model.notify_item_changed(item)
        if self._settings.live_update:
            self._trigger_live_update()

    def _on_inline_config_changed(self, item: PoseItem, idx: int) -> None:
        item.selected_config_idx = idx
        if item.selected_joint_config:
            self._show_preview(item.selected_joint_config)
        self._planner.invalidate()
        self._update_controls()
        if self._settings.live_update:
            self._trigger_live_update()

    def _on_pose_settings_clicked(self, item: PoseItem) -> None:
        from wandelbots.omni.ui.tool.trajectory_planner.widgets.motion_settings_dialog import (
            MotionSettingsDialog,
            blending_from_dict,
            limits_from_dict,
            blending_to_dict,
            limits_to_dict,
        )

        def _on_apply(blending, limits_override):
            item.blending = blending_to_dict(blending)
            item.limits_override = limits_to_dict(limits_override)
            self._pose_model.notify_item_changed(item)
            self._planner.invalidate()
            if self._settings.live_update:
                self._trigger_live_update()

        def _on_tcp_changed(tcp_name: str | None):
            item.tcp_name = tcp_name
            self._pose_model.notify_item_changed(item)
            self._planner.invalidate()
            self._ik_manager.fetch_ik_for_pose(item)

        MotionSettingsDialog(
            title=f"Motion Settings - {item.name_model.get_value_as_string()}",
            blending=blending_from_dict(item.blending),
            limits_override=limits_from_dict(item.limits_override),
            on_apply=_on_apply,
            motion_group_limits=self._motion_group_limits
            or {
                "tcp_velocity": self._settings.tcp_velocity,
                "tcp_acceleration": self._settings.tcp_acceleration,
            },
            tcp_names=self._mg_setup.tcp_names,
            current_tcp=item.tcp_name,
            on_tcp_changed=_on_tcp_changed,
        )

    def _on_plan_invalidated(self) -> None:
        self._planner.invalidate()
        self._preview.hide()
        self._update_controls()

    def _on_ik_progress(self, pending: int, total: int) -> None:
        if pending > 0:
            done = total - pending
            progress = done / total if total > 0 else 0.0
            self._progress.show(progress)
            self._progress.set_hint(f"Computing IK for {pending} pose(s)...")
        elif self._executor.state in (ExecutionState.IDLE, ExecutionState.TEARING_DOWN):
            self._progress.hide()
        self._update_controls()

    def _on_ik_complete(self, item: PoseItem) -> None:
        self._pose_model.notify_item_changed(item)
        if item is self._selected_pose_item and item.selected_joint_config:
            self._show_preview(item.selected_joint_config)
        if self._ik_manager.ik_pending_count == 0:
            self.refresh_tree_view()
        self._update_controls()

    def _on_reachability_complete(self, reachable: int, unreachable: int) -> None:
        self._progress.hide()
        self.refresh_tree_view()

    def _on_calculate_iks(self) -> None:
        self._ik_manager.refresh_all_ik()

    def _on_plan(self) -> None:
        self._planner.plan()
        self._controls.set_cancel_label()

    def _on_replan(self) -> None:
        self._planner.invalidate()
        self._planner.plan()
        self._controls.set_cancel_label()

    def _on_execute_toggle(self) -> None:
        state = self._executor.state
        if state == ExecutionState.PAUSED:
            self._executor.resume()
        elif state == ExecutionState.EXECUTING:
            self._executor.pause()
        elif state in (ExecutionState.IDLE, ExecutionState.TEARING_DOWN):
            jt = self._planner.planned_joint_trajectory
            if not jt:
                nm.post_notification(
                    "No planned trajectory to execute.",
                    duration=4.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return
            num_commands = max(len(self._pose_model.items) - 1, 1)
            self._preview.hide()
            self._progress.show(0.05)
            self._executor.execute(jt, num_commands)

    def _on_force_stop(self) -> None:
        self._executor.stop()

    def _on_plan_started(self) -> None:
        self._progress.show(0.0)
        self._preview.hide()

    def _on_plan_progress(self, value: float, msg: str) -> None:
        self._progress.update(value)
        if msg:
            self._progress.set_hint(msg)

    def _on_plan_complete(self, joint_trajectory: wb_v2_models.JointTrajectory) -> None:
        self._controls.set_trajectory_planned(True)
        duration = None
        if joint_trajectory.times:
            duration = joint_trajectory.times[-1] - joint_trajectory.times[0]
        self._controls.show_execution_time(duration)

        items = self._pose_model.items
        if joint_trajectory.times and joint_trajectory.locations and items:
            times = joint_trajectory.times
            locations = joint_trajectory.locations
            last_idx = len(items) - 1
            for i, item in enumerate(items):
                if i == last_idx:
                    item.cycle_time_s = None
                    continue
                indices = [j for j, loc in enumerate(locations) if int(loc) == i]
                item.cycle_time_s = (
                    times[indices[-1]] - times[indices[0]] if indices else None
                )
        else:
            for item in items:
                item.cycle_time_s = None

        for item in self._pose_model.items:
            if item.joint_configs and not item.selected_joint_config:
                item.selected_config_idx = 0

        self._pose_model.notify_item_changed(None)
        self.refresh_tree_view()
        self._progress.hide()
        self._update_controls()

        run_coroutine(
            self._planner.visualize_trajectory(self._settings.trajectory_color)
        )
        # Defer user feedback to _on_plan_stored so the planned-waypoint count and
        # the stored version are shown together in a single notification.
        self._planned_waypoint_count = len(joint_trajectory.joint_positions)

    def _on_plan_stored(self, version: str | None) -> None:
        """Show one combined message with the waypoint count and stored version.

        ``version`` is None when storage was skipped or failed. The version is also
        written to the carb log by the orchestrator.
        """
        count = getattr(self, "_planned_waypoint_count", None)
        waypoints = f"{count} waypoints" if count is not None else "trajectory"
        if version:
            self._progress.set_hint(f"Planned · stored {version}")
            message = f"Trajectory planned ({waypoints}), stored as {version}."
        else:
            self._progress.set_hint("Planned · storage failed")
            message = f"Trajectory planned ({waypoints}); storage failed (see log)."
        nm.post_notification(
            message,
            duration=4.0,
            status=nm.NotificationStatus.INFO,
        )

    def _on_plan_failed(self, error: str) -> None:
        self._controls.set_trajectory_planned(False)
        self._pose_model.notify_item_changed(None)
        self.refresh_tree_view()
        self._progress.hide()
        self._update_controls()

    def _on_execution_started(self) -> None:
        self._controls.set_pause_label()

    def _on_execution_paused(self) -> None:
        self._controls.set_resume_label()

    def _on_execution_progress(self, value: float, msg: str) -> None:
        self._progress.update(value)
        if msg:
            self._progress.set_hint(msg)

    def _on_execution_location(self, location: float, total: float) -> None:
        new_idx = int(location)
        if new_idx != self._pose_delegate.executing_index:
            self._pose_delegate.executing_index = new_idx
            self._pose_model.notify_item_changed(None)

    def _on_execution_joint_update(self, joint_positions: list[float]) -> None:
        mg_config = self._mg_setup.mg_config
        if not mg_config or not mg_config.prim_path:
            return
        exec_color = [0.2, 0.8, 1.0, 0.4]
        self._preview.show(
            mg_config.prim_path,
            joint_positions,
            color=exec_color,
            tool_colliders=self._cached_tool_colliders,
        )

    def _on_execution_done(self) -> None:
        self._progress.hide()
        self._pose_delegate.executing_index = None
        self._pose_model.notify_item_changed(None)
        self._update_controls()

    def _show_preview(self, joint_positions: list[float]) -> None:
        mg = self._mg_setup.mg_config
        if not mg or not mg.prim_path:
            return
        color = list(self._settings.overlay_color) + [0.3]
        self._preview.show(
            mg.prim_path,
            joint_positions,
            color=color,
            tool_colliders=self._cached_tool_colliders,
            filled=False,
        )

    def _fetch_tool_colliders_for_setup(self) -> None:
        cs = self._mg_setup.selected_collision_setup
        if not cs or not self._mg_setup.mg_config:
            self._cached_tool_colliders = None
            self._cached_collision_setup_name = None
            return
        run_coroutine(self._do_fetch_tool_colliders())

    async def _do_fetch_tool_colliders(self) -> None:
        api_config = self._mg_setup.get_api_configuration()
        mg = self._mg_setup.mg_config
        cs = self._mg_setup.selected_collision_setup
        if not api_config or not mg or not cs:
            return
        msc = mg.motion_stream_configuration
        try:
            async with get_api_client_from_config(api_config) as api_client:
                setup = await wb_v2.StoreCollisionSetupsApi(
                    api_client
                ).get_stored_collision_setup(cell=msc.cell, setup=cs)
                self._cached_tool_colliders = setup.tool if setup.tool else None
                self._cached_collision_setup_name = cs
        except Exception as exc:
            carb.log_warn(f"Failed to fetch tool colliders: {exc}")
            self._cached_tool_colliders = None
            self._cached_collision_setup_name = None
            return
        item = self._selected_pose_item
        if item and item.selected_joint_config:
            self._show_preview(item.selected_joint_config)

    def _on_tree_selection_changed(self, selection: list[PoseItem]) -> None:
        if self._syncing_selection:
            return
        if not selection:
            self._selected_pose_item = None
            self._on_selection_changed(None)
            self._preview.hide()
            return

        item = selection[0]
        if isinstance(item, PoseDetailItem):
            item = item.parent

        self._selected_pose_item = item
        self._on_selection_changed(item)

        if item.selected_joint_config:
            self._show_preview(item.selected_joint_config)
        else:
            self._preview.hide()
            if not item.joint_configs and not item.ik_loading:
                self._ik_manager.fetch_ik_for_pose(item)

        self._syncing_selection = True
        omni.usd.get_context().get_selection().set_selected_prim_paths(
            [item.prim_path], False
        )
        self._syncing_selection = False

    def _on_stage_event(self, event) -> None:
        if event.type != int(omni.usd.StageEventType.HIERARCHY_CHANGED):
            return
        self._check_deleted_prims()

    def _check_deleted_prims(self) -> None:
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        removed = [
            item.prim_path
            for item in self._pose_model.items
            if not stage.GetPrimAtPath(item.prim_path)
            or not stage.GetPrimAtPath(item.prim_path).IsValid()
        ]
        if removed:
            for prim_path in removed:
                self._pose_model.remove_pose(prim_path)
            self._planner.invalidate()
            defer_call(self.refresh_tree_view)

    def _on_prim_changed(self, prim_path: str) -> None:
        if self._debounce_task is not None:
            self._debounce_task.cancel()
        self._debounce_task = run_coroutine(self._debounced_update(prim_path))

    async def _debounced_update(self, prim_path: str) -> None:
        await asyncio.sleep(_DEBOUNCE_DELAY)
        self._debounce_task = None
        self._refresh_pose(prim_path)
        if self._settings.live_update:
            self._trigger_live_update()

    def _refresh_pose(self, prim_path: str) -> None:
        if not self._pose_model.get_items_by_path(prim_path):
            return
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            self._pose_model.remove_pose(prim_path)
            self._planner.invalidate()
            defer_call(self.refresh_tree_view)
            return
        try:
            pose = self._get_pose_relative_to_mg(prim_path, stage)
            items = self._pose_model.get_items_by_path(prim_path)
            for item in items:
                item.update_pose(pose)
                item.reachable = None
                item.joint_configs = []
                item.selected_config_idx = 0
                if item is self._selected_pose_item:
                    self._preview.hide()
                self._pose_model.notify_item_changed(item)
                self._ik_manager.fetch_ik_for_pose(item, silent=True)
            self._planner.invalidate()
            defer_call(self.refresh_tree_view)
            self._update_controls()
        except Exception as exc:
            carb.log_warn(f"Failed to refresh pose for {prim_path}: {exc}")

    def _update_controls(self) -> None:
        items = self._pose_model.items
        all_ready = (
            all(not item.ik_loading and item.joint_configs for item in items)
            if items
            else False
        )
        self._controls.update(
            has_poses=bool(items),
            all_iks_ready=all_ready,
            has_trajectory=self._planner.trajectory_planned
            and self._planner.planned_joint_trajectory is not None,
            has_motion_group=self._mg_setup.mg_config is not None,
        )

    def _recalculate_all_poses(self) -> None:
        for item in self._pose_model.items:
            try:
                pose = self._get_pose_relative_to_mg(item.prim_path)
                item.update_pose(pose)
                item.reachable = None
            except Exception as exc:
                carb.log_warn(f"Failed to recalculate pose for {item.prim_path}: {exc}")
        self._pose_model.notify_item_changed(None)

    def _trigger_live_update(self) -> None:
        items = self._pose_model.items
        if not items or not self._mg_setup.mg_config:
            return
        self._ik_manager.check_reachability()
        if len(items) >= 2:
            self._planner.plan()

    def _rebuild_and_reselect(self, item: PoseItem) -> None:
        self._rebuild_fn()
        if self.tree_view and item in self._pose_model.items:
            self._syncing_selection = True
            self.tree_view.selection = [item]
            self._syncing_selection = False
        if item.selected_joint_config:
            self._show_preview(item.selected_joint_config)
        else:
            self._preview.hide()
