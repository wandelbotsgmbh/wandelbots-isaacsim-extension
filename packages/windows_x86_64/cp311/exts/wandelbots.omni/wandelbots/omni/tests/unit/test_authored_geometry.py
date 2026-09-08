"""Unit tests for the authored-collider expansion shared by the collision
setup export and the reachability Attach Tool flow."""

from __future__ import annotations

import omni.kit.test
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

from wandelbots.omni.core.collision.authored_geometry import (
    collider_points_local,
    expand_collider_prims,
)


class TestExpandColliderPrims(omni.kit.test.AsyncTestCase):
    def _make_group_collider_stage(self) -> Usd.Stage:
        """A group prim carrying the CollisionAPI (the Colliders Preset on an
        assembly) over a mix of geometry children."""
        stage = Usd.Stage.CreateInMemory("TestExpandColliderPrims")
        group = UsdGeom.Xform.Define(stage, "/World/Group").GetPrim()
        UsdPhysics.CollisionAPI.Apply(group)
        group.CreateAttribute("physics:approximation", Sdf.ValueTypeNames.Token).Set(
            "boundingCube"
        )

        UsdGeom.Plane.Define(stage, "/World/Group/Floor")
        UsdGeom.Cube.Define(stage, "/World/Group/Box")
        mesh = UsdGeom.Mesh.Define(stage, "/World/Group/Part")
        mesh.GetPointsAttr().Set([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)])
        # A descendant with its own CollisionAPI is visited on its own by the
        # sweep and must not be duplicated by the expansion.
        tagged = UsdGeom.Mesh.Define(stage, "/World/Group/OwnCollider").GetPrim()
        UsdPhysics.CollisionAPI.Apply(tagged)
        return stage

    async def test_expansion_keeps_planes(self):
        # A plane under a group-level CollisionAPI is a supported export
        # collider (NOVA has a parametric plane) and must not vanish during
        # expansion; the tool flow rejects it later via collider_points_local.
        stage = self._make_group_collider_stage()
        group = stage.GetPrimAtPath("/World/Group")

        expanded = expand_collider_prims([group], Usd.TimeCode.Default())
        paths = {prim.GetPath().pathString for prim, _ in expanded}

        self.assertIn("/World/Group/Floor", paths)
        self.assertIn("/World/Group/Box", paths)
        self.assertIn("/World/Group/Part", paths)
        self.assertNotIn("/World/Group/OwnCollider", paths)

    async def test_expansion_inherits_group_approximation(self):
        stage = self._make_group_collider_stage()
        group = stage.GetPrimAtPath("/World/Group")

        expanded = dict(
            (prim.GetPath().pathString, approximation)
            for prim, approximation in expand_collider_prims(
                [group], Usd.TimeCode.Default()
            )
        )

        self.assertEqual(expanded["/World/Group/Part"], "boundingCube")

    async def test_plane_has_no_point_set(self):
        # The consumer contract for planes: collider_points_local returns
        # None, so the export builds its parametric plane and the tool flow
        # skips it with a warning.
        stage = self._make_group_collider_stage()
        plane = stage.GetPrimAtPath("/World/Group/Floor")

        self.assertIsNone(collider_points_local(plane, Usd.TimeCode.Default()))
