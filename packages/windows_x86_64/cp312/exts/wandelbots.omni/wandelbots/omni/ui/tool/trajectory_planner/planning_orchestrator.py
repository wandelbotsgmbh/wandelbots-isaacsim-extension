"""Async trajectory planning orchestration."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import replace
from typing import TYPE_CHECKING, Callable

import carb
import numpy as np

if TYPE_CHECKING:
    from wandelbots.omni.ui.tool.trajectory_planner.events import (
        TrajectoryPlannerEvents,
    )
import omni.kit.app
import omni.kit.notification_manager as nm
import omni.usd
from omni.kit.async_engine import run_coroutine
from pxr import Tf

import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.ui.tool.trajectory_planner.pose_tree_widget import (
    PoseModel,
)
from wandelbots.omni.ui.tool.trajectory_planner.service import (
    get_trajectory_planner_service,
)
from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.manipulators.utils import get_link_0_from_motion_group_prim
from wandelbots.omni.utils.api import ApiConfiguration, get_api_client_from_config
from wandelbots.omni.utils.math import euler_to_rotvec, rotvec_to_matrix
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
    get_trajectory_planner_store,
    migrate_blending_dict,
)
from wandelbots.omni.ui.tool.planner_utils import (
    PlanFailure,
    PlanSuccess,
    TrajectorySegmentSpec,
    plan_trajectory_segments,
    plan_collision_free,
)
from wandelbots.omni.visualization import get_trajectory_builder
from wandelbots.omni.visualization.models import (
    PatchTrajectoryData,
    SpherePrim,
    TrajectoryData,
    TrajectoryMarker,
    TrajectoryOptions,
)

# Amber spheres on the curve where the collision-free planner inserted a via
# point; distinct from the user's pose gizmos and the trajectory colors.
_VIA_POINT_MARKER = SpherePrim(type="sphere", radius=15.0, color=(255, 191, 0))

# Nova StoreObject key prefixes. The ExportedSkill (NOVA request format) is stored
# under TRAJECTORY_PLAN_PREFIX; the lossless TrajectoryPlannerConfig used for
# "Load by name" is stored under TRAJECTORY_PLAN_CONFIG_PREFIX.
TRAJECTORY_PLAN_PREFIX = "trajectory-plan/"
TRAJECTORY_PLAN_CONFIG_PREFIX = "trajectory-plan-config/"


def _resolve_blending(
    pose_bl: dict | None,
    settings: dict,
) -> wb_v2_models.MotionCommandBlending | None:
    if pose_bl is not None:
        return wb_v2_models.MotionCommandBlending.from_dict(
            migrate_blending_dict(pose_bl)
        )
    global_bl = settings.get("global_blending")
    if global_bl is not None:
        return wb_v2_models.MotionCommandBlending.from_dict(
            migrate_blending_dict(global_bl)
        )
    if settings.get("auto_blending", False):
        return wb_v2_models.MotionCommandBlending(
            wb_v2_models.BlendingAuto(
                min_velocity_in_percent=settings.get(
                    "blending_min_velocity_percent", 50
                ),
                blending_name="BlendingAuto",
            )
        )
    return None


def _resolve_limits_override(
    pose_lo: dict | None,
    settings: dict,
) -> wb_v2_models.LimitsOverride | None:
    if pose_lo is not None:
        return wb_v2_models.LimitsOverride.from_dict(pose_lo)
    global_lo = settings.get("global_limits_override")
    if global_lo is not None:
        return wb_v2_models.LimitsOverride.from_dict(global_lo)
    return None


def _build_motion_commands(
    poses: list[WSPose],
    motion_types: list[str],
    selected_joint_positions: list[list[float] | None] | None,
    pose_blending: list[dict | None],
    pose_limits_override: list[dict | None],
    settings: dict,
) -> list[wb_v2_models.MotionCommand]:
    commands: list[wb_v2_models.MotionCommand] = []
    for i, pose in enumerate(poses):
        nova_pose = pose.to_nova_pose()
        mt = motion_types[i] if i < len(motion_types) else "PathCartesianPTP"

        if mt == "PathJointPTP":
            joint_pos = (
                selected_joint_positions[i]
                if selected_joint_positions and i < len(selected_joint_positions)
                else None
            )
            if joint_pos:
                path = wb_v2_models.MotionCommandPath(
                    wb_v2_models.PathJointPTP(
                        target_joint_position=joint_pos,
                        path_definition_name="PathJointPTP",
                    )
                )
            else:
                carb.log_warn(
                    f"  Motion command {i}: PathJointPTP requested but no joint "
                    f"config available, falling back to PathCartesianPTP"
                )
                path = wb_v2_models.MotionCommandPath(
                    wb_v2_models.PathCartesianPTP(
                        target_pose=nova_pose,
                        path_definition_name="PathCartesianPTP",
                    )
                )
                mt = "PathCartesianPTP (fallback)"
        elif mt == "PathLine":
            path = wb_v2_models.MotionCommandPath(
                wb_v2_models.PathLine(
                    target_pose=nova_pose, path_definition_name="PathLine"
                )
            )
        else:
            path = wb_v2_models.MotionCommandPath(
                wb_v2_models.PathCartesianPTP(
                    target_pose=nova_pose, path_definition_name="PathCartesianPTP"
                )
            )

        bl = pose_blending[i] if i < len(pose_blending) else None
        lo = pose_limits_override[i] if i < len(pose_limits_override) else None
        blending = _resolve_blending(bl, settings)
        limits_override = _resolve_limits_override(lo, settings)

        commands.append(
            wb_v2_models.MotionCommand(
                path=path,
                blending=blending,
                limits_override=limits_override,
            )
        )
        carb.log_info(
            f"  Motion command {i}: {mt} → "
            f"pos={list(nova_pose.position)}, orient={list(nova_pose.orientation)}"
        )

    return commands


_MAX_PREVIEW_POINTS = 2000
_SEGMENT_TRAJECTORY_COLOR = (128, 128, 128)
_FAILED_TRAJECTORY_COLOR = (255, 0, 0)


def _decimate_indices(n: int, max_points: int = _MAX_PREVIEW_POINTS) -> list[int]:
    """Indices of a uniform sample of ``n`` items, always keeping first and last.

    Returned so callers can sample parallel arrays (e.g. joint positions and the
    matching ``times``) with the exact same indices.
    """
    if n <= 0:
        return []
    if n <= max_points:
        return list(range(n))
    step = (n - 1) / (max_points - 1)
    return sorted({int(round(i * step)) for i in range(max_points)} | {0, n - 1})


def _decimate_for_preview(
    joint_positions: list[list[float]], max_points: int = _MAX_PREVIEW_POINTS
) -> list[list[float]]:
    """Uniformly sample joint positions for trajectory visualization.

    Keeps the first and last waypoint. Used only for the preview curve so FK and
    USD geometry stay cheap for long trajectories; execution uses the full set.
    """
    idxs = _decimate_indices(len(joint_positions), max_points)
    return [joint_positions[i] for i in idxs]


def _segment_speeds(poses: list[list[float]], times: list[float]) -> list[float]:
    """TCP Cartesian speed (mm/s) for each segment between consecutive poses.

    ``poses`` are 6D Cartesian poses ([x, y, z, ...] in mm) aligned 1:1 with
    ``times`` (seconds). Returns ``len(poses) - 1`` speeds; a non-positive time
    delta yields 0 for that segment.
    """
    speeds: list[float] = []
    for i in range(len(poses) - 1):
        a, b = poses[i], poses[i + 1]
        dist = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5
        dt = times[i + 1] - times[i]
        speeds.append(dist / dt if dt > 0 else 0.0)
    return speeds


def _speeds_to_colors(
    speeds: list[float], reference_speed: float | None = None
) -> list[tuple[int, int, int]]:
    """Map per-segment speeds to a red(slow)->green(fast) RGB gradient.

    Normalizes by ``reference_speed`` when given and positive (so colors are
    comparable across plans, e.g. the TCP velocity limit), otherwise by the max
    observed speed. Returns one (r, g, b) tuple per speed.
    """
    if not speeds:
        return []
    denom = (
        reference_speed if (reference_speed and reference_speed > 0) else max(speeds)
    )
    colors: list[tuple[int, int, int]] = []
    for s in speeds:
        ratio = 0.0 if denom <= 0 else max(0.0, min(1.0, s / denom))
        colors.append((int(round(255 * (1 - ratio))), int(round(255 * ratio)), 0))
    return colors


def _group_indices_by_tcp(items, default_tcp: str | None) -> list[list[int]]:
    """Group target-pose indices into contiguous runs sharing one effective TCP.

    Effective TCP = ``item.tcp_name or default_tcp``. Each run is planned with its
    own TCP and the per-run trajectories are merged.
    """
    runs: list[list[int]] = []
    for i, item in enumerate(items):
        tcp = item.tcp_name or default_tcp
        if runs and (items[runs[-1][-1]].tcp_name or default_tcp) == tcp:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def failed_pose_index(
    segment_runs: list[list[int]], failure: PlanFailure
) -> int | None:
    """Index into the full pose list (start pose = 0) of the pose whose motion failed.

    ``segment_runs`` holds, per planned segment, the target-pose indices it covers.
    The error location counts motion commands within the failed segment: 0 is the
    segment start, integer k is the arrival at command k, k.x lies on command k+1.
    """
    if failure.segment_index is None:
        return None
    if not 0 <= failure.segment_index < len(segment_runs):
        return None
    run = segment_runs[failure.segment_index]
    if not run:
        return None
    location = failure.error_location_on_trajectory
    if location is None:
        return run[-1] + 1
    command_index = max(0, math.ceil(location) - 1)
    return run[min(command_index, len(run) - 1)] + 1


def mark_planning_failure(items, failed_index: int | None) -> None:
    """Set the per-pose ``planned`` flags after a failed plan.

    Poses before the failure planned fine, the failing pose is marked False (red
    row) and later poses were never attempted. Without a known index nothing
    counts as planned.
    """
    for index, item in enumerate(items):
        if failed_index is None or index > failed_index:
            item.planned = None
        else:
            item.planned = index < failed_index


def _failure_notification(items, failed_index: int | None) -> str:
    if failed_index is None or not 0 <= failed_index < len(items):
        return "Planning failed. See log for details."
    name = items[failed_index].name_model.get_value_as_string()
    return f"Planning failed at '{name}'. See log for details."


def _position_blend_from_dict(
    pose_bl: dict | None, settings: dict
) -> wb_v2_models.BlendingPosition | None:
    """Resolve a pose's effective blend and return it only if it is a position blend.

    The mergeTrajectories segment blending accepts only a ``BlendingPosition``; an
    auto blend (or none) maps to a hard transition (None) at the TCP boundary.
    """
    blend = _resolve_blending(pose_bl, settings)
    inner = getattr(blend, "actual_instance", None) if blend else None
    if isinstance(inner, wb_v2_models.BlendingPosition):
        return inner
    return None


class PlanningOrchestrator:
    """Manages trajectory planning lifecycle: validate, plan, visualize."""

    def __init__(
        self,
        pose_model: PoseModel,
        get_api_config: Callable[[], ApiConfiguration | None],
        get_stream_params: Callable[[], tuple[str, str, str] | None],
        get_mg_prim_path: Callable[[], str | None],
        get_selected_tcp: Callable[[], str | None],
        get_collision_setup: Callable[[], str | None],
        get_settings: Callable[[], dict],
        events: "TrajectoryPlannerEvents",
        get_tcp_for_item: Callable | None = None,
        get_mounting_offset: Callable[[], tuple[float, float, float] | None]
        | None = None,
        get_mounting_rotation: Callable[[], tuple[float, float, float] | None]
        | None = None,
        get_reference_frame_path: Callable[[], str | None] | None = None,
    ) -> None:
        self._pose_model = pose_model
        self._get_api_config = get_api_config
        self._get_stream_params = get_stream_params
        self._get_mg_prim_path = get_mg_prim_path
        self._get_selected_tcp = get_selected_tcp
        self._get_collision_setup = get_collision_setup
        self._get_settings = get_settings
        self._events = events
        self._get_tcp_for_item = get_tcp_for_item
        self._get_mounting_offset = get_mounting_offset
        self._get_mounting_rotation = get_mounting_rotation
        self._get_reference_frame_path = get_reference_frame_path

        self._plan_task: asyncio.Task | None = None
        self._visualize_task: asyncio.Task | None = None
        self._trajectory_planned: bool = False
        self._planned_joint_trajectory: wb_v2_models.JointTrajectory | None = None
        self._planned_via_joint_positions: list[list[float]] | None = None
        self._planned_tcp: str | None = None
        self._trajectory_name: str | None = None
        self._segment_trajectory_names: list[str] = []
        # Segment / failed-curve renders still waiting on forward kinematics. They
        # are cancelled together with the curve cleanup so a slow render cannot
        # author a stale curve after a newer plan has already cleared them.
        self._segment_visualize_tasks: set[asyncio.Future] = set()
        self._total_plan_segments: int = 0
        self._skill_name: str = ""
        # The drawn curve is kept on edit-driven invalidation and only marked
        # stale; it is removed/overwritten just-in-time when the same skill is
        # re-planned. See invalidate() / _do_plan().
        self._trajectory_stale: bool = False
        # Last per-segment color list applied to the curve (velocity gradient),
        # re-applied by update_trajectory_color so a solid-color patch doesn't
        # clobber the gradient.
        self._last_color_list: list | None = None

    @property
    def trajectory_planned(self) -> bool:
        return self._trajectory_planned

    def _get_planning_tcp(self) -> str | None:
        """Return the globally selected TCP for planning."""
        return self._get_selected_tcp()

    @property
    def planned_joint_trajectory(self) -> wb_v2_models.JointTrajectory | None:
        return self._planned_joint_trajectory

    @property
    def planned_via_joint_positions(self) -> list[list[float]] | None:
        """Via points the collision-free planner inserted into the last plan."""
        return self._planned_via_joint_positions

    @property
    def planned_tcp(self) -> str | None:
        """TCP name used for the last successful plan."""
        return self._planned_tcp

    @property
    def trajectory_name(self) -> str | None:
        return self._trajectory_name

    def set_skill_name(self, name: str) -> None:
        self._skill_name = name

    def destroy(self) -> None:
        if self._plan_task is not None:
            self._plan_task.cancel()
            self._plan_task = None
        self._cancel_visualize_task()
        self._remove_trajectory_visualization()
        self._remove_segment_trajectories()

    def _cancel_visualize_task(self) -> None:
        """Stop any in-flight visualize_trajectory() build.

        Called whenever something else is about to authoritatively clear or
        replace the visualization (a fresh plan, an explicit invalidate, or
        teardown) so a stale build can't keep authoring USD after the caller
        has moved on to newer data.
        """
        if self._visualize_task is not None:
            self._visualize_task.cancel()
            self._visualize_task = None

    def plan(self) -> None:
        if self._plan_task is not None and not self._plan_task.done():
            carb.log_info(
                "plan() called while already planning - cancelling current task."
            )
            self._plan_task.cancel()
            return

        params = self._get_stream_params()
        if not params:
            nm.post_notification(
                "Select a motion group before planning.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        poses = self._pose_model.items
        if len(poses) < 2:
            nm.post_notification(
                "At least 2 poses are required to plan a trajectory.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        carb.log_info(
            f"plan() starting: {len(poses)} poses, params={params}, "
            f"tcp={self._get_selected_tcp()}, collision={self._get_collision_setup()}"
        )
        self._plan_task = run_coroutine(self._do_plan())
        self._events.plan_started.emit()

    def invalidate(self, remove_visualization: bool = False) -> None:
        carb.log_info(
            f"invalidate() called, was_planned={self._trajectory_planned}, "
            f"remove_visualization={remove_visualization}"
        )
        # By default keep the drawn curve and the planned trajectory in memory;
        # just mark them stale so the user still sees the last result until they
        # re-plan. Only an explicit request (or a re-plan's just-in-time removal
        # in _do_plan) removes the curve.
        if remove_visualization:
            if self._trajectory_planned:
                self._trajectory_planned = False
                self._planned_joint_trajectory = None
                self._planned_via_joint_positions = None
            self._trajectory_stale = False
            self._cancel_visualize_task()
            self._remove_trajectory_visualization()
        elif self._trajectory_planned:
            self._trajectory_stale = True
        # Transient per-segment previews are always cleared; they are only
        # meaningful during an in-progress plan.
        self._remove_segment_trajectories()
        for item in self._pose_model.items:
            item.reachable = None
            item.planned = None
        self._events.plan_invalidated.emit()

    def set_planned(self, planned: bool) -> None:
        self._trajectory_planned = planned

    def restore_trajectory(
        self,
        joint_trajectory: wb_v2_models.JointTrajectory,
        via_joint_positions: list[list[float]] | None = None,
    ) -> None:
        self._planned_joint_trajectory = joint_trajectory
        self._planned_via_joint_positions = via_joint_positions or None
        self._trajectory_planned = True
        self._trajectory_stale = False

    @property
    def trajectory_stale(self) -> bool:
        return self._trajectory_stale

    async def _do_plan(self) -> None:
        api_config = self._get_api_config()
        params = self._get_stream_params()
        if not api_config or not params:
            nm.post_notification(
                "Select a motion group before planning.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        cell, controller, motion_group = params
        poses = self._pose_model.items
        planning_tcp = self._get_planning_tcp()

        # For visualization, use the per-pose TCP override if all poses agree
        tcp_overrides = {item.tcp_name for item in poses if item.tcp_name}
        if len(tcp_overrides) == 1:
            self._planned_tcp = next(iter(tcp_overrides))
        else:
            self._planned_tcp = planning_tcp

        self._events.plan_progress.emit(0.0, "Planning trajectory...")
        await omni.kit.app.get_app().next_update_async()

        first_pose = poses[0]
        if not first_pose.selected_joint_config:
            # Use the per-pose TCP for IK (ghost objects need their actual TCP)
            start_tcp = (
                self._get_tcp_for_item(first_pose)
                if self._get_tcp_for_item
                else planning_tcp
            )
            service = get_trajectory_planner_service()
            try:
                ik_result = await service.fetch_ik(
                    api_configuration=api_config,
                    cell=cell,
                    controller=controller,
                    motion_group=motion_group,
                    pose=first_pose.pose,
                    tcp_name=start_tcp,
                    collision_setup_name=self._get_collision_setup(),
                )
                if not ik_result.joint_configs:
                    nm.post_notification(
                        "No IK solution for start pose. Cannot plan.",
                        duration=5.0,
                        status=nm.NotificationStatus.WARNING,
                    )
                    return
                first_pose.joint_configs = ik_result.joint_configs
                first_pose.selected_config_idx = 0
            except Exception as exc:
                carb.log_warn(f"IK for start pose failed: {exc}")
                nm.post_notification(
                    "IK for start pose failed. See log for details.",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return

        start_joint_position = first_pose.selected_joint_config
        target_poses = poses[1:]

        # A pose edited just before Plan/Replan may not yet have had its IK
        # refetched by the debounced watcher (see TrajectoryPlannerController.
        # _refresh_pose / _recalculate_all_poses): fetch live for any target
        # pose still missing joint configs, so collision-free planning and
        # PathJointPTP never silently run without a valid seed for it. The
        # unreachable-poses check further below aborts cleanly if a pose
        # truly has no IK solution.
        poses_missing_ik = [item for item in target_poses if not item.joint_configs]
        if poses_missing_ik:
            service = get_trajectory_planner_service()
            for item in poses_missing_ik:
                tcp = (
                    self._get_tcp_for_item(item)
                    if self._get_tcp_for_item
                    else planning_tcp
                )
                try:
                    ik_result = await service.fetch_ik(
                        api_configuration=api_config,
                        cell=cell,
                        controller=controller,
                        motion_group=motion_group,
                        pose=item.pose,
                        tcp_name=tcp,
                        collision_setup_name=self._get_collision_setup(),
                    )
                    item.joint_configs = ik_result.joint_configs
                    item.selected_config_idx = 0
                    item.reachable = bool(ik_result.joint_configs)
                except Exception as exc:
                    carb.log_warn(
                        f"IK for '{item.name_model.get_value_as_string()}' "
                        f"failed during plan: {exc}"
                    )
                    item.reachable = False

        # Each pose is interpreted in its own TCP frame. Mixed TCPs are planned as
        # per-TCP segments and merged (see the non-collision branch below), so no
        # reprojection into a single planning TCP is needed.
        ws_poses = [item.pose for item in target_poses]
        motion_types = [item.motion_type for item in target_poses]
        collision_setup = self._get_collision_setup()

        target_joint_positions: list[list[list[float]]] | None = None
        if collision_setup:
            target_joint_positions = []
            for item in target_poses:
                if item.joint_configs:
                    # Put the user-selected config first so the CF planner
                    # tries it before falling back to other IK solutions.
                    selected = item.selected_joint_config
                    if selected and selected in item.joint_configs:
                        others = [c for c in item.joint_configs if c != selected]
                        ordered = [selected] + others
                    else:
                        ordered = list(item.joint_configs)
                    target_joint_positions.append(ordered)
                else:
                    target_joint_positions = None
                    break

        # For PathJointPTP motion commands, pass the selected joint config per pose.
        selected_joint_positions: list[list[float] | None] | None = None
        if "PathJointPTP" in motion_types:
            selected_joint_positions = [
                item.selected_joint_config for item in target_poses
            ]

        unreachable_items = [item for item in target_poses if item.reachable is False]
        if unreachable_items:
            for item in poses:
                item.planned = False
            self._pose_model.notify_item_changed(None)
            names = ", ".join(
                item.name_model.get_value_as_string() for item in unreachable_items
            )
            nm.post_notification(
                f"Cannot plan: unreachable poses ({names}). Fix them first.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            self._trajectory_planned = False
            return

        settings = self._get_settings()
        plan_cf = bool(settings.get("plan_collision_free", False))
        # Validate before touching the existing trajectory so a misconfiguration
        # (collision-free requested without a scene) never wipes the last plan.
        if plan_cf and not collision_setup:
            nm.post_notification(
                "Collision-free planning needs an active collision scene. "
                "Select a collision scene or turn off collision-free planning.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            self._events.plan_failed.emit(
                PlanFailure(error="No collision scene selected")
            )
            return

        for item in poses:
            item.planned = True

        self._remove_segment_trajectories()
        # Re-planning always supersedes the current visualization: cancel any
        # build still in flight from a prior plan before clearing the curve,
        # so a stale task can't keep authoring USD after this newer plan wins.
        self._cancel_visualize_task()
        self._remove_trajectory_visualization()
        self._total_plan_segments = len(ws_poses)

        self._events.plan_progress.emit(
            0.0, f"Planning {self._total_plan_segments} segments..."
        )

        # Per-pose blending/limits from PoseItems
        pose_blending = [item.blending for item in target_poses]
        pose_limits_override = [item.limits_override for item in target_poses]

        carb.log_info(
            f"Planning trajectory: {len(ws_poses)} target poses, "
            f"tcp={planning_tcp!r}, collision={collision_setup}"
        )
        carb.log_verbose(
            f"start_joints=[{', '.join(f'{v:.3f}' for v in start_joint_position)}], "
            f"motion_types={motion_types}"
        )
        carb.log_verbose(
            f"Planning settings: tcp_vel={settings.get('tcp_velocity')}, "
            f"tcp_acc={settings.get('tcp_acceleration')}, "
            f"auto_blending={settings.get('auto_blending')}, "
            f"blending_min_vel%={settings.get('blending_min_velocity_percent')}, "
            f"global_blending={settings.get('global_blending')}, "
            f"global_limits_override={settings.get('global_limits_override')}, "
            f"payload={settings.get('payload_name')}/{settings.get('payload_mass')}, "
            f"cf_max_iterations={settings.get('cf_max_iterations')}, "
            f"cf_step_size={settings.get('cf_step_size')}"
        )
        carb.log_verbose(
            f"Per-pose blending={pose_blending}, limits_override={pose_limits_override}"
        )

        # Target-pose indices per planned segment: collision-free plans one
        # segment per pose, motion-type planning one per contiguous TCP run. A
        # failure reports its segment index, which maps back to a pose through this.
        segment_runs = (
            [[i] for i in range(len(target_poses))]
            if plan_cf
            else _group_indices_by_tcp(target_poses, planning_tcp)
        )

        try:
            if plan_cf:
                carb.log_info(
                    f"Routing to COLLISION-FREE (collision_setup='{collision_setup}'). "
                    f"Motion types will NOT be used."
                )
                result = await plan_collision_free(
                    api_configuration=api_config,
                    cell=cell,
                    controller=controller,
                    motion_group=motion_group,
                    start_joint_position=start_joint_position,
                    target_configs=target_joint_positions or [],
                    tcp_name=planning_tcp,
                    collision_setup_name=collision_setup,
                    tcp_velocity_limit=settings.get("tcp_velocity"),
                    tcp_acceleration_limit=settings.get("tcp_acceleration"),
                    cycle_time=None,
                    payload_name=settings.get("payload_name"),
                    payload_mass=settings.get("payload_mass"),
                    cf_max_iterations=settings.get("cf_max_iterations", 10000),
                    cf_step_size=settings.get("cf_step_size"),
                    global_limits_override=settings.get("global_limits_override"),
                    status_fn=self._on_status,
                    segment_planned_fn=self._on_segment,
                )
            else:
                # Split target poses into contiguous same-TCP runs; plan each with
                # its own TCP and merge. This time-scales every segment against its
                # actual tool (no single-TCP reprojection).
                seg_specs: list[TrajectorySegmentSpec] = []
                for run_pos, run in enumerate(segment_runs):
                    seg_tcp = target_poses[run[0]].tcp_name or planning_tcp
                    seg_cmds = _build_motion_commands(
                        [ws_poses[i] for i in run],
                        [motion_types[i] for i in run],
                        [selected_joint_positions[i] for i in run]
                        if selected_joint_positions is not None
                        else None,
                        [pose_blending[i] for i in run],
                        [pose_limits_override[i] for i in run],
                        settings,
                    )
                    # Inter-segment blend lives on the run's last pose; ignored on
                    # the final run.
                    blending = (
                        _position_blend_from_dict(pose_blending[run[-1]], settings)
                        if run_pos < len(segment_runs) - 1
                        else None
                    )
                    seg_specs.append(
                        TrajectorySegmentSpec(
                            tcp_name=seg_tcp,
                            motion_commands=seg_cmds,
                            blending=blending,
                        )
                    )
                # Representative TCP for the merged-trajectory visualization.
                self._planned_tcp = seg_specs[0].tcp_name
                self._total_plan_segments = len(seg_specs)
                carb.log_info(
                    f"Planning {len(seg_specs)} TCP segment(s): "
                    f"{[s.tcp_name for s in seg_specs]}"
                )
                result = await plan_trajectory_segments(
                    api_configuration=api_config,
                    cell=cell,
                    controller=controller,
                    motion_group=motion_group,
                    segments=seg_specs,
                    start_joint_position=start_joint_position,
                    tcp_velocity_limit=settings.get("tcp_velocity"),
                    tcp_acceleration_limit=settings.get("tcp_acceleration"),
                    payload_name=settings.get("payload_name"),
                    payload_mass=settings.get("payload_mass"),
                    collision_setup_name=collision_setup,
                    status_fn=self._on_status,
                    segment_planned_fn=self._on_segment_planned,
                )

            if isinstance(result, PlanSuccess):
                joint_positions = result.joint_trajectory.joint_positions
                self._planned_joint_trajectory = result.joint_trajectory
                self._planned_via_joint_positions = result.via_joint_positions
                self._trajectory_planned = True
                self._trajectory_stale = False
                carb.log_info(f"Trajectory planned: {len(joint_positions)} waypoints")
                self._remove_segment_trajectories()

                self._events.plan_complete.emit(result.joint_trajectory)

                run_coroutine(self._store_to_nova())
            else:
                self._handle_plan_failure(result, segment_runs, target_poses, plan_cf)
        except asyncio.CancelledError:
            carb.log_info("Trajectory planning cancelled by user.")
            self._remove_segment_trajectories()
            self._trajectory_planned = False
            mark_planning_failure(poses, None)
            self._events.plan_failed.emit(PlanFailure(error="Cancelled"))
        except Exception as exc:
            carb.log_warn(f"Plan trajectory failed: {exc}")
            nm.post_notification(
                "Plan trajectory failed. See log for details.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            self._trajectory_planned = False
            mark_planning_failure(poses, None)
            self._events.plan_failed.emit(PlanFailure(error=str(exc)))
        finally:
            self._plan_task = None

    def _handle_plan_failure(
        self,
        result: PlanFailure,
        segment_runs: list[list[int]],
        target_poses: list,
        plan_cf: bool,
    ) -> None:
        failure = self._with_collision_free_ghost(result, target_poses, plan_cf)
        poses = self._pose_model.items
        failed_index = failed_pose_index(segment_runs, failure)
        mark_planning_failure(poses, failed_index)
        self._pose_model.notify_item_changed(None)
        carb.log_warn(f"Planning failed: {failure.error}")
        nm.post_notification(
            _failure_notification(poses, failed_index),
            duration=5.0,
            status=nm.NotificationStatus.WARNING,
        )
        self._trajectory_planned = False
        self._events.plan_failed.emit(failure)
        if failure.partial_joint_positions:
            self.visualize_failed_trajectory(failure.partial_joint_positions)

    @staticmethod
    def _with_collision_free_ghost(
        failure: PlanFailure, target_poses: list, plan_cf: bool
    ) -> PlanFailure:
        """Collision-free failures carry no joint data; show the unreachable target."""
        if not plan_cf or failure.failed_joint_position is not None:
            return failure
        index = failure.segment_index
        if index is None or not 0 <= index < len(target_poses):
            return failure
        return replace(
            failure, failed_joint_position=target_poses[index].selected_joint_config
        )

    def _on_status(self, msg: str) -> None:
        carb.log_verbose(f"Planning status: {msg}")
        current = 0.0
        if self._total_plan_segments > 0:
            current = 0.5 / self._total_plan_segments
        self._events.plan_progress.emit(current, msg)

    def _on_segment(self, segment_idx: int, joint_positions: list[list[float]]) -> None:
        carb.log_verbose(
            f"Segment {segment_idx + 1}/{self._total_plan_segments} planned: "
            f"{len(joint_positions)} waypoints"
        )
        if self._total_plan_segments > 0:
            progress = (segment_idx + 1) / self._total_plan_segments
            self._events.plan_progress.emit(
                progress,
                f"Planning {segment_idx + 1}/{self._total_plan_segments}",
            )

    def _on_segment_planned(
        self,
        segment_idx: int,
        joint_positions: list[list[float]],
        tcp_name: str | None,
    ) -> None:
        """Progress callback for per-TCP segment planning.

        Progress only — per-segment FK + USD previews are intentionally not drawn
        here: building curve geometry for every segment on the main thread stalls
        the UI during multi-segment planning. The merged trajectory is visualized
        once on completion.
        """
        self._on_segment(segment_idx, joint_positions)

    async def _store_to_nova(self) -> None:
        version: str | None = None
        try:
            api_config = self._get_api_config()
            params = self._get_stream_params()
            if not api_config or not params:
                return
            cell = params[0]

            from wandelbots.omni.router.v2.teaching import build_skill
            from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_window import (
                TrajectoryPlannerWindow,
            )

            configs = TrajectoryPlannerWindow.get_live_configs()
            if configs is None:
                store = get_trajectory_planner_store()
                configs = store.load_configs()
            config = next((c for c in configs if c.name == self._skill_name), None)
            if not config:
                carb.log_warn(
                    f"NOVA store: skill '{self._skill_name}' not found in store."
                )
                return

            stage = omni.usd.get_context().get_stage()
            if not stage:
                carb.log_warn("NOVA store: no USD stage available.")
                return

            skill = await build_skill(config, stage)
            key = f"{TRAJECTORY_PLAN_PREFIX}{self._skill_name}"

            async with get_api_client_from_config(api_config) as api_client:
                store_api = wb_v2.StoreObjectApi(api_client)
                # Version increments on every plan, starting at v1, derived from the
                # previously stored plan so it survives restarts.
                skill.version = await self._next_export_version(store_api, cell, key)
                payload_bytes = json.dumps(skill.model_dump()).encode("utf-8")
                # NOTE: x_metadata is omitted because the API client does not
                # JSON-encode dict header values, causing aiohttp to raise
                # "Cannot serialize non-str key".  The skill name and type
                # are already encoded in the key and the payload envelope.
                await store_api.store_object(
                    cell=cell,
                    key=key,
                    any_value=payload_bytes,
                )
                # Also store the full, lossless skill config so it can be loaded
                # back into the planner by name (the ExportedSkill above is a NOVA
                # request format and cannot reconstruct the config). Keyed under
                # CONFIG_KEY_PREFIX so the Load dialog can list loadable skills.
                config_key = f"{TRAJECTORY_PLAN_CONFIG_PREFIX}{self._skill_name}"
                config_bytes = json.dumps(
                    {"version": skill.version, "config": config.model_dump()}
                ).encode("utf-8")
                await store_api.store_object(
                    cell=cell,
                    key=config_key,
                    any_value=config_bytes,
                )
            version = skill.version
            carb.log_info(
                f"NOVA store: stored skill '{self._skill_name}' as {version} "
                f"under key '{key}'."
            )
        except Exception as exc:
            carb.log_warn(
                f"NOVA store: failed to store skill '{self._skill_name}': {exc}"
            )
        finally:
            # Always emit once so the UI can show a single combined message
            # (version on success, None when storage was skipped or failed).
            self._events.plan_stored.emit(version)

    async def _next_export_version(self, store_api, cell: str, key: str) -> str:
        """Next export version tag ("v1", "v2", ...).

        Reads the version of the previously stored plan at ``key`` and increments
        it, so the version advances on every plan and persists across restarts.
        Starts at ``v1`` when no prior plan exists (or it has no parseable version).
        """
        n = 0
        try:
            raw = await store_api.get_object(cell=cell, key=key)
            data = json.loads(bytes(raw).decode("utf-8"))
            prev = str(data.get("version", "")).lstrip("vV")
            if prev.isdigit():
                n = int(prev)
        except Exception as exc:
            carb.log_verbose(
                f"NOVA store: no previous version for '{key}' ({exc}); starting at v1."
            )
        return f"v{n + 1}"

    def _mounting_offset(self) -> tuple[float, float, float] | None:
        """Visualization-only XYZ translation (mm) from the widget, or None
        when not wired."""
        if self._get_mounting_offset is None:
            return None
        return self._get_mounting_offset()

    def _mounting_rotation(self) -> tuple[float, float, float] | None:
        """Visualization-only XYZ rotation (degrees, extrinsic) from the
        widget, or None when not wired."""
        if self._get_mounting_rotation is None:
            return None
        return self._get_mounting_rotation()

    def _visualization_parent_path(self) -> str | None:
        """Prim the trajectory visualization is anchored to: the user-picked
        reference frame when set and valid, otherwise the motion group prim.
        Visualization only — never changes the planning request."""
        mg_prim_path = self._get_mg_prim_path()
        if self._get_reference_frame_path is None:
            return mg_prim_path
        ref_path = self._get_reference_frame_path()
        if not ref_path:
            return mg_prim_path
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(ref_path) if stage else None
        if prim and prim.IsValid():
            return ref_path
        return mg_prim_path

    def _container_pose(self) -> list[float] | None:
        """Visualization-only local transform for the trajectory container.

        The curve waypoints are in the robot link_0 frame; the container is parented
        at ``_visualization_parent_path()`` (reference frame or motion group prim), so
        it must carry the pose of link_0 *relative to* that parent for the curve to
        land correctly. The mounting offset (translation, mm; rotation, degrees) is
        composed in the link_0 frame — rotation is applied first, then translation.
        Returns ``None`` only when the motion group / stage is unavailable.
        """
        mg_prim_path = self._get_mg_prim_path()
        stage = omni.usd.get_context().get_stage()
        if not mg_prim_path or stage is None:
            return None
        parent_path = self._visualization_parent_path()
        mg_prim = stage.GetPrimAtPath(mg_prim_path)
        link0 = (
            get_link_0_from_motion_group_prim(mg_prim)
            if mg_prim and mg_prim.IsValid()
            else None
        )
        link0_path = link0.GetPath().pathString if link0 else mg_prim_path

        # inv(parent_world) @ link0_world — the parent-relative pose of link_0.
        base = PrimUtils.get_relative_prim_pose(parent_path, link0_path)

        offset = self._mounting_offset()
        rotation = self._mounting_rotation()
        if not offset and not rotation:
            return list(base.pose)
        # Compose the mounting offset in the link_0 frame: rotate first, then
        # translate (mounting_matrix = T @ R).
        matrix = PrimUtils.pose_to_matrix(base.pose)
        mounting_matrix = np.eye(4)
        if rotation:
            rx, ry, rz = euler_to_rotvec(list(rotation), order="xyz", degrees=True)
            mounting_matrix[:3, :3] = rotvec_to_matrix(rx, ry, rz)
        if offset:
            mounting_matrix[0, 3], mounting_matrix[1, 3], mounting_matrix[2, 3] = offset
        return PrimUtils.matrix_to_pose(matrix @ mounting_matrix).tolist()

    def visualize_segment(
        self,
        segment_idx: int,
        joint_positions: list[list[float]],
        tcp_name: str | None = None,
    ) -> None:
        self._start_segment_visualization(
            self._do_visualize_segment(
                f"segment_{segment_idx}", joint_positions, tcp_name
            )
        )

    def visualize_failed_trajectory(self, joint_positions: list[list[float]]) -> None:
        """Draw the start-to-error part of a failed plan as a red curve.

        The curve is tracked like a segment preview, so the next plan or an
        invalidation removes it.
        """
        self._start_segment_visualization(
            self._do_visualize_segment(
                "failed",
                _decimate_for_preview(joint_positions),
                color=_FAILED_TRAJECTORY_COLOR,
            )
        )

    def _start_segment_visualization(self, render) -> None:
        task = run_coroutine(render)
        self._segment_visualize_tasks.add(task)
        task.add_done_callback(self._segment_visualize_tasks.discard)

    def _cancel_segment_visualizations(self) -> None:
        for task in list(self._segment_visualize_tasks):
            task.cancel()
        self._segment_visualize_tasks.clear()

    async def _do_visualize_segment(
        self,
        suffix: str,
        joint_positions: list[list[float]],
        tcp_name: str | None = None,
        color: tuple[int, int, int] = _SEGMENT_TRAJECTORY_COLOR,
    ) -> None:
        api_config = self._get_api_config()
        params = self._get_stream_params()
        mg_prim_path = self._get_mg_prim_path()
        if not api_config or not params or not mg_prim_path:
            return
        cell, controller, motion_group = params
        try:
            service = get_trajectory_planner_service()
            # Use the segment's own TCP when provided so the preview matches the
            # tool actually used for that segment.
            tcp_name = tcp_name or self._planned_tcp or self._get_selected_tcp()
            tcp_poses = await service.forward_kinematics(
                api_configuration=api_config,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                joint_positions=joint_positions,
                tcp_name=tcp_name,
            )
            safe_name = Tf.MakeValidIdentifier(self._skill_name.replace(" ", "_"))
            name = f"{safe_name}_{suffix}"
            # A curve of the same name (e.g. a redrawn failed curve) is replaced,
            # and the name is tracked before authoring so cleanup always finds it.
            if name in self._segment_trajectory_names:
                self._remove_segment_trajectory(name)
            else:
                self._segment_trajectory_names.append(name)
            get_trajectory_builder().create_trajectory(
                TrajectoryData(
                    name=name,
                    parent_prim_path=self._visualization_parent_path(),
                    poses=tcp_poses,
                    options=TrajectoryOptions(color=color, width=4.0),
                    container_pose=self._container_pose(),
                )
            )
        except Exception as exc:
            carb.log_warn(f"Failed to visualize {suffix} curve: {exc}")

    async def visualize_trajectory(self, trajectory_color: list[float]) -> bool:
        """Render the trajectory curve. Returns True when the curve was drawn.

        Re-entrancy guard: auto-visualize after planning, the window's Refresh
        action, and the velocity-coloring toggle can all call this concurrently
        for the same deterministic trajectory name. Cancel any in-flight call,
        wait for it to unwind, then start and await a fresh one — unlike plan()
        (cancel-and-return), none of the callers here retry, so a superseded
        call must not silently drop the visualization.
        """
        if self._visualize_task is not None and not self._visualize_task.done():
            carb.log_info(
                "visualize_trajectory() called while already visualizing - "
                "cancelling current task."
            )
            self._visualize_task.cancel()
            try:
                await self._visualize_task
            except asyncio.CancelledError:
                pass

        task = run_coroutine(self._do_visualize_trajectory(trajectory_color))
        self._visualize_task = task
        try:
            return await task
        except asyncio.CancelledError:
            return False
        finally:
            if self._visualize_task is task:
                self._visualize_task = None

    async def _do_visualize_trajectory(self, trajectory_color: list[float]) -> bool:
        api_config = self._get_api_config()
        params = self._get_stream_params()
        mg_prim_path = self._get_mg_prim_path()
        if (
            not api_config
            or not params
            or not mg_prim_path
            or not self._planned_joint_trajectory
        ):
            carb.log_warn(
                f"visualize_trajectory: skipping — "
                f"api_config={bool(api_config)}, params={bool(params)}, "
                f"mg_prim_path={mg_prim_path}, "
                f"has_trajectory={self._planned_joint_trajectory is not None}"
            )
            return False
        cell, controller, motion_group = params
        try:
            service = get_trajectory_planner_service()
            tcp_name = self._planned_tcp or self._get_selected_tcp()
            # Decimate the preview only (execution uses the full-resolution
            # trajectory). FK + USD curve build for many thousands of points blocks
            # the main thread; a sampled curve is visually identical. The same
            # indices sample ``times`` so the velocity gradient stays aligned.
            jt = self._planned_joint_trajectory
            idxs = _decimate_indices(len(jt.joint_positions))
            preview_joints = [jt.joint_positions[i] for i in idxs]
            preview_times = (
                [jt.times[i] for i in idxs]
                if jt.times and len(jt.times) == len(jt.joint_positions)
                else None
            )
            tcp_poses = await service.forward_kinematics(
                api_configuration=api_config,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                joint_positions=preview_joints,
                tcp_name=tcp_name,
            )
            trajectory_builder = get_trajectory_builder()
            self._remove_trajectory_visualization()
            safe_name = Tf.MakeValidIdentifier(self._skill_name.replace(" ", "_"))
            self._trajectory_name = f"{safe_name}_trajectory"
            carb.log_info(
                f"Creating trajectory '{self._trajectory_name}' at "
                f"{mg_prim_path} with {len(tcp_poses)} poses"
            )
            # Color by TCP speed (green=fast, red=slow) when the velocity-coloring
            # setting is on and timing is available; otherwise use the solid color.
            settings = self._get_settings()
            color: object = tuple(int(c * 255) for c in trajectory_color)
            if (
                settings.get("velocity_coloring", False)
                and preview_times
                and len(tcp_poses) == len(preview_times)
                and len(tcp_poses) >= 2
            ):
                reference = settings.get("tcp_velocity")
                color = _speeds_to_colors(
                    _segment_speeds(tcp_poses, preview_times), reference
                )
            self._last_color_list = color if isinstance(color, list) else None
            # Async build: yields to the Kit update loop between phases so a large
            # curve doesn't freeze the UI after planning.
            await trajectory_builder.create_trajectory_async(
                TrajectoryData(
                    name=self._trajectory_name,
                    parent_prim_path=self._visualization_parent_path(),
                    poses=tcp_poses,
                    options=TrajectoryOptions(
                        color=color,
                        width=10.0,
                    ),
                    container_pose=self._container_pose(),
                )
            )
            await self._draw_via_point_markers(service, api_config, params, tcp_name)
            return True
        except asyncio.CancelledError:
            carb.log_info(
                f"visualize_trajectory('{self._trajectory_name}') cancelled - "
                "superseded by a newer call."
            )
            raise
        except Exception as exc:
            import traceback

            carb.log_warn(
                f"Trajectory visualization failed: {exc}\n{traceback.format_exc()}"
            )
            return False

    async def _draw_via_point_markers(
        self,
        service,
        api_config: ApiConfiguration,
        params: tuple[str, str, str],
        tcp_name: str | None,
    ) -> None:
        """Mark where the collision-free planner inserted via points on the curve.

        Markers are children of the trajectory prim, so they follow the reference
        frame and mounting offset and are removed together with the curve. They are
        decoration only: a failure here must not block Execute, which is gated on
        the curve having been drawn.
        """
        if not self._planned_via_joint_positions or not self._trajectory_name:
            return
        cell, controller, motion_group = params
        try:
            via_poses = await service.forward_kinematics(
                api_configuration=api_config,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                joint_positions=self._planned_via_joint_positions,
                tcp_name=tcp_name,
            )
            get_trajectory_builder().create_marker(
                self._trajectory_name,
                TrajectoryMarker(prim=_VIA_POINT_MARKER, poses=via_poses),
            )
        except Exception as exc:
            carb.log_warn(f"Failed to mark collision-free via points: {exc}")

    def update_trajectory_color(self, trajectory_color: list[float]) -> None:
        """Update the color of the existing trajectory visualization without re-computing FK."""
        if not self._trajectory_name:
            return
        try:
            trajectory_builder = get_trajectory_builder()
            # Preserve the velocity gradient: when the curve was drawn with a
            # per-segment color list, re-apply it rather than collapsing it to a
            # single solid color picked in settings.
            color: object = (
                self._last_color_list
                if self._last_color_list is not None
                else tuple(int(c * 255) for c in trajectory_color)
            )
            trajectory_builder.update_trajectory(
                self._trajectory_name,
                PatchTrajectoryData(
                    options=TrajectoryOptions(color=color),
                ),
            )
        except Exception as exc:
            carb.log_warn(f"Failed to update trajectory color: {exc}")

    def _remove_segment_trajectories(self) -> None:
        # Cancel first: a render still waiting on forward kinematics would
        # otherwise author its curve after this cleanup has already run.
        self._cancel_segment_visualizations()
        for name in self._segment_trajectory_names:
            self._remove_segment_trajectory(name)
        self._segment_trajectory_names.clear()

    @staticmethod
    def _remove_segment_trajectory(name: str) -> None:
        try:
            get_trajectory_builder().remove_trajectory(name)
        except Exception as exc:
            carb.log_verbose(f"Segment curve '{name}' not removed: {exc}")

    def _remove_trajectory_visualization(self) -> None:
        self._last_color_list = None
        if self._trajectory_name:
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                self._trajectory_name = None
                return
            try:
                get_trajectory_builder().remove_trajectory(self._trajectory_name)
            except Exception as e:
                carb.log_warn(f"Failed to remove trajectory visualization: {e}")
            self._trajectory_name = None
