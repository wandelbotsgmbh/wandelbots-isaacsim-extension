"""Unit tests for the stage-change filters that guard the main thread.

Both cover the same failure: a viewport gizmo edit resyncs a property path,
and code that treats every resync as structural then does full-stage work per
mouse sample.
"""

import omni.kit.test
import omni.usd
from contextlib import contextmanager
from unittest import mock
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

import wandelbots.omni.manipulators.articulation_cache as articulation_cache
from wandelbots.omni.tests.stage_utils import use_stage
from wandelbots.omni.manipulators.articulation_cache import get_articulation_cache
from wandelbots.omni.manipulators.motion_group import MotionGroup
from wandelbots.omni.manipulators.utils import (
    get_scene_motion_group_prim_paths,
    release_scene_motion_group_prim_cache,
    scene_motion_group_prim_cache_is_warm,
)
from wandelbots.omni.utils.prims import is_pose_xform_op

ROBOT_A = "/World/RobotA"
ROBOT_B = "/World/RobotB"


class TestSceneMotionGroupPrimCacheResync(omni.kit.test.AsyncTestCase):
    """The prim cache must only drop on edits that change the prim set.

    Whether it survived an edit is not visible through the public call, which
    simply re-traverses and returns the same answer either way.
    """

    async def setUp(self):
        release_scene_motion_group_prim_cache()

    async def tearDown(self):
        release_scene_motion_group_prim_cache()

    @contextmanager
    def _create_stage(self):
        stage = Usd.Stage.CreateInMemory("TestSceneMotionGroupPrimCacheResync")
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        UsdGeom.Xform.Define(stage, "/World")
        robot = UsdGeom.Xform.Define(stage, ROBOT_A).GetPrim()
        UsdPhysics.ArticulationRootAPI.Apply(robot)
        with use_stage(stage):
            yield stage

    async def test_new_xform_op_keeps_cache(self):
        with self._create_stage() as stage:
            self.assertEqual(get_scene_motion_group_prim_paths(), [ROBOT_A])
            self.assertTrue(scene_motion_group_prim_cache_is_warm())

            # What the gizmo does on a first scale: create xformOp:scale and
            # append it to xformOpOrder. Resyncs the property, not the prim.
            xformable = UsdGeom.Xformable(stage.GetPrimAtPath(ROBOT_A))
            xformable.AddScaleOp().Set(Gf.Vec3f(2.0, 2.0, 2.0))

            self.assertTrue(scene_motion_group_prim_cache_is_warm())

    async def test_new_articulation_prim_drops_cache(self):
        with self._create_stage() as stage:
            self.assertEqual(get_scene_motion_group_prim_paths(), [ROBOT_A])

            robot_b = UsdGeom.Xform.Define(stage, ROBOT_B).GetPrim()
            UsdPhysics.ArticulationRootAPI.Apply(robot_b)

            self.assertFalse(scene_motion_group_prim_cache_is_warm())
            self.assertEqual(
                sorted(get_scene_motion_group_prim_paths()), [ROBOT_A, ROBOT_B]
            )

    async def test_removed_prim_drops_cache(self):
        with self._create_stage() as stage:
            self.assertEqual(get_scene_motion_group_prim_paths(), [ROBOT_A])

            stage.RemovePrim(ROBOT_A)

            self.assertFalse(scene_motion_group_prim_cache_is_warm())
            self.assertEqual(get_scene_motion_group_prim_paths(), [])


class TestPoseXformOpFilter(omni.kit.test.AsyncTestCase):
    """PrimPoseWatcher must react to every xform op that moves a prim."""

    async def test_accepts_every_pose_op(self):
        for op in (
            "xformOp:translate",
            "xformOp:orient",
            "xformOp:rotateXYZ",
            "xformOp:rotateZYX",
            "xformOp:scale",
            "xformOp:transform",
            "xformOpOrder",
        ):
            with self.subTest(op=op):
                self.assertTrue(is_pose_xform_op(Sdf.Path(f"{ROBOT_A}.{op}")))

    async def test_rejects_unrelated_paths(self):
        self.assertFalse(is_pose_xform_op(None))
        self.assertFalse(is_pose_xform_op(Sdf.Path(ROBOT_A)))
        self.assertFalse(is_pose_xform_op(Sdf.Path(f"{ROBOT_A}.purpose")))
        self.assertFalse(is_pose_xform_op(Sdf.Path(f"{ROBOT_A}.visibility")))


ROBOT = "/World/CacheRobot"
LINK_0 = f"{ROBOT}/link_0"
LINK_1 = f"{ROBOT}/link_1"
# A second branch the motion group is not under, the shape an asset uses when
# it folds several motion groups into one articulation.
LIFT = "/World/CacheLift"
LIFT_LINK_0 = f"{LIFT}/link_0"
LIFT_LINK_1 = f"{LIFT}/link_1"


async def _new_robot_stage() -> Usd.Stage:
    """Context stage with an articulation root whose PhysX anchor is link_0."""
    await omni.usd.get_context().new_stage_async()
    stage = omni.usd.get_context().get_stage()
    UsdGeom.Xform.Define(stage, "/World")
    UsdPhysics.ArticulationRootAPI.Apply(UsdGeom.Xform.Define(stage, ROBOT).GetPrim())
    UsdPhysics.RigidBodyAPI.Apply(UsdGeom.Xform.Define(stage, LINK_0).GetPrim())
    return stage


