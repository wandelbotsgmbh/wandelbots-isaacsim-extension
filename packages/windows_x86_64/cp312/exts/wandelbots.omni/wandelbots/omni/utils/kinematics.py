import math
from dataclasses import dataclass, field

import carb
import wandelbots_api_client.v2 as wb

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.manipulators import MotionStreamConfiguration
from wandelbots.omni.utils.api import get_api_client_from_config


@dataclass
class InverseKinematicsResult:
    joint_configs: list[list[float]] = field(default_factory=list)
    joint_limits: list[tuple[float, float]] = field(default_factory=list)


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
) -> InverseKinematicsResult:
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

            joint_limits = description.operation_limits.auto_limits
            joint_position_limits = (
                [joint.position for joint in joint_limits.joints]
                if joint_limits
                else None
            )

            response = await wb.KinematicsApi(api_client).inverse_kinematics(
                cell=stream_config.cell,
                inverse_kinematics_request=wb.models.InverseKinematicsRequest(
                    motion_group_model=description.motion_group_model,
                    joint_position_limits=joint_position_limits,
                    tcp_poses=[pose.to_nova_pose()],
                    tcp_offset=tcp_offset.to_nova_pose(),
                    collision_setups=collision_setups,
                    reference_joint_position=preferred_joint_values,
                ),
            )

            joints = response.joints[0] if response.joints else []
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
            carb.log_verbose(f"IK fetch failed: {error}")
            return InverseKinematicsResult()

    return InverseKinematicsResult(
        joint_configs=sort_joint_configs_by_proximity(joints, preferred_joint_values),
        joint_limits=per_joint_limits,
    )
