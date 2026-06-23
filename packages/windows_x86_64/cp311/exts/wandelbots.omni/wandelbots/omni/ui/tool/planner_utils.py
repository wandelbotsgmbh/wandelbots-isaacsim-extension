from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

import carb
import wandelbots_api_client.v2 as wb
import wandelbots_api_client.v2.models as wb_models

from wandelbots.omni.datatypes import WSPose, JointPositions
from wandelbots.omni.manipulators import MotionStreamConfiguration
from wandelbots.omni.utils.api import ApiConfiguration, get_api_client_from_config


MotionCommand = (
    wb_models.PathCartesianPTP
    | wb_models.PathCircle
    | wb_models.PathCubicSpline
    | wb_models.PathJointPTP
    | wb_models.PathLine
)


@dataclass(frozen=True)
class PlanSuccess:
    joint_trajectory: wb_models.JointTrajectory


@dataclass(frozen=True)
class PlanFailure:
    error: str


PlanResult = PlanSuccess | PlanFailure


async def get_motion_group_pose(
    motion_stream_config: MotionStreamConfiguration,
    tcp_offset: wb_models.TcpOffset,
) -> tuple[WSPose, JointPositions]:
    async with motion_stream_config.get_api_client() as api:
        motion_group_description = await wb.MotionGroupApi(
            api
        ).get_motion_group_description(
            cell=motion_stream_config.cell,
            controller=motion_stream_config.controller,
            motion_group=motion_stream_config.motion_group,
        )

        motion_group_state = await wb.MotionGroupApi(
            api
        ).get_current_motion_group_state(
            cell=motion_stream_config.cell,
            controller=motion_stream_config.controller,
            motion_group=motion_stream_config.motion_group,
        )

        motion_group_pose = (
            await wb.KinematicsApi(api).forward_kinematics(
                cell=motion_stream_config.cell,
                forward_kinematics_request=wb_models.ForwardKinematicsRequest(
                    motion_group_model=motion_group_description.motion_group_model,
                    joint_positions=[motion_group_state.joint_position],
                    tcp_offset=tcp_offset.pose,
                    mounting=motion_group_description.mounting,
                ),
            )
        ).tcp_poses[0]

        return WSPose(
            pose=[
                motion_group_pose.position[0],
                motion_group_pose.position[1],
                motion_group_pose.position[2],
                motion_group_pose.orientation[0],
                motion_group_pose.orientation[1],
                motion_group_pose.orientation[2],
            ]
        ), motion_group_state.joint_position


async def get_tcp_offset_by_name(
    motion_stream_config: MotionStreamConfiguration, tcp_name: str
) -> wb_models.TcpOffset:
    async with get_api_client_from_config(
        motion_stream_config.get_api_configuration()
    ) as api:
        motion_group_description = await wb.MotionGroupApi(
            api
        ).get_motion_group_description(
            cell=motion_stream_config.cell,
            controller=motion_stream_config.controller,
            motion_group=motion_stream_config.motion_group,
        )

        for tcp_id, tcp_offset in motion_group_description.tcps.items():
            if tcp_id == tcp_name:
                return tcp_offset
    return None


async def pose_to_joint_positions(
    motion_stream_configuration: MotionStreamConfiguration,
    tcp_offset: wb_models.TcpOffset,
    target_pose: WSPose,
) -> list[list[float | int]]:
    async with get_api_client_from_config(
        motion_stream_configuration.get_api_configuration()
    ) as api:
        motion_group_description: wb_models.MotionGroupDescription = (
            await wb.MotionGroupApi(api).get_motion_group_description(
                cell=motion_stream_configuration.cell,
                controller=motion_stream_configuration.controller,
                motion_group=motion_stream_configuration.motion_group,
            )
        )

        motion_group_setup = wb_models.MotionGroupSetup(
            motion_group_model=motion_group_description.motion_group_model,
            tcp_offset=tcp_offset.pose,
            cycle_time=8,
            global_limits=motion_group_description.operation_limits.auto_limits,
        )

        kinematics_api = wb.KinematicsApi(api)
        inverse_kinematic_request = wb_models.InverseKinematicsRequest(
            tcp_poses=[target_pose.to_nova_pose()],
            motion_group_model=motion_group_setup.motion_group_model,
            tcp_offset=motion_group_setup.tcp_offset,
            mounting=motion_group_setup.mounting,
            joint_position_limits=[
                limit.position for limit in motion_group_setup.global_limits.joints
            ],
        )
        return (
            await kinematics_api.inverse_kinematics(
                cell=motion_stream_configuration.cell,
                inverse_kinematics_request=inverse_kinematic_request,
            )
        ).joints


