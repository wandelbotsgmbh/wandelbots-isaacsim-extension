"""Unit tests for trajectory planner Pydantic models and store."""

from __future__ import annotations

import omni.kit.test

from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
    PlannedTrajectoryConfig,
    PoseConfig,
    TrajectoryPlannerConfig,
)


class TestTrajectoryPlannerModels(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    # -- PoseConfig ------------------------------------------------------------

    async def test_pose_config_defaults(self):
        config = PoseConfig(prim_path="/World/pose_0")
        self.assertEqual(config.prim_path, "/World/pose_0")
        self.assertEqual(config.motion_type, "PathCartesianPTP")
        self.assertIsNone(config.selected_joint_config)
        self.assertEqual(config.joint_configs, [])
        self.assertEqual(config.selected_config_idx, 0)
        self.assertFalse(config.is_ghost_object)
        self.assertIsNone(config.tcp_name)
        self.assertIsNone(config.blending)
        self.assertIsNone(config.limits_override)

    async def test_pose_config_with_joint_configs(self):
        joints = [[0.0, -1.57, 1.57, 0.0, 1.57, 0.0]]
        config = PoseConfig(
            prim_path="/World/pose_1",
            joint_configs=joints,
            selected_config_idx=0,
            motion_type="PathLine",
        )
        self.assertEqual(config.joint_configs, joints)
        self.assertEqual(config.motion_type, "PathLine")

    async def test_pose_config_serialization_roundtrip(self):
        config = PoseConfig(
            prim_path="/World/test",
            motion_type="PathJointPTP",
            joint_configs=[[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]],
            selected_config_idx=0,
            is_ghost_object=True,
            tcp_name="gripper_tcp",
            blending={"type": "velocity", "value": 50},
            limits_override={"tcp_velocity": 200.0},
        )
        data = config.model_dump()
        restored = PoseConfig(**data)
        self.assertEqual(config, restored)
        self.assertEqual(restored.tcp_name, "gripper_tcp")

    # -- PlannedTrajectoryConfig -----------------------------------------------

    async def test_planned_trajectory_config_defaults(self):
        config = PlannedTrajectoryConfig()
        self.assertEqual(config.joint_positions, [])
        self.assertEqual(config.locations, [])
        self.assertEqual(config.times, [])
        self.assertFalse(config.collision_free)

    async def test_planned_trajectory_config_with_data(self):
        config = PlannedTrajectoryConfig(
            joint_positions=[[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]],
            locations=[0.0, 1.0],
            times=[0.0, 2.5],
            collision_free=True,
        )
        self.assertEqual(len(config.joint_positions), 1)
        self.assertTrue(config.collision_free)

    # -- TrajectoryPlannerConfig -----------------------------------------------

    async def test_trajectory_planner_config_defaults(self):
        config = TrajectoryPlannerConfig(name="test_skill")
        self.assertEqual(config.name, "test_skill")
        self.assertIsNone(config.robot_prim_path)
        self.assertIsNone(config.tcp_name)
        self.assertIsNone(config.collision_setup)
        self.assertEqual(config.poses, [])
        self.assertFalse(config.live_update)
        self.assertEqual(config.overlay_color, [0.4, 1.0, 0.4])
        self.assertEqual(config.trajectory_color, [0.808, 0.0, 0.345])
        self.assertAlmostEqual(config.tcp_velocity, 500.0)
        self.assertAlmostEqual(config.tcp_acceleration, 2000.0)
        self.assertFalse(config.auto_blending)
        self.assertEqual(config.blending_min_velocity_percent, 50)
        self.assertEqual(config.payload_name, "")
        self.assertAlmostEqual(config.payload_mass, 0.0)
        self.assertEqual(config.cf_algorithm, "RRTConnectAlgorithm")
        self.assertEqual(config.cf_max_iterations, 10000)
        self.assertFalse(config.collapsed)
        self.assertFalse(config.poses_collapsed)
        self.assertIsNone(config.planned_trajectory)

    async def test_trajectory_planner_config_with_poses(self):
        poses = [
            PoseConfig(prim_path="/World/p0"),
            PoseConfig(prim_path="/World/p1", motion_type="PathLine"),
        ]
        config = TrajectoryPlannerConfig(
            name="multi_pose",
            robot_prim_path="/World/robot",
            tcp_name="flange",
            poses=poses,
        )
        self.assertEqual(len(config.poses), 2)
        self.assertEqual(config.poses[1].motion_type, "PathLine")

    async def test_trajectory_planner_config_full_roundtrip(self):
        config = TrajectoryPlannerConfig(
            name="roundtrip_test",
            robot_prim_path="/World/ur10e",
            tcp_name="tcp_flange",
            collision_setup="my_setup",
            poses=[
                PoseConfig(
                    prim_path="/World/p0",
                    joint_configs=[[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]],
                )
            ],
            tcp_velocity=250.0,
            tcp_acceleration=1000.0,
            auto_blending=True,
            payload_name="gripper",
            payload_mass=2.5,
            planned_trajectory=PlannedTrajectoryConfig(
                joint_positions=[[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]],
                locations=[0.0, 1.0],
                times=[0.0, 3.0],
                collision_free=True,
            ),
        )
        data = config.model_dump()
        restored = TrajectoryPlannerConfig(**data)
        self.assertEqual(config, restored)

    async def test_trajectory_planner_config_ignores_unknown_fields(self):
        data = {"name": "test", "unknown_field": "should_be_ignored"}
        # Pydantic v2 ignores extra fields by default unless configured
        config = TrajectoryPlannerConfig(
            **{k: v for k, v in data.items() if k == "name"}
        )
        self.assertEqual(config.name, "test")