def _move_anchor_to_link_1(stage: Usd.Stage) -> None:
    """Move the prim PhysX registers the articulation under from link_0 to link_1."""
    stage.RemovePrim(LINK_0)
    UsdPhysics.RigidBodyAPI.Apply(UsdGeom.Xform.Define(stage, LINK_1).GetPrim())


class TestArticulationCacheResync(omni.kit.test.AsyncTestCase):
    """Articulation handles must follow hierarchy edits, not wait for STOP.

    Runs against the context stage, the one the cache tracks.
    """

    async def setUp(self):
        self.stage = await _new_robot_stage()
        self.robot = self.stage.GetPrimAtPath(ROBOT)
        self.cache = get_articulation_cache()

    async def tearDown(self):
        await omni.usd.get_context().new_stage_async()

    async def test_transform_edit_keeps_handle(self):
        handle = self.cache.get_articulation_for_motion_group(self.robot)

        UsdGeom.Xformable(self.robot).AddScaleOp().Set(Gf.Vec3f(2.0, 2.0, 2.0))

        self.assertIs(handle, self.cache.get_articulation_for_motion_group(self.robot))

    async def test_unrelated_prim_keeps_handle(self):
        handle = self.cache.get_articulation_for_motion_group(self.robot)

        UsdGeom.Xform.Define(self.stage, "/World/Unrelated")

        self.assertIs(handle, self.cache.get_articulation_for_motion_group(self.robot))

    async def test_moved_rigid_body_re_resolves_handle(self):
        handle = self.cache.get_articulation_for_motion_group(self.robot)
        self.assertEqual(LINK_0, handle.articulation_root_path)

        _move_anchor_to_link_1(self.stage)

        refreshed = self.cache.get_articulation_for_motion_group(self.robot)
        self.assertEqual(LINK_1, refreshed.articulation_root_path)

    async def test_an_authored_root_in_another_branch_still_follows_edits(self):
        """The motion group is not under the articulation it names, so a resync
        there does not touch its own path - the resolution has to drop anyway."""
        UsdPhysics.ArticulationRootAPI.Apply(
            UsdGeom.Xform.Define(self.stage, LIFT).GetPrim()
        )
        UsdPhysics.RigidBodyAPI.Apply(
            UsdGeom.Xform.Define(self.stage, LIFT_LINK_0).GetPrim()
        )
        self.robot.CreateRelationship(
            articulation_cache.ARTICULATION_ROOT_RELATIONSHIP
        ).SetTargets([Sdf.Path(LIFT)])

        handle = self.cache.get_articulation_for_motion_group(self.robot)
        self.assertEqual(LIFT_LINK_0, handle.articulation_root_path)

        self.stage.RemovePrim(LIFT_LINK_0)
        UsdPhysics.RigidBodyAPI.Apply(
            UsdGeom.Xform.Define(self.stage, LIFT_LINK_1).GetPrim()
        )

        refreshed = self.cache.get_articulation_for_motion_group(self.robot)
        self.assertEqual(LIFT_LINK_1, refreshed.articulation_root_path)


class TestMotionGroupArticulationResync(omni.kit.test.AsyncTestCase):
    """A moved anchor must reach the live stream, not just a fresh cache query.

    MotionGroupService keeps one MotionGroup alive for the whole PLAY session,
    so evicting the cache maps does nothing on its own for a running stream.
    """

    async def setUp(self):
        self.stage = await _new_robot_stage()
        self.robot = self.stage.GetPrimAtPath(ROBOT)

    async def tearDown(self):
        await omni.usd.get_context().new_stage_async()

    def _bare_motion_group(self) -> MotionGroup:
        """MotionGroup without running __init__, which needs a NOVA config."""
        motion_group = MotionGroup.__new__(MotionGroup)
        motion_group._stage = self.stage
        motion_group._configuration = mock.MagicMock()
        motion_group._configuration.prim_path = ROBOT
        motion_group._articulation_cache_handle = (
            get_articulation_cache().get_articulation_for_motion_group(self.robot)
        )
        return motion_group

    async def test_moved_rigid_body_reaches_the_motion_group(self):
        motion_group = self._bare_motion_group()

        # Stand in for SingleArticulation so the assertion reads as the
        # anchor path and no articulation is built without physics.
        with mock.patch.object(
            articulation_cache, "SingleArticulation", lambda path: path
        ):
            self.assertEqual(LINK_0, motion_group.articulation)

            _move_anchor_to_link_1(self.stage)

            self.assertEqual(LINK_1, motion_group.articulation)

    async def test_deleted_prim_keeps_the_last_handle(self):
        motion_group = self._bare_motion_group()

        self.stage.RemovePrim(ROBOT)

        with mock.patch.object(
            articulation_cache, "SingleArticulation", lambda path: path
        ):
            # No prim to resolve from, so the last handle stands and the
            # caller's is_valid check decides.
            self.assertEqual(LINK_0, motion_group.articulation)
