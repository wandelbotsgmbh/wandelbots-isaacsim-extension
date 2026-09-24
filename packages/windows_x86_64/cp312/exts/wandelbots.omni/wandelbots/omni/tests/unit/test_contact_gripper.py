"""Unit tests for the contact gripper's candidate scan.

The scan skips a subtree whose bound misses the helper volume, so that bound
has to cover everything inside the subtree. Two kinds of authoring make a
bound smaller than the geometry below it: an out-of-date extentsHint on a
model prim, which is why the scan reads bounds without hints, and an
invisible descendant, which is why it prunes on bounds that ignore
visibility.

The attach_all tests cover fixing every overlapping candidate at once and
skipping what already follows the helper.
"""

from __future__ import annotations

import omni.kit.test
import omni.timeline
import omni.usd
from pxr import Gf, Kind, Usd, UsdGeom, Vt

from wandelbots.omni.utils.contact_gripper import ContactGripperModel

_HELPER_PATH = "/World/Helper"
_GROUP_PATH = "/World/Group"
_TARGET_PATH = "/World/Group/Target"


class TestContactGripperCandidateScan(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        await omni.usd.get_context().new_stage_async()
        self.gripper = ContactGripperModel()

    async def tearDown(self):
        self.gripper.restore_all()
        self.gripper.destroy()

    def _build_stage_with_stale_extents_hint(self) -> None:
        """A helper cube overlapping a cube whose parent claims, through a
        wrong extentsHint, to sit somewhere far away.

        The parent is a Scope. It has a bound, so the scan can skip it, but it
        cannot be attached itself, so the scan has to look inside it to find
        the target.
        """
        stage = omni.usd.get_context().get_stage()
        world = UsdGeom.Xform.Define(stage, "/World")
        # The hint is only read when every ancestor is a model too.
        Usd.ModelAPI(world.GetPrim()).SetKind(Kind.Tokens.group)

        UsdGeom.Cube.Define(stage, _HELPER_PATH).GetSizeAttr().Set(1.0)

        group = UsdGeom.Scope.Define(stage, _GROUP_PATH)
        Usd.ModelAPI(group.GetPrim()).SetKind(Kind.Tokens.component)
        far_away = [Gf.Vec3f(10, 10, 10), Gf.Vec3f(11, 11, 11)]
        UsdGeom.ModelAPI(group.GetPrim()).SetExtentsHint(
            # One min/max pair per purpose: default, proxy, render.
            Vt.Vec3fArray(far_away * 3)
        )

        UsdGeom.Cube.Define(stage, _TARGET_PATH).GetSizeAttr().Set(1.0)

    def _build_stage_with_invisible_target(self) -> None:
        """A helper cube overlapping an invisible cube whose parent's only
        visible geometry sits far away.

        A bound leaves invisible descendants out, so the parent bound misses
        the helper while the invisible cube itself overlaps it.
        """
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World")
        UsdGeom.Cube.Define(stage, _HELPER_PATH).GetSizeAttr().Set(1.0)

        UsdGeom.Xform.Define(stage, _GROUP_PATH)
        far_away = UsdGeom.Cube.Define(stage, f"{_GROUP_PATH}/FarAway")
        far_away.GetSizeAttr().Set(1.0)
        far_away.AddTranslateOp().Set(Gf.Vec3d(50.0, 0.0, 0.0))

        target = UsdGeom.Cube.Define(stage, _TARGET_PATH)
        target.GetSizeAttr().Set(1.0)
        target.MakeInvisible()

    async def test_attaches_descendant_of_prim_with_stale_extents_hint(self):
        self._build_stage_with_stale_extents_hint()

        self.assertTrue(self.gripper.attach(_HELPER_PATH, [], []))
        self.assertEqual(self.gripper.attached_prim_path, _TARGET_PATH)

    async def test_attaches_invisible_descendant_of_prim_bounded_far_away(self):
        self._build_stage_with_invisible_target()

        self.assertTrue(self.gripper.attach(_HELPER_PATH, [], []))
        self.assertEqual(self.gripper.attached_prim_path, _TARGET_PATH)

    async def test_no_candidate_for_guide_prim_inside_the_helper_volume(self):
        """A guide prim has no bound of its own, so it is not a candidate.

        That is what lets the scan prune a subtree whose bound leaves guide
        geometry out.
        """
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World")
        UsdGeom.Cube.Define(stage, _HELPER_PATH).GetSizeAttr().Set(1.0)

        guide = UsdGeom.Cube.Define(stage, _TARGET_PATH)
        guide.GetSizeAttr().Set(1.0)
        guide.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)

        self.assertFalse(self.gripper.attach(_HELPER_PATH, [], []))
        self.assertEqual(self.gripper.attached_prim_path, "")

    async def test_no_candidate_outside_the_helper_volume(self):
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World")
        UsdGeom.Cube.Define(stage, _HELPER_PATH).GetSizeAttr().Set(1.0)

        distant = UsdGeom.Cube.Define(stage, "/World/Distant")
        distant.GetSizeAttr().Set(1.0)
        distant.AddTranslateOp().Set(Gf.Vec3d(50.0, 0.0, 0.0))

        self.assertFalse(self.gripper.attach(_HELPER_PATH, [], []))
        self.assertEqual(self.gripper.attached_prim_path, "")


