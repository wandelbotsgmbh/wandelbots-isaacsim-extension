"""Poses read in the scene are in the robot's base frame; NOVA solves in the
frame its mounting starts from.

The reference values were read off a running scene: a Yaskawa GP215 mounted
650 mm up, placed from a pose relative to link_0. NOVA was sent the base pose
as if it were in its own frame, and the gripper came to rest exactly the
mounting below the target.
"""

import math

import omni.kit.test
import wandelbots_api_client.v2.models as wb_models

from wandelbots.omni.utils.kinematics import base_pose_to_world

_TOLERANCE = 1e-6

GP215_MOUNTING = wb_models.Pose(position=[0, 0, 650], orientation=[0, 0, 0])
TARGET_IN_BASE = [726.828, 2055.121, 1634.799, 0.118, -2.04, -2.063]


class TestBasePoseToWorld(omni.kit.test.AsyncTestCase):
    def _assert_pose(self, expected, got):
        for want, have in zip(expected, got):
            self.assertAlmostEqual(want, have, delta=_TOLERANCE)

    async def test_a_raised_mount_lifts_the_pose_by_its_height(self):
        world = base_pose_to_world(TARGET_IN_BASE, GP215_MOUNTING)

        self._assert_pose(
            [726.828, 2055.121, 1634.799 + 650, 0.118, -2.04, -2.063], world
        )

    async def test_no_mounting_leaves_the_pose_alone(self):
        self._assert_pose(TARGET_IN_BASE, base_pose_to_world(TARGET_IN_BASE, None))

    async def test_a_turned_mount_turns_the_position_too(self):
        """A base pose along x ends up along y under a quarter turn about z."""
        quarter_turn = wb_models.Pose(
            position=[100, 0, 0], orientation=[0, 0, math.pi / 2]
        )

        world = base_pose_to_world([500, 0, 0, 0, 0, 0], quarter_turn)

        self._assert_pose([100, 500, 0], world[:3])
        self.assertAlmostEqual(math.pi / 2, world[5], delta=_TOLERANCE)
