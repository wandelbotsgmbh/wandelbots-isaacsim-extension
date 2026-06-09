"""Unit tests for planner_utils planning functions and error parsing utilities."""

from __future__ import annotations

import json
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
from wandelbots.omni.ui.tool.planner_utils import (
    PlanFailure,
    PlanSuccess,
    TrajectorySegmentSpec,
    _format_error_feedback,
    _parse_error_from_raw,
    plan_trajectory,
    plan_trajectory_segments,
)


class TestParsePlanTrajectoryError(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_returns_none_for_invalid_json(self):
        self.assertIsNone(_parse_error_from_raw("not json"))
        self.assertIsNone(_parse_error_from_raw(None))
        self.assertIsNone(_parse_error_from_raw(b""))

    async def test_returns_none_for_success_response(self):
        data = {"response": {"joint_positions": [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]]}}
        result = _parse_error_from_raw(json.dumps(data))
        self.assertIsNone(result)

    async def test_extracts_error_feedback_name(self):
        data = {
            "response": {
                "joint_positions": None,
                "error_feedback": {"error_feedback_name": "JointLimitViolation"},
            }
        }
        result = _parse_error_from_raw(json.dumps(data))
        self.assertIn("JointLimitViolation", result)

    async def test_extracts_error_with_invalid_tcp_pose(self):
        data = {
            "response": {
                "joint_positions": None,
                "error_feedback": {
                    "error_feedback_name": "UnreachablePose",
                    "invalid_tcp_pose": {"position": [100, 200, 300]},
                },
            }
        }
        result = _parse_error_from_raw(json.dumps(data))
        self.assertIn("UnreachablePose", result)
        self.assertIn("pose=", result)

    async def test_extracts_error_with_joint_index(self):
        data = {
            "response": {
                "joint_positions": None,
                "error_feedback": {
                    "error_feedback_name": "JointLimitExceeded",
                    "joint_index": 3,
                    "joint_position": 7.5,
                },
            }
        }
        result = _parse_error_from_raw(json.dumps(data))
        self.assertIn("joint_index=3", result)
        self.assertIn("joint_position=7.5", result)

    async def test_returns_fallback_error_name(self):
        data = {"response": {"error_feedback_name": "SomeError"}}
        result = _parse_error_from_raw(json.dumps(data))
        self.assertIn("SomeError", result)

    async def test_returns_none_when_no_response_dict(self):
        data = {"other_key": "value"}
        result = _parse_error_from_raw(json.dumps(data))
        self.assertIsNone(result)


