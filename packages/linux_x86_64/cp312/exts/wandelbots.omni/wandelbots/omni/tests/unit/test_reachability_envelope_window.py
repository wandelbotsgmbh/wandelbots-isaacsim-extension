"""Pose comparison in the Reachability Envelope window.

The target pose is read relative to the robot's base, which is a simulated rigid
body: while the timeline plays PhysX rewrites that frame every step, so the
relative pose jitters even for a pose prim nobody touches. A comparison that
counts such jitter as movement restarts the settle timer on every poll, so no IK
solve ever starts and "Place robot" waits forever.
"""

import omni.kit.test

from wandelbots.omni.ui.tool.reachability_envelope.reachability_envelope_window import (
    adopted_tcp_index,
    pose_vectors_match,
    tcp_index_for,
)

POSE = [100.0, 200.0, 300.0, 0.1, 0.2, 0.3]


class TestPoseVectorsMatch(omni.kit.test.AsyncTestCase):
    async def test_identical_vectors_match(self):
        self.assertTrue(pose_vectors_match(POSE, list(POSE)))

    async def test_physics_jitter_still_counts_as_the_same_pose(self):
        jittered = [100.0001, 199.9998, 300.0002, 0.1, 0.2, 0.3]

        self.assertTrue(pose_vectors_match(POSE, jittered))

    async def test_a_tenth_of_a_millimetre_counts_as_moved(self):
        self.assertFalse(pose_vectors_match(POSE, [100.1, 200.0, 300.0, 0.1, 0.2, 0.3]))

    async def test_rotation_keeps_its_own_tolerance(self):
        self.assertTrue(
            pose_vectors_match(POSE, [100.0, 200.0, 300.0, 0.1001, 0.2, 0.3])
        )
        self.assertFalse(
            pose_vectors_match(POSE, [100.0, 200.0, 300.0, 0.11, 0.2, 0.3])
        )

    async def test_missing_or_mismatched_vectors_never_match(self):
        self.assertFalse(pose_vectors_match(POSE, None))
        self.assertFalse(pose_vectors_match(None, POSE))
        self.assertFalse(pose_vectors_match(POSE, POSE[:5]))


class TestTcpIndexFor(omni.kit.test.AsyncTestCase):
    """Reloading the description used to reset the TCP combo to index 0, which
    silently re-solves the target against whichever TCP the description lists
    first - a different tool and a different answer."""

    async def test_the_targets_own_tcp_wins(self):
        self.assertEqual(2, tcp_index_for(["Flange", "klt", "klt_3"], "klt_3"))

    async def test_a_tcp_the_description_no_longer_offers_falls_back(self):
        self.assertEqual(0, tcp_index_for(["Flange", "klt"], "gone"))

    async def test_no_stored_choice_falls_back(self):
        self.assertEqual(0, tcp_index_for(["Flange", "klt"], None))

    async def test_an_empty_description_is_index_zero(self):
        self.assertEqual(0, tcp_index_for([], "klt"))


class TestAdoptedTcpIndex(omni.kit.test.AsyncTestCase):
    """Selecting a target switches to the TCP it names.

    The description loads before any target is selected, so the TCP picked
    then belongs to nothing - the flange, as a rule. A gripper target solved
    for the flange puts the gripper its own offset away from the pose.
    """

    async def test_a_target_that_names_its_tcp_switches_to_it(self):
        self.assertEqual(1, adopted_tcp_index(["Flange", "gripper"], 0, "gripper"))

    async def test_a_target_without_a_tcp_keeps_the_current_choice(self):
        self.assertEqual(1, adopted_tcp_index(["Flange", "gripper"], 1, None))

    async def test_a_tcp_the_description_does_not_offer_keeps_the_current_choice(self):
        self.assertEqual(1, adopted_tcp_index(["Flange", "gripper"], 1, "gone"))
