"""Unit tests for the reachability envelope's local forward-kinematics sweep."""

from __future__ import annotations

import asyncio
import json

import numpy as np
import omni.kit.test
import wandelbots_api_client.v2.models as wb_models

from wandelbots.omni.reachability import warp_fk
from wandelbots.omni.reachability.dh_chain import DHChain
from wandelbots.omni.reachability.envelope_service import (
    FULL_GREEN_MARGIN,
    EnvelopeContext,
    EnvelopeService,
    grade_margins,
)
from wandelbots.omni.reachability.ik_probe import encode_ik_request, joint_margins

_TOLERANCE = 1e-3


def _chain(link_mm: float = 400.0, joints: int = 2) -> DHChain:
    zeros = np.zeros(joints, dtype=np.float32)
    return DHChain(
        a=np.full(joints, link_mm, dtype=np.float32),
        alpha=zeros,
        d=zeros,
        theta0=zeros,
        sign=np.ones(joints, dtype=np.float32),
        lower=np.full(joints, -np.pi, dtype=np.float32),
        upper=np.full(joints, np.pi, dtype=np.float32),
    )


def _context(chain: DHChain | None = None) -> EnvelopeContext:
    return EnvelopeContext(
        model_name="Test",
        cell="cell",
        inverse_kinematics=None,
        chain=chain or _chain(),
        joint_position_limits=None,
        nova_tcp_offset=None,
        tcp_offset_mm=None,
    )


class TestComputeEnvelope(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.service = EnvelopeService()

    async def test_the_sweep_needs_no_backend(self):
        """The cloud is local: no IK probe is set on the context."""
        envelope = self.service.compute_envelope(_context(), 60.0, num_samples=4000)
        self.assertGreater(envelope.centres_mm.shape[0], 0)

    async def test_voxels_stay_within_the_arm_s_reach(self):
        chain = _chain(link_mm=400.0, joints=2)
        envelope = self.service.compute_envelope(
            _context(chain), 60.0, num_samples=8000
        )
        reach = 2 * 400.0 + np.sqrt(3.0) * envelope.voxel_mm
        self.assertLessEqual(
            float(np.linalg.norm(envelope.centres_mm, axis=1).max()), reach
        )

    async def test_likelihood_is_normalised(self):
        envelope = self.service.compute_envelope(_context(), 60.0, num_samples=8000)
        self.assertEqual(envelope.likelihood.shape[0], envelope.centres_mm.shape[0])
        self.assertGreaterEqual(float(envelope.likelihood.min()), 0.0)
        self.assertLessEqual(float(envelope.likelihood.max()), 1.0)

    async def test_the_drawn_size_is_the_one_asked_for(self):
        envelope = self.service.compute_envelope(_context(), 45.0, num_samples=4000)
        self.assertAlmostEqual(envelope.voxel_mm, 45.0, delta=_TOLERANCE)

    async def test_a_finer_size_yields_more_voxels(self):
        context = _context()
        coarse = self.service.compute_envelope(context, 120.0, num_samples=20000)
        fine = self.service.compute_envelope(context, 40.0, num_samples=20000)
        self.assertGreater(fine.centres_mm.shape[0], coarse.centres_mm.shape[0])

    async def test_the_sweep_is_reused_across_densities(self):
        """Re-voxelising must not re-sample; that cache is why the slider is cheap."""
        context = _context()
        self.service.compute_envelope(context, 120.0, num_samples=20000)
        first = context._sweep_positions
        self.service.compute_envelope(context, 40.0, num_samples=20000)
        self.assertIs(context._sweep_positions, first)

    async def test_a_new_sample_count_re_sweeps(self):
        context = _context()
        self.service.compute_envelope(context, 60.0, num_samples=4000)
        first = context._sweep_positions
        self.service.compute_envelope(context, 60.0, num_samples=8000)
        self.assertIsNot(context._sweep_positions, first)


class TestVoxelize(omni.kit.test.AsyncTestCase):
    async def test_points_in_one_cell_collapse_to_one_voxel(self):
        points = np.array(
            [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0], [3.0, 3.0, 3.0]], np.float32
        )
        centres, counts = warp_fk.voxelize(points, 100.0)
        self.assertEqual(centres.shape[0], 1)
        self.assertEqual(int(counts[0]), 3)

    async def test_separate_cells_stay_separate(self):
        points = np.array([[10.0, 0.0, 0.0], [210.0, 0.0, 0.0]], np.float32)
        centres, _counts = warp_fk.voxelize(points, 100.0)
        self.assertEqual(centres.shape[0], 2)

    async def test_centres_sit_at_the_cell_middle(self):
        centres, _ = warp_fk.voxelize(np.array([[10.0, 10.0, 10.0]], np.float32), 100.0)
        self.assertTrue(np.allclose(centres[0], [50.0, 50.0, 50.0], atol=_TOLERANCE))

    async def test_nothing_to_voxelize(self):
        centres, counts = warp_fk.voxelize(np.empty((0, 3), np.float32), 50.0)
        self.assertEqual(centres.shape, (0, 3))
        self.assertEqual(counts.shape, (0,))