_PARTS_PATH = "/World/Parts"
_PART_A_PATH = "/World/Parts/A"
_PART_B_PATH = "/World/Parts/B"
_FAR_AWAY_PATH = "/World/Parts/FarAway"
_PARTS_PATTERN = [f"{_PARTS_PATH}/*"]


class TestContactGripperAttachAll(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        await omni.usd.get_context().new_stage_async()
        self.gripper = ContactGripperModel()
        self._build_stage()

    async def tearDown(self):
        self.gripper.restore_all()
        self.gripper.destroy()

    def _build_stage(self) -> None:
        """A helper cube with two part cubes inside it and one far away.

        Each part carries a child cube, so the parts pattern also matches
        prims below an attached prim.
        """
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World")
        UsdGeom.Cube.Define(stage, _HELPER_PATH).GetSizeAttr().Set(1.0)
        UsdGeom.Xform.Define(stage, _PARTS_PATH)
        self._define_part(_PART_A_PATH, Gf.Vec3d(0.2, 0.0, 0.0))
        self._define_part(_PART_B_PATH, Gf.Vec3d(-0.2, 0.0, 0.0))
        self._define_part(_FAR_AWAY_PATH, Gf.Vec3d(50.0, 0.0, 0.0))

    @staticmethod
    def _define_part(path: str, translation: Gf.Vec3d) -> None:
        stage = omni.usd.get_context().get_stage()
        part = UsdGeom.Cube.Define(stage, path)
        part.GetSizeAttr().Set(0.1)
        part.AddTranslateOp().Set(translation)
        UsdGeom.Cube.Define(stage, f"{path}/Detail").GetSizeAttr().Set(0.05)

    @staticmethod
    def _world_position(path: str) -> Gf.Vec3d:
        prim = omni.usd.get_context().get_stage().GetPrimAtPath(path)
        return omni.usd.get_world_transform_matrix(prim).ExtractTranslation()

    async def test_attaches_every_overlapping_candidate(self):
        self.assertTrue(
            self.gripper.attach(_HELPER_PATH, _PARTS_PATTERN, [], attach_all=True)
        )
        self.assertEqual(self.gripper.attached_prim_paths, [_PART_A_PATH, _PART_B_PATH])
        self.assertEqual(self.gripper.attached_prim_path, _PART_B_PATH)

    async def test_without_attach_all_only_the_first_candidate_is_attached(self):
        self.assertTrue(self.gripper.attach(_HELPER_PATH, _PARTS_PATTERN, []))
        self.assertEqual(self.gripper.attached_prim_paths, [_PART_A_PATH])
        self.assertFalse(self.gripper.attach(_HELPER_PATH, _PARTS_PATTERN, []))

    async def test_skips_ancestors_of_an_attached_prim(self):
        self.gripper.attach(_HELPER_PATH, [_PART_A_PATH], [], attach_all=True)

        # "/World/*" also matches the Parts group, which holds part A.
        self.assertTrue(
            self.gripper.attach(_HELPER_PATH, ["/World/*"], [], attach_all=True)
        )
        self.assertEqual(self.gripper.attached_prim_paths, [_PART_A_PATH, _PART_B_PATH])

    async def test_attaches_prims_that_enter_the_volume_later(self):
        self.gripper.attach(_HELPER_PATH, _PARTS_PATTERN, [], attach_all=True)
        self.assertFalse(
            self.gripper.attach(_HELPER_PATH, _PARTS_PATTERN, [], attach_all=True)
        )

        stage = omni.usd.get_context().get_stage()
        far_away = UsdGeom.Xformable(stage.GetPrimAtPath(_FAR_AWAY_PATH))
        far_away.GetOrderedXformOps()[0].Set(Gf.Vec3d(0.0, 0.2, 0.0))

        self.assertTrue(
            self.gripper.attach(_HELPER_PATH, _PARTS_PATTERN, [], attach_all=True)
        )
        self.assertEqual(
            self.gripper.attached_prim_paths,
            [_PART_A_PATH, _PART_B_PATH, _FAR_AWAY_PATH],
        )

    async def test_release_lets_go_of_every_attached_prim(self):
        self.gripper.attach(_HELPER_PATH, _PARTS_PATTERN, [], attach_all=True)
        released = []
        self.gripper.on_released = released.append

        self.assertTrue(self.gripper.release())
        self.assertEqual(released, [_PART_A_PATH, _PART_B_PATH])
        self.assertFalse(self.gripper.is_attached)
        self.assertEqual(self.gripper.attached_prim_paths, [])

    async def test_attached_prims_follow_the_helper(self):
        self.gripper.attach(_HELPER_PATH, _PARTS_PATTERN, [], attach_all=True)
        stage = omni.usd.get_context().get_stage()
        helper = UsdGeom.Xformable(stage.GetPrimAtPath(_HELPER_PATH))
        helper.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 3.0))

        # release() snaps the held prims to the helper one last time.
        self.gripper.release()

        for part_path, expected_x in ((_PART_A_PATH, 0.2), (_PART_B_PATH, -0.2)):
            position = self._world_position(part_path)
            self.assertTrue(
                Gf.IsClose(position, Gf.Vec3d(expected_x, 0.0, 3.0), 1e-6),
                f"{part_path} ended at {position}",
            )

    async def test_restore_all_keeps_the_original_animation(self):
        """Grabbing a conveyor-driven prim must not flatten its time samples
        to the single pose it had when attached."""
        stage = omni.usd.get_context().get_stage()
        mover = UsdGeom.Cube.Define(stage, "/World/Mover")
        mover.GetSizeAttr().Set(0.1)
        translate_op = mover.AddTranslateOp()
        translate_op.Set(Gf.Vec3d(0.0, 0.0, 0.0), Usd.TimeCode(0.0))
        translate_op.Set(Gf.Vec3d(0.0, 0.0, 3.0), Usd.TimeCode(10.0))
        omni.timeline.get_timeline_interface().set_current_time(0.0)

        self.assertTrue(self.gripper.attach(_HELPER_PATH, ["/World/Mover"], []))
        self.gripper.restore_all()

        restored_ops = UsdGeom.Xformable(mover.GetPrim()).GetOrderedXformOps()
        self.assertEqual(len(restored_ops), 1)
        restored_attr = restored_ops[0].GetAttr()
        self.assertEqual(restored_attr.Get(Usd.TimeCode(0.0)), Gf.Vec3d(0.0, 0.0, 0.0))
        self.assertEqual(restored_attr.Get(Usd.TimeCode(10.0)), Gf.Vec3d(0.0, 0.0, 3.0))