class TestFormatErrorFeedback(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_format_with_error_feedback_name(self):
        feedback = MagicMock()
        feedback.actual_instance = feedback
        feedback.error_feedback_name = "CollisionDetected"
        feedback.invalid_tcp_pose = None
        feedback.joint_index = None

        result_inner = MagicMock()
        result_inner.error_feedback = feedback

        result = _format_error_feedback(result_inner)
        self.assertIn("CollisionDetected", result)

    async def test_format_with_invalid_tcp_pose(self):
        pose = MagicMock()
        pose.position = [100, 200, 300]
        feedback = MagicMock()
        feedback.actual_instance = feedback
        feedback.error_feedback_name = "UnreachablePose"
        feedback.invalid_tcp_pose = pose
        feedback.joint_index = None

        result_inner = MagicMock()
        result_inner.error_feedback = feedback

        result = _format_error_feedback(result_inner)
        self.assertIn("UnreachablePose", result)
        self.assertIn("pose.position=", result)

    async def test_format_without_feedback(self):
        result_inner = MagicMock()
        result_inner.error_feedback = None
        result = _format_error_feedback(result_inner)
        self.assertIsNotNone(result)


class TestPlanResult(omni.kit.test.AsyncTestCase):
    async def test_plan_success(self):
        trajectory = MagicMock()
        result = PlanSuccess(joint_trajectory=trajectory)
        self.assertIsInstance(result, PlanSuccess)
        self.assertEqual(result.joint_trajectory, trajectory)

    async def test_plan_failure(self):
        result = PlanFailure(error="Planning failed")
        self.assertIsInstance(result, PlanFailure)
        self.assertEqual(result.error, "Planning failed")


class TestPlanTrajectory(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.api_config = make_mock_api_configuration()

    async def tearDown(self):
        pass

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_plan_trajectory_simple_success(
        self, mock_get_client, mock_fetch_ctx
    ):
        import wandelbots_api_client.v2.models as wb_v2_models

        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description = make_mock_description()
        ctx.joint_position_limits = None
        ctx.collision_setups = None
        mock_fetch_ctx.return_value = ctx

        joint_trajectory = wb_v2_models.JointTrajectory(
            joint_positions=SAMPLE_JOINT_CONFIGS[:2],
            locations=[0.0, 1.0],
            times=[0.0, 2.0],
        )
        mock_response = MagicMock()
        mock_response.response.actual_instance = joint_trajectory

        mock_plan_api = AsyncMock()
        mock_plan_api.plan_trajectory.return_value = mock_response

        motion_commands = [
            wb_v2_models.MotionCommand(
                path=wb_v2_models.MotionCommandPath(
                    wb_v2_models.PathCartesianPTP(
                        target_pose=wb_v2_models.Pose(
                            position=[600.0, 200.0, 300.0],
                            orientation=[0.0, 3.14, 0.0],
                        )
                    )
                )
            )
        ]

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_trajectory(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                motion_commands=motion_commands,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
                tcp_name=SAMPLE_TCP_NAME,
            )

        self.assertIsInstance(result, PlanSuccess)
        self.assertEqual(result.joint_trajectory, joint_trajectory)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_plan_trajectory_api_exception_with_raw_fallback(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description = make_mock_description()
        ctx.joint_position_limits = None
        ctx.collision_setups = None
        mock_fetch_ctx.return_value = ctx

        mock_plan_api = AsyncMock()
        mock_plan_api.plan_trajectory.side_effect = Exception("Deserialization failed")

        error_json = json.dumps(
            {
                "response": {
                    "joint_positions": None,
                    "error_feedback": {"error_feedback_name": "UnreachablePose"},
                }
            }
        ).encode()
        mock_raw_response = AsyncMock()
        mock_raw_response.read.return_value = error_json
        mock_plan_api.plan_trajectory_without_preload_content.return_value = (
            mock_raw_response
        )

        import wandelbots_api_client.v2.models as wb_v2_models

        motion_commands = [
            wb_v2_models.MotionCommand(
                path=wb_v2_models.MotionCommandPath(
                    wb_v2_models.PathCartesianPTP(
                        target_pose=wb_v2_models.Pose(
                            position=[600.0, 200.0, 300.0],
                            orientation=[0.0, 3.14, 0.0],
                        )
                    )
                )
            )
        ]

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_trajectory(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                motion_commands=motion_commands,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
            )

        self.assertIsInstance(result, PlanFailure)
        self.assertIn("UnreachablePose", result.error)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_plan_trajectory_api_exception_propagates(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description = make_mock_description()
        ctx.joint_position_limits = None
        ctx.collision_setups = None
        mock_fetch_ctx.return_value = ctx

        mock_plan_api = AsyncMock()
        mock_plan_api.plan_trajectory.side_effect = Exception("API timeout")

        mock_raw_response = AsyncMock()
        mock_raw_response.read.return_value = b"not valid json"
        mock_plan_api.plan_trajectory_without_preload_content.return_value = (
            mock_raw_response
        )

        import wandelbots_api_client.v2.models as wb_v2_models

        motion_commands = [
            wb_v2_models.MotionCommand(
                path=wb_v2_models.MotionCommandPath(
                    wb_v2_models.PathCartesianPTP(
                        target_pose=wb_v2_models.Pose(
                            position=[600.0, 200.0, 300.0],
                            orientation=[0.0, 3.14, 0.0],
                        )
                    )
                )
            )
        ]

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            with self.assertRaises(Exception) as cm:
                await plan_trajectory(
                    self.api_config,
                    SAMPLE_CELL,
                    SAMPLE_CONTROLLER,
                    SAMPLE_MOTION_GROUP,
                    motion_commands=motion_commands,
                    start_joint_position=SAMPLE_JOINT_CONFIGS[0],
                )
            self.assertIn("API timeout", str(cm.exception))


def _cmd(pos):
    import wandelbots_api_client.v2.models as wb_v2_models

    return wb_v2_models.MotionCommand(
        path=wb_v2_models.MotionCommandPath(
            wb_v2_models.PathCartesianPTP(
                target_pose=wb_v2_models.Pose(
                    position=pos, orientation=[0.0, 3.14, 0.0]
                )
            )
        )
    )


def _jt(joint_positions):
    import wandelbots_api_client.v2.models as wb_v2_models

    return wb_v2_models.JointTrajectory(
        joint_positions=joint_positions,
        locations=[float(i) for i in range(len(joint_positions))],
        times=[float(i) for i in range(len(joint_positions))],
    )


def _plan_resp(joint_trajectory):
    resp = MagicMock()
    resp.response.actual_instance = joint_trajectory
    return resp


class TestPlanTrajectorySegments(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.api_config = make_mock_api_configuration()

    async def tearDown(self):
        pass

    def _ctx(self):
        ctx = MagicMock()
        ctx.model_name = "UR10e"
        ctx.tcp_offset = None
        ctx.description = make_mock_description()
        ctx.joint_position_limits = None
        ctx.collision_setups = None
        return ctx

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_two_segments_chain_and_merge(self, mock_get_client, mock_fetch_ctx):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()

        seg1_last = SAMPLE_JOINT_CONFIGS[1]
        jt1 = _jt([SAMPLE_JOINT_CONFIGS[0], seg1_last])
        jt2 = _jt([SAMPLE_JOINT_CONFIGS[2], SAMPLE_JOINT_CONFIGS[0]])
        merged = _jt(SAMPLE_JOINT_CONFIGS)

        mock_plan_api = AsyncMock()
        mock_plan_api.plan_trajectory.side_effect = [_plan_resp(jt1), _plan_resp(jt2)]
        merge_resp = MagicMock()
        merge_resp.joint_trajectory = merged
        mock_plan_api.merge_trajectories.return_value = merge_resp

        segments = [
            TrajectorySegmentSpec(
                tcp_name="tcp_a", motion_commands=[_cmd([600, 0, 300])]
            ),
            TrajectorySegmentSpec(
                tcp_name="tcp_b", motion_commands=[_cmd([600, 200, 300])]
            ),
        ]

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_trajectory_segments(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                segments=segments,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
            )

        self.assertIsInstance(result, PlanSuccess)
        self.assertEqual(result.joint_trajectory, merged)
        # one plan call per segment
        self.assertEqual(mock_plan_api.plan_trajectory.call_count, 2)
        # second segment starts where the first ended (start-joint chaining)
        second_req = mock_plan_api.plan_trajectory.call_args_list[1].kwargs[
            "plan_trajectory_request"
        ]
        self.assertEqual(second_req.start_joint_position, seg1_last)
        # exactly one merge with one segment per planned trajectory
        self.assertEqual(mock_plan_api.merge_trajectories.call_count, 1)
        merge_req = mock_plan_api.merge_trajectories.call_args.kwargs[
            "merge_trajectories_request"
        ]
        self.assertEqual(len(merge_req.trajectory_segments), 2)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_single_segment_no_merge(self, mock_get_client, mock_fetch_ctx):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()

        jt = _jt([SAMPLE_JOINT_CONFIGS[0], SAMPLE_JOINT_CONFIGS[1]])
        mock_plan_api = AsyncMock()
        mock_plan_api.plan_trajectory.return_value = _plan_resp(jt)

        segments = [
            TrajectorySegmentSpec(
                tcp_name="tcp_a", motion_commands=[_cmd([600, 0, 300])]
            )
        ]
        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_trajectory_segments(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                segments=segments,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
            )

        self.assertIsInstance(result, PlanSuccess)
        self.assertEqual(result.joint_trajectory, jt)
        self.assertEqual(mock_plan_api.plan_trajectory.call_count, 1)
        mock_plan_api.merge_trajectories.assert_not_called()

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_segment_failure_short_circuits(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()

        # First segment returns a non-JointTrajectory instance -> PlanFailure.
        fail_resp = MagicMock()
        fail_resp.response.actual_instance = MagicMock()  # not a JointTrajectory
        mock_plan_api = AsyncMock()
        mock_plan_api.plan_trajectory.return_value = fail_resp
        # raw fallback path returns no parsable error
        raw = AsyncMock()
        raw.read.return_value = b""
        mock_plan_api.plan_trajectory_without_preload_content.return_value = raw

        segments = [
            TrajectorySegmentSpec(
                tcp_name="tcp_a", motion_commands=[_cmd([600, 0, 300])]
            ),
            TrajectorySegmentSpec(
                tcp_name="tcp_b", motion_commands=[_cmd([600, 200, 300])]
            ),
        ]
        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_trajectory_segments(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                segments=segments,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
            )

        self.assertIsInstance(result, PlanFailure)
        self.assertIn("Segment 1/2", result.error)
        # stopped after the first (failing) segment; no merge
        self.assertEqual(mock_plan_api.plan_trajectory.call_count, 1)
        mock_plan_api.merge_trajectories.assert_not_called()
