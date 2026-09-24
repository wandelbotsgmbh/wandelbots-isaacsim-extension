"""Unit tests for the Reachability Envelope window's indicators."""

from __future__ import annotations

import omni.kit.test
from pxr import Sdf

from wandelbots.omni.ui.tool.reachability_envelope.reachability_envelope_window import (
    _MOTION_BLOCKED,
    _MOTION_CHECKING,
    _MOTION_IDLE,
    _MOTION_PLANNABLE,
    _VERDICT_IDLE,
    _VERDICT_REACHABLE,
    _VERDICT_SOLVING,
    _VERDICT_UNREACHABLE,
    envelope_is_wanted,
    format_tcp_pose,
    motion_chip,
    state_chip,
)
from wandelbots.omni.utils.prims import (
    is_pose_xform_op,
    xform_op_carries_rotation,
)

_TOLERANCE = 1e-6


class TestStateChip(omni.kit.test.AsyncTestCase):
    async def test_idle_without_a_selection(self):
        caption, _color = state_chip(selected=False, solved=False, answered=False)
        self.assertEqual(caption, _VERDICT_IDLE)

    async def test_solving_until_the_first_answer_lands(self):
        caption, _color = state_chip(selected=True, solved=False, answered=False)
        self.assertEqual(caption, _VERDICT_SOLVING)

    async def test_unreachable_once_the_answer_is_in(self):
        caption, _color = state_chip(selected=True, solved=False, answered=True)
        self.assertEqual(caption, _VERDICT_UNREACHABLE)

    async def test_reachable_with_a_solution(self):
        caption, _color = state_chip(selected=True, solved=True, answered=True)
        self.assertEqual(caption, _VERDICT_REACHABLE)


class TestMotionChip(omni.kit.test.AsyncTestCase):
    async def test_idle_without_an_ik_solution(self):
        """A moving pose has no solution yet, so it must not claim a failure."""
        caption, _color = motion_chip(solved=False, checking=False, plannable=False)
        self.assertEqual(caption, _MOTION_IDLE)

    async def test_checking_while_the_plan_request_is_out(self):
        caption, _color = motion_chip(solved=True, checking=True, plannable=None)
        self.assertEqual(caption, _MOTION_CHECKING)

    async def test_checking_before_an_answer_exists(self):
        caption, _color = motion_chip(solved=True, checking=False, plannable=None)
        self.assertEqual(caption, _MOTION_CHECKING)

    async def test_plannable(self):
        caption, _color = motion_chip(solved=True, checking=False, plannable=True)
        self.assertEqual(caption, _MOTION_PLANNABLE)

    async def test_not_plannable(self):
        caption, _color = motion_chip(solved=True, checking=False, plannable=False)
        self.assertEqual(caption, _MOTION_BLOCKED)


class TestTransformAttribute(omni.kit.test.AsyncTestCase):
    """The window and the envelope overlay share one placement predicate.

    They used to carry a copy each, and the overlay's copy missed
    ``xformOp:rotateXYZ`` - the op the viewport gizmo writes for a prim with
    Euler rotation - so the cloud went stale on exactly the turns the window
    re-solved for.
    """

    async def test_placement_attributes_wake_the_solver(self):
        for suffix in (":translate", ":transform", ":orient", ":rotateXYZ", ":scale"):
            self.assertTrue(
                is_pose_xform_op(Sdf.Path(f"/World/Pose_01.xformOp{suffix}")),
                suffix,
            )

    async def test_other_attributes_are_ignored(self):
        self.assertFalse(is_pose_xform_op(Sdf.Path("/World/Pose_01.visibility")))

    async def test_no_path_is_not_a_transform(self):
        self.assertFalse(is_pose_xform_op(None))


class TestXformOpCarriesRotation(omni.kit.test.AsyncTestCase):
    async def test_rotation_ops_turn_the_prim(self):
        for suffix in (":orient", ":rotateXYZ", ":rotateZ", ":transform"):
            self.assertTrue(
                xform_op_carries_rotation(Sdf.Path(f"/World/Pose_01.xformOp{suffix}")),
                suffix,
            )

    async def test_a_reordered_stack_can_turn_it_too(self):
        self.assertTrue(
            xform_op_carries_rotation(Sdf.Path("/World/Pose_01.xformOpOrder"))
        )

    async def test_moving_and_scaling_do_not(self):
        for suffix in (":translate", ":scale", ":translate:pivot"):
            self.assertFalse(
                xform_op_carries_rotation(Sdf.Path(f"/World/Pose_01.xformOp{suffix}")),
                suffix,
            )

    async def test_other_attributes_are_not_rotations(self):
        self.assertFalse(
            xform_op_carries_rotation(Sdf.Path("/World/Pose_01.visibility"))
        )
        self.assertFalse(xform_op_carries_rotation(None))


class TestEnvelopeIsWanted(omni.kit.test.AsyncTestCase):
    """The density slider's floor means "no cloud".

    It used to mean "the coarsest cloud the slider can make", so dragging it
    all the way down left a cloud on screen and read as a broken control.
    """

    async def test_a_zero_density_draws_nothing(self):
        self.assertFalse(envelope_is_wanted(True, 0.0))

    async def test_any_density_above_zero_draws(self):
        self.assertTrue(envelope_is_wanted(True, 0.05))
        self.assertTrue(envelope_is_wanted(True, 1.0))

    async def test_the_checkbox_still_wins(self):
        self.assertFalse(envelope_is_wanted(False, 1.0))


class TestFormatTcpPose(omni.kit.test.AsyncTestCase):
    """What the copy chip hands out has to be what the fields show.

    The pose used to live in one text label that was both the display and the
    clipboard source; now it lives in six coordinate fields, so the format is
    its own function and is pinned here.
    """

    async def test_position_keeps_two_decimals_and_rotation_four(self):
        self.assertEqual(
            "[1299.88,-978.09,378.38,1.2110,1.2070,1.2090]",
            format_tcp_pose(
                [1299.8765, -978.0912, 378.3842, 1.21104, 1.20701, 1.20899]
            ),
        )

    async def test_zeros_are_still_six_numbers(self):
        self.assertEqual(
            "[0.00,0.00,0.00,0.0000,0.0000,0.0000]", format_tcp_pose([0] * 6)
        )
