"""Unit tests for collision export helpers: robot-own vs equipment
classification of link-parented colliders, and the link-chain reachability
check that guards the export."""

from __future__ import annotations

import omni.kit.test
from pxr import Sdf, Usd, UsdGeom

from wandelbots.omni.core.collision.collision_export_service import (
    UnsupportedLinkAttachmentError,
    is_stage_authored_equipment,
    raise_for_unreachable_link_attachments,
)

ROBOT_ASSET = """#usda 1.0
(
    defaultPrim = "robot"
)

def Xform "robot"
{
    def Xform "link_0"
    {
        def Xform "visuals"
        {
            def Mesh "mesh_0"
            {
            }
        }
    }
}
"""

EQUIPMENT_ASSET = """#usda 1.0
(
    defaultPrim = "dresspack"
)

def Xform "dresspack"
{
    def Xform "visuals"
    {
        def Mesh "hose"
        {
        }
    }
}
"""


def _anonymous_layer(content: str) -> Sdf.Layer:
    layer = Sdf.Layer.CreateAnonymous(".usda")
    layer.ImportFromString(content)
    return layer


class TestIsStageAuthoredEquipment(omni.kit.test.AsyncTestCase):
    def _make_referenced_robot_stage(self) -> tuple[Usd.Stage, Sdf.Layer]:
        """Stage with /World/Robot referencing a robot asset layer, so the
        robot's own prims are not defined in the stage's local layer stack."""
        stage = Usd.Stage.CreateInMemory("TestIsStageAuthoredEquipment")
        robot_layer = _anonymous_layer(ROBOT_ASSET)
        robot = UsdGeom.Xform.Define(stage, "/World/Robot").GetPrim()
        robot.GetReferences().AddReference(robot_layer.identifier)
        return stage, robot_layer

    async def test_referenced_robot_geometry_is_not_equipment(self):
        stage, _layer = self._make_referenced_robot_stage()
        link = stage.GetPrimAtPath("/World/Robot/link_0")
        collider = stage.GetPrimAtPath("/World/Robot/link_0/visuals/mesh_0")
        self.assertTrue(collider.IsValid())

        self.assertFalse(is_stage_authored_equipment(collider, link, stage))

    async def test_locally_defined_prim_is_equipment(self):
        stage, _layer = self._make_referenced_robot_stage()
        link = stage.GetPrimAtPath("/World/Robot/link_0")
        cube = UsdGeom.Cube.Define(stage, "/World/Robot/link_0/Cube").GetPrim()

        self.assertTrue(is_stage_authored_equipment(cube, link, stage))

    async def test_referenced_equipment_with_own_visuals_is_equipment(self):
        """Regression: equipment assets often carry their own 'visuals' scope
        (link_0/dresspack/visuals/hose). Only the link-level visuals scope
        marks robot geometry - a nested one must not disqualify the extra."""
        stage, _layer = self._make_referenced_robot_stage()
        link = stage.GetPrimAtPath("/World/Robot/link_0")
        equipment_layer = _anonymous_layer(EQUIPMENT_ASSET)
        mount = UsdGeom.Xform.Define(stage, "/World/Robot/link_0/dresspack").GetPrim()
        mount.GetReferences().AddReference(equipment_layer.identifier)
        collider = stage.GetPrimAtPath("/World/Robot/link_0/dresspack/visuals/hose")
        self.assertTrue(collider.IsValid())

        self.assertTrue(is_stage_authored_equipment(collider, link, stage))

    async def test_flattened_robot_visuals_is_not_equipment(self):
        """Robots flattened into the stage's own layers define everything
        locally; the link-level visuals scope still counts as the robot."""
        stage = Usd.Stage.CreateInMemory("TestIsStageAuthoredEquipment")
        link = UsdGeom.Xform.Define(stage, "/World/Robot/link_0").GetPrim()
        visuals = UsdGeom.Xform.Define(stage, "/World/Robot/link_0/visuals").GetPrim()
        mesh = UsdGeom.Mesh.Define(
            stage, "/World/Robot/link_0/visuals/mesh_0"
        ).GetPrim()

        self.assertFalse(is_stage_authored_equipment(visuals, link, stage))
        self.assertFalse(is_stage_authored_equipment(mesh, link, stage))

    async def test_flattened_positioner_visuals_scope_is_not_equipment(self):
        """Positioners/rails name their collider scopes visuals_<n>."""
        stage = Usd.Stage.CreateInMemory("TestIsStageAuthoredEquipment")
        link = UsdGeom.Xform.Define(stage, "/World/Positioner/link_1").GetPrim()
        visuals = UsdGeom.Xform.Define(
            stage, "/World/Positioner/link_1/visuals_1"
        ).GetPrim()

        self.assertFalse(is_stage_authored_equipment(visuals, link, stage))

    async def test_visuals_prefixed_equipment_name_is_equipment(self):
        """A user prim whose name merely starts with 'visuals' is not the
        robot's collider scope."""
        stage = Usd.Stage.CreateInMemory("TestIsStageAuthoredEquipment")
        link = UsdGeom.Xform.Define(stage, "/World/Robot/link_0").GetPrim()
        mount = UsdGeom.Xform.Define(
            stage, "/World/Robot/link_0/visuals_mount"
        ).GetPrim()

        self.assertTrue(is_stage_authored_equipment(mount, link, stage))


class TestLinkAttachmentReachability(omni.kit.test.AsyncTestCase):
    async def test_attachments_within_the_chain_are_accepted(self):
        link_attachments = {"/World/Robot/link_1": {"link_1/Cube": None}}
        link_index_by_path = {"/World/Robot/link_1": 1}

        raise_for_unreachable_link_attachments(
            link_attachments, link_index_by_path, link_count=6
        )

    async def test_attachment_beyond_the_chain_raises(self):
        """Colliders on a link the kinematic chain does not reach used to be
        dropped with a warning, which shipped a setup missing geometry."""
        link_attachments = {
            "/World/Robot/link_1": {"link_1/Cube": None},
            "/World/Robot/link_9": {"link_9/Cube": None, "link_9/Sphere": None},
        }
        link_index_by_path = {"/World/Robot/link_1": 1, "/World/Robot/link_9": 9}

        with self.assertRaises(UnsupportedLinkAttachmentError) as raised:
            raise_for_unreachable_link_attachments(
                link_attachments, link_index_by_path, link_count=6
            )

        message = str(raised.exception)
        self.assertIn("link_9/Cube", message)
        self.assertIn("link_9/Sphere", message)
        self.assertNotIn("link_1/Cube", message)
