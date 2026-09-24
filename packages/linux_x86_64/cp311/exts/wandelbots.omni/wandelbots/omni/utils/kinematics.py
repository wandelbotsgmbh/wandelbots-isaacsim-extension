import asyncio
import math
from dataclasses import dataclass, field

import carb
import wandelbots_api_client.v2 as wb
import wandelbots_api_client.v2.models as wb_models

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.manipulators import MotionStreamConfiguration
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.utils.math import matrix_to_pose, pose_to_matrix


@dataclass
class InverseKinematicsResult:
    joint_configs: list[list[float]] = field(default_factory=list)
    joint_limits: list[tuple[float, float]] = field(default_factory=list)


# Last tcp pose (world-of-discourse: base-frame [x, y, z, rx, ry, rz]) for which IK
# succeeded, per motion group. Diagnostic aid only — lets a failed IK log how far the
# rejected pose was from one that recently worked.
_last_successful_pose: dict[tuple[str, str, str], list[float]] = {}

# Motion groups for which the solver self-consistency check (below) has already run
# once. Diagnostic aid only — avoids re-checking on every failed frame while dragging.
_solver_self_check_done: set[tuple[str, str, str]] = set()


def base_pose_to_world(pose: list[float], mounting) -> list[float]:
    """*pose*, given in the robot's base frame, in the frame NOVA solves in.

    Poses in the scene are read relative to link_0, and link_0 is where the
    mounting ends: the scene already stands the robot on its mount. NOVA's IK
    and planning take poses in the frame the mounting starts from, so a base
    pose sent as it is lands the tool exactly the mounting away from its
    target. *mounting* is a NOVA ``Pose`` (mm, rotation vector); None means
    the two frames coincide.
    """
    if mounting is None:
        return [float(v) for v in pose]
    mount = pose_to_matrix(list(mounting.position) + list(mounting.orientation))
    return [float(v) for v in matrix_to_pose(mount @ pose_to_matrix(list(pose)))]


def _pose_delta(pose_a: list[float], pose_b: list[float]) -> tuple[float, float]:
    """Position delta (mm, L2) and orientation delta (rad, L2) between two [x,y,z,rx,ry,rz] poses."""
    position_delta = math.sqrt(
        sum((a - b) ** 2 for a, b in zip(pose_a[:3], pose_b[:3]))
    )
    orientation_delta = math.sqrt(
        sum((a - b) ** 2 for a, b in zip(pose_a[3:], pose_b[3:]))
    )
    return position_delta, orientation_delta


def joint_config_signs(
    config: list[float],
    joint_limits: list[tuple[float, float]] | None = None,
) -> str:
    """Signs of joints 1, 3, 5 (indices 0, 2, 4) as a compact string, e.g. '++-'.

    When joint_limits are supplied, comparison is made against the range midpoint
    (lower + upper) / 2 rather than 0, so asymmetric joint ranges are handled correctly.
    """

    def _midpoint(joint_idx: int) -> float:
        if not joint_limits or joint_idx >= len(joint_limits):
            return 0.0
        lower, upper = joint_limits[joint_idx]
        return (lower + upper) / 2.0

    return "".join(
        "+" if config[joint_idx] >= _midpoint(joint_idx) else "-"
        for joint_idx in [0, 2, 4]
        if joint_idx < len(config)
    )


def weighted_joint_distance(joints_a: list[float], joints_b: list[float]) -> float:
    """Weighted L2 distance with base joints counting exponentially more than wrist joints."""
    num_joints = len(joints_a)
    weights = [2 ** (num_joints - 1 - i) for i in range(num_joints)]
    return math.sqrt(
        sum(
            weight * (val_a - val_b) ** 2
            for weight, val_a, val_b in zip(weights, joints_a, joints_b)
        )
    )


def sort_joint_configs_by_proximity(
    joint_configs: list[list[float]],
    reference: list[float] | None,
) -> list[list[float]]:
    """Sort by weighted distance so base-joint deviations outweigh wrist deviations.

    Weight for joint i (0 = base): 2^(n-1-i), e.g. [32, 16, 8, 4, 2, 1] for 6-DOF.
    """
    if not reference or not joint_configs:
        return list(joint_configs)

    return sorted(
        joint_configs, key=lambda config: weighted_joint_distance(config, reference)
    )


