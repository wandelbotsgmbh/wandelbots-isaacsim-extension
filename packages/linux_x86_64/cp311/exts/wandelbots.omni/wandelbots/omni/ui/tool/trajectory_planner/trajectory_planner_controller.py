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

from wandelbots.omni.manipulators import get_motion_group_service
from wandelbots.omni.ui.tool.trajectory_planner.cells import is_joint_config_editable
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
from wandelbots.omni.utils.kinematics import weighted_joint_distance
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
        self._last_motion_group_id: tuple | None = None
        # Execute is gated on a successful visualization, not just a successful
        # plan — this is True only once the curve has been drawn.
        self._trajectory_visualized: bool = False
        # One-shot flag: a config is being restored (load / reopen). It drives a
        # restore-safe motion-group init in on_rebuild that refreshes the tree and
        # selectors without clearing restored joint configs or velocity/accel.
        self._restoring: bool = False
        self._edit_mode: bool = False
        self._planned_waypoint_count: int | None = None
        # Absolute location of the first played waypoint; non-zero only when
        # executing a "start from here" slice (locations are rebased to 0).
        self._execution_location_offset: float = 0.0

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
        ev.start_from_here_requested.connect(self._on_start_from_here)
        ev.go_to_requested.connect(self._on_go_to)
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
        # On restore, drive a one-shot motion-group init now that the tree view +
        # motion group context exist: refreshes the tree (poses show) and the
        # TCP/collision selectors, fetches reference limits — without clearing the
        # restored joint configs or overwriting restored velocity/accel (identity
        # was pre-seeded by begin_restore, so it is treated as unchanged).
        if self._restoring and self._mg_setup.mg_config:
            self._on_motion_group_changed(self._mg_setup.mg_config)
            self._restoring = False
        if self._mg_setup.mg_config:
            cs = self._mg_setup.selected_collision_setup
            if cs and self._cached_collision_setup_name != cs:
                self._fetch_tool_colliders_for_setup()
        # NOTE: a restored trajectory's curve is NOT redrawn automatically — the
        # user triggers it explicitly via the window's Refresh action.
        self._update_controls()

    def refresh_trajectory(self) -> None:
        """Redraw this skill's restored trajectory curve (window Refresh action)."""
        if self._planner.planned_joint_trajectory is None:
            return
        run_coroutine(self._refresh_trajectory_async())

    async def _refresh_trajectory_async(self) -> None:
        ok = await self._planner.visualize_trajectory(self._settings.trajectory_color)
        # A successfully drawn curve also enables Execute for a restored plan.
        self._trajectory_visualized = ok
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
        # Only discard cached IK / joint configs when the motion group actually
        # changes identity. Re-picking the same group (or a transient rebuild)
        # must not wipe restored joint-config selections.
        mg_id = self._motion_group_identity(config)
        identity_changed = mg_id != self._last_motion_group_id
        self._last_motion_group_id = mg_id
        if identity_changed:
            for item in self._pose_model.items:
                item.joint_configs = []
                item.selected_config_idx = 0
            # Switching to a different robot invalidates the old plan entirely.
            self._planner.invalidate(remove_visualization=True)
        self._preview.hide()
        self._controls.set_trajectory_planned(self._planner.trajectory_planned)
        self._update_controls()
        if config:
            # During restore, keep the restored velocity/accel (fetch only the
            # reference limits for the override dialog).
            run_coroutine(
                self._fetch_and_apply_auto_limits(preserve_limits=self._restoring)
            )
            if identity_changed and self._pose_model.items:
                self._ik_manager.refresh_all_ik()

    @staticmethod
    def _motion_group_identity(config) -> tuple | None:
        """Stable identity for a motion group config (cell, controller, group)."""
        if not config:
            return None
        try:
            msc = config.motion_stream_configuration
            return (msc.cell, msc.controller, msc.motion_group)
        except Exception:
            return None

    def begin_restore(self) -> None:
        """Mark that a restored config's motion group is set.

        Pre-seeds the identity so the restore-time motion-group init (run in
        on_rebuild) is treated as *unchanged* — it refreshes the tree/selectors
        and fetches limit references without clearing the restored joint configs
        or overwriting restored velocity/accel.
        """
        self._restoring = True
        self._last_motion_group_id = self._motion_group_identity(
            self._mg_setup.mg_config
        )

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

    async def _fetch_and_apply_auto_limits(self, preserve_limits: bool = False) -> None:
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
                # On restore, keep the user's saved velocity/accel; still record the
                # auto limits below as the reference for the override dialog.
                if not preserve_limits:
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
        # Selecting a collision scene no longer forces collision-free planning;
        # the planning mode is controlled by the independent "Collision-free
        # Planning" toggle (rendered below the collision selector). We still fetch
        # the tool colliders so the preview is correct in either mode.
        if setup is None and self._settings.plan_collision_free:
            # Collision-free is meaningless without a scene — turn it off.
            self._set_plan_collision_free(False)
        self._planner.invalidate()
        self._fetch_tool_colliders_for_setup()
        defer_call(self._rebuild_fn)

    def _set_plan_collision_free(self, enabled: bool) -> None:
        """Single source of truth for the collision-free planning mode."""
        self._settings.plan_collision_free = enabled
        self._pose_delegate.collision_free = enabled
        self._pose_model.collision_free = enabled
        self._controls.set_collision_free(enabled)
        self._settings.set_collision_free(enabled)

    def _on_plan_collision_free_changed(self, enabled: bool) -> None:
        self._set_plan_collision_free(enabled)
        self._planner.invalidate()
        defer_call(self._rebuild_fn)

    def _on_setting_changed(self, key: str, value) -> None:
        if key == "live_update" and value:
            self._trigger_live_update()
        elif key == "overlay_color":
            pass
        elif key == "trajectory_color":
            if self._planner.planned_joint_trajectory:
                self._planner.update_trajectory_color(self._settings.trajectory_color)
        elif key == "velocity_coloring":
            # Re-render the curve so it switches between the speed gradient and the
            # solid color (the gradient is recomputed from FK + times).
            if self._planner.planned_joint_trajectory:
                run_coroutine(
                    self._planner.visualize_trajectory(self._settings.trajectory_color)
                )
        elif key == "plan_collision_free":
            self._on_plan_collision_free_changed(bool(value))
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
        # Not visualized yet — keep Execute disabled until the curve is drawn.
        self._trajectory_visualized = False
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
            if not self._validate_motion_group():
                return
            jt = self._planner.planned_joint_trajectory
            if not jt:
                nm.post_notification(
                    "No planned trajectory to execute.",
                    duration=4.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return
            # Normal execution runs from the start; no location rebasing.
            self._execution_location_offset = 0.0
            num_commands = max(len(self._pose_model.items) - 1, 1)
            self._preview.hide()
            self._progress.show(0.05)
            self._executor.execute(jt, num_commands)

    def _on_force_stop(self) -> None:
        self._executor.stop()

    def _validate_motion_group(self) -> bool:
        """Warn (and return False) when no motion group is selected or connected.

        A motion group is "connected" while its prim carries the MotionGroupAPI;
        disconnecting removes that API (see MotionGroupService.remove_motion_group),
        so we query the shared service for the current state.
        """
        mg = self._mg_setup.mg_config
        if mg is None:
            nm.post_notification(
                "No motion group selected.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            return False
        service = get_motion_group_service()
        connected = False
        if service is not None:
            try:
                connected = service.has_motion_group(mg.prim_path)
            except Exception:
                connected = False
        if not connected:
            nm.post_notification(
                "Motion group is not connected.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            return False
        return True

    def _on_go_to(self, item: PoseItem) -> None:
        if not self._validate_motion_group():
            return
        joints = item.selected_joint_config
        if not joints:
            nm.post_notification(
                "No joint configuration available for this pose.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            return
        self._executor.go_to(joints)

    def _on_start_from_here(self) -> None:
        if not self._validate_motion_group():
            return
        jt = self._planner.planned_joint_trajectory
        if not jt:
            nm.post_notification(
                "No planned trajectory to execute.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            return
        item = self._selected_pose_item
        if item is None:
            nm.post_notification(
                "Select a pose to start from.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            return
        pose_index = self._pose_model.get_item_index(item)
        carb.log_info(
            f"Start from here: selected='{item.name_model.get_value_as_string()}', "
            f"pose_index={pose_index}, poses={len(self._pose_model.items)}"
        )
        sliced, loc_offset = self._slice_trajectory_from(jt, max(pose_index, 0))
        num_commands = max(len(self._pose_model.items) - 1, 1)
        # The player reports rebased locations (sliced trajectory starts at 0); add
        # the offset back so the executing-pose highlight maps to the right pose.
        self._execution_location_offset = loc_offset
        self._preview.hide()
        self._progress.show(0.05)
        self._executor.execute(sliced, num_commands)

    @staticmethod
    def _slice_trajectory_from(
        jt: wb_v2_models.JointTrajectory, pose_index: int
    ) -> tuple[wb_v2_models.JointTrajectory, float]:
        """Slice the trajectory to begin at ``pose_index``, rebased to location 0.

        Returns ``(sliced_trajectory, location_offset)``. The player requires the
        trajectory to start at location 0 (and time 0), so both arrays are rebased;
        ``location_offset`` is the original location of the first kept waypoint, used
        to map the player's rebased location back to absolute pose indices.
        """
        if pose_index <= 0 or not jt.locations:
            return jt, 0.0
        start = next(
            (i for i, loc in enumerate(jt.locations) if loc >= pose_index), None
        )
        if start is None or start == 0:
            return jt, 0.0
        loc0 = jt.locations[start]
        t0 = jt.times[start] if jt.times else 0.0
        sliced = wb_v2_models.JointTrajectory(
            joint_positions=jt.joint_positions[start:],
            times=[t - t0 for t in jt.times[start:]],
            locations=[loc - loc0 for loc in jt.locations[start:]],
        )
        return sliced, loc0

    def _on_plan_started(self) -> None:
        self._progress.show(0.0)
        self._preview.hide()

    def _on_plan_progress(self, value: float, msg: str) -> None:
        self._progress.update(value)
        if msg:
            self._progress.set_hint(msg)

    def _on_plan_complete(self, joint_trajectory: wb_v2_models.JointTrajectory) -> None:
        # Execute is enabled only once the trajectory has been visualized — see
        # _visualize_then_hide. Keep it disabled until then.
        self._trajectory_visualized = False
        duration = None
        if joint_trajectory.times:
            duration = joint_trajectory.times[-1] - joint_trajectory.times[0]
        self._controls.show_execution_time(duration)

        items = self._pose_model.items
        if joint_trajectory.times and joint_trajectory.locations and items:
            times = joint_trajectory.times
            locations = joint_trajectory.locations
            positions = joint_trajectory.joint_positions
            collision_free = self._pose_delegate.collision_free
            last_idx = len(items) - 1
            for i, item in enumerate(items):
                indices = [j for j, loc in enumerate(locations) if int(loc) == i]
                # cycle_time_s is the segment duration; the last pose has no segment.
                if i == last_idx:
                    item.cycle_time_s = None
                else:
                    item.cycle_time_s = (
                        times[indices[-1]] - times[indices[0]] if indices else None
                    )
                # For poses whose config the planner derives (read-only label), select
                # the IK solution nearest to the joints the planner actually used at
                # that pose's arrival waypoint.
                if (
                    indices
                    and item.joint_configs
                    and not is_joint_config_editable(
                        i, item.motion_type, collision_free
                    )
                ):
                    planned = positions[indices[0]]
                    item.selected_config_idx = min(
                        range(len(item.joint_configs)),
                        key=lambda k, p=planned, it=item: weighted_joint_distance(
                            p, it.joint_configs[k]
                        ),
                    )
        else:
            for item in items:
                item.cycle_time_s = None

        for item in self._pose_model.items:
            if item.joint_configs and not item.selected_joint_config:
                item.selected_config_idx = 0

        self._pose_model.notify_item_changed(None)
        self.refresh_tree_view()
        self._update_controls()

        # Keep the progress bar up while the curve renders; hide it only once the
        # trajectory has finished drawing.
        self._progress.update(1.0)
        self._progress.set_hint("Rendering trajectory...")
        run_coroutine(self._visualize_then_hide())
        # Defer user feedback to _on_plan_stored so the planned-waypoint count and
        # the stored version are shown together in a single notification.
        self._planned_waypoint_count = len(joint_trajectory.joint_positions)

    async def _visualize_then_hide(self) -> None:
        ok = False
        try:
            ok = await self._planner.visualize_trajectory(
                self._settings.trajectory_color
            )
        finally:
            self._progress.hide()
        # Enable Execute only once the trajectory was successfully visualized.
        self._trajectory_visualized = ok
        self._update_controls()

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
        new_idx = int(location + self._execution_location_offset)
        old_idx = self._pose_delegate.executing_index
        if new_idx == old_idx:
            return
        self._pose_delegate.executing_index = new_idx
        # Repaint only the rows whose highlight state changed. notify_item_changed
        # with a concrete item reliably re-invokes build_widget for that row;
        # passing None signals a structural change and may not repaint built rows.
        items = self._pose_model.items
        for idx in {old_idx, new_idx}:
            if idx is not None and 0 <= idx < len(items):
                self._pose_model.notify_item_changed(items[idx])

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
        self._execution_location_offset = 0.0
        last_idx = self._pose_delegate.executing_index
        self._pose_delegate.executing_index = None
        items = self._pose_model.items
        if last_idx is not None and 0 <= last_idx < len(items):
            # Repaint the formerly highlighted row to clear it.
            self._pose_model.notify_item_changed(items[last_idx])
        else:
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
        prev = self._selected_pose_item
        if not selection:
            self._selected_pose_item = None
            self._update_go_to_visibility(prev, None)
            self._on_selection_changed(None)
            self._preview.hide()
            return

        item = selection[0]
        if isinstance(item, PoseDetailItem):
            item = item.parent

        self._selected_pose_item = item
        self._update_go_to_visibility(prev, item)
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

    def _update_go_to_visibility(
        self, prev: PoseItem | None, current: PoseItem | None
    ) -> None:
        """Keep the per-row "Go to" button only on the selected row."""
        if prev is current:
            return
        self._pose_delegate.selected_item = current

        # Refresh the previously and newly selected rows so the button moves.
        # Deferred so the rebuild happens after the TreeView has applied its own
        # selection highlight (rebuilding mid-selection-event drops the highlight).
        def _refresh(ws=weakref.ref(self), prev=prev, current=current) -> None:
            self = ws()
            if self is None:
                return
            for item in {prev, current}:
                if item is not None and item in self._pose_model.items:
                    self._pose_model.notify_item_changed(item)

        defer_call(_refresh)

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
            # Execute only once the trajectory has actually been visualized.
            has_trajectory=self._planner.trajectory_planned
            and self._planner.planned_joint_trajectory is not None
            and self._trajectory_visualized,
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
