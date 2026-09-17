"""Unit tests for the contact gripper's candidate scan.

The scan skips a subtree whose bound misses the helper volume, so that bound
has to cover everything inside the subtree. Two kinds of authoring make a
bound smaller than the geometry below it: an out-of-date extentsHint on a
model prim, which is why the scan reads bounds without hints, and an
invisible descendant, which is why it prunes on bounds that ignore
visibility.
"""

from __future__ import annotations

import omni.kit.test
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
