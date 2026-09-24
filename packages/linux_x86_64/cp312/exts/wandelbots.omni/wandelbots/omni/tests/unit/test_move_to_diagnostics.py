"""Unit tests for the move-to diagnostics helpers.

``execute_move_to`` returning True used to cover three different outcomes: the
robot moved and arrived, the planner returned a degenerate trajectory that was
assumed to mean "already at target", and the robot moved on the controller while
the articulation in the scene never followed. Those are told apart by the joint
target extracted from the motion command, the residual against the state read
back afterwards, and the stream flags - so each of those is tested here.
"""

import omni.kit.test

import wandelbots_api_client.v2 as wb

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.teaching.move_to_service import (
    MoveToDiagnostics,
    _max_joint_residual,
    _target_joint_position,
    _tcp_reached,
    _tcp_residuals,
    _target_tcp_pose,
)


def _joint_ptp(target: list[float]) -> wb.MotionCommand:
    return wb.MotionCommand(
        path=wb.MotionCommandPath(
            wb.PathJointPTP(
                target_joint_position=target,
                path_definition_name="PathJointPTP",
            )
        )
    )


class TestTargetJointPosition(omni.kit.test.AsyncTestCase):
    async def test_joint_ptp_target_is_extracted(self):
        command = _joint_ptp([0.1, -0.2, 0.3])
        self.assertEqual(_target_joint_position(command), [0.1, -0.2, 0.3])

    async def test_command_without_joint_target_yields_none(self):
        class _CartesianPath:
            """Stand-in for any path that has no joint target."""

            target_pose = object()

        class _Command:
            path = _CartesianPath()

        self.assertIsNone(_target_joint_position(_Command()))

    async def test_empty_target_yields_none(self):
        """An empty list must not be mistaken for a verifiable target."""
        self.assertIsNone(_target_joint_position(_joint_ptp([])))

    async def test_path_without_a_oneof_wrapper_is_read_directly(self):
        class _Path:
            target_joint_position = [0.5]

        class _Command:
            path = _Path()

        self.assertEqual(_target_joint_position(_Command()), [0.5])


class TestMaxJointResidual(omni.kit.test.AsyncTestCase):
    async def test_largest_per_axis_difference(self):
        residual = _max_joint_residual([0.0, 1.0, 2.0], [0.01, 1.0, 1.95])
        self.assertAlmostEqual(residual, 0.05, places=9)

    async def test_sign_is_ignored(self):
        self.assertAlmostEqual(_max_joint_residual([0.0], [-0.25]), 0.25, places=9)

    async def test_missing_or_mismatched_input_is_not_verifiable(self):
        self.assertIsNone(_max_joint_residual(None, [0.0]))
        self.assertIsNone(_max_joint_residual([0.0], None))
        self.assertIsNone(_max_joint_residual([], []))
        self.assertIsNone(_max_joint_residual([0.0, 0.0], [0.0]))


class TestSceneFollowed(omni.kit.test.AsyncTestCase):
    async def test_live_stream_means_the_scene_followed(self):
        diagnostics = MoveToDiagnostics(stream_live=True, streamable=True)
        self.assertTrue(diagnostics.scene_followed)

    async def test_nothing_in_the_scene_follows_this_motion_group(self):
        """A real controller has no stream to miss, so this is not a problem."""
        diagnostics = MoveToDiagnostics(stream_live=False, streamable=False)
        self.assertTrue(diagnostics.scene_followed)

    async def test_streamable_robot_without_a_live_stream_did_not_follow(self):
        diagnostics = MoveToDiagnostics(stream_live=False, streamable=True)
        self.assertFalse(diagnostics.scene_followed)

    async def test_unchecked_stream_on_a_streamable_robot_is_reported(self):
        """auto_play_simulation off leaves stream_live None - still unverified."""
        diagnostics = MoveToDiagnostics(stream_live=None, streamable=True)
        self.assertFalse(diagnostics.scene_followed)

    async def test_defaults_do_not_claim_arrival(self):
        diagnostics = MoveToDiagnostics()
        self.assertIsNone(diagnostics.reached)
        self.assertIsNone(diagnostics.max_joint_residual)
        self.assertFalse(diagnostics.already_at_target)
        self.assertEqual(diagnostics.failure, "")


def _cartesian_ptp(position: list[float], orientation: list[float]) -> wb.MotionCommand:
    return wb.MotionCommand(
        path=wb.MotionCommandPath(
            wb.PathCartesianPTP(
                target_pose=wb.models.Pose(position=position, orientation=orientation),
                path_definition_name="PathCartesianPTP",
            )
        )
    )


class TestTargetTcpPose(omni.kit.test.AsyncTestCase):
    """A Cartesian or line move has no joint target, only a pose.

    Without reading it, a degenerate plan for such a move was reported as
    "already at target" with nothing at all to back the claim.
    """

    async def test_cartesian_target_is_extracted(self):
        command = _cartesian_ptp([100.0, 0.0, 50.0], [0.0, 0.0, 0.5])

        self.assertEqual([100.0, 0.0, 50.0, 0.0, 0.0, 0.5], _target_tcp_pose(command))

    async def test_a_joint_ptp_has_no_tcp_target(self):
        self.assertIsNone(_target_tcp_pose(_joint_ptp([0.1, 0.2])))


class TestTcpResiduals(omni.kit.test.AsyncTestCase):
    async def test_the_same_pose_has_no_gap(self):
        target = [100.0, 0.0, 50.0, 0.0, 0.0, 0.5]
        distance_mm, angle_rad = _tcp_residuals(target, WSPose(pose=target))

        self.assertAlmostEqual(0.0, distance_mm, places=9)
        self.assertAlmostEqual(0.0, angle_rad, places=9)

    async def test_a_shifted_pose_reports_the_distance(self):
        distance_mm, angle_rad = _tcp_residuals(
            [100.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            WSPose(pose=[103.0, 4.0, 0.0, 0.0, 0.0, 0.0]),
        )

        self.assertAlmostEqual(5.0, distance_mm, places=6)
        self.assertAlmostEqual(0.0, angle_rad, places=9)

    async def test_a_turned_pose_reports_the_angle(self):
        _distance_mm, angle_rad = _tcp_residuals(
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            WSPose(pose=[0.0, 0.0, 0.0, 0.0, 0.0, 0.2]),
        )

        self.assertAlmostEqual(0.2, angle_rad, places=6)

    async def test_a_missing_target_cannot_be_compared(self):
        self.assertIsNone(_tcp_residuals(None, WSPose(pose=[0.0] * 6)))
        self.assertIsNone(_tcp_residuals([0.0] * 6, None))


class TestTcpReached(omni.kit.test.AsyncTestCase):
    async def test_inside_both_tolerances_counts_as_arrived(self):
        self.assertTrue(_tcp_reached((0.5, 1e-3)))

    async def test_too_far_away_does_not(self):
        self.assertFalse(_tcp_reached((5.0, 1e-3)))

    async def test_turned_too_far_does_not_either(self):
        self.assertFalse(_tcp_reached((0.1, 0.5)))

    async def test_nothing_to_judge_is_not_an_answer(self):
        """None must not read as success: that is the bug being fixed."""
        self.assertIsNone(_tcp_reached(None))
