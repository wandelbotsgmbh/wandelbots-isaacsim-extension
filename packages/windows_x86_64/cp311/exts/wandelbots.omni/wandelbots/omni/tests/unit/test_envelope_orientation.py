"""When a turned pose is worth recomputing the reachability cloud.

Which positions are reachable depends on the orientation asked for, so the
cloud follows the pose's rotation. A drag emits a change notice per mouse
sample and every recompute is a round of IK batches, so a turn only counts
once it is large enough to move the cloud visibly.
"""

import omni.kit.test

from wandelbots.omni.ui.overlay.reachability_envelope.reachability_envelope_overlay import (
    rotvec_matches,
)


class TestRotvecMatches(omni.kit.test.AsyncTestCase):
    async def test_a_hand_tremor_is_the_same_orientation(self):
        self.assertTrue(rotvec_matches([0.5, 0.0, 0.0], [0.501, 0.0, 0.0]))

    async def test_a_visible_turn_is_a_different_one(self):
        self.assertFalse(rotvec_matches([0.5, 0.0, 0.0], [0.9, 0.0, 0.0]))

    async def test_identical_vectors_match(self):
        self.assertTrue(rotvec_matches([0.1, -0.2, 0.3], [0.1, -0.2, 0.3]))