async def get_operation_limits(
    motion_stream_configuration: MotionStreamConfiguration,
) -> wb_models.OperationLimits:
    async with get_api_client_from_config(
        motion_stream_configuration.get_api_configuration()
    ) as api:
        motion_group_description: wb_models.MotionGroupDescription = (
            await wb.MotionGroupApi(api).get_motion_group_description(
                cell=motion_stream_configuration.cell,
                controller=motion_stream_configuration.controller,
                motion_group=motion_stream_configuration.motion_group,
            )
        )
        return motion_group_description.operation_limits


async def plan_motion_group_move_to(
    motion_stream_configuration: MotionStreamConfiguration,
    tcp_offset: wb_models.TcpOffset,
    start_joints: JointPositions,
    global_limits: wb_models.LimitSet,
    motion_commands: list[MotionCommand],
    cycle_time: int = 8,
) -> wb_models.JointTrajectory:
    carb.log_verbose("Planning path...")

    stream_config = motion_stream_configuration
    async with get_api_client_from_config(stream_config.get_api_configuration()) as api:
        motion_group_description: wb_models.MotionGroupDescription = (
            await wb.MotionGroupApi(api).get_motion_group_description(
                cell=stream_config.cell,
                controller=stream_config.controller,
                motion_group=stream_config.motion_group,
            )
        )

        planning_api = wb.TrajectoryPlanningApi(api)
        motion_group_setup = wb_models.MotionGroupSetup(
            motion_group_model=motion_group_description.motion_group_model,
            tcp_offset=tcp_offset.pose,
            cycle_time=cycle_time,
            global_limits=global_limits,
        )
        carb.log_info(f"Planning from {start_joints} to ...")
        for motion_command in motion_commands:
            carb.log_info(f" - {motion_command}")

        planning_response_raw = (
            await planning_api.plan_trajectory_without_preload_content(
                cell=stream_config.cell,
                plan_trajectory_request=wb_models.PlanTrajectoryRequest(
                    start_joint_position=start_joints,
                    motion_commands=motion_commands,
                    motion_group_setup=motion_group_setup,
                ),
            )
        )
        data = await planning_response_raw.json()
        if "error_feedback" in data["response"]:
            carb.log_warn("Planning failed")
            carb.log_warn(data["response"]["error_feedback"])
            raise RuntimeError(data["response"]["error_feedback"])

        carb.log_info("Received planning response")
        if planning_response_raw.status != 200:
            carb.log_warn(f"Planning failed: {planning_response_raw.status} - {data}")
            raise RuntimeError("Planning failed, see log for more info.")
        return wb_models.JointTrajectory.from_dict(data["response"])


async def create_joint_p2p_command_from_pose(
    motion_stream_configuration: MotionStreamConfiguration,
    tcp: str,
    target_pose: WSPose,
) -> MotionCommand:
    tcp_offset = await get_tcp_offset_by_name(motion_stream_configuration, tcp)

    target_joint_positions = await pose_to_joint_positions(
        motion_stream_configuration,
        tcp_offset=tcp_offset,
        target_pose=target_pose,
    )

    if len(target_joint_positions[0]) == 0:
        carb.log_error(f"Could not find joint solution for target pose {target_pose}")
        return None

    return wb_models.MotionCommand(
        path=wb_models.MotionCommandPath(
            wb_models.PathJointPTP(target_joint_position=target_joint_positions[0][0])
        )
    )


_REQUEST_TIMEOUT = 120.0


