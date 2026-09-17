"""The warnings that fire when a robot's geometry is not what NOVA thinks.

Two things go wrong silently. A wrong robot variant connects and streams
without error, so the only symptom is a constant offset in every pose; the KUKA
QUANTEC reaches are 200 mm apart, which is what the tolerance has to catch
while ignoring authored rounding. And an asset can disagree with the kinematics
of the arm it describes: the FANUC M-900iB/400L ships with its flange turned
180 degrees about the tool axis, so every taught pose is turned with it.

Only authored geometry is compared, so these tests build joints and a flange
and never pose anything.
"""

import math

import omni.kit.test
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from wandelbots.omni.manipulators.geometry_check import (
    GEOMETRY_TOLERANCE_MILLIMETERS,
    Chain,
    FlangeFrame,
    GeometryCheck,
    chain_offset_millimeters,
    flange_offset_millimeters,
    flange_rotation_degrees,
    pivots_differ,
    read_chain,
    read_flange_frame,
)

# The R2700 / R2900 step, the case this check exists for.
VARIANT_STEP_MILLIMETERS = 200.0
R2900 = Chain(
    pivots=((0.0, 0.0, 0.0), (2900.0, 0.0, 634.0)), flange=(3140.0, 0.0, 634.0)
)
R2700 = Chain(
    pivots=((0.0, 0.0, 0.0), (2700.0, 0.0, 634.0)), flange=(2940.0, 0.0, 634.0)
)

# The FANUC M-900iB/400L flange at joint zero, as the asset authors it and as
# NOVA's kinematics report it. Same position, half a turn about the tool axis.
FANUC_ASSET_FLANGE = FlangeFrame(
    position_millimeters=(2890.0, 0.0, 1370.0), rotation=(0.0, math.pi / 2.0, 0.0)
)
FANUC_KINEMATICS_FLANGE = FlangeFrame(
    position_millimeters=(2890.0, 0.0, 1370.0), rotation=(2.221441, 0.0, 2.221441)
)


def define_arm(stage, joint_positions, flange, hose=False):
    """An arm of joint_<n> prims plus a flange, optionally with a hose package."""
    robot = UsdGeom.Xform.Define(stage, "/Robot").GetPrim()
    for index, position in enumerate(joint_positions, start=1):
        joint = UsdPhysics.RevoluteJoint.Define(stage, f"/Robot/joint_{index}")
        joint.CreateLocalPos0Attr().Set(Gf.Vec3f(*position))
    if hose:
        # Named support_joint_0, which must not be read as joint_0.
        support = UsdPhysics.RevoluteJoint.Define(stage, "/Robot/support_joint_0")
        support.CreateLocalPos0Attr().Set(Gf.Vec3f(-0.447, 0.0, 0.605))
    link = UsdGeom.Xform.Define(stage, "/Robot/link_6")
    tcp = UsdGeom.Xform.Define(stage, "/Robot/link_6/tcp_flange")
    tcp.AddTranslateOp().Set(Gf.Vec3d(*flange))
    return robot, link


def define_flange(stage, position, turn_degrees=0.0, axis=(0.0, 0.0, 1.0)):
    """A robot root with a flange, optionally turned about *axis*."""
    UsdGeom.Xform.Define(stage, "/Robot")
    UsdGeom.Xform.Define(stage, "/Robot/link_6")
    flange = UsdGeom.Xform.Define(stage, "/Robot/link_6/tcp_flange")
    flange.AddTranslateOp().Set(Gf.Vec3d(*position))
    if turn_degrees:
        rotation = Gf.Rotation(Gf.Vec3d(*axis), turn_degrees)
        flange.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(rotation.GetQuat())
    return flange


class TestReadChain(omni.kit.test.AsyncTestCase):
    async def test_reads_the_joints_and_the_flange(self):
        stage = Usd.Stage.CreateInMemory("TestReadChain")
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        define_arm(stage, [(0.0, 0.0, 0.0), (2.9, 0.0, 0.634)], (3.14, 0.0, 0.634))

        chain = read_chain(stage, "/Robot")

        self.assertEqual(2, len(chain.pivots))
        self.assertAlmostEqual(2900.0, chain.pivots[-1][0], places=3)
        self.assertAlmostEqual(3140.0, chain.flange[0], places=3)

    async def test_a_centimetre_stage_still_reads_millimetres(self):
        """metersPerUnit, not a hard-coded factor of 1000.

        The scene and the downloaded model are separate stages, so a scene
        authored in centimetres has to come out in the same units as the model
        it is compared against.
        """
        stage = Usd.Stage.CreateInMemory("TestReadChainCentimetres")
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)
        define_arm(stage, [(0.0, 0.0, 0.0), (290.0, 0.0, 63.4)], (314.0, 0.0, 63.4))

        chain = read_chain(stage, "/Robot")

        self.assertAlmostEqual(2900.0, chain.pivots[-1][0], places=3)
        self.assertAlmostEqual(3140.0, chain.flange[0], places=3)

    async def test_the_hose_support_joint_is_not_read_as_joint_0(self):
        """support_joint_0 would otherwise land in the chain and shift it."""
        stage = Usd.Stage.CreateInMemory("TestHoseIgnored")
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        define_arm(
            stage, [(0.0, 0.0, 0.0), (2.9, 0.0, 0.634)], (3.14, 0.0, 0.634), hose=True
        )

        chain = read_chain(stage, "/Robot")

        self.assertEqual(2, len(chain.pivots))

    async def test_an_arm_without_joints_reads_as_nothing(self):
        stage = Usd.Stage.CreateInMemory("TestNoJoints")
        UsdGeom.Xform.Define(stage, "/Robot")

        self.assertIsNone(read_chain(stage, "/Robot"))