class TestSampleJointSpace(omni.kit.test.AsyncTestCase):
    async def test_samples_respect_the_joint_limits(self):
        chain = _chain()
        chain.lower = np.array([-0.5, -0.25], dtype=np.float32)
        chain.upper = np.array([0.5, 0.25], dtype=np.float32)
        samples = warp_fk.sample_joint_space(chain, 2000, seed=0)
        self.assertEqual(samples.shape, (2000, 2))
        self.assertTrue(bool((samples >= chain.lower - _TOLERANCE).all()))
        self.assertTrue(bool((samples <= chain.upper + _TOLERANCE).all()))

    async def test_the_seed_makes_it_repeatable(self):
        chain = _chain()
        self.assertTrue(
            np.array_equal(
                warp_fk.sample_joint_space(chain, 500, seed=7),
                warp_fk.sample_joint_space(chain, 500, seed=7),
            )
        )


class _FakeInverseKinematics:
    """Answers IK for every pose inside a sphere, and can fail whole batches.

    The real API answers a batch with one entry per pose, an empty one meaning
    "no solution" - which is what the envelope filters on.
    """

    def __init__(self, radius_mm: float = 500.0, fail_after: int | None = None):
        self._radius_mm = radius_mm
        self._fail_after = fail_after
        self.batches = 0

    async def joint_margins(self, cell, body, lower, upper):
        """Margin falls off towards the sphere's surface, NaN outside it."""
        self.batches += 1
        if self._fail_after is not None and self.batches > self._fail_after:
            raise asyncio.TimeoutError("no answer")
        distances = np.array(
            [
                float(np.linalg.norm(np.asarray(pose["position"])))
                for pose in json.loads(body)["tcp_poses"]
            ]
        )
        margins = 1.0 - distances / self._radius_mm
        margins[distances > self._radius_mm] = np.nan
        return margins


class TestComputeOrientedEnvelope(omni.kit.test.AsyncTestCase):
    """The cloud narrowed to one orientation.

    Which positions are reachable depends on the orientation asked for, so the
    orientation-free sweep answers a different question than a user placing an
    oriented pose is asking.
    """

    async def setUp(self):
        self.service = EnvelopeService()

    def _context_with(self, api) -> EnvelopeContext:
        context = _context()
        context.inverse_kinematics = api
        return context

    async def test_only_voxels_nova_solves_are_kept(self):
        api = _FakeInverseKinematics(radius_mm=300.0)
        context = self._context_with(api)

        envelope = await self.service.compute_oriented_envelope(
            context, [0.0, 0.0, 0.0], 60.0, num_samples=4000
        )

        self.assertGreater(envelope.centres_mm.shape[0], 0)
        distances = np.linalg.norm(envelope.centres_mm, axis=1)
        self.assertLessEqual(float(distances.max()), 300.0 + _TOLERANCE)

    async def test_it_is_a_subset_of_the_orientation_free_sweep(self):
        """A position reachable at one orientation is reachable at some, so the
        sweep bounds the candidates."""
        context = self._context_with(_FakeInverseKinematics(radius_mm=300.0))
        sweep = self.service.compute_envelope(_context(), 60.0, num_samples=4000)

        oriented = await self.service.compute_oriented_envelope(
            context, [0.0, 0.0, 0.0], 60.0, num_samples=4000
        )

        self.assertLessEqual(oriented.centres_mm.shape[0], sweep.centres_mm.shape[0])

    async def test_points_are_graded_by_their_joint_margin(self):
        """Near the sphere's surface the fake margin shrinks, so the grade does."""
        context = self._context_with(_FakeInverseKinematics(radius_mm=300.0))

        envelope = await self.service.compute_oriented_envelope(
            context, [0.0, 0.0, 0.0], 60.0, num_samples=4000
        )

        distances = np.linalg.norm(envelope.centres_mm, axis=1)
        inner = envelope.likelihood[distances < 100.0]
        outer = envelope.likelihood[distances > 250.0]
        self.assertTrue(inner.size and outer.size)
        self.assertGreater(float(inner.mean()), float(outer.mean()))

    async def test_the_orientation_is_carried_on_the_result(self):
        context = self._context_with(_FakeInverseKinematics())

        envelope = await self.service.compute_oriented_envelope(
            context, [0.1, 0.2, 0.3], 60.0, num_samples=4000
        )

        self.assertEqual([0.1, 0.2, 0.3], envelope.orientation_rotvec)
        self.assertFalse(envelope.incomplete)

    async def test_an_unanswered_batch_marks_the_envelope_incomplete(self):
        """Drawing the survivors as if they were all there is would be a lie."""
        context = self._context_with(_FakeInverseKinematics(fail_after=0))

        envelope = await self.service.compute_oriented_envelope(
            context, [0.0, 0.0, 0.0], 60.0, num_samples=4000
        )

        self.assertTrue(envelope.incomplete)

    async def test_the_sweep_itself_carries_no_orientation(self):
        self.assertIsNone(
            self.service.compute_envelope(_context(), 60.0).orientation_rotvec
        )