def _parse_error_from_raw(raw_json: bytes | str) -> str | None:
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return None

    response = data.get("response") if isinstance(data, dict) else None
    if not isinstance(response, dict):
        return None

    if response.get("joint_positions"):
        return None

    error_feedback = response.get("error_feedback")
    if isinstance(error_feedback, dict):
        name = error_feedback.get("error_feedback_name", "UnknownError")
        parts = [name]
        if "invalid_tcp_pose" in error_feedback:
            parts.append(f"pose={error_feedback['invalid_tcp_pose']}")
        if "joint_index" in error_feedback:
            parts.append(f"joint_index={error_feedback['joint_index']}")
        if "joint_position" in error_feedback:
            parts.append(f"joint_position={error_feedback['joint_position']}")
        return " | ".join(parts)

    return response.get("error_feedback_name", "Planning failed (unknown error)")


def _format_error_feedback(result_inner: object) -> str:
    feedback = getattr(result_inner, "error_feedback", None)
    if feedback is None:
        return str(result_inner)

    actual = getattr(feedback, "actual_instance", feedback)
    name = getattr(actual, "error_feedback_name", None) or type(actual).__name__
    parts = [name]

    if hasattr(actual, "invalid_tcp_pose") and actual.invalid_tcp_pose:
        pose = actual.invalid_tcp_pose
        pos = list(pose.position) if pose.position else None
        parts.append(f"pose.position={pos}")
    if hasattr(actual, "joint_index") and actual.joint_index is not None:
        parts.append(f"joint_index={actual.joint_index}")

    return " | ".join(parts)


async def _call_plan_trajectory(
    planning_api: wb.TrajectoryPlanningApi,
    cell: str,
    request: wb_models.PlanTrajectoryRequest,
) -> PlanResult:
    try:
        response = await planning_api.plan_trajectory(
            cell=cell,
            plan_trajectory_request=request,
            _request_timeout=_REQUEST_TIMEOUT,
        )
    except Exception as deser_exc:
        carb.log_info(f"SDK deserialization failed, attempting raw parse: {deser_exc}")
        raw_response = await planning_api.plan_trajectory_without_preload_content(
            cell=cell,
            plan_trajectory_request=request,
            _request_timeout=_REQUEST_TIMEOUT,
        )
        raw_body = await raw_response.read()
        error_msg = _parse_error_from_raw(raw_body)
        if error_msg:
            return PlanFailure(error=error_msg)
        raise deser_exc

    result_inner = response.response.actual_instance
    if isinstance(result_inner, wb_models.JointTrajectory):
        return PlanSuccess(joint_trajectory=result_inner)
    return PlanFailure(error=_format_error_feedback(result_inner))


async def plan_trajectory(
    api_configuration: ApiConfiguration,
    cell: str,
    controller: str,
    motion_group: str,
    motion_commands: list[wb_models.MotionCommand],
    start_joint_position: list[float],
    tcp_name: str | None = None,
    tcp_velocity_limit: float | None = None,
    tcp_acceleration_limit: float | None = None,
    cycle_time: float | None = None,
    payload_name: str | None = None,
    payload_mass: float | None = None,
    status_fn: Callable[[str], None] | None = None,
) -> PlanResult:
    from wandelbots.omni.ui.tool.trajectory_planner.service.helpers import (
        build_motion_group_setup,
        fetch_motion_group_context,
    )

    if status_fn:
        status_fn("Fetching motion group description...")

    async with get_api_client_from_config(api_configuration) as api_client:
        ctx = await fetch_motion_group_context(
            api_client,
            cell=cell,
            controller=controller,
            motion_group=motion_group,
            tcp_name=tcp_name,
        )

        mg_setup = build_motion_group_setup(
            ctx.description,
            ctx.tcp_offset,
            tcp_velocity_limit=tcp_velocity_limit,
            tcp_acceleration_limit=tcp_acceleration_limit,
            cycle_time=cycle_time,
            payload_name=payload_name,
            payload_mass=payload_mass,
        )

        request = wb_models.PlanTrajectoryRequest(
            motion_group_setup=mg_setup,
            start_joint_position=start_joint_position,
            motion_commands=motion_commands,
        )
        carb.log_info(
            f"plan_trajectory: {len(motion_commands)} commands, "
            f"start_joints={start_joint_position}"
        )

        if status_fn:
            status_fn(
                f"Planning trajectory with {len(motion_commands)} motion commands..."
            )

        planning_api = wb.TrajectoryPlanningApi(api_client)
        return await _call_plan_trajectory(planning_api, cell, request)


