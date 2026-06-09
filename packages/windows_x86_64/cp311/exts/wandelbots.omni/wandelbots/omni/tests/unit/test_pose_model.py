"""Unit tests for PoseModel and PoseItem tree data structures."""

from __future__ import annotations

import omni.kit.test

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.ui.tool.trajectory_planner.pose_tree_widget import (
    PoseItem,
    PoseModel,
)


class TestPoseItem(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    async def test_creation_with_defaults(self):
        pose = WSPose(pose=[100.0, 200.0, 300.0, 0.0, 0.0, 0.0])
        item = PoseItem(prim_path="/World/p0", name="Pose_0", pose=pose)

        self.assertEqual(item.prim_path, "/World/p0")
        self.assertEqual(item.name_model.get_value_as_string(), "Pose_0")
        self.assertEqual(item.pose, pose)
        self.assertTrue(item.is_visible)
        self.assertFalse(item.is_ghost_object)
        self.assertIsNone(item.reachable)
        self.assertIsNone(item.planned)
        self.assertEqual(item.joint_configs, [])
        self.assertEqual(item.selected_config_idx, 0)
        self.assertFalse(item.ik_loading)
        self.assertIsNone(item.blending)
        self.assertIsNone(item.limits_override)

    async def test_selected_joint_config_returns_correct_config(self):
        pose = WSPose(pose=[0, 0, 0, 0, 0, 0])
        item = PoseItem(prim_path="/World/p0", name="P", pose=pose)
        item.joint_configs = [
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        ]
        item.selected_config_idx = 1

        self.assertEqual(item.selected_joint_config, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])

    async def test_selected_joint_config_returns_none_when_empty(self):
        pose = WSPose(pose=[0, 0, 0, 0, 0, 0])
        item = PoseItem(prim_path="/World/p0", name="P", pose=pose)
        self.assertIsNone(item.selected_joint_config)

    async def test_selected_joint_config_returns_none_when_idx_out_of_range(self):
        pose = WSPose(pose=[0, 0, 0, 0, 0, 0])
        item = PoseItem(prim_path="/World/p0", name="P", pose=pose)
        item.joint_configs = [[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]]
        item.selected_config_idx = 5  # Out of range
        self.assertIsNone(item.selected_joint_config)

    async def test_update_pose(self):
        pose = WSPose(pose=[0, 0, 0, 0, 0, 0])
        item = PoseItem(prim_path="/World/p0", name="P", pose=pose)

        new_pose = WSPose(pose=[100.0, 200.0, 300.0, 1.0, 2.0, 3.0])
        item.update_pose(new_pose)

        self.assertEqual(item.pose, new_pose)
        self.assertIn("100.0", item.pose_model.get_value_as_string())

    async def test_set_name(self):
        pose = WSPose(pose=[0, 0, 0, 0, 0, 0])
        item = PoseItem(prim_path="/World/p0", name="Original", pose=pose)

        item.set_name("Renamed")
        self.assertEqual(item.name_model.get_value_as_string(), "Renamed")

    async def test_detail_children_created(self):
        pose = WSPose(pose=[0, 0, 0, 0, 0, 0])
        item = PoseItem(prim_path="/World/p0", name="P", pose=pose)

        self.assertEqual(len(item._detail_children), 3)
        self.assertEqual(item._detail_children[0].detail_type, "tcp")
        self.assertEqual(item._detail_children[1].detail_type, "joint_config")
        self.assertEqual(item._detail_children[2].detail_type, "overrides")
        for child in item._detail_children:
            self.assertEqual(child.parent, item)


