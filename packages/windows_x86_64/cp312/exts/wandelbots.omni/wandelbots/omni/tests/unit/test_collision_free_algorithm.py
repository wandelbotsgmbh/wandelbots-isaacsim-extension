"""The RRT-Connect settings sent for collision-free planning.

The API client still carries the superseded ``adaptive_step_size`` and
``max_step_size`` fields and serialises their defaults whether or not they are
set. Sending a fixed ``step_size`` on its own therefore puts an adaptive
setting and a fixed one in the same request, which leaves the server free to
pick either. These tests pin the payload instead of the constructor call, since
that ambiguity is only visible once the model is serialised.
"""

from __future__ import annotations

import omni.kit.test

from wandelbots.omni.utils.kinematics import build_collision_free_algorithm


class TestCollisionFreeAlgorithm(omni.kit.test.AsyncTestCase):
    async def test_no_step_size_leaves_the_search_to_size_its_own_steps(self):
        payload = build_collision_free_algorithm(10000).to_dict()

        self.assertNotIn("step_size", payload)
        self.assertTrue(payload["adaptive_step_size"])

    async def test_a_step_size_is_not_contradicted_by_the_superseded_fields(self):
        payload = build_collision_free_algorithm(10000, 0.05).to_dict()

        self.assertAlmostEqual(0.05, payload["step_size"])
        self.assertFalse(payload["adaptive_step_size"])
        self.assertAlmostEqual(0.05, payload["max_step_size"])

    async def test_the_iteration_limit_is_carried_through(self):
        self.assertEqual(
            250, build_collision_free_algorithm(250).to_dict()["max_iterations"]
        )
        self.assertEqual(
            250, build_collision_free_algorithm(250, 0.05).to_dict()["max_iterations"]
        )
