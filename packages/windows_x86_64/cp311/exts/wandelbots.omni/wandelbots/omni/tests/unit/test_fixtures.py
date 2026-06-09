"""Shared test fixtures for trajectory planner unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.datatypes import WSPose


SAMPLE_JOINT_CONFIGS = [
    [0.0, -1.57, 1.57, 0.0, 1.57, 0.0],
    [0.1, -1.47, 1.47, 0.1, 1.47, 0.1],
    [0.2, -1.37, 1.37, 0.2, 1.37, 0.2],
]

SAMPLE_POSE = WSPose(pose=[500.0, 200.0, 300.0, 0.0, 3.14, 0.0])
SAMPLE_POSES = [
    WSPose(pose=[500.0, 200.0, 300.0, 0.0, 3.14, 0.0]),
    WSPose(pose=[600.0, 200.0, 300.0, 0.0, 3.14, 0.0]),
    WSPose(pose=[700.0, 200.0, 300.0, 0.0, 3.14, 0.0]),
]

SAMPLE_CELL = "cell"
SAMPLE_CONTROLLER = "ur10e"
SAMPLE_MOTION_GROUP = "0@ur10e"
SAMPLE_TCP_NAME = "tcp_flange"
SAMPLE_ROBOT_PRIM = "/World/cell/workspace_ur10e/universalrobots_ur10e"


def _make_joint_limits(num_joints: int = 6) -> list[wb_v2_models.JointLimits]:
    """Create real JointLimits instances for test descriptions."""
    return [
        wb_v2_models.JointLimits(
            position=wb_v2_models.LimitRange(lower_limit=-6.28, upper_limit=6.28),
            velocity=6.28,
            acceleration=25.0,
        )
        for _ in range(num_joints)
    ]


def make_mock_description(
    model_name: str = "UniversalRobots_UR10e",
    num_joints: int = 6,
    tcp_name: str | None = None,
    tcp_pose: object | None = None,
) -> MagicMock:
    """Create a mock MotionGroupDescription with real Pydantic sub-models.

    Uses real wb_v2_models instances for fields that get passed into Pydantic
    constructors (mounting, auto_limits, joints) to avoid validation errors.
    """
    desc = MagicMock()
    desc.motion_group_model = model_name
    desc.cycle_time = 4  # int, milliseconds
    desc.mounting = wb_v2_models.Pose(
        position=[0.0, 0.0, 0.0], orientation=[0.0, 0.0, 0.0]
    )

    # TCPs
    if tcp_name and tcp_pose:
        tcp_data = MagicMock()
        tcp_data.pose = tcp_pose
        desc.tcps = {tcp_name: tcp_data}
    else:
        desc.tcps = {}

    # Operation limits with real model instances
    joint_limits = _make_joint_limits(num_joints)
    tcp_limits = wb_v2_models.CartesianLimits(
        velocity=1000.0,
        acceleration=4000.0,
        orientation_velocity=6.28,
        orientation_acceleration=25.0,
    )
    auto_limits = wb_v2_models.LimitSet(
        joints=joint_limits,
        tcp=tcp_limits,
        elbow=None,
        flange=None,
    )

    desc.operation_limits = MagicMock()
    desc.operation_limits.auto_limits = auto_limits

    return desc


def make_mock_api_configuration() -> MagicMock:
    """Create a mock ApiConfiguration."""
    config = MagicMock()
    config.base_url = "http://127.0.0.1:8011/omniservice/api/v2"
    config.base_url_websocket = "ws://127.0.0.1:8011/omniservice/api/v2"
    config.access_token = "test-token"
    return config
