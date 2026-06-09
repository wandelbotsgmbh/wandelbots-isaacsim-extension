"""Async trajectory planning orchestration."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Callable

import carb

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
from wandelbots.omni.utils.api import ApiConfiguration, get_api_client_from_config
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
    get_trajectory_planner_store,
)
from wandelbots.omni.ui.tool.planner_utils import (
    PlanSuccess,
    TrajectorySegmentSpec,
    plan_trajectory_segments,
    plan_collision_free,
)
from wandelbots.omni.visualization import get_trajectory_builder
from wandelbots.omni.visualization.models import (
    PatchTrajectoryData,
    TrajectoryData,
    TrajectoryOptions,
)


def _resolve_blending(
    pose_bl: dict | None,
    settings: dict,
) -> wb_v2_models.MotionCommandBlending | None:
    if pose_bl is not None:
        return wb_v2_models.MotionCommandBlending.from_dict(pose_bl)
    global_bl = settings.get("global_blending")
    if global_bl is not None:
        return wb_v2_models.MotionCommandBlending.from_dict(global_bl)
    if settings.get("auto_blending", False):
        return wb_v2_models.MotionCommandBlending(
            wb_v2_models.BlendingAuto(
                min_velocity_in_percent=settings.get(
                    "blending_min_velocity_percent", 50
                )
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
                    wb_v2_models.PathJointPTP(target_joint_position=joint_pos)
                )
            else:
                carb.log_warn(
                    f"  Motion command {i}: PathJointPTP requested but no joint "
                    f"config available, falling back to PathCartesianPTP"
                )
                path = wb_v2_models.MotionCommandPath(
                    wb_v2_models.PathCartesianPTP(target_pose=nova_pose)
                )
                mt = "PathCartesianPTP (fallback)"
        elif mt == "PathLine":
            path = wb_v2_models.MotionCommandPath(
                wb_v2_models.PathLine(target_pose=nova_pose)
            )
        else:
            path = wb_v2_models.MotionCommandPath(
                wb_v2_models.PathCartesianPTP(target_pose=nova_pose)
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


def _decimate_for_preview(
    joint_positions: list[list[float]], max_points: int = _MAX_PREVIEW_POINTS
) -> list[list[float]]:
    """Uniformly sample joint positions for trajectory visualization.

    Keeps the first and last waypoint. Used only for the preview curve so FK and
    USD geometry stay cheap for long trajectories; execution uses the full set.
    """
    n = len(joint_positions)
    if n <= max_points:
        return joint_positions
    step = (n - 1) / (max_points - 1)
    idxs = sorted({int(round(i * step)) for i in range(max_points)} | {0, n - 1})
    return [joint_positions[i] for i in idxs]


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

        self._plan_task: asyncio.Task | None = None
        self._trajectory_planned: bool = False
        self._planned_joint_trajectory: wb_v2_models.JointTrajectory | None = None
        self._planned_tcp: str | None = None
        self._trajectory_name: str | None = None
        self._segment_trajectory_names: list[str] = []
        self._total_plan_segments: int = 0
        self._skill_name: str = ""

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
        self._remove_trajectory_visualization()
        self._remove_segment_trajectories()

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

    def invalidate(self) -> None:
        carb.log_info(f"invalidate() called, was_planned={self._trajectory_planned}")
        if self._trajectory_planned:
            self._trajectory_planned = False
            self._planned_joint_trajectory = None
        self._remove_trajectory_visualization()
        self._remove_segment_trajectories()
        for item in self._pose_model.items:
            item.reachable = None
            item.planned = None

    def set_planned(self, planned: bool) -> None:
        self._trajectory_planned = planned

    def restore_trajectory(
        self, joint_trajectory: wb_v2_models.JointTrajectory
    ) -> None:
        self._planned_joint_trajectory = joint_trajectory
        self._trajectory_planned = True

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

        for item in poses:
            item.planned = True

        self._remove_segment_trajectories()
        self._remove_trajectory_visualization()
        self._total_plan_segments = len(ws_poses)

        self._events.plan_progress.emit(
            0.0, f"Planning {self._total_plan_segments} segments..."
        )

        settings = self._get_settings()

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
            f"cf_algorithm={settings.get('cf_algorithm')}, "
            f"cf_max_iterations={settings.get('cf_max_iterations')}"
        )
        carb.log_verbose(
            f"Per-pose blending={pose_blending}, limits_override={pose_limits_override}"
        )

        try:
            if collision_setup:
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
                    cf_algorithm=settings.get("cf_algorithm", "RRTConnectAlgorithm"),
                    cf_max_iterations=settings.get("cf_max_iterations", 10000),
                    global_limits_override=settings.get("global_limits_override"),
                    status_fn=self._on_status,
                    segment_planned_fn=self._on_segment,
                )
            else:
                # Split target poses into contiguous same-TCP runs; plan each with
                # its own TCP and merge. This time-scales every segment against its
                # actual tool (no single-TCP reprojection).
                runs = _group_indices_by_tcp(target_poses, planning_tcp)
                seg_specs: list[TrajectorySegmentSpec] = []
                for run_pos, run in enumerate(runs):
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
                        if run_pos < len(runs) - 1
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
                    status_fn=self._on_status,
                    segment_planned_fn=self._on_segment_planned,
                )

            if isinstance(result, PlanSuccess):
                joint_positions = result.joint_trajectory.joint_positions
                self._planned_joint_trajectory = result.joint_trajectory
                self._trajectory_planned = True
                carb.log_info(f"Trajectory planned: {len(joint_positions)} waypoints")
                self._remove_segment_trajectories()

                self._events.plan_complete.emit(result.joint_trajectory)

                run_coroutine(self._store_to_nova())
            else:
                carb.log_warn(f"Planning failed: {result.error}")
                nm.post_notification(
                    "Planning failed. See log for details.",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                self._trajectory_planned = False
                self._events.plan_failed.emit(result.error)
        except asyncio.CancelledError:
            carb.log_info("Trajectory planning cancelled by user.")
            self._remove_segment_trajectories()
            self._trajectory_planned = False
            self._events.plan_failed.emit("Cancelled")
        except Exception as exc:
            carb.log_warn(f"Plan trajectory failed: {exc}")
            nm.post_notification(
                "Plan trajectory failed. See log for details.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            self._trajectory_planned = False
            self._events.plan_failed.emit(str(exc))
        finally:
            self._plan_task = None

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
            key = f"trajectory-plan/{self._skill_name}"

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

    def visualize_segment(
        self,
        segment_idx: int,
        joint_positions: list[list[float]],
        tcp_name: str | None = None,
    ) -> None:
        run_coroutine(
            self._do_visualize_segment(segment_idx, joint_positions, tcp_name)
        )

    async def _do_visualize_segment(
        self,
        segment_idx: int,
        joint_positions: list[list[float]],
        tcp_name: str | None = None,
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
            name = f"{safe_name}_segment_{segment_idx}"
            trajectory_builder = get_trajectory_builder()
            trajectory_builder.create_trajectory(
                TrajectoryData(
                    name=name,
                    parent_prim_path=mg_prim_path,
                    poses=tcp_poses,
                    options=TrajectoryOptions(color=(128, 128, 128), width=4.0),
                )
            )
            self._segment_trajectory_names.append(name)
        except Exception as exc:
            carb.log_warn(f"Failed to visualize segment {segment_idx}: {exc}")

    async def visualize_trajectory(self, trajectory_color: list[float]) -> None:
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
            return
        cell, controller, motion_group = params
        try:
            service = get_trajectory_planner_service()
            tcp_name = self._planned_tcp or self._get_selected_tcp()
            # Decimate the preview only (execution uses the full-resolution
            # trajectory). FK + USD curve build for many thousands of points blocks
            # the main thread; a sampled curve is visually identical.
            preview_joints = _decimate_for_preview(
                self._planned_joint_trajectory.joint_positions
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
            trajectory_builder.create_trajectory(
                TrajectoryData(
                    name=self._trajectory_name,
                    parent_prim_path=mg_prim_path,
                    poses=tcp_poses,
                    options=TrajectoryOptions(
                        color=tuple(int(c * 255) for c in trajectory_color),
                        width=10.0,
                    ),
                )
            )
        except Exception as exc:
            import traceback

            carb.log_warn(
                f"Trajectory visualization failed: {exc}\n{traceback.format_exc()}"
            )

    def update_trajectory_color(self, trajectory_color: list[float]) -> None:
        """Update the color of the existing trajectory visualization without re-computing FK."""
        if not self._trajectory_name:
            return
        try:
            trajectory_builder = get_trajectory_builder()
            color_rgb = tuple(int(c * 255) for c in trajectory_color)
            trajectory_builder.update_trajectory(
                self._trajectory_name,
                PatchTrajectoryData(
                    options=TrajectoryOptions(color=color_rgb),
                ),
            )
        except Exception as exc:
            carb.log_warn(f"Failed to update trajectory color: {exc}")

    def _remove_segment_trajectories(self) -> None:
        if not self._segment_trajectory_names:
            return
        trajectory_builder = get_trajectory_builder()
        for name in self._segment_trajectory_names:
            try:
                trajectory_builder.remove_trajectory(name)
            except Exception:
                pass
        self._segment_trajectory_names.clear()

    def _remove_trajectory_visualization(self) -> None:
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