class TestChainComparison(omni.kit.test.AsyncTestCase):
    async def test_the_same_arm_has_no_offset(self):
        self.assertEqual((0.0, 0.0, 0.0), chain_offset_millimeters(R2900, R2900))
        self.assertFalse(pivots_differ(R2900, R2900))

    async def test_a_shorter_reach_shows_as_the_variant_step(self):
        offset = chain_offset_millimeters(R2900, R2700)

        self.assertAlmostEqual(-VARIANT_STEP_MILLIMETERS, offset[0], places=3)
        self.assertTrue(pivots_differ(R2900, R2700))

    async def test_a_moved_joint_is_caught_even_when_the_flange_agrees(self):
        moved = Chain(
            pivots=((0.0, 0.0, 0.0), (2800.0, 0.0, 634.0)), flange=R2900.flange
        )

        self.assertEqual((0.0, 0.0, 0.0), chain_offset_millimeters(R2900, moved))
        self.assertTrue(pivots_differ(R2900, moved))


class TestGeometryCheck(omni.kit.test.AsyncTestCase):
    async def test_matching_geometry_reports_no_warning(self):
        check = GeometryCheck(
            model_name="KUKA_KR240_R2900", offset_millimeters=(0.0, -0.0, 0.004)
        )

        self.assertTrue(check.matches)
        self.assertEqual("", check.warning())

    async def test_variant_mismatch_names_the_model_and_the_axis(self):
        check = GeometryCheck(
            model_name="KUKA_KR270_R2700",
            offset_millimeters=(-VARIANT_STEP_MILLIMETERS, 0.0, 0.0),
        )

        self.assertFalse(check.matches)
        self.assertEqual(
            "Scene robot does not match KUKA_KR270_R2700: "
            "flange off by (-200, 0, 0) mm. "
            "Poses taught in the scene will not match the robot.",
            check.warning(),
        )

    async def test_a_moved_joint_warns_without_claiming_a_flange_offset(self):
        check = GeometryCheck(
            model_name="model", offset_millimeters=(0.0, 0.0, 0.0), joints_moved=True
        )

        self.assertFalse(check.matches)
        self.assertIn("joints sit in different places", check.warning())

    async def test_a_different_joint_count_warns(self):
        check = GeometryCheck(
            model_name="model",
            offset_millimeters=(0.0, 0.0, 0.0),
            joint_count_differs=True,
        )

        self.assertFalse(check.matches)
        self.assertIn("different number of joints", check.warning())

    async def test_just_past_the_tolerance_warns(self):
        check = GeometryCheck(
            model_name="model",
            offset_millimeters=(GEOMETRY_TOLERANCE_MILLIMETERS + 0.001, 0.0, 0.0),
        )

        self.assertFalse(check.matches)


class TestReadFlangeFrame(omni.kit.test.AsyncTestCase):
    async def test_reads_the_position_in_millimeters(self):
        stage = Usd.Stage.CreateInMemory("TestFlangeFrame")
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        define_flange(stage, (2.89, 0.0, 1.37))

        frame = read_flange_frame(stage, "/Robot")

        self.assertAlmostEqual(2890.0, frame.position_millimeters[0], places=3)
        self.assertAlmostEqual(1370.0, frame.position_millimeters[2], places=3)
        self.assertAlmostEqual(0.0, math.hypot(*frame.rotation), places=6)

    async def test_a_centimetre_stage_still_reads_millimetres(self):
        """metersPerUnit, not a hard-coded factor of 1000."""
        stage = Usd.Stage.CreateInMemory("TestFlangeCentimetres")
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)
        define_flange(stage, (289.0, 0.0, 137.0))

        frame = read_flange_frame(stage, "/Robot")

        self.assertAlmostEqual(2890.0, frame.position_millimeters[0], places=3)
        self.assertAlmostEqual(1370.0, frame.position_millimeters[2], places=3)

    async def test_a_turned_flange_reads_as_a_rotation(self):
        stage = Usd.Stage.CreateInMemory("TestFlangeTurned")
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        define_flange(stage, (2.89, 0.0, 1.37), turn_degrees=180.0)

        frame = read_flange_frame(stage, "/Robot")

        self.assertAlmostEqual(math.pi, math.hypot(*frame.rotation), places=6)

    async def test_a_robot_without_a_flange_reads_as_nothing(self):
        stage = Usd.Stage.CreateInMemory("TestNoFlange")
        UsdGeom.Xform.Define(stage, "/Robot")

        self.assertIsNone(read_flange_frame(stage, "/Robot"))


