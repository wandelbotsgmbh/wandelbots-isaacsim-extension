"""Unit tests for TCP-based segmentation of exported trajectory plans.

A PlanTrajectoryRequest can only carry one TCP (motion_group_setup.tcp_offset),
so a skill whose poses use different TCPs is exported as several segments that a
consumer plans individually and then merges via the mergeTrajectories endpoint.
These tests cover the pure grouping/blending helpers that drive that split.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import omni.kit.test
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.router.v2.teaching import (
    _effective_tcp,
    _group_poses_by_tcp,
    _segment_blending,
    build_skill,
)
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
    PoseConfig,
    TrajectoryPlannerConfig,
)


def _config(
    default_tcp: str | None, pose_tcps: list[str | None]
) -> TrajectoryPlannerConfig:
    return TrajectoryPlannerConfig(
        name="seg_test",
        tcp_name=default_tcp,
        poses=[
            PoseConfig(prim_path=f"/World/p{i}", tcp_name=tcp)
            for i, tcp in enumerate(pose_tcps)
        ],
    )


class TestEffectiveTcp(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_per_pose_override_wins(self):
        config = _config("default", [None])
        self.assertEqual(_effective_tcp(config.poses[0], config), "default")
        config.poses[0].tcp_name = "gripper"
        self.assertEqual(_effective_tcp(config.poses[0], config), "gripper")

    async def test_falls_back_to_skill_tcp(self):
        config = _config("skill_tcp", [None])
        self.assertEqual(_effective_tcp(config.poses[0], config), "skill_tcp")


class TestGroupPosesByTcp(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_single_tcp_is_one_run(self):
        config = _config("a", [None, None, None])  # start + 2 targets
        self.assertEqual(_group_poses_by_tcp(config.poses, config), [[1, 2]])

    async def test_contiguous_runs_split_on_tcp_change(self):
        # start + targets: a, a, b, b, a  -> runs [1,2] [3,4] [5]
        config = _config("a", [None, None, None, "b", "b", "a"])
        self.assertEqual(
            _group_poses_by_tcp(config.poses, config), [[1, 2], [3, 4], [5]]
        )
        runs = _group_poses_by_tcp(config.poses, config)
        tcps = [_effective_tcp(config.poses[r[0]], config) for r in runs]
        self.assertEqual(tcps, ["a", "b", "a"])

    async def test_alternating_tcps(self):
        config = _config("a", [None, "a", "b", "a"])
        self.assertEqual(_group_poses_by_tcp(config.poses, config), [[1], [2], [3]])

    async def test_no_target_poses(self):
        config = _config("a", [None])  # only the start pose
        self.assertEqual(_group_poses_by_tcp(config.poses, config), [])

    async def test_empty_poses(self):
        config = _config("a", [])
        self.assertEqual(_group_poses_by_tcp(config.poses, config), [])


class TestSegmentBlending(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_position_blend_is_exported(self):
        config = _config("a", [None, None])
        position = wb_v2_models.BlendingPosition(position_zone_radius=12.5)
        config.poses[1].blending = wb_v2_models.MotionCommandBlending(
            position
        ).to_dict()
        result = _segment_blending(config.poses[1], config, None)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["position_zone_radius"], 12.5)

    async def test_auto_blend_falls_back_to_hard_transition(self):
        config = _config("a", [None, None])
        auto = wb_v2_models.BlendingAuto(min_velocity_in_percent=50)
        config.poses[1].blending = wb_v2_models.MotionCommandBlending(auto).to_dict()
        self.assertIsNone(_segment_blending(config.poses[1], config, None))

    async def test_no_blend_returns_none(self):
        config = _config("a", [None, None])
        self.assertIsNone(_segment_blending(config.poses[1], config, None))


def _mg_setup() -> wb_v2_models.MotionGroupSetup:
    return wb_v2_models.MotionGroupSetup(
        motion_group_model="UR10e",
        cycle_time=4,
        tcp_offset=wb_v2_models.Pose(
            position=[0.0, 0.0, 0.0], orientation=[0.0, 0.0, 0.0]
        ),
    )


async def _fake_fetch(config, stage, tcp_names):
    return {t: _mg_setup() for t in tcp_names}


_FAKE_POSE = wb_v2_models.Pose(position=[1.0, 2.0, 3.0], orientation=[0.0, 0.0, 0.0])


class TestSegmentedExport(omni.kit.test.AsyncTestCase):
    """build_skill -> ExportedSkill shape for single- vs multi-TCP skills."""

    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    def _config_with_joints(self, default_tcp, pose_tcps):
        config = _config(default_tcp, pose_tcps)
        for p in config.poses:
            p.selected_joint_config = [0.0, -1.57, 1.57, 0.0, 1.57, 0.0]
        return config

    @patch(
        "wandelbots.omni.router.v2.teaching._resolve_pose_for_prim",
        return_value=_FAKE_POSE,
    )
    @patch(
        "wandelbots.omni.router.v2.teaching._fetch_motion_group_setups",
        new=_fake_fetch,
    )
    async def test_multi_tcp_emits_segmented_plan(self, _mock_pose):
        # start(a), target(a), target(b), target(b) -> runs [[1],[2,3]] => 2 segments
        config = self._config_with_joints("a", [None, None, "b", "b"])
        config.global_limits_override = {"tcp_velocity_limit": 250.0}
        # position blend on the boundary pose (last of run 0) -> inter-segment blend
        pos = wb_v2_models.BlendingPosition(position_zone_radius=12.5)
        config.poses[1].blending = wb_v2_models.MotionCommandBlending(pos).to_dict()

        skill = await build_skill(config, MagicMock())

        self.assertIsNone(skill.plan_trajectory_request)
        sp = skill.plan_segmented_trajectory
        self.assertIsNotNone(sp)
        self.assertEqual(len(sp.segments), 2)
        self.assertEqual(sp.limits_override, {"tcp_velocity_limit": 250.0})
        self.assertIsNone(sp.collision_setups)
        # shared merge motion_group_setup is present and well-formed
        self.assertEqual(sp.motion_group_setup.get("motion_group_model"), "UR10e")
        # each segment request round-trips, and segment TCPs match the runs
        self.assertEqual([s.tcp_name for s in sp.segments], ["a", "b"])
        for seg in sp.segments:
            wb_v2_models.PlanTrajectoryRequest.from_dict(seg.plan_trajectory_request)
        # blend on the first (non-final) segment, none on the last
        self.assertIsNotNone(sp.segments[0].blending)
        self.assertAlmostEqual(sp.segments[0].blending["position_zone_radius"], 12.5)
        self.assertIsNone(sp.segments[-1].blending)

    @patch(
        "wandelbots.omni.router.v2.teaching._resolve_pose_for_prim",
        return_value=_FAKE_POSE,
    )
    @patch(
        "wandelbots.omni.router.v2.teaching._fetch_motion_group_setups",
        new=_fake_fetch,
    )
    async def test_single_tcp_emits_flat_request(self, _mock_pose):
        config = self._config_with_joints("a", [None, None, None])

        skill = await build_skill(config, MagicMock())

        self.assertIsNone(skill.plan_segmented_trajectory)
        self.assertIsNotNone(skill.plan_trajectory_request)
        wb_v2_models.PlanTrajectoryRequest.from_dict(skill.plan_trajectory_request)
