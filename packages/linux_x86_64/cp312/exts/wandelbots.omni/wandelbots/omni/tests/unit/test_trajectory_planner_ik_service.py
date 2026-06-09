"""Unit tests for IKService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import omni.kit.test
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.tests.unit.test_fixtures import (
    SAMPLE_CELL,
    SAMPLE_CONTROLLER,
    SAMPLE_JOINT_CONFIGS,
    SAMPLE_MOTION_GROUP,
    SAMPLE_POSE,
    SAMPLE_POSES,
    SAMPLE_TCP_NAME,
    make_mock_api_configuration,
)
from wandelbots.omni.ui.tool.trajectory_planner.service.ik_service import (
    IKResult,
    IKService,
)


class TestIKService(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.service = IKService()
        self.api_config = make_mock_api_configuration()

    async def tearDown(self):
        pass

    def _make_ik_response(self, joint_configs: list[list[float]] | None = None):
        response = MagicMock()
        if joint_configs:
            response.joints = [joint_configs]
        else:
            response.joints = [[]]
        return response

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.fetch_motion_group_context"
    )
    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.get_api_client_from_config"
    )
    async def test_fetch_ik_success(self, mock_get_client, mock_fetch_ctx):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description.mounting = wb_v2_models.Pose(
            position=[0, 0, 0], orientation=[0, 0, 0]
        )
        ctx.joint_position_limits = None
        ctx.collision_setups = None
        mock_fetch_ctx.return_value = ctx

        expected_joints = SAMPLE_JOINT_CONFIGS[:2]
        mock_ik_api = AsyncMock()
        mock_ik_api.inverse_kinematics.return_value = self._make_ik_response(
            expected_joints
        )

        with patch(
            "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.wb_v2.KinematicsApi",
            return_value=mock_ik_api,
        ):
            result = await self.service.fetch_ik(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                SAMPLE_POSE,
                tcp_name=SAMPLE_TCP_NAME,
            )

        self.assertIsInstance(result, IKResult)
        self.assertEqual(result.joint_configs, expected_joints)
        self.assertIsNone(result.error)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.fetch_motion_group_context"
    )
    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.get_api_client_from_config"
    )
    async def test_fetch_ik_empty_result(self, mock_get_client, mock_fetch_ctx):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description.mounting = wb_v2_models.Pose(
            position=[0, 0, 0], orientation=[0, 0, 0]
        )
        ctx.joint_position_limits = None
        ctx.collision_setups = None
        mock_fetch_ctx.return_value = ctx

        mock_ik_api = AsyncMock()
        mock_ik_api.inverse_kinematics.return_value = self._make_ik_response(None)

        with patch(
            "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.wb_v2.KinematicsApi",
            return_value=mock_ik_api,
        ):
            result = await self.service.fetch_ik(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                SAMPLE_POSE,
            )

        self.assertIsInstance(result, IKResult)
        self.assertEqual(result.joint_configs, [])
        self.assertIsNone(result.error)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.fetch_motion_group_context"
    )
    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.get_api_client_from_config"
    )
    async def test_fetch_ik_batch_all_success(self, mock_get_client, mock_fetch_ctx):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description.mounting = wb_v2_models.Pose(
            position=[0, 0, 0], orientation=[0, 0, 0]
        )
        ctx.joint_position_limits = None
        ctx.collision_setups = None
        mock_fetch_ctx.return_value = ctx

        mock_ik_api = AsyncMock()
        # Each call returns different configs
        configs_per_pose = [
            [SAMPLE_JOINT_CONFIGS[0]],
            [SAMPLE_JOINT_CONFIGS[1]],
            [SAMPLE_JOINT_CONFIGS[2]],
        ]
        call_count = [0]

        async def mock_inverse_kinematics(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            return self._make_ik_response(configs_per_pose[idx % len(configs_per_pose)])

        mock_ik_api.inverse_kinematics = mock_inverse_kinematics

        callback_results = []

        def on_result(idx, result):
            callback_results.append((idx, result))

        with patch(
            "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.wb_v2.KinematicsApi",
            return_value=mock_ik_api,
        ):
            results = await self.service.fetch_ik_batch(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                SAMPLE_POSES,
                on_result=on_result,
            )

        self.assertEqual(len(results), 3)
        for r in results:
            self.assertIsInstance(r, IKResult)
            self.assertGreater(len(r.joint_configs), 0)

        # Callback should have been called for each pose
        self.assertEqual(len(callback_results), 3)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.fetch_motion_group_context"
    )
    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.get_api_client_from_config"
    )
    async def test_fetch_ik_batch_partial_failure(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description.mounting = wb_v2_models.Pose(
            position=[0, 0, 0], orientation=[0, 0, 0]
        )
        ctx.joint_position_limits = None
        ctx.collision_setups = None
        mock_fetch_ctx.return_value = ctx

        mock_ik_api = AsyncMock()
        call_count = [0]

        async def mock_inverse_kinematics(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 1:
                raise Exception("Pose unreachable")
            return self._make_ik_response([SAMPLE_JOINT_CONFIGS[0]])

        mock_ik_api.inverse_kinematics = mock_inverse_kinematics

        with patch(
            "wandelbots.omni.ui.tool.trajectory_planner.service.ik_service.wb_v2.KinematicsApi",
            return_value=mock_ik_api,
        ):
            results = await self.service.fetch_ik_batch(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                SAMPLE_POSES,
            )

        self.assertEqual(len(results), 3)
        # First and third should succeed
        self.assertGreater(len(results[0].joint_configs), 0)
        self.assertIsNone(results[0].error)
        # Second should have error
        self.assertIsNotNone(results[1].error)
        self.assertIn("unreachable", results[1].error)
        # Third should succeed
        self.assertGreater(len(results[2].joint_configs), 0)


class TestIKResult(omni.kit.test.AsyncTestCase):
    async def test_ik_result_defaults(self):
        result = IKResult(joint_configs=[[1.0, 2.0, 3.0]])
        self.assertIsNone(result.error)
        self.assertEqual(len(result.joint_configs), 1)

    async def test_ik_result_with_error(self):
        result = IKResult(joint_configs=[], error="Pose unreachable")
        self.assertEqual(result.joint_configs, [])
        self.assertEqual(result.error, "Pose unreachable")
