"""Unit tests for ExecutionService."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import omni.kit.test

from wandelbots.omni.tests.unit.test_fixtures import (
    SAMPLE_CELL,
    SAMPLE_CONTROLLER,
    SAMPLE_JOINT_CONFIGS,
    SAMPLE_MOTION_GROUP,
    SAMPLE_TCP_NAME,
    make_mock_api_configuration,
    make_mock_description,
)
from wandelbots.omni.ui.tool.trajectory_planner.service.execution_service import (
    ExecutionService,
)


class TestExecutionService(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.service = ExecutionService()
        self.api_config = make_mock_api_configuration()

    async def tearDown(self):
        pass

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.fetch_motion_group_context"
    )
    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.get_api_client_from_config"
    )
    async def test_forward_kinematics_returns_tcp_poses(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description = make_mock_description()
        mock_fetch_ctx.return_value = ctx

        expected_tcp_poses = [
            [500.0, 200.0, 300.0, 0.0, 3.14, 0.0],
            [600.0, 200.0, 300.0, 0.0, 3.14, 0.0],
        ]

        mock_fk_response = MagicMock()
        # FK response returns poses with position and orientation
        tcp_pose_1 = MagicMock()
        tcp_pose_1.position = [500.0, 200.0, 300.0]
        tcp_pose_1.orientation = [0.0, 3.14, 0.0]
        tcp_pose_2 = MagicMock()
        tcp_pose_2.position = [600.0, 200.0, 300.0]
        tcp_pose_2.orientation = [0.0, 3.14, 0.0]
        mock_fk_response.tcp_poses = [tcp_pose_1, tcp_pose_2]

        mock_kin_api = AsyncMock()
        mock_kin_api.forward_kinematics.return_value = mock_fk_response

        with patch(
            "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.wb_v2.KinematicsApi",
            return_value=mock_kin_api,
        ):
            result = await self.service.forward_kinematics(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                SAMPLE_JOINT_CONFIGS[:2],
                tcp_name=SAMPLE_TCP_NAME,
            )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], expected_tcp_poses[0])
        self.assertEqual(result[1], expected_tcp_poses[1])

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.fetch_motion_group_context"
    )
    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.get_api_client_from_config"
    )
    async def test_move_to_start_completes_within_tolerance(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description = make_mock_description()
        mock_fetch_ctx.return_value = ctx

        # Mock state response showing robot already at target
        mock_mg_api = AsyncMock()
        state = MagicMock()
        state.joint_position = SAMPLE_JOINT_CONFIGS[0]
        mock_mg_api.get_current_motion_group_state.return_value = state

        # Mock planning API (won't be called if already at target)
        mock_plan_api = AsyncMock()

        with patch(
            "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.wb_v2.MotionGroupApi",
            return_value=mock_mg_api,
        ):
            with patch(
                "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.wb_v2.TrajectoryPlanningApi",
                return_value=mock_plan_api,
            ):
                # Should complete without error when already at position
                await self.service.move_to_start(
                    self.api_config,
                    SAMPLE_CELL,
                    SAMPLE_CONTROLLER,
                    SAMPLE_MOTION_GROUP,
                    target_joint_position=SAMPLE_JOINT_CONFIGS[0],
                    tcp_name=SAMPLE_TCP_NAME,
                )

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.fetch_motion_group_context"
    )
    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.get_api_client_from_config"
    )
    async def test_move_to_start_respects_stop_event(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description = make_mock_description()
        mock_fetch_ctx.return_value = ctx

        # State shows robot far from target
        mock_mg_api = AsyncMock()
        state = MagicMock()
        state.joint_position = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        mock_mg_api.get_current_motion_group_state.return_value = state

        # Mock the planning API to return a valid response
        mock_plan_response = AsyncMock()
        mock_plan_response.status = 200
        mock_plan_response.json = AsyncMock(
            return_value={
                "response": {
                    "joint_positions": [SAMPLE_JOINT_CONFIGS[0]],
                    "locations": [0.0, 1.0],
                    "times": [0.0, 1.0],
                }
            }
        )
        mock_plan_api = AsyncMock()
        mock_plan_api.plan_trajectory_without_preload_content.return_value = (
            mock_plan_response
        )

        stop_event = asyncio.Event()
        stop_event.set()  # Already signaled

        with patch(
            "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.wb_v2.MotionGroupApi",
            return_value=mock_mg_api,
        ):
            with patch(
                "wandelbots.omni.ui.tool.trajectory_planner.service.execution_service.wb_v2.TrajectoryPlanningApi",
                return_value=mock_plan_api,
            ):
                # move_to_start plans first, then checks stop_event → raises CancelledError
                with self.assertRaises(asyncio.CancelledError):
                    await self.service.move_to_start(
                        self.api_config,
                        SAMPLE_CELL,
                        SAMPLE_CONTROLLER,
                        SAMPLE_MOTION_GROUP,
                        target_joint_position=SAMPLE_JOINT_CONFIGS[0],
                        stop_event=stop_event,
                    )
