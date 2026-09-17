"""An asset can name the articulation a motion group belongs to.

The joint traversal only reaches a root that sits on the far side of the motion
group's root_joint. An asset that folds several motion groups into one
articulation puts the root somewhere no traversal from the motion group leads -
so it authors the answer, and the extension reads it.
"""

import omni.kit.test
import omni.usd
from pxr import Sdf, UsdGeom, UsdPhysics

from wandelbots.omni.manipulators.articulation_cache import (
    ARTICULATION_ROOT_RELATIONSHIP,
    get_root_articulation_path,
)


def _define_articulation(stage, path: str):
    prim = UsdGeom.Xform.Define(stage, path).GetPrim()
    UsdPhysics.ArticulationRootAPI.Apply(prim)
    return prim


class TestAuthoredArticulationRoot(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        await omni.usd.get_context().new_stage_async()
        self.stage = omni.usd.get_context().get_stage()

    async def test_an_authored_root_in_another_branch_is_used(self):
        _define_articulation(self.stage, "/World/LiftUnit")
        motion_group = UsdGeom.Xform.Define(
            self.stage, "/World/Robot/ArmLeft"
        ).GetPrim()
        motion_group.CreateRelationship(ARTICULATION_ROOT_RELATIONSHIP).SetTargets(
            [Sdf.Path("/World/LiftUnit")]
        )

        self.assertEqual("/World/LiftUnit", get_root_articulation_path(motion_group))

    async def test_without_the_relationship_the_traversal_still_decides(self):
        """Assets that predate the relationship must behave exactly as before."""
        motion_group = UsdGeom.Xform.Define(
            self.stage, "/World/Robot/ArmLeft"
        ).GetPrim()

        self.assertEqual(
            "/World/Robot/ArmLeft", get_root_articulation_path(motion_group)
        )

    async def test_an_empty_relationship_falls_back(self):
        motion_group = UsdGeom.Xform.Define(
            self.stage, "/World/Robot/ArmLeft"
        ).GetPrim()
        motion_group.CreateRelationship(ARTICULATION_ROOT_RELATIONSHIP)

        self.assertEqual(
            "/World/Robot/ArmLeft", get_root_articulation_path(motion_group)
        )

    async def test_a_root_that_does_not_exist_falls_back(self):
        """A stale path must not send SingleArticulation at nothing."""
        motion_group = UsdGeom.Xform.Define(
            self.stage, "/World/Robot/ArmLeft"
        ).GetPrim()
        motion_group.CreateRelationship(ARTICULATION_ROOT_RELATIONSHIP).SetTargets(
            [Sdf.Path("/World/Gone")]
        )

        self.assertEqual(
            "/World/Robot/ArmLeft", get_root_articulation_path(motion_group)
        )
