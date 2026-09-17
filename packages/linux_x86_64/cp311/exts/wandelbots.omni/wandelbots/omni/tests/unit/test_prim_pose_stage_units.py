"""Pose conversion on stages that are not authored in metres.

``metersPerUnit`` only cancels out on a metre stage, so a wrong conversion is
invisible there. A centimetre stage reported a 200 mm TCP offset as 2 million mm
(CSI-2980), which is what these tests pin down.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import omni.kit.test
from omni.ui import scene as sc
from pxr import Gf, Usd, UsdGeom

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.tests.stage_utils import use_stage
from wandelbots.omni.ui.overlay.collision_world.collision_world_overlay import (
    collider_transform,
)
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.scene import SceneUtils

# One offset, expressed once, so the expected value never drifts from the input.
OFFSET_MILLIMETERS = 200.0
STAGE_UNITS = {"metre": 1.0, "centimetre": 0.01, "millimetre": 0.001}


@contextmanager
def stage_with_units(meters_per_unit: float):
    stage = Usd.Stage.CreateInMemory("TestPrimPoseStageUnits")
    UsdGeom.SetStageMetersPerUnit(stage, meters_per_unit)
    with use_stage(stage):
        yield stage


def define_prim_at(stage: Usd.Stage, path: str, translation: float) -> None:
    xformable = UsdGeom.Xformable(UsdGeom.Xform.Define(stage, path))
    xformable.AddTranslateOp().Set(Gf.Vec3d(translation, 0.0, 0.0))
    xformable.AddOrientOp().Set(Gf.Quatf(1.0, 0.0, 0.0, 0.0))


class TestGetPrimPoseStageUnits(omni.kit.test.AsyncTestCase):
    async def test_offset_reads_as_millimeters_on_every_stage_unit(self):
        for label, meters_per_unit in STAGE_UNITS.items():
            with self.subTest(stage=label), stage_with_units(meters_per_unit) as stage:
                authored = (OFFSET_MILLIMETERS / 1000.0) / meters_per_unit
                define_prim_at(stage, "/Tcp", authored)

                pose = PrimUtils.get_prim_pose("/Tcp", stage=stage)

                self.assertAlmostEqual(OFFSET_MILLIMETERS, pose.pose[0], places=6)

    async def test_centimetre_stage_is_not_off_by_ten_thousand(self):
        """Regression for CSI-2980: dividing by metersPerUnit gave 2e6 mm here."""
        with stage_with_units(0.01) as stage:
            define_prim_at(stage, "/Tcp", 20.0)

            pose = PrimUtils.get_prim_pose("/Tcp", stage=stage)

            self.assertAlmostEqual(OFFSET_MILLIMETERS, pose.pose[0], places=6)
            self.assertLess(pose.pose[0], 1000.0)

    async def test_matches_the_scene_utils_conversion(self):
        for label, meters_per_unit in STAGE_UNITS.items():
            with self.subTest(stage=label), stage_with_units(meters_per_unit) as stage:
                authored = (OFFSET_MILLIMETERS / 1000.0) / meters_per_unit
                define_prim_at(stage, "/Tcp", authored)

                pose = PrimUtils.get_prim_pose("/Tcp", stage=stage)

                self.assertAlmostEqual(
                    SceneUtils.value_to_millimeters(authored, stage),
                    pose.pose[0],
                    places=6,
                )


class TestPrimPoseRoundTripStageUnits(omni.kit.test.AsyncTestCase):
    async def test_written_pose_reads_back_unchanged(self):
        """set_prim_pose is the inverse of get_prim_pose on any stage unit."""
        for label, meters_per_unit in STAGE_UNITS.items():
            with self.subTest(stage=label), stage_with_units(meters_per_unit) as stage:
                define_prim_at(stage, "/Tcp", 0.0)
                written = WSPose(pose=[OFFSET_MILLIMETERS, 0.0, 0.0, 0.0, 0.0, 0.0])

                PrimUtils.set_prim_pose("/Tcp", written, stage)

                read_back = PrimUtils.get_prim_pose("/Tcp", stage=stage)
                self.assertAlmostEqual(OFFSET_MILLIMETERS, read_back.pose[0], places=6)

    async def test_written_pose_lands_in_stage_units(self):
        with stage_with_units(0.01) as stage:
            define_prim_at(stage, "/Tcp", 0.0)

            PrimUtils.set_prim_pose(
                "/Tcp",
                WSPose(pose=[OFFSET_MILLIMETERS, 0.0, 0.0, 0.0, 0.0, 0.0]),
                stage,
            )

            translate = (
                UsdGeom.Xformable(stage.GetPrimAtPath("/Tcp"))
                .GetOrderedXformOps()[0]
                .Get()
            )
            # 200 mm is 20 cm, so 20 in a centimetre stage's own units.
            self.assertAlmostEqual(20.0, translate[0], places=6)


class TestStageUnitScaleFactor(omni.kit.test.AsyncTestCase):
    """The scale factor the overlays and previews build their transforms from.

    They need stage units per millimetre. Writing that as
    ``meters_per_unit / 1000`` instead of ``millimeters_to_stage_value(1.0)``
    agrees only on a metre stage and is off by metersPerUnit squared everywhere
    else - the same mistake as CSI-2980, one direction over.
    """

    async def test_scale_factor_converts_millimetres_to_stage_units(self):
        for label, meters_per_unit in STAGE_UNITS.items():
            with self.subTest(stage=label), stage_with_units(meters_per_unit) as stage:
                scale = SceneUtils.millimeters_to_stage_value(1.0, stage)

                self.assertAlmostEqual(
                    OFFSET_MILLIMETERS * scale,
                    (OFFSET_MILLIMETERS / 1000.0) / meters_per_unit,
                    places=9,
                )

    async def test_centimetre_stage_scale_is_not_the_inverted_factor(self):
        with stage_with_units(0.01) as stage:
            scale = SceneUtils.millimeters_to_stage_value(1.0, stage)

            # 200 mm is 20 cm, so 20 stage units - not 0.002.
            self.assertAlmostEqual(20.0, OFFSET_MILLIMETERS * scale, places=9)
            self.assertNotAlmostEqual(0.01 / 1000.0, scale, places=9)

    async def test_the_two_directions_undo_each_other(self):
        for label, meters_per_unit in STAGE_UNITS.items():
            with self.subTest(stage=label), stage_with_units(meters_per_unit) as stage:
                stage_value = SceneUtils.millimeters_to_stage_value(
                    OFFSET_MILLIMETERS, stage
                )

                self.assertAlmostEqual(
                    OFFSET_MILLIMETERS,
                    SceneUtils.value_to_millimeters(stage_value, stage),
                    places=6,
                )


class TestColliderTransformStageUnits(omni.kit.test.AsyncTestCase):
    """The scale a corrected call site actually builds.

    The class above pins the helper, which this MR does not change. This one
    pins one of the call sites it corrects, so putting the inverted factor back
    fails a test instead of passing quietly.
    """

    def _collider_at_origin(self):
        pose = SimpleNamespace(position=[0.0, 0.0, 0.0], orientation=[0.0, 0.0, 0.0])
        return SimpleNamespace(pose=pose)

    async def test_the_scale_is_stage_units_per_millimetre(self):
        for label, meters_per_unit in STAGE_UNITS.items():
            with self.subTest(stage=label), stage_with_units(meters_per_unit) as stage:
                transform = collider_transform(
                    sc.Matrix44(), self._collider_at_origin(), meters_per_unit
                )

                self.assertAlmostEqual(
                    SceneUtils.millimeters_to_stage_value(1.0, stage),
                    transform[0],
                    places=9,
                )

    async def test_a_centimetre_stage_is_not_the_inverted_factor(self):
        with stage_with_units(0.01):
            transform = collider_transform(
                sc.Matrix44(), self._collider_at_origin(), 0.01
            )

            # 1 mm is 0.1 cm, so 0.1 stage units - not 0.00001.
            self.assertAlmostEqual(0.1, transform[0], places=9)
            self.assertNotAlmostEqual(0.01 / 1000.0, transform[0], places=9)