@dataclass(frozen=True)
class TrajectorySegmentSpec:
    """One contiguous run of motion commands planned with a single TCP.

    A PlanTrajectoryRequest carries the TCP only in motion_group_setup.tcp_offset,
    so a trajectory that switches TCP is planned as several segments and merged.
    """

    tcp_name: str | None
    motion_commands: list[wb_models.MotionCommand]
    # Blend into the NEXT segment (merge-time). None on the last segment.
    blending: wb_models.BlendingPosition | None = None


async def plan_trajectory_segments(
    api_configuration: ApiConfiguration,
    cell: str,
    controller: str,
    motion_group: str,
    segments: list[TrajectorySegmentSpec],
    start_joint_position: list[float],
    tcp_velocity_limit: float | None = None,
    tcp_acceleration_limit: float | None = None,
    cycle_time: float | None = None,
    payload_name: str | None = None,
    payload_mass: float | None = None,
    collision_setup_name: str | None = None,
    status_fn: Callable[[str], None] | None = None,
    segment_planned_fn: Callable[[int, list[list[float]], str | None], None]
    | None = None,
) -> PlanResult:
    """Plan each segment with its own TCP, chain the start joints, then merge.

    Mirrors ``plan_collision_free``: each segment is time-scaled against its own
    TCP's limits/offset, and the per-segment joint trajectories are merged via the
    ``mergeTrajectories`` endpoint (with per-segment position blending) into one
    executable trajectory. A single segment is returned without a merge round-trip.

    When ``collision_setup_name`` is given, the collision scene is attached to every
    motion-group setup so normal (motion-type) planning still respects it.
    """
    from wandelbots.omni.ui.tool.trajectory_planner.service.helpers import (
        build_motion_group_setup,
        fetch_motion_group_context,
    )

    if not segments:
        return PlanFailure(error="No motion segments to plan.")

    if status_fn:
        status_fn("Fetching motion group description...")

    async with get_api_client_from_config(api_configuration) as api_client:
        # Fetch the description once; build a MotionGroupSetup per distinct TCP.
        default_tcp = segments[0].tcp_name
        ctx = await fetch_motion_group_context(
            api_client,
            cell=cell,
            controller=controller,
            motion_group=motion_group,
            tcp_name=default_tcp,
            collision_setup_name=collision_setup_name,
        )
        description = ctx.description

        if collision_setup_name and ctx.collision_setups is None:
            return PlanFailure(
                error=f"Failed to fetch collision setup: {collision_setup_name}"
            )

        setups: dict[str | None, wb_models.MotionGroupSetup] = {}
        for tcp in {seg.tcp_name for seg in segments}:
            if tcp == default_tcp:
                tcp_offset = ctx.tcp_offset
            elif tcp and getattr(description, "tcps", None):
                tcp_data = description.tcps.get(tcp)
                tcp_offset = tcp_data.pose if tcp_data else None
            else:
                tcp_offset = ctx.tcp_offset
            setups[tcp] = build_motion_group_setup(
                description,
                tcp_offset,
                tcp_velocity_limit=tcp_velocity_limit,
                tcp_acceleration_limit=tcp_acceleration_limit,
                cycle_time=cycle_time,
                payload_name=payload_name,
                payload_mass=payload_mass,
            )
            # Respect the collision scene during normal planning too.
            if ctx.collision_setups:
                setups[tcp].collision_setups = ctx.collision_setups

        import omni.kit.app

        planning_api = wb.TrajectoryPlanningApi(api_client)
        planned: list[wb_models.JointTrajectory] = []
        current_start = start_joint_position
        for i, seg in enumerate(segments):
            if status_fn:
                status_fn(
                    f"Planning segment {i + 1}/{len(segments)} "
                    f"(tcp={seg.tcp_name!r}, {len(seg.motion_commands)} commands)..."
                )
            # Yield to the Kit update loop so the viewport keeps rendering between
            # the sequential per-segment plan calls (otherwise the UI appears frozen).
            await omni.kit.app.get_app().next_update_async()
            request = wb_models.PlanTrajectoryRequest(
                motion_group_setup=setups[seg.tcp_name],
                start_joint_position=current_start,
                motion_commands=seg.motion_commands,
            )
            result = await _call_plan_trajectory(planning_api, cell, request)
            if isinstance(result, PlanFailure):
                return PlanFailure(
                    error=f"Segment {i + 1}/{len(segments)} failed: {result.error}"
                )
            jt = result.joint_trajectory
            planned.append(jt)
            current_start = jt.joint_positions[-1]
            if segment_planned_fn:
                segment_planned_fn(i, jt.joint_positions, seg.tcp_name)

        if len(planned) == 1:
            return PlanSuccess(joint_trajectory=planned[0])

        if status_fn:
            status_fn(f"Merging {len(planned)} segments...")
        merge_segments = [
            wb_models.MergeTrajectoriesSegment(
                trajectory=jt,
                blending=seg.blending,
            )
            for jt, seg in zip(planned, segments)
        ]
        merge_request = wb_models.MergeTrajectoriesRequest(
            motion_group_setup=setups[segments[0].tcp_name],
            trajectory_segments=merge_segments,
        )
        merge_response = await planning_api.merge_trajectories(
            cell=cell,
            merge_trajectories_request=merge_request,
        )
        merged = merge_response.joint_trajectory
        if merged:
            return PlanSuccess(joint_trajectory=merged)
        return PlanFailure(error="Merge trajectories returned empty result")