async def fetch_joint_configs_for_pose(
    stream_config: MotionStreamConfiguration,
    pose: WSPose,
    tcp_offset: WSPose,
    preferred_joint_values: list[float] | None = None,
    collision_setups: dict | None = None,
    description: wb.models.MotionGroupDescription | None = None,
    mounting_override: wb.models.Pose | None = None,
    diagnostics: bool = True,
) -> InverseKinematicsResult:
    """Batched IK for one pose.

    ``diagnostics=False`` skips the failure-analysis extras on an unsolved
    pose (unclamped retry, solver self-check, last-good-pose delta warning) —
    use it for high-frequency callers like the live IK tool, where an
    unreachable pose is an expected state, not an anomaly to investigate.
    """
    api_config = stream_config.get_api_configuration()

    async with get_api_client_from_config(api_config) as api_client:
        try:
            if description is None:
                description = await wb.MotionGroupApi(
                    api_client
                ).get_motion_group_description(
                    cell=stream_config.cell,
                    controller=stream_config.controller,
                    motion_group=stream_config.motion_group,
                )

            # The description can be incomplete while the motion group is still
            # being configured (e.g. motion_group_model is None) — IK can't run
            # then. Fail clearly instead of crashing.
            if not getattr(description, "motion_group_model", None):
                carb.log_warn(
                    "IK skipped: motion group description not ready "
                    "(no motion_group_model). Try again in a moment."
                )
                return InverseKinematicsResult()

            operation_limits = getattr(description, "operation_limits", None)
            joint_limits = operation_limits.auto_limits if operation_limits else None
            joint_position_limits = (
                [joint.position for joint in joint_limits.joints]
                if joint_limits
                else None
            )
            effective_mounting = (
                mounting_override
                if mounting_override is not None
                else description.mounting
            )

            nova_pose = WSPose(
                pose=base_pose_to_world(pose.pose, effective_mounting)
            ).to_nova_pose()

            # tcp_offset is optional in the request (None == flange); never call
            # .to_nova_pose() on a missing offset.
            nova_tcp_offset = (
                tcp_offset.to_nova_pose() if tcp_offset is not None else None
            )

            # Without an explicit preference (e.g. no joint config picked yet for
            # this ghost pose), bias the IK solution toward the robot's current
            # joint position instead of leaving it unconstrained.
            reference_joint_position = preferred_joint_values
            if reference_joint_position is None:
                try:
                    current_state = await wb.MotionGroupApi(
                        api_client
                    ).get_current_motion_group_state(
                        cell=stream_config.cell,
                        controller=stream_config.controller,
                        motion_group=stream_config.motion_group,
                    )
                    reference_joint_position = list(current_state.joint_position)
                except Exception as state_error:
                    carb.log_verbose(
                        f"Could not fetch current motion group state for IK "
                        f"reference: {state_error}"
                    )

            ik_request = wb.models.InverseKinematicsRequest(
                motion_group_model=description.motion_group_model,
                joint_position_limits=joint_position_limits,
                tcp_poses=[nova_pose],
                tcp_offset=nova_tcp_offset,
                mounting=effective_mounting,
                collision_setups=collision_setups,
                reference_joint_position=reference_joint_position,
            )
            carb.log_info(
                f"IK request (cell={stream_config.cell}): {ik_request.to_dict()}"
            )

            response = await wb.KinematicsApi(api_client).inverse_kinematics(
                cell=stream_config.cell,
                inverse_kinematics_request=ik_request,
            )
            carb.log_info(f"IK response: {response.to_dict()}")

            joints = response.joints[0] if response.joints else []

            # Multi-seed fallback. The Generic IK solver is local and seeded from
            # reference_joint_position, so a poorly-conditioned seed — e.g. the arm
            # parked fully extended / near-singular — makes it miss solutions that
            # genuinely exist within limits. When the primary seed finds nothing,
            # retry with a spread of well-conditioned seeds derived from the joint
            # limits (mid-range, plus base-joint rotations to escape orientation-
            # dependent local minima). The retries run CONCURRENTLY (one request
            # of wall-clock, cheap enough for the live tool), and the solutions
            # of every succeeding seed are unioned so callers can offer distinct
            # IK branches instead of a single local answer.
            if not joints and joint_position_limits:
                midpoint = [
                    (limit.lower_limit + limit.upper_limit) / 2.0
                    if limit
                    and limit.lower_limit is not None
                    and limit.upper_limit is not None
                    else 0.0
                    for limit in joint_position_limits
                ]
                fallback_seeds = [midpoint]
                for base_angle in (-2.0, -1.0, 1.0, 2.0, 3.0):
                    seed = list(midpoint)
                    seed[0] = base_angle
                    fallback_seeds.append(seed)

                async def _retry_with_seed(seed: list[float]):
                    return await wb.KinematicsApi(api_client).inverse_kinematics(
                        cell=stream_config.cell,
                        inverse_kinematics_request=wb.models.InverseKinematicsRequest(
                            motion_group_model=description.motion_group_model,
                            joint_position_limits=joint_position_limits,
                            tcp_poses=[nova_pose],
                            tcp_offset=nova_tcp_offset,
                            mounting=effective_mounting,
                            collision_setups=collision_setups,
                            reference_joint_position=seed,
                        ),
                    )

                retries = await asyncio.gather(
                    *(_retry_with_seed(seed) for seed in fallback_seeds),
                    return_exceptions=True,
                )
                merged: list[list[float]] = []
                seen: set[tuple] = set()
                for retry in retries:
                    if isinstance(retry, BaseException):
                        carb.log_verbose(f"IK fallback seed failed: {retry}")
                        continue
                    for solution in retry.joints[0] if retry.joints else []:
                        key = tuple(round(float(v), 3) for v in solution)
                        if key not in seen:
                            seen.add(key)
                            merged.append(list(solution))
                if merged:
                    carb.log_info(
                        "IK: primary seed found no solution; parallel multi-seed "
                        f"fallback found {len(merged)} distinct config(s)."
                    )
                    joints = merged

            mg_key = (
                stream_config.cell,
                stream_config.controller,
                stream_config.motion_group,
            )
            requested_pose = [
                *ik_request.tcp_poses[0].position,
                *ik_request.tcp_poses[0].orientation,
            ]
            if joints:
                _last_successful_pose[mg_key] = requested_pose
            elif diagnostics:
                last_good = _last_successful_pose.get(mg_key)
                if last_good is not None:
                    position_delta, orientation_delta = _pose_delta(
                        requested_pose, last_good
                    )
                    carb.log_warn(
                        "IK found no solution; delta from last successful pose for "
                        f"this motion group: position={position_delta:.1f}mm, "
                        f"orientation={orientation_delta:.3f}rad "
                        f"(last successful pose: {last_good})"
                    )

                # Diagnostic-only: does dropping the auto-mode joint limits change
                # the outcome? Never used for the returned result — just tells us
                # whether "unreachable" means "outside auto_limits" or "unreachable
                # regardless of limits".
                if joint_position_limits:
                    try:
                        unclamped_response = await wb.KinematicsApi(
                            api_client
                        ).inverse_kinematics(
                            cell=stream_config.cell,
                            inverse_kinematics_request=wb.models.InverseKinematicsRequest(
                                motion_group_model=ik_request.motion_group_model,
                                tcp_poses=ik_request.tcp_poses,
                                tcp_offset=ik_request.tcp_offset,
                                mounting=ik_request.mounting,
                                collision_setups=ik_request.collision_setups,
                                reference_joint_position=ik_request.reference_joint_position,
                            ),
                        )
                        if unclamped_response.joints and unclamped_response.joints[0]:
                            carb.log_warn(
                                "IK diagnostic: pose IS reachable without "
                                "joint_position_limits — auto_limits are likely why "
                                "the constrained request failed."
                            )
                        else:
                            carb.log_warn(
                                "IK diagnostic: pose has no solution even without "
                                "joint_position_limits — genuinely unreachable for "
                                "this motion group/TCP, not a limits issue."
                            )
                    except Exception as diag_error:
                        carb.log_verbose(
                            f"IK diagnostic (unclamped retry) failed: {diag_error}"
                        )

                # Diagnostic-only, one-shot per motion group: can the solver
                # reproduce the robot's OWN current flange pose? That pose is
                # reachable by definition (the robot is already there), so if this
                # fails too, IK is broken for this motion group/model regardless of
                # target — a backend/model-config issue, not a specific-pose one.
                if mg_key not in _solver_self_check_done:
                    _solver_self_check_done.add(mg_key)
                    try:
                        current_state_check = await wb.MotionGroupApi(
                            api_client
                        ).get_current_motion_group_state(
                            cell=stream_config.cell,
                            controller=stream_config.controller,
                            motion_group=stream_config.motion_group,
                        )
                        if current_state_check.flange_pose is not None:
                            self_check_response = await wb.KinematicsApi(
                                api_client
                            ).inverse_kinematics(
                                cell=stream_config.cell,
                                inverse_kinematics_request=wb.models.InverseKinematicsRequest(
                                    motion_group_model=ik_request.motion_group_model,
                                    tcp_poses=[current_state_check.flange_pose],
                                    tcp_offset=None,
                                    mounting=ik_request.mounting,
                                    reference_joint_position=list(
                                        current_state_check.joint_position
                                    ),
                                ),
                            )
                            if (
                                self_check_response.joints
                                and self_check_response.joints[0]
                            ):
                                carb.log_verbose(
                                    "IK diagnostic: solver reproduces the robot's "
                                    "own current flange pose — solver itself is "
                                    "functioning for this motion group."
                                )
                            else:
                                carb.log_error(
                                    "IK diagnostic: solver could NOT reproduce the "
                                    "robot's own current flange pose — IK appears "
                                    "broken for this motion group/model regardless "
                                    "of target, likely a backend kinematic-model "
                                    "issue rather than an unreachable pose."
                                )
                    except Exception as self_check_error:
                        carb.log_verbose(
                            f"IK diagnostic (self-consistency check) failed: {self_check_error}"
                        )

            per_joint_limits: list[tuple[float, float]] = (
                [
                    (
                        limit.lower_limit
                        if limit and limit.lower_limit is not None
                        else 0.0,
                        limit.upper_limit
                        if limit and limit.upper_limit is not None
                        else 0.0,
                    )
                    for limit in joint_position_limits
                ]
                if joint_position_limits
                else []
            )
        except Exception as error:
            carb.log_warn(f"IK fetch failed: {error}")
            return InverseKinematicsResult()

    return InverseKinematicsResult(
        joint_configs=sort_joint_configs_by_proximity(joints, reference_joint_position),
        joint_limits=per_joint_limits,
    )


