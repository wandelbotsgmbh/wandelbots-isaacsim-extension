"""Unit tests for planner_utils planning functions and error parsing utilities."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import omni.kit.test
import wandelbots_api_client.v2.models as wb_v2_models

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
    plan_collision_free,
    joint_position_from_failed_response,
    plan_failure_from_raw,
    plan_failure_from_response,
    plan_trajectory,
    plan_trajectory_segments,
)


def _failed_response(feedback, joint_trajectory=None, location=1.3):
    return wb_v2_models.PlanTrajectoryFailedResponse(
        error_feedback=wb_v2_models.PlanTrajectoryFailedResponseErrorFeedback(feedback),
        error_location_on_trajectory=location,
        joint_trajectory=joint_trajectory,
    )


def _out_of_workspace():
    return wb_v2_models.FeedbackOutOfWorkspace(
        error_feedback_name="FeedbackOutOfWorkspace"
    )


class TestParsePlanTrajectoryError(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_returns_none_for_invalid_json(self):
        self.assertIsNone(plan_failure_from_raw("not json"))
        self.assertIsNone(plan_failure_from_raw(None))
        self.assertIsNone(plan_failure_from_raw(b""))

    async def test_returns_none_for_success_response(self):
        data = {"response": {"joint_positions": [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]]}}
        self.assertIsNone(plan_failure_from_raw(json.dumps(data)))

    async def test_extracts_error_feedback_name(self):
        data = {
            "response": {
                "joint_positions": None,
                "error_feedback": {"error_feedback_name": "JointLimitViolation"},
            }
        }
        result = plan_failure_from_raw(json.dumps(data)).error
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
        result = plan_failure_from_raw(json.dumps(data)).error
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
        result = plan_failure_from_raw(json.dumps(data)).error
        self.assertIn("joint_index=3", result)
        self.assertIn("joint_position=7.5", result)

    async def test_returns_fallback_error_name(self):
        data = {"response": {"error_feedback_name": "SomeError"}}
        result = plan_failure_from_raw(json.dumps(data)).error
        self.assertIn("SomeError", result)

    async def test_returns_none_when_no_response_dict(self):
        data = {"other_key": "value"}
        self.assertIsNone(plan_failure_from_raw(json.dumps(data)))


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

        result = plan_failure_from_response(result_inner).error
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

        result = plan_failure_from_response(result_inner).error
        self.assertIn("UnreachablePose", result)
        self.assertIn("pose.position=", result)

    async def test_format_without_feedback(self):
        result_inner = MagicMock()
        result_inner.error_feedback = None
        result = plan_failure_from_response(result_inner).error
        self.assertIsNotNone(result)


class TestPlanResult(omni.kit.test.AsyncTestCase):
    async def test_plan_success(self):
        trajectory = MagicMock()
        result = PlanSuccess(joint_trajectory=trajectory)
        self.assertIsInstance(result, PlanSuccess)
        self.assertEqual(result.joint_trajectory, trajectory)
        self.assertIsNone(result.via_joint_positions)

    async def test_plan_failure(self):
        result = PlanFailure(error="Planning failed")
        self.assertIsInstance(result, PlanFailure)
        self.assertEqual(result.error, "Planning failed")
        self.assertIsNone(result.failed_joint_position)
        self.assertIsNone(result.partial_joint_positions)
        self.assertIsNone(result.error_location_on_trajectory)
        self.assertIsNone(result.segment_index)


class TestJointPositionFromFailedResponse(omni.kit.test.AsyncTestCase):
    async def test_prefers_feedback_joint_position(self):
        feedback = wb_v2_models.FeedbackJointLimitExceeded(
            joint_index=2,
            joint_position=SAMPLE_JOINT_CONFIGS[2],
            error_feedback_name="FeedbackJointLimitExceeded",
        )
        response = _failed_response(feedback, _jt(SAMPLE_JOINT_CONFIGS[:2]))

        self.assertEqual(
            joint_position_from_failed_response(response), SAMPLE_JOINT_CONFIGS[2]
        )

    async def test_singularity_uses_singular_joint_position(self):
        feedback = wb_v2_models.FeedbackSingularity(
            singular_joint_position=SAMPLE_JOINT_CONFIGS[1],
            error_feedback_name="FeedbackSingularity",
        )

        self.assertEqual(
            joint_position_from_failed_response(_failed_response(feedback)),
            SAMPLE_JOINT_CONFIGS[1],
        )

    async def test_falls_back_to_last_trajectory_sample(self):
        response = _failed_response(_out_of_workspace(), _jt(SAMPLE_JOINT_CONFIGS[:2]))

        self.assertEqual(
            joint_position_from_failed_response(response), SAMPLE_JOINT_CONFIGS[1]
        )

    async def test_returns_none_without_joint_data(self):
        response = _failed_response(_out_of_workspace())

        self.assertIsNone(joint_position_from_failed_response(response))
        self.assertIsNone(joint_position_from_failed_response(None))

    async def test_plan_failure_from_response_carries_all_fields(self):
        feedback = wb_v2_models.FeedbackCollision(
            joint_position=SAMPLE_JOINT_CONFIGS[2],
            error_feedback_name="FeedbackCollision",
        )
        response = _failed_response(
            feedback, _jt(SAMPLE_JOINT_CONFIGS[:2]), location=1.3
        )

        failure = plan_failure_from_response(response)

        self.assertIn("FeedbackCollision", failure.error)
        self.assertEqual(failure.failed_joint_position, SAMPLE_JOINT_CONFIGS[2])
        self.assertEqual(failure.partial_joint_positions, SAMPLE_JOINT_CONFIGS[:2])
        self.assertAlmostEqual(failure.error_location_on_trajectory, 1.3, places=9)
        self.assertIsNone(failure.segment_index)

    async def test_plan_failure_from_response_tolerates_unknown_schema(self):
        failure = plan_failure_from_response(None)

        self.assertIn("did not match", failure.error)
        self.assertIsNone(failure.failed_joint_position)
        self.assertIsNone(failure.partial_joint_positions)


class TestPlanFailureFromRaw(omni.kit.test.AsyncTestCase):
    def _raw(self, error_feedback, joint_trajectory=None, location=0.5):
        response = {
            "joint_positions": None,
            "error_feedback": error_feedback,
            "error_location_on_trajectory": location,
        }
        if joint_trajectory is not None:
            response["joint_trajectory"] = {"joint_positions": joint_trajectory}
        return json.dumps({"response": response})

    async def test_extracts_joint_position_list(self):
        raw = self._raw(
            {"error_feedback_name": "FeedbackCollision", "joint_position": [0.1, 0.2]}
        )

        failure = plan_failure_from_raw(raw)

        self.assertIn("FeedbackCollision", failure.error)
        self.assertEqual(failure.failed_joint_position, [0.1, 0.2])
        self.assertAlmostEqual(failure.error_location_on_trajectory, 0.5, places=9)

    async def test_ignores_scalar_joint_position(self):
        raw = self._raw(
            {
                "error_feedback_name": "JointLimitExceeded",
                "joint_index": 3,
                "joint_position": 7.5,
            }
        )

        failure = plan_failure_from_raw(raw)

        self.assertIn("joint_index=3", failure.error)
        self.assertIsNone(failure.failed_joint_position)

    async def test_falls_back_to_trajectory_last_sample(self):
        raw = self._raw(
            {"error_feedback_name": "FeedbackOutOfWorkspace"},
            joint_trajectory=SAMPLE_JOINT_CONFIGS[:2],
        )

        failure = plan_failure_from_raw(raw)

        self.assertEqual(failure.failed_joint_position, SAMPLE_JOINT_CONFIGS[1])
        self.assertEqual(failure.partial_joint_positions, SAMPLE_JOINT_CONFIGS[:2])

    async def test_returns_none_for_success_response(self):
        data = {"response": {"joint_positions": [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]]}}

        self.assertIsNone(plan_failure_from_raw(json.dumps(data)))
        self.assertIsNone(plan_failure_from_raw("not json"))


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
        ctx.mounting = None
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
                        ),
                        path_definition_name="PathCartesianPTP",
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
    async def test_plan_trajectory_failure_carries_joint_data(
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
        ctx.mounting = None
        mock_fetch_ctx.return_value = ctx

        feedback = wb_v2_models.FeedbackCollision(
            joint_position=SAMPLE_JOINT_CONFIGS[2],
            error_feedback_name="FeedbackCollision",
        )
        mock_plan_api = AsyncMock()
        mock_plan_api.plan_trajectory.return_value = _plan_resp(
            _failed_response(feedback, _jt(SAMPLE_JOINT_CONFIGS[:2]), location=1.3)
        )

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_trajectory(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                motion_commands=[_cmd([600.0, 200.0, 300.0])],
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
                tcp_name=SAMPLE_TCP_NAME,
            )

        self.assertIsInstance(result, PlanFailure)
        self.assertIn("FeedbackCollision", result.error)
        self.assertEqual(result.failed_joint_position, SAMPLE_JOINT_CONFIGS[2])
        self.assertEqual(result.partial_joint_positions, SAMPLE_JOINT_CONFIGS[:2])
        self.assertAlmostEqual(result.error_location_on_trajectory, 1.3, places=9)

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
        ctx.mounting = None
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
                        ),
                        path_definition_name="PathCartesianPTP",
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
        ctx.mounting = None
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
                        ),
                        path_definition_name="PathCartesianPTP",
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
                ),
                path_definition_name="PathCartesianPTP",
            )
        )
    )


def _joint_cmd(joints):
    return wb_v2_models.MotionCommand(
        path=wb_v2_models.MotionCommandPath(
            wb_v2_models.PathJointPTP(
                target_joint_position=joints, path_definition_name="PathJointPTP"
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
        ctx.mounting = None
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
        # error_feedback must be a real None: an auto-created child mock would
        # leak a MagicMock into _format_error_feedback's string assembly.
        fail_resp = MagicMock()
        fail_resp.response.actual_instance = MagicMock(error_feedback=None)
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
        self.assertEqual(result.segment_index, 0)
        self.assertIsNone(result.partial_joint_positions)
        # stopped after the first (failing) segment; no merge
        self.assertEqual(mock_plan_api.plan_trajectory.call_count, 1)
        mock_plan_api.merge_trajectories.assert_not_called()

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_second_segment_failure_carries_partial_and_index(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()

        first = _jt([SAMPLE_JOINT_CONFIGS[0], SAMPLE_JOINT_CONFIGS[1]])
        feedback = wb_v2_models.FeedbackCollision(
            joint_position=SAMPLE_JOINT_CONFIGS[2],
            error_feedback_name="FeedbackCollision",
        )
        failed = _failed_response(
            feedback,
            _jt([SAMPLE_JOINT_CONFIGS[1], SAMPLE_JOINT_CONFIGS[2]]),
            location=0.4,
        )
        mock_plan_api = AsyncMock()
        mock_plan_api.plan_trajectory.side_effect = [
            _plan_resp(first),
            _plan_resp(failed),
        ]

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
        self.assertIn("Segment 2/2", result.error)
        self.assertIn("FeedbackCollision", result.error)
        self.assertEqual(result.segment_index, 1)
        self.assertEqual(result.failed_joint_position, SAMPLE_JOINT_CONFIGS[2])
        # start-to-error: the planned first segment plus the failing partial
        self.assertEqual(
            result.partial_joint_positions,
            [
                SAMPLE_JOINT_CONFIGS[0],
                SAMPLE_JOINT_CONFIGS[1],
                SAMPLE_JOINT_CONFIGS[1],
                SAMPLE_JOINT_CONFIGS[2],
            ],
        )
        self.assertAlmostEqual(result.error_location_on_trajectory, 0.4, places=9)
        mock_plan_api.merge_trajectories.assert_not_called()

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_collision_setup_applied_to_segment_setups(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        ctx = self._ctx()
        # A real CollisionSetup: MotionGroupSetup validates on assignment and
        # rejects a MagicMock for its collision_setups dict.
        ctx.collision_setups = {"scene": wb_v2_models.CollisionSetup()}
        mock_fetch_ctx.return_value = ctx

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
                collision_setup_name="scene",
            )

        self.assertIsInstance(result, PlanSuccess)
        # The collision scene is attached to the motion group setup so normal
        # (motion-type) planning respects it.
        req = mock_plan_api.plan_trajectory.call_args.kwargs["plan_trajectory_request"]
        self.assertEqual(req.motion_group_setup.collision_setups, ctx.collision_setups)
        # fetch_motion_group_context was asked for the named collision setup
        self.assertEqual(
            mock_fetch_ctx.call_args.kwargs.get("collision_setup_name"), "scene"
        )

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_no_collision_setup_leaves_setups_none(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()  # collision_setups = None

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
        req = mock_plan_api.plan_trajectory.call_args.kwargs["plan_trajectory_request"]
        self.assertIsNone(req.motion_group_setup.collision_setups)


def _cf_resp(joint_trajectory, motion_commands):
    """A plan-collision-free response: a JointTrajectory plus the motion
    commands that produced it (a direct leg, or a leg plus a via-point when
    the segment needed to route around an obstacle)."""
    resp = MagicMock()
    resp.response.actual_instance = joint_trajectory
    resp.motion_commands = motion_commands
    return resp


class TestPlanCollisionFree(omni.kit.test.AsyncTestCase):
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
        ctx.mounting = None
        return ctx

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_segments_concatenate_into_one_plan_trajectory_call(
        self, mock_get_client, mock_fetch_ctx
    ):
        """Merge-trajectories must never be called: it rejects a valid plan
        whenever a segment needed a via-point (NOVA-side locationVector bug).
        Each segment's own motion commands are concatenated and handed to a
        single plan-trajectory call instead."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()

        seg1_last = SAMPLE_JOINT_CONFIGS[1]
        seg2_last = SAMPLE_JOINT_CONFIGS[2]
        jt1 = _jt([SAMPLE_JOINT_CONFIGS[0], seg1_last])
        jt2 = _jt([seg1_last, seg2_last])
        # Segment 0 needed a via-point to route around an obstacle (two
        # motion commands); segment 1 was a direct connection (one command).
        commands1 = [_cmd([600, 0, 300]), _cmd([600, 100, 300])]
        commands2 = [_cmd([600, 200, 300])]

        mock_plan_api = AsyncMock()
        mock_plan_api.plan_collision_free.side_effect = [
            _cf_resp(jt1, commands1),
            _cf_resp(jt2, commands2),
        ]
        final_jt = _jt(SAMPLE_JOINT_CONFIGS)
        mock_plan_api.plan_trajectory.return_value = _plan_resp(final_jt)

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_collision_free(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
                target_configs=[[seg1_last], [seg2_last]],
            )

        self.assertIsInstance(result, PlanSuccess)
        self.assertEqual(result.joint_trajectory, final_jt)
        mock_plan_api.merge_trajectories.assert_not_called()
        self.assertEqual(mock_plan_api.plan_trajectory.call_count, 1)
        final_req = mock_plan_api.plan_trajectory.call_args.kwargs[
            "plan_trajectory_request"
        ]
        self.assertEqual(final_req.motion_commands, commands1 + commands2)
        # the original start, not the last segment's chained start
        self.assertEqual(final_req.start_joint_position, SAMPLE_JOINT_CONFIGS[0])

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_via_points_are_returned_with_the_plan(
        self, mock_get_client, mock_fetch_ctx
    ):
        """The joint legs a segment inserted before its target are via points;
        the target itself is the user's pose and not one of them."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()

        via = [0.5, -1.0, 1.2, 0.0, 1.5, 0.0]
        jt1 = _jt([SAMPLE_JOINT_CONFIGS[0], via, SAMPLE_JOINT_CONFIGS[1]])
        jt2 = _jt([SAMPLE_JOINT_CONFIGS[1], SAMPLE_JOINT_CONFIGS[2]])
        mock_plan_api = AsyncMock()
        mock_plan_api.plan_collision_free.side_effect = [
            _cf_resp(jt1, [_joint_cmd(via), _joint_cmd(SAMPLE_JOINT_CONFIGS[1])]),
            _cf_resp(jt2, [_joint_cmd(SAMPLE_JOINT_CONFIGS[2])]),
        ]
        mock_plan_api.plan_trajectory.return_value = _plan_resp(
            _jt(SAMPLE_JOINT_CONFIGS)
        )

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_collision_free(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
                target_configs=[[SAMPLE_JOINT_CONFIGS[1]], [SAMPLE_JOINT_CONFIGS[2]]],
            )

        self.assertIsInstance(result, PlanSuccess)
        self.assertEqual(result.via_joint_positions, [via])

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_direct_segments_have_no_via_points(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()

        jt1 = _jt([SAMPLE_JOINT_CONFIGS[0], SAMPLE_JOINT_CONFIGS[1]])
        mock_plan_api = AsyncMock()
        mock_plan_api.plan_collision_free.return_value = _cf_resp(
            jt1, [_joint_cmd(SAMPLE_JOINT_CONFIGS[1])]
        )
        mock_plan_api.plan_trajectory.return_value = _plan_resp(jt1)

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_collision_free(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
                target_configs=[[SAMPLE_JOINT_CONFIGS[1]]],
            )

        self.assertIsInstance(result, PlanSuccess)
        self.assertIsNone(result.via_joint_positions)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_segment_failure_short_circuits_before_final_plan(
        self, mock_get_client, mock_fetch_ctx
    ):
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()

        # error_feedback must be a real None: an auto-created child mock would
        # leak a MagicMock into _format_error_feedback's string assembly.
        fail_resp = MagicMock()
        fail_resp.response.actual_instance = MagicMock(error_feedback=None)
        mock_plan_api = AsyncMock()
        mock_plan_api.plan_collision_free.return_value = fail_resp
        raw = AsyncMock()
        raw.read.return_value = b""
        mock_plan_api.plan_collision_free_without_preload_content.return_value = raw

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_collision_free(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
                target_configs=[[SAMPLE_JOINT_CONFIGS[1]]],
            )

        self.assertIsInstance(result, PlanFailure)
        mock_plan_api.plan_trajectory.assert_not_called()
        mock_plan_api.merge_trajectories.assert_not_called()

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_missing_motion_commands_treated_as_segment_failure(
        self, mock_get_client, mock_fetch_ctx
    ):
        """motion_commands is optional on the response model. A trajectory
        without it can't be replayed through plan-trajectory, so it must fail
        the segment rather than silently drop that leg from the final plan -
        which would replay a shorter, uninspected path than the one that was
        actually collision-checked."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()

        jt = _jt([SAMPLE_JOINT_CONFIGS[0], SAMPLE_JOINT_CONFIGS[1]])
        mock_plan_api = AsyncMock()
        mock_plan_api.plan_collision_free.return_value = _cf_resp(jt, None)

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            result = await plan_collision_free(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
                target_configs=[[SAMPLE_JOINT_CONFIGS[1]]],
            )

        self.assertIsInstance(result, PlanFailure)
        mock_plan_api.plan_trajectory.assert_not_called()
        mock_plan_api.merge_trajectories.assert_not_called()

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.service.helpers.fetch_motion_group_context"
    )
    @patch("wandelbots.omni.ui.tool.planner_utils.get_api_client_from_config")
    async def test_limits_override_applied_only_to_last_command_of_segment(
        self, mock_get_client, mock_fetch_ctx
    ):
        """global_limits_override applies at the end of each pose-to-pose
        segment - i.e. at the actual target pose - not at an internal
        via-point the segment's own collision-free solution may have
        inserted, matching the old per-segment MergeTrajectoriesSegment
        behavior."""
        mock_client = AsyncMock()
        mock_get_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_get_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_fetch_ctx.return_value = self._ctx()

        seg_last = SAMPLE_JOINT_CONFIGS[1]
        jt = _jt([SAMPLE_JOINT_CONFIGS[0], seg_last])
        via_point_command = _cmd([600, 0, 300])
        target_command = _cmd([600, 100, 300])

        mock_plan_api = AsyncMock()
        mock_plan_api.plan_collision_free.return_value = _cf_resp(
            jt, [via_point_command, target_command]
        )
        mock_plan_api.plan_trajectory.return_value = _plan_resp(jt)

        with patch(
            "wandelbots.omni.ui.tool.planner_utils.wb.TrajectoryPlanningApi",
            return_value=mock_plan_api,
        ):
            await plan_collision_free(
                self.api_config,
                SAMPLE_CELL,
                SAMPLE_CONTROLLER,
                SAMPLE_MOTION_GROUP,
                start_joint_position=SAMPLE_JOINT_CONFIGS[0],
                target_configs=[[seg_last]],
                global_limits_override={"tcp_velocity_limit": 100},
            )

        final_req = mock_plan_api.plan_trajectory.call_args.kwargs[
            "plan_trajectory_request"
        ]
        self.assertIsNone(final_req.motion_commands[0].limits_override)
        self.assertIsNotNone(final_req.motion_commands[1].limits_override)
        self.assertEqual(
            final_req.motion_commands[1].limits_override.tcp_velocity_limit, 100
        )