class TestContactGripperOverlapRules(omni.kit.test.AsyncTestCase):
    """What counts as overlapping the sensor, and what an exclusion covers."""

    async def setUp(self):
        await omni.usd.get_context().new_stage_async()
        self.gripper = ContactGripperModel()
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World")
        UsdGeom.Cube.Define(stage, _HELPER_PATH).GetSizeAttr().Set(1.0)

    async def tearDown(self):
        self.gripper.restore_all()
        self.gripper.destroy()

    @staticmethod
    def _define_cube(path: str, translation: Gf.Vec3d, size: float = 0.5):
        stage = omni.usd.get_context().get_stage()
        cube = UsdGeom.Cube.Define(stage, path)
        cube.GetSizeAttr().Set(size)
        cube.AddTranslateOp().Set(translation)
        return cube

    async def test_group_hull_spanning_the_helper_is_not_an_overlap(self):
        """Two cubes far left and far right: the group's hull covers the
        helper, but no geometry comes near it."""
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World/Ring")
        self._define_cube("/World/Ring/Left", Gf.Vec3d(-5.0, 0.0, 0.0))
        self._define_cube("/World/Ring/Right", Gf.Vec3d(5.0, 0.0, 0.0))

        self.assertFalse(self.gripper.attach(_HELPER_PATH, ["/World/Ring"], []))

    async def test_group_with_touching_geometry_is_attached_as_a_whole(self):
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World/Box")
        self._define_cube("/World/Box/Body", Gf.Vec3d(0.0, 0.0, 0.0))
        self._define_cube("/World/Box/Handle", Gf.Vec3d(5.0, 0.0, 0.0))

        self.assertTrue(self.gripper.attach(_HELPER_PATH, ["/World/Box"], []))
        self.assertEqual(self.gripper.attached_prim_path, "/World/Box")

    async def test_excluded_prim_excludes_everything_below_it(self):
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World/Robot")
        self._define_cube("/World/Robot/Finger", Gf.Vec3d(0.0, 0.0, 0.0))

        self.assertFalse(self.gripper.attach(_HELPER_PATH, [], ["/World/Robot"]))

    async def test_animated_prim_is_tested_at_the_current_frame(self):
        """Default value far away, time sample at the current frame inside
        the helper."""
        cube = self._define_cube("/World/Mover", Gf.Vec3d(50.0, 0.0, 0.0))
        cube.GetOrderedXformOps()[0].Set(Gf.Vec3d(0.0, 0.0, 0.0), Usd.TimeCode(0.0))
        omni.timeline.get_timeline_interface().set_current_time(0.0)

        self.assertTrue(self.gripper.attach(_HELPER_PATH, ["/World/Mover"], []))

    async def test_hidden_sensor_group_still_has_bounds(self):
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World/Sensor")
        volume = self._define_cube("/World/Sensor/Volume", Gf.Vec3d(0.0, 0.0, 0.0), 1.0)
        volume.MakeInvisible()
        self._define_cube("/World/Part", Gf.Vec3d(0.0, 0.0, 0.0))

        self.assertTrue(self.gripper.attach("/World/Sensor", ["/World/Part"], []))