#: What the step size field starts at. NOVA's own value, read from the API
#: model rather than copied in here.
DEFAULT_STEP_SIZE: float = wb_models.RRTConnectAlgorithm.model_fields[
    "max_step_size"
].default

#: Our own floor, not NOVA's: below this the search stops advancing.
MIN_STEP_SIZE: float = 0.01


def clamp_step_size(step_size: float | None) -> float | None:
    """The step size collision-free planning can use, None to leave it adaptive.

    inf and nan reach this from hand-edited configs and from float(), which
    reads "inf" and "1e309" as numbers. Neither can size a search step, so
    they fall back to adaptive rather than into a request or a saved config.
    """
    if step_size is None or not math.isfinite(step_size):
        return None
    return max(MIN_STEP_SIZE, step_size)


def build_collision_free_algorithm(
    max_iterations: int, step_size: float | None = None
) -> wb_models.CollisionFreeAlgorithm:
    """RRT-Connect settings for collision-free planning and for skill export.

    A *step_size* of None leaves the search to size its own steps. A value
    fixes the step, and the superseded ``adaptive_step_size`` and
    ``max_step_size`` are set to match it: the client serialises their
    defaults either way, so setting ``step_size`` alone would send an adaptive
    setting alongside the fixed one and leave the server free to pick either.
    """
    if step_size is None:
        return wb_models.CollisionFreeAlgorithm(
            wb_models.RRTConnectAlgorithm(
                max_iterations=max_iterations,
                algorithm_name="RRTConnectAlgorithm",
            )
        )
    return wb_models.CollisionFreeAlgorithm(
        wb_models.RRTConnectAlgorithm(
            max_iterations=max_iterations,
            algorithm_name="RRTConnectAlgorithm",
            step_size=wb_models.RRTConnectAlgorithmStepSize(step_size),
            adaptive_step_size=False,
            max_step_size=step_size,
        )
    )
