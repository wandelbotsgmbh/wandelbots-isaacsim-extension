"""Unit tests for TrajectoryPlannerStore persistence."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import omni.kit.test

from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
    PoseConfig,
    TrajectoryPlannerConfig,
    TrajectoryPlannerStore,
)


class TestTrajectoryPlannerStore(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store.BaseStore.__init__",
        return_value=None,
    )
    async def test_save_configs_serializes_to_data(self, mock_init):
        store = TrajectoryPlannerStore.__new__(TrajectoryPlannerStore)
        store._data = {}
        store.save_data = MagicMock()

        configs = [
            TrajectoryPlannerConfig(
                name="skill_1",
                robot_prim_path="/World/robot",
                poses=[PoseConfig(prim_path="/World/p0")],
            ),
            TrajectoryPlannerConfig(name="skill_2"),
        ]
        store.save_configs(configs)

        self.assertIn("skills", store._data)
        self.assertEqual(len(store._data["skills"]), 2)
        self.assertEqual(store._data["skills"][0]["name"], "skill_1")
        self.assertEqual(store._data["skills"][1]["name"], "skill_2")
        store.save_data.assert_called_once()

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store.BaseStore.__init__",
        return_value=None,
    )
    async def test_load_configs_deserializes_from_data(self, mock_init):
        store = TrajectoryPlannerStore.__new__(TrajectoryPlannerStore)
        store._data = {
            "skills": [
                {
                    "name": "loaded_skill",
                    "robot_prim_path": "/World/ur10e",
                    "tcp_name": "flange",
                    "poses": [
                        {
                            "prim_path": "/World/p0",
                            "joint_configs": [[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]],
                        }
                    ],
                    "tcp_velocity": 300.0,
                }
            ]
        }
        configs = store.load_configs()

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].name, "loaded_skill")
        self.assertEqual(configs[0].tcp_velocity, 300.0)
        self.assertEqual(len(configs[0].poses), 1)
        self.assertEqual(configs[0].poses[0].prim_path, "/World/p0")

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store.BaseStore.__init__",
        return_value=None,
    )
    async def test_load_configs_handles_corrupt_entries(self, mock_init):
        store = TrajectoryPlannerStore.__new__(TrajectoryPlannerStore)
        store._data = {
            "skills": [
                {"name": "valid_skill"},
                {"invalid": "missing_name_field"},  # no 'name' key → validation error
                {"name": "another_valid"},
            ]
        }
        configs = store.load_configs()

        # Should skip the invalid entry and load the valid ones
        self.assertEqual(len(configs), 2)
        self.assertEqual(configs[0].name, "valid_skill")
        self.assertEqual(configs[1].name, "another_valid")

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store.BaseStore.__init__",
        return_value=None,
    )
    async def test_load_configs_empty_data(self, mock_init):
        store = TrajectoryPlannerStore.__new__(TrajectoryPlannerStore)
        store._data = {}
        configs = store.load_configs()
        self.assertEqual(configs, [])

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store.BaseStore.__init__",
        return_value=None,
    )
    async def test_load_configs_legacy_sections_key(self, mock_init):
        """Verify backward compatibility with old 'sections' key name."""
        store = TrajectoryPlannerStore.__new__(TrajectoryPlannerStore)
        store._data = {"sections": [{"name": "legacy_skill", "poses": []}]}
        configs = store.load_configs()

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].name, "legacy_skill")

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store.BaseStore.__init__",
        return_value=None,
    )
    async def test_save_then_load_roundtrip(self, mock_init):
        store = TrajectoryPlannerStore.__new__(TrajectoryPlannerStore)
        store._data = {}
        store.save_data = MagicMock()

        original = [
            TrajectoryPlannerConfig(
                name="roundtrip",
                tcp_velocity=123.4,
                poses=[
                    PoseConfig(
                        prim_path="/World/p",
                        motion_type="PathLine",
                        joint_configs=[[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]],
                    )
                ],
            )
        ]
        store.save_configs(original)
        loaded = store.load_configs()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].name, "roundtrip")
        self.assertAlmostEqual(loaded[0].tcp_velocity, 123.4)
        self.assertEqual(loaded[0].poses[0].motion_type, "PathLine")