class TestFirstReachablePoint(omni.kit.test.AsyncTestCase):
    """Checking candidates that came from somewhere else against real IK.

    The snap fallback offers points out of the drawn cloud, which can have been
    computed at another orientation than the pose has. Handing one over
    unchecked moved the pose onto a point the verdict then rejected.
    """

    async def setUp(self):
        self.service = EnvelopeService()

    def _context_with(self, api) -> EnvelopeContext:
        context = _context()
        context.inverse_kinematics = api
        return context

    async def test_the_first_candidate_that_solves_wins(self):
        context = self._context_with(_FakeInverseKinematics(radius_mm=300.0))
        candidates = np.array([[900.0, 0.0, 0.0], [800.0, 0.0, 0.0], [100.0, 0.0, 0.0]])

        point = await self.service.first_reachable_point(
            context, candidates, [0.0, 0.0, 0.0]
        )

        self.assertIsNotNone(point)
        self.assertAlmostEqual(100.0, float(point[0]), delta=_TOLERANCE)

    async def test_nothing_reachable_is_answered_with_none(self):
        context = self._context_with(_FakeInverseKinematics(radius_mm=50.0))
        candidates = np.array([[900.0, 0.0, 0.0], [800.0, 0.0, 0.0]])

        self.assertIsNone(
            await self.service.first_reachable_point(
                context, candidates, [0.0, 0.0, 0.0]
            )
        )

    async def test_an_unanswered_request_is_not_a_reachable_point(self):
        context = self._context_with(_FakeInverseKinematics(fail_after=0))
        candidates = np.array([[10.0, 0.0, 0.0]])

        self.assertIsNone(
            await self.service.first_reachable_point(
                context, candidates, [0.0, 0.0, 0.0]
            )
        )

    async def test_no_candidates_asks_nothing(self):
        api = _FakeInverseKinematics()
        context = self._context_with(api)

        self.assertIsNone(
            await self.service.first_reachable_point(
                context, np.zeros((0, 3)), [0.0, 0.0, 0.0]
            )
        )
        self.assertEqual(0, api.batches)


