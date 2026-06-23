"""Unit tests for pure helper functions in the trajectory planner.

Covers preview decimation, velocity-gradient coloring, and the Nova skill-config
payload (de)serialization used by "Load by name".
"""

from __future__ import annotations

import json

import omni.kit.test

from wandelbots.omni.ui.tool.trajectory_planner.planning_orchestrator import (
    _decimate_indices,
    _segment_speeds,
    _speeds_to_colors,
)
from wandelbots.omni.ui.tool.trajectory_planner.nova_skill_store import (
    _config_from_payload,
)
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
    TrajectoryPlannerConfig,
)


class TestDecimateIndices(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_returns_all_indices_when_small(self):
        self.assertEqual(_decimate_indices(0), [])
        self.assertEqual(_decimate_indices(5, max_points=10), [0, 1, 2, 3, 4])

    async def test_keeps_first_and_last_and_caps_count(self):
        idxs = _decimate_indices(1000, max_points=200)
        self.assertEqual(idxs[0], 0)
        self.assertEqual(idxs[-1], 999)
        self.assertLessEqual(len(idxs), 200)
        # strictly increasing, unique
        self.assertEqual(idxs, sorted(set(idxs)))

    async def test_samples_align_for_parallel_arrays(self):
        joints = list(range(1000))
        times = [i * 0.01 for i in range(1000)]
        idxs = _decimate_indices(len(joints))
        sampled_joints = [joints[i] for i in idxs]
        sampled_times = [times[i] for i in idxs]
        self.assertEqual(len(sampled_joints), len(sampled_times))
        self.assertEqual(sampled_joints[0], 0)
        self.assertEqual(sampled_joints[-1], 999)


class TestSegmentSpeeds(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_speed_is_distance_over_time(self):
        poses = [[0, 0, 0], [100, 0, 0], [100, 0, 0], [200, 0, 0]]
        times = [0.0, 1.0, 2.0, 2.5]
        speeds = _segment_speeds(poses, times)
        self.assertEqual(len(speeds), 3)
        self.assertAlmostEqual(speeds[0], 100.0)  # 100mm / 1s
        self.assertAlmostEqual(speeds[1], 0.0)  # no movement
        self.assertAlmostEqual(speeds[2], 200.0)  # 100mm / 0.5s

    async def test_zero_or_negative_dt_yields_zero(self):
        poses = [[0, 0, 0], [100, 0, 0]]
        times = [1.0, 1.0]
        self.assertEqual(_segment_speeds(poses, times), [0.0])


class TestSpeedsToColors(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_empty(self):
        self.assertEqual(_speeds_to_colors([]), [])

    async def test_fast_is_green_slow_is_red(self):
        colors = _speeds_to_colors([0.0, 200.0])  # normalized by max=200
        self.assertEqual(colors[0], (255, 0, 0))  # slow -> red
        self.assertEqual(colors[1], (0, 255, 0))  # fast -> green

    async def test_reference_speed_normalization_and_clamp(self):
        # ref below the actual speed clamps the ratio at 1 (full green).
        colors = _speeds_to_colors([200.0], reference_speed=100.0)
        self.assertEqual(colors[0], (0, 255, 0))
        # half of reference -> midpoint
        colors = _speeds_to_colors([200.0], reference_speed=400.0)
        self.assertEqual(colors[0], (128, 128, 0))


class TestNovaConfigPayload(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_roundtrip_wrapped_payload(self):
        config = TrajectoryPlannerConfig(
            name="cf_skill", collision_setup="setup", plan_collision_free=True
        )
        raw = json.dumps({"version": "v3", "config": config.model_dump()}).encode(
            "utf-8"
        )
        restored = _config_from_payload(raw)
        self.assertEqual(restored, config)

    async def test_roundtrip_bare_payload(self):
        config = TrajectoryPlannerConfig(name="bare_skill")
        raw = json.dumps(config.model_dump()).encode("utf-8")
        restored = _config_from_payload(raw)
        self.assertEqual(restored, config)