class TestFlangeComparison(omni.kit.test.AsyncTestCase):
    async def test_the_same_frame_agrees(self):
        self.assertEqual(
            (0.0, 0.0, 0.0),
            flange_offset_millimeters(FANUC_ASSET_FLANGE, FANUC_ASSET_FLANGE),
        )
        self.assertAlmostEqual(
            0.0,
            flange_rotation_degrees(FANUC_ASSET_FLANGE, FANUC_ASSET_FLANGE),
            places=6,
        )

    async def test_the_fanuc_asset_is_half_a_turn_from_its_kinematics(self):
        """CSI-2985: same flange position, turned about the tool axis."""
        offset = flange_offset_millimeters(FANUC_ASSET_FLANGE, FANUC_KINEMATICS_FLANGE)

        self.assertEqual((0.0, 0.0, 0.0), offset)
        self.assertAlmostEqual(
            180.0,
            flange_rotation_degrees(FANUC_ASSET_FLANGE, FANUC_KINEMATICS_FLANGE),
            places=2,
        )

    async def test_a_shifted_flange_shows_the_millimetres(self):
        """The KUKA KR270_R2700 case: asset 2.93 m, kinematics 2.94 m."""
        asset = FlangeFrame(position_millimeters=(2930.0, 0.0, 0.0), rotation=(0, 0, 0))
        kinematics = FlangeFrame(
            position_millimeters=(2940.0, 0.0, 0.0), rotation=(0, 0, 0)
        )

        offset = flange_offset_millimeters(asset, kinematics)

        self.assertAlmostEqual(10.0, offset[0], places=6)
        self.assertAlmostEqual(
            0.0, flange_rotation_degrees(asset, kinematics), places=6
        )


class TestKinematicsMismatch(omni.kit.test.AsyncTestCase):
    async def test_a_turned_flange_warns_and_names_the_model(self):
        check = GeometryCheck(
            model_name="FANUC_M900iB400L",
            offset_millimeters=(0.0, 0.0, 0.0),
            kinematics_rotation_degrees=180.0,
        )

        self.assertFalse(check.matches)
        self.assertTrue(check.scene_matches)
        self.assertEqual(
            "FANUC_M900iB400L asset does not match NOVA kinematics: "
            "flange rotated 180 deg. "
            "Poses taught in the scene will not match the robot.",
            check.warning(),
        )

    async def test_a_shifted_flange_warns_with_millimetres(self):
        check = GeometryCheck(
            model_name="KUKA_KR270_R2700",
            offset_millimeters=(0.0, 0.0, 0.0),
            kinematics_offset_millimeters=(10.0, 0.0, 0.0),
        )

        self.assertFalse(check.matches)
        self.assertIn("flange off by (10, 0, 0) mm", check.warning())

    async def test_authored_rounding_is_not_a_mismatch(self):
        check = GeometryCheck(
            model_name="model",
            offset_millimeters=(0.0, 0.0, 0.0),
            kinematics_offset_millimeters=(0.004, 0.0, 0.0),
            kinematics_rotation_degrees=0.023,
        )

        self.assertTrue(check.matches)
        self.assertEqual("", check.warning())

    async def test_both_mismatches_read_the_same(self):
        """Same kind of problem, so neither may sound worse than the other."""
        scene = GeometryCheck(
            model_name="model", offset_millimeters=(-VARIANT_STEP_MILLIMETERS, 0.0, 0.0)
        )
        kinematics = GeometryCheck(
            model_name="model",
            offset_millimeters=(0.0, 0.0, 0.0),
            kinematics_rotation_degrees=180.0,
        )

        for message in (scene.warning(), kinematics.warning()):
            self.assertIn("does not match", message)
            self.assertTrue(
                message.endswith("Poses taught in the scene will not match the robot."),
                message,
            )

    async def test_the_scene_mismatch_is_reported_first(self):
        """The scene is the half the user can put right, so it wins."""
        check = GeometryCheck(
            model_name="model",
            offset_millimeters=(-VARIANT_STEP_MILLIMETERS, 0.0, 0.0),
            kinematics_rotation_degrees=180.0,
        )

        self.assertFalse(check.matches)
        self.assertIn("Scene robot does not match", check.warning())

    async def test_kinematics_unavailable_is_not_a_warning(self):
        """Forward kinematics not answering leaves both fields at zero, and not
        knowing must stay quiet rather than accuse a correct robot."""
        check = GeometryCheck(model_name="model", offset_millimeters=(0.0, 0.0, 0.0))

        self.assertTrue(check.kinematics_match)
        self.assertEqual("", check.warning())