class _RecordingInverseKinematics(_FakeInverseKinematics):
    """Remembers every pose it was asked about, in the frame it was sent."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.positions = []

    async def joint_margins(self, cell, body, lower, upper):
        self.positions.extend(
            pose["position"] for pose in json.loads(body)["tcp_poses"]
        )
        return await super().joint_margins(cell, body, lower, upper)


class TestEnvelopeRequestFrame(omni.kit.test.AsyncTestCase):
    """The envelope and the snap search ask NOVA in NOVA's frame.

    Their candidates are base-frame points, the same frame the scene draws
    them in. Sent unconverted to a mounted robot, every answer is about a
    point the mounting away from the one drawn.
    """

    async def setUp(self):
        self.service = EnvelopeService()

    async def test_a_mounted_robot_is_asked_about_the_raised_point(self):
        api = _RecordingInverseKinematics(radius_mm=5000.0)
        context = _context()
        context.inverse_kinematics = api
        context.mounting = wb_models.Pose(position=[0, 0, 650], orientation=[0, 0, 0])

        await self.service.first_reachable_point(
            context, np.array([[100.0, 200.0, 300.0]]), [0.0, 0.0, 0.0]
        )

        self.assertTrue(api.positions)
        for x, y, z in api.positions:
            self.assertAlmostEqual(100.0, x, delta=_TOLERANCE)
            self.assertAlmostEqual(200.0, y, delta=_TOLERANCE)
            self.assertAlmostEqual(950.0, z, delta=_TOLERANCE)

    async def test_the_answer_stays_in_the_base_frame(self):
        """Only the question changes frame; the point handed back is the one drawn."""
        api = _RecordingInverseKinematics(radius_mm=5000.0)
        context = _context()
        context.inverse_kinematics = api
        context.mounting = wb_models.Pose(position=[0, 0, 650], orientation=[0, 0, 0])

        point = await self.service.first_reachable_point(
            context, np.array([[100.0, 200.0, 300.0]]), [0.0, 0.0, 0.0]
        )

        self.assertAlmostEqual(300.0, float(point[2]), delta=_TOLERANCE)


class TestIkProbeEncoding(omni.kit.test.AsyncTestCase):
    """The IK body is formatted by hand, so it has to stay what NOVA parses."""

    async def test_the_body_carries_every_pose_and_field(self):
        body = encode_ik_request(
            {
                "motion_group_model": "Test",
                "tcp_poses": [],
                "reference_joint_position": [0.5],
            },
            np.array([[1.0, 2.0, 3.0], [-4.5, 5.25, 1e-7]]),
            [0.1, 0.2, 0.3],
        )

        request = json.loads(body)
        self.assertEqual("Test", request["motion_group_model"])
        self.assertEqual([0.5], request["reference_joint_position"])
        self.assertEqual(
            [[1.0, 2.0, 3.0], [-4.5, 5.25, 1e-7]],
            [pose["position"] for pose in request["tcp_poses"]],
        )
        for pose in request["tcp_poses"]:
            self.assertEqual([0.1, 0.2, 0.3], pose["orientation"])

    async def test_no_positions_is_an_empty_pose_list(self):
        body = encode_ik_request(
            {"motion_group_model": "Test"}, np.zeros((0, 3)), [0, 0, 0]
        )

        self.assertEqual([], json.loads(body)["tcp_poses"])

    async def test_a_pose_without_solution_has_no_margin(self):
        response = b'{"joints": [[[0.0, 0.0]], []]}'

        margins = joint_margins(response, np.array([-1.0, -1.0]), np.array([1.0, 1.0]))

        self.assertTrue(np.isfinite(margins[0]))
        self.assertTrue(np.isnan(margins[1]))

    async def test_the_tightest_joint_decides_a_solution(self):
        response = b'{"joints": [[[0.0, 0.5]]]}'

        margins = joint_margins(response, np.array([-1.0, -1.0]), np.array([1.0, 1.0]))

        self.assertAlmostEqual(0.5, float(margins[0]), delta=_TOLERANCE)

    async def test_the_loosest_solution_decides_a_pose(self):
        response = b'{"joints": [[[0.9, 0.0], [0.0, 0.2]]]}'

        margins = joint_margins(response, np.array([-1.0, -1.0]), np.array([1.0, 1.0]))

        self.assertAlmostEqual(0.8, float(margins[0]), delta=_TOLERANCE)

    async def test_a_joint_on_its_limit_has_no_margin(self):
        response = b'{"joints": [[[1.0, 0.0]]]}'

        margins = joint_margins(response, np.array([-1.0, -1.0]), np.array([1.0, 1.0]))

        self.assertAlmostEqual(0.0, float(margins[0]), delta=_TOLERANCE)


class TestGradeMargins(omni.kit.test.AsyncTestCase):
    """One colour scale for every cloud, so colours compare across orientations."""

    async def test_a_margin_grades_the_same_in_any_cloud(self):
        tight_cloud = grade_margins(np.array([0.05, 0.1, 0.2]))
        loose_cloud = grade_margins(np.array([0.2, 0.4, 0.5]))

        self.assertAlmostEqual(
            float(tight_cloud[2]), float(loose_cloud[0]), delta=_TOLERANCE
        )

    async def test_a_cloud_of_tight_solutions_stays_orange(self):
        grades = grade_margins(np.array([0.02, 0.05, 0.1]))

        self.assertLess(float(grades.max()), 0.5)

    async def test_the_full_green_margin_and_beyond_are_full_green(self):
        grades = grade_margins(np.array([FULL_GREEN_MARGIN, 1.0]))

        self.assertTrue(np.allclose(grades, 1.0))
