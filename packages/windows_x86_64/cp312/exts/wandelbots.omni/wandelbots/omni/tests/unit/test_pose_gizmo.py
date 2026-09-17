import omni.kit.test
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

from wandelbots.omni.ui.tool.trajectory_planner.pose_utils import (
    GIZMO_PROTOTYPE_PATH,
    GIZMO_PROTOTYPE_SCOPE,
    create_pose_prim,
    embed_gizmo,
)


class TestPoseGizmo(omni.kit.test.AsyncTestCase):
    def make_stage(self) -> Usd.Stage:
        stage = Usd.Stage.CreateInMemory("TestPoseGizmo")
        UsdGeom.Xform.Define(stage, "/World")
        return stage

    def reopen_without_session_layer(self, stage: Usd.Stage) -> Usd.Stage:
        """Compose the stage from its root layer alone, the way a reload does."""
        reopened = Usd.Stage.Open(Sdf.Layer.CreateAnonymous())
        reopened.GetRootLayer().TransferContent(stage.GetRootLayer())
        return reopened

    async def test_poses_share_a_single_gizmo_prototype(self):
        stage = self.make_stage()

        first_path = create_pose_prim(stage, parent_path="/World")
        second_path = create_pose_prim(stage, parent_path="/World")

        self.assertEqual(1, len(stage.GetPrototypes()))
        layer = stage.GetRootLayer()
        self.assertTrue(layer.GetPrimAtPath(GIZMO_PROTOTYPE_PATH).nameChildren)
        for path in (first_path, second_path):
            self.assertTrue(stage.GetPrimAtPath(path).IsInstance())
            # The geometry lives in the template only, never copied into the pose.
            self.assertFalse(layer.GetPrimAtPath(path).nameChildren)

    async def test_template_is_hidden_but_poses_are_not(self):
        stage = self.make_stage()

        pose_path = create_pose_prim(stage, parent_path="/World")

        scope = UsdGeom.Scope.Get(stage, GIZMO_PROTOTYPE_SCOPE)
        self.assertEqual(UsdGeom.Tokens.invisible, scope.GetVisibilityAttr().Get())
        self.assertEqual(
            UsdGeom.Tokens.inherited,
            UsdGeom.Imageable(stage.GetPrimAtPath(pose_path)).ComputeVisibility(),
        )

    async def test_pose_keeps_its_gizmo_when_a_marker_was_created_first(self):
        stage = self.make_stage()

        # Trajectory markers are authored into the session layer, which is dropped
        # on reload.
        previous_target = stage.GetEditTarget()
        stage.SetEditTarget(stage.GetSessionLayer())
        embed_gizmo(stage, "/World/marker_0")
        stage.SetEditTarget(previous_target)

        pose_path = create_pose_prim(stage, parent_path="/World")

        self.assertIsNotNone(stage.GetRootLayer().GetPrimAtPath(GIZMO_PROTOTYPE_PATH))
        reopened = self.reopen_without_session_layer(stage)
        self.assertEqual(1, len(reopened.GetPrototypes()))
        self.assertTrue(reopened.GetPrimAtPath(pose_path).IsInstance())
        self.assertFalse(reopened.GetPrimAtPath("/World/marker_0"))

    async def test_embed_gizmo_replaces_what_the_prim_was(self):
        stage = self.make_stage()
        mesh = UsdGeom.Mesh.Define(stage, "/World/Thing")
        UsdGeom.Xform.Define(stage, "/World/Thing/Leftover")
        UsdPhysics.RigidBodyAPI.Apply(mesh.GetPrim())
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())

        embed_gizmo(stage, "/World/Thing")

        prim = stage.GetPrimAtPath("/World/Thing")
        self.assertEqual("Xform", prim.GetTypeName())
        self.assertFalse(prim.HasAPI(UsdPhysics.RigidBodyAPI))
        self.assertFalse(prim.HasAPI(UsdPhysics.CollisionAPI))
        self.assertFalse(stage.GetPrimAtPath("/World/Thing/Leftover"))

    async def test_template_never_references_itself(self):
        stage = self.make_stage()

        create_pose_prim(stage, parent_path="/World")
        embed_gizmo(stage, GIZMO_PROTOTYPE_PATH)

        self.assertFalse(stage.GetPrimAtPath(GIZMO_PROTOTYPE_PATH).IsInstance())