async def plan_collision_free(
    api_configuration: ApiConfiguration,
    cell: str,
    controller: str,
    motion_group: str,
    start_joint_position: list[float],
    target_configs: list[list[list[float]]],
    tcp_name: str | None = None,
    collision_setup_name: str | None = None,
    tcp_velocity_limit: float | None = None,
    tcp_acceleration_limit: float | None = None,
    cycle_time: float | None = None,
    payload_name: str | None = None,
    payload_mass: float | None = None,
    cf_algorithm: str = "RRTConnectAlgorithm",
    cf_max_iterations: int = 10000,
    global_limits_override: dict | None = None,
    status_fn: Callable[[str], None] | None = None,
    segment_planned_fn: Callable[[int, list[list[float]]], None] | None = None,
) -> PlanResult:
    from wandelbots.omni.ui.tool.trajectory_planner.service.helpers import (
        build_motion_group_setup,
        fetch_motion_group_context,
    )

    limits_override = (
        wb_models.LimitsOverride.from_dict(global_limits_override)
        if global_limits_override
        else None
    )

    _last_yield_time = 0.0

    async def _status(msg: str) -> None:
        nonlocal _last_yield_time
        carb.log_info(msg)
        if status_fn:
            status_fn(msg)
        now = time.monotonic()
        if now - _last_yield_time >= 0.5:
            _last_yield_time = now
            import omni.kit.app

            await omni.kit.app.get_app().next_update_async()

    async with get_api_client_from_config(api_configuration) as api_client:
        ctx = await fetch_motion_group_context(
            api_client,
            cell=cell,
            controller=controller,
            motion_group=motion_group,
            tcp_name=tcp_name,
            collision_setup_name=collision_setup_name,
        )

        if collision_setup_name and ctx.collision_setups is None:
            return PlanFailure(
                error=f"Failed to fetch collision setup: {collision_setup_name}"
            )

        mg_setup = build_motion_group_setup(
            ctx.description,
            ctx.tcp_offset,
            tcp_velocity_limit=tcp_velocity_limit,
            tcp_acceleration_limit=tcp_acceleration_limit,
            cycle_time=cycle_time,
            payload_name=payload_name,
            payload_mass=payload_mass,
        )
        if ctx.collision_setups:
            mg_setup.collision_setups = ctx.collision_setups

        if cf_algorithm == "MidpointInsertionAlgorithm":
            algorithm = wb_models.CollisionFreeAlgorithm(
                wb_models.MidpointInsertionAlgorithm(max_iterations=cf_max_iterations)
            )
        else:
            algorithm = wb_models.CollisionFreeAlgorithm(
                wb_models.RRTConnectAlgorithm(max_iterations=cf_max_iterations)
            )

        planning_api = wb.TrajectoryPlanningApi(api_client)
        segments: list[wb_models.JointTrajectory] = []
        current_start_configs: list[list[float]] = [start_joint_position]
        last_error_msg: str = ""

        for i, tc in enumerate(target_configs):
            total_attempts = len(current_start_configs) * len(tc)
            await _status(
                f"Segment {i}/{len(target_configs) - 1}: "
                f"{len(current_start_configs)} start × {len(tc)} target "
                f"= {total_attempts} attempts"
            )

            segment_planned = False
            attempt = 0
            for s_idx, start_cfg in enumerate(current_start_configs):
                if segment_planned:
                    break
                for t_idx, target_cfg in enumerate(tc):
                    attempt += 1
                    await _status(
                        f"Segment {i} attempt {attempt}/{total_attempts}: "
                        f"start config {s_idx + 1}/{len(current_start_configs)}, "
                        f"target config {t_idx + 1}/{len(tc)}"
                    )
                    request = wb_models.PlanCollisionFreeRequest(
                        motion_group_setup=mg_setup,
                        start_joint_position=start_cfg,
                        target=target_cfg,
                        algorithm=algorithm,
                    )
                    try:
                        response = await planning_api.plan_collision_free(
                            cell=cell,
                            plan_collision_free_request=request,
                            _request_timeout=_REQUEST_TIMEOUT,
                        )
                    except Exception as exc:
                        try:
                            raw_resp = await planning_api.plan_collision_free_without_preload_content(
                                cell=cell,
                                plan_collision_free_request=request,
                                _request_timeout=_REQUEST_TIMEOUT,
                            )
                            raw_body = await raw_resp.read()
                            parsed = _parse_error_from_raw(raw_body)
                            last_error_msg = parsed or str(exc)
                        except Exception:
                            last_error_msg = str(exc)
                        await _status(
                            f"Segment {i} attempt {attempt}/{total_attempts} "
                            f"raised exception: {last_error_msg}"
                        )
                        continue

                    result_inner = response.response.actual_instance
                    if isinstance(result_inner, wb_models.JointTrajectory):
                        segments.append(result_inner)
                        current_start_configs = [result_inner.joint_positions[-1]]
                        segment_planned = True
                        await _status(
                            f"Segment {i} succeeded on attempt "
                            f"{attempt}/{total_attempts} "
                            f"({len(result_inner.joint_positions)} samples)"
                        )
                        if segment_planned_fn:
                            segment_planned_fn(i, result_inner.joint_positions)
                        break
                    else:
                        last_error_msg = _format_error_feedback(result_inner)
                        await _status(
                            f"Segment {i} attempt {attempt}/{total_attempts} "
                            f"failed: {last_error_msg}"
                        )

            if not segment_planned:
                return PlanFailure(
                    error=(
                        f"Collision-free planning failed for segment {i} "
                        f"after {total_attempts} attempts. "
                        f"Last error: {last_error_msg}"
                    )
                )

        await _status(f"Merging {len(segments)} collision-free segments...")
        merge_segments = [
            wb_models.MergeTrajectoriesSegment(
                trajectory=seg,
                collision_setups=ctx.collision_setups,
                limits_override=limits_override,
            )
            for seg in segments
        ]
        merge_request = wb_models.MergeTrajectoriesRequest(
            motion_group_setup=mg_setup,
            trajectory_segments=merge_segments,
        )
        merge_response = await planning_api.merge_trajectories(
            cell=cell,
            merge_trajectories_request=merge_request,
        )
        merged = merge_response.joint_trajectory
        if merged:
            await _status(f"Merged trajectory: {len(merged.joint_positions)} samples")
            return PlanSuccess(joint_trajectory=merged)
        return PlanFailure(error="Merge trajectories returned empty result")