class TestPoseModel(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.model = PoseModel()

    async def tearDown(self):
        pass

    async def test_initial_state_empty(self):
        self.assertEqual(self.model.items, [])
        self.assertEqual(self.model.get_item_children(None), [])

    async def test_add_pose(self):
        pose = WSPose(pose=[100, 200, 300, 0, 0, 0])
        item = self.model.add_pose("/World/p0", "Pose_0", pose)

        self.assertIsInstance(item, PoseItem)
        self.assertEqual(len(self.model.items), 1)
        self.assertEqual(self.model.items[0].prim_path, "/World/p0")

    async def test_add_multiple_poses(self):
        for i in range(3):
            self.model.add_pose(
                f"/World/p{i}", f"Pose_{i}", WSPose(pose=[i * 100, 0, 0, 0, 0, 0])
            )

        self.assertEqual(len(self.model.items), 3)

    async def test_remove_pose(self):
        self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        self.model.add_pose("/World/p1", "P1", WSPose(pose=[0, 0, 0, 0, 0, 0]))

        self.model.remove_pose("/World/p0")

        self.assertEqual(len(self.model.items), 1)
        self.assertEqual(self.model.items[0].prim_path, "/World/p1")

    async def test_remove_nonexistent_pose(self):
        self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        self.model.remove_pose("/World/nonexistent")
        self.assertEqual(len(self.model.items), 1)

    async def test_get_item_by_path(self):
        self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        self.model.add_pose("/World/p1", "P1", WSPose(pose=[0, 0, 0, 0, 0, 0]))

        item = self.model.get_item_by_path("/World/p1")
        self.assertIsNotNone(item)
        self.assertEqual(item.prim_path, "/World/p1")

    async def test_get_item_by_path_not_found(self):
        item = self.model.get_item_by_path("/World/missing")
        self.assertIsNone(item)

    async def test_get_item_index(self):
        self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        item = self.model.add_pose("/World/p1", "P1", WSPose(pose=[0, 0, 0, 0, 0, 0]))

        self.assertEqual(self.model.get_item_index(item), 1)

    async def test_get_item_index_not_found(self):
        pose = WSPose(pose=[0, 0, 0, 0, 0, 0])
        orphan = PoseItem(prim_path="/World/orphan", name="O", pose=pose)
        self.assertEqual(self.model.get_item_index(orphan), -1)

    async def test_move_up(self):
        self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        item = self.model.add_pose("/World/p1", "P1", WSPose(pose=[0, 0, 0, 0, 0, 0]))

        self.model.move_up(item)

        self.assertEqual(self.model.items[0].prim_path, "/World/p1")
        self.assertEqual(self.model.items[1].prim_path, "/World/p0")

    async def test_move_up_first_item_no_change(self):
        item = self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        self.model.add_pose("/World/p1", "P1", WSPose(pose=[0, 0, 0, 0, 0, 0]))

        self.model.move_up(item)

        self.assertEqual(self.model.items[0].prim_path, "/World/p0")

    async def test_move_down(self):
        item = self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        self.model.add_pose("/World/p1", "P1", WSPose(pose=[0, 0, 0, 0, 0, 0]))

        self.model.move_down(item)

        self.assertEqual(self.model.items[0].prim_path, "/World/p1")
        self.assertEqual(self.model.items[1].prim_path, "/World/p0")

    async def test_move_down_last_item_no_change(self):
        self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        item = self.model.add_pose("/World/p1", "P1", WSPose(pose=[0, 0, 0, 0, 0, 0]))

        self.model.move_down(item)

        self.assertEqual(self.model.items[1].prim_path, "/World/p1")

    async def test_clear(self):
        for i in range(5):
            self.model.add_pose(
                f"/World/p{i}", f"P{i}", WSPose(pose=[0, 0, 0, 0, 0, 0])
            )

        self.model.clear()
        self.assertEqual(self.model.items, [])

    async def test_get_ordered_paths(self):
        self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        self.model.add_pose("/World/p1", "P1", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        self.model.add_pose("/World/p2", "P2", WSPose(pose=[0, 0, 0, 0, 0, 0]))

        paths = self.model.get_ordered_paths()
        self.assertEqual(paths, ["/World/p0", "/World/p1", "/World/p2"])

    async def test_get_item_value_model_count(self):
        self.assertEqual(self.model.get_item_value_model_count(), 3)

    async def test_get_item_children_for_pose_item(self):
        item = self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        item.motion_type = "PathJointPTP"

        children = self.model.get_item_children(item)
        # Without blending/limits_override, overrides detail should be filtered
        detail_types = [c.detail_type for c in children]
        self.assertIn("tcp", detail_types)
        self.assertIn("joint_config", detail_types)
        self.assertNotIn("overrides", detail_types)

    async def test_get_item_children_includes_overrides_when_set(self):
        item = self.model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))
        item.blending = {"type": "velocity", "value": 50}

        children = self.model.get_item_children(item)
        detail_types = [c.detail_type for c in children]
        self.assertIn("overrides", detail_types)
