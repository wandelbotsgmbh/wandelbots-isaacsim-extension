"""Unit tests for resolving a viewport selection to a pose prim.

The tool targets exactly two kinds of object: a pose prim and a ghost object.
Clicking a ghost object in the viewport selects the mesh inside it, not the
prim carrying GhostObjectAPI, so "which prim did that click mean" needs the
walk these tests pin down.
"""

import omni.kit.test

from wandelbots.omni.ui.tool.reachability_envelope.reachability_envelope_window import (
    deepest_pose_ancestor,
    resolve_pose_prim_path,
)


def _is_pose(*paths: str):
    known = set(paths)
    return lambda path: path in known


class TestResolvePosePrimPath(omni.kit.test.AsyncTestCase):
    async def test_selected_prim_is_the_pose(self):
        self.assertEqual(
            resolve_pose_prim_path(
                "/World/ghosts/Ghost_01", _is_pose("/World/ghosts/Ghost_01")
            ),
            "/World/ghosts/Ghost_01",
        )

    async def test_click_on_a_mesh_resolves_to_the_ghost_object(self):
        self.assertEqual(
            resolve_pose_prim_path(
                "/World/ghosts/Ghost_01/geometry/mesh",
                _is_pose("/World/ghosts/Ghost_01"),
            ),
            "/World/ghosts/Ghost_01",
        )

    async def test_innermost_pose_wins(self):
        """A pose nested inside another pose is the one that was clicked."""
        self.assertEqual(
            resolve_pose_prim_path(
                "/World/poses/group/Pose_02/gizmo",
                _is_pose("/World/poses/group", "/World/poses/group/Pose_02"),
            ),
            "/World/poses/group/Pose_02",
        )

    async def test_a_folder_around_poses_is_not_a_pose(self):
        """Only poses and ghost objects match, so a plain group resolves to nothing."""
        self.assertIsNone(
            resolve_pose_prim_path(
                "/World/poses/group", _is_pose("/World/poses/group/Pose_02")
            )
        )

    async def test_unrelated_selection_resolves_to_nothing(self):
        self.assertIsNone(
            resolve_pose_prim_path(
                "/World/robot/link_3", _is_pose("/World/poses/group/Pose_02")
            )
        )

    async def test_the_walk_stops_before_the_pseudo_root(self):
        """/World is never a pose, however it happens to be classified."""
        self.assertIsNone(resolve_pose_prim_path("/World/robot", _is_pose("/World")))

    async def test_empty_and_relative_paths_are_rejected(self):
        self.assertIsNone(resolve_pose_prim_path("", _is_pose()))
        self.assertIsNone(resolve_pose_prim_path("World/poses", _is_pose()))


class TestDeepestPoseAncestor(omni.kit.test.AsyncTestCase):
    async def test_innermost_pose_wins_over_the_enclosing_one(self):
        poses = ["/World/poses/group", "/World/poses/group/Pose_02"]
        self.assertEqual(
            deepest_pose_ancestor("/World/poses/group/Pose_02/mesh", poses),
            "/World/poses/group/Pose_02",
        )

    async def test_exact_match(self):
        poses = ["/World/poses/group/Pose_02"]
        self.assertEqual(
            deepest_pose_ancestor("/World/poses/group/Pose_02", poses),
            "/World/poses/group/Pose_02",
        )

    async def test_a_sibling_prefix_is_not_a_match(self):
        """/World/poses/group2 must not match a path under /World/poses/group."""
        poses = ["/World/poses/group2"]
        self.assertIsNone(deepest_pose_ancestor("/World/poses/group/Pose_02", poses))

    async def test_no_match_yields_none(self):
        self.assertIsNone(deepest_pose_ancestor("/World/robot", ["/World/poses/p"]))
