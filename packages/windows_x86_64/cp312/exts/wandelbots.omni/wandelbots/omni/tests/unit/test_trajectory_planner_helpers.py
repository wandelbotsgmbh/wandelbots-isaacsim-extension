"""Unit tests for trajectory planner service helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import omni.kit.test

from wandelbots.omni.tests.unit.test_fixtures import (
    SAMPLE_CELL,
    SAMPLE_CONTROLLER,
    SAMPLE_MOTION_GROUP,
    SAMPLE_TCP_NAME,
    make_mock_description,
)
from wandelbots.omni.ui.tool.trajectory_planner.service.helpers import (
    MotionGroupContext,
    build_global_limits,
    build_motion_group_setup,
    extract_joint_position_limits,
    fetch_motion_group_context,
)


class TestExtractJointPositionLimits(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_extracts_limits_from_valid_description(self):
        desc = make_mock_description(num_joints=6)
        limits = extract_joint_position_limits(desc)

        self.assertIsNotNone(limits)
        self.assertEqual(len(limits), 6)
        self.assertAlmostEqual(limits[0].lower_limit, -6.28)
        self.assertAlmostEqual(limits[0].upper_limit, 6.28)

    async def test_returns_none_when_no_operation_limits(self):
        desc = MagicMock()
        desc.operation_limits = None
        limits = extract_joint_position_limits(desc)
        self.assertIsNone(limits)

    async def test_returns_none_when_no_auto_limits(self):
        desc = MagicMock()
        desc.operation_limits = MagicMock()
        desc.operation_limits.auto_limits = None
        limits = extract_joint_position_limits(desc)
        self.assertIsNone(limits)

    async def test_returns_none_when_no_joints(self):
        desc = MagicMock()
        desc.operation_limits = MagicMock()
        desc.operation_limits.auto_limits = MagicMock()
        desc.operation_limits.auto_limits.joints = None
        limits = extract_joint_position_limits(desc)
        self.assertIsNone(limits)

    async def test_skips_joints_without_position(self):
        desc = make_mock_description(num_joints=3)
        # Set one joint's position to None
        desc.operation_limits.auto_limits.joints[1].position = None
        limits = extract_joint_position_limits(desc)

        self.assertIsNotNone(limits)
        self.assertEqual(len(limits), 2)


class TestBuildGlobalLimits(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_returns_base_limits_when_no_overrides(self):
        desc = make_mock_description()
        result = build_global_limits(desc, None, None)
        # No override → returns auto_limits unchanged
        self.assertEqual(result, desc.operation_limits.auto_limits)

    async def test_returns_base_limits_when_zero_overrides(self):
        desc = make_mock_description()
        result = build_global_limits(desc, 0.0, 0.0)
        self.assertEqual(result, desc.operation_limits.auto_limits)

    async def test_overrides_velocity(self):
        desc = make_mock_description()
        result = build_global_limits(desc, 200.0, None)

        self.assertIsNotNone(result)
        self.assertEqual(result.tcp.velocity, 200.0)
        # Acceleration should come from base
        self.assertEqual(result.tcp.acceleration, 4000.0)

    async def test_overrides_acceleration(self):
        desc = make_mock_description()
        result = build_global_limits(desc, None, 1500.0)

        self.assertIsNotNone(result)
        self.assertEqual(result.tcp.acceleration, 1500.0)
        # Velocity should come from base
        self.assertEqual(result.tcp.velocity, 1000.0)

    async def test_overrides_both(self):
        desc = make_mock_description()
        result = build_global_limits(desc, 300.0, 1200.0)

        self.assertIsNotNone(result)
        self.assertEqual(result.tcp.velocity, 300.0)
        self.assertEqual(result.tcp.acceleration, 1200.0)

    async def test_handles_no_base_limits(self):
        desc = make_mock_description()
        desc.operation_limits.auto_limits = None
        result = build_global_limits(desc, 500.0, None)

        self.assertIsNotNone(result)
        self.assertEqual(result.tcp.velocity, 500.0)


class TestBuildMotionGroupSetup(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_basic_setup_without_overrides(self):
        import wandelbots_api_client.v2.models as wb_v2_models

        desc = make_mock_description(model_name="UR10e")
        tcp_offset = wb_v2_models.Pose(
            position=[0.0, 0.0, 100.0], orientation=[0.0, 0.0, 0.0]
        )

        result = build_motion_group_setup(desc, tcp_offset)

        self.assertEqual(result.motion_group_model, "UR10e")
        self.assertEqual(result.cycle_time, 4)
        self.assertEqual(result.mounting, desc.mounting)
        self.assertEqual(result.tcp_offset, tcp_offset)
        self.assertIsNone(result.payload)

    async def test_setup_with_payload(self):
        desc = make_mock_description()
        result = build_motion_group_setup(
            desc, None, payload_name="gripper", payload_mass=2.5
        )

        self.assertIsNotNone(result.payload)
        self.assertEqual(result.payload.name, "gripper")
        self.assertEqual(result.payload.payload, 2.5)

    async def test_setup_with_cycle_time_override(self):
        desc = make_mock_description()
        result = build_motion_group_setup(desc, None, cycle_time=8)
        self.assertEqual(result.cycle_time, 8)

    async def test_setup_uses_description_cycle_time_when_none(self):
        desc = make_mock_description()
        result = build_motion_group_setup(desc, None, cycle_time=None)
        self.assertEqual(result.cycle_time, 4)

    async def test_setup_with_velocity_limits(self):
        desc = make_mock_description()
        result = build_motion_group_setup(
            desc, None, tcp_velocity_limit=250.0, tcp_acceleration_limit=1000.0
        )
        self.assertIsNotNone(result.global_limits)
        self.assertEqual(result.global_limits.tcp.velocity, 250.0)
        self.assertEqual(result.global_limits.tcp.acceleration, 1000.0)


class TestFetchMotionGroupContext(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.wb_v2.MotionGroupApi"
    )
    async def test_fetches_basic_context(self, mock_mg_api_cls):
        mock_api = AsyncMock()
        mock_mg_api_cls.return_value = mock_api
        desc = make_mock_description(model_name="UR10e")
        mock_api.get_motion_group_description.return_value = desc

        api_client = MagicMock()
        ctx = await fetch_motion_group_context(
            api_client, SAMPLE_CELL, SAMPLE_CONTROLLER, SAMPLE_MOTION_GROUP
        )

        self.assertIsInstance(ctx, MotionGroupContext)
        self.assertEqual(ctx.model_name, "UR10e")
        self.assertIsNone(ctx.tcp_offset)
        self.assertIsNone(ctx.collision_setups)
        self.assertIsNone(ctx.joint_position_limits)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.wb_v2.StoreCollisionSetupsApi"
    )
    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.wb_v2.MotionGroupApi"
    )
    async def test_fetches_context_with_tcp_and_collision(
        self, mock_mg_api_cls, mock_collision_api_cls
    ):
        mock_api = AsyncMock()
        mock_mg_api_cls.return_value = mock_api
        tcp_pose = MagicMock()
        desc = make_mock_description(tcp_name=SAMPLE_TCP_NAME, tcp_pose=tcp_pose)
        mock_api.get_motion_group_description.return_value = desc

        mock_collision_api = AsyncMock()
        mock_collision_api_cls.return_value = mock_collision_api
        collision_setup = MagicMock()
        mock_collision_api.get_stored_collision_setup.return_value = collision_setup

        api_client = MagicMock()
        ctx = await fetch_motion_group_context(
            api_client,
            SAMPLE_CELL,
            SAMPLE_CONTROLLER,
            SAMPLE_MOTION_GROUP,
            tcp_name=SAMPLE_TCP_NAME,
            collision_setup_name="my_setup",
        )

        self.assertEqual(ctx.tcp_offset, tcp_pose)
        self.assertIsNotNone(ctx.collision_setups)
        self.assertIn("my_setup", ctx.collision_setups)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.wb_v2.MotionGroupApi"
    )
    async def test_fetches_joint_limits_when_requested(self, mock_mg_api_cls):
        mock_api = AsyncMock()
        mock_mg_api_cls.return_value = mock_api
        desc = make_mock_description(num_joints=6)
        mock_api.get_motion_group_description.return_value = desc

        api_client = MagicMock()
        ctx = await fetch_motion_group_context(
            api_client,
            SAMPLE_CELL,
            SAMPLE_CONTROLLER,
            SAMPLE_MOTION_GROUP,
            include_joint_limits=True,
        )

        self.assertIsNotNone(ctx.joint_position_limits)
        self.assertEqual(len(ctx.joint_position_limits), 6)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.wb_v2.StoreCollisionSetupsApi"
    )
    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.wb_v2.MotionGroupApi"
    )
    async def test_handles_collision_setup_fetch_error(
        self, mock_mg_api_cls, mock_collision_api_cls
    ):
        mock_api = AsyncMock()
        mock_mg_api_cls.return_value = mock_api
        desc = make_mock_description()
        mock_api.get_motion_group_description.return_value = desc

        mock_collision_api = AsyncMock()
        mock_collision_api_cls.return_value = mock_collision_api
        mock_collision_api.get_stored_collision_setup.side_effect = Exception(
            "Not found"
        )

        api_client = MagicMock()
        ctx = await fetch_motion_group_context(
            api_client,
            SAMPLE_CELL,
            SAMPLE_CONTROLLER,
            SAMPLE_MOTION_GROUP,
            collision_setup_name="missing_setup",
        )

        # Should not raise, collision_setups should be None
        self.assertIsNone(ctx.collision_setups)
