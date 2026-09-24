"""Payload properties from the stage's rigid bodies.

Boxes have closed-form inertia (m (b^2 + c^2) / 12 about the centre), so the
PhysX values, the frame change, the parallel axis combination and the unit
conversion can all be pinned down exactly.
"""

import numpy as np
import omni.kit.test
import omni.usd
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from wandelbots.omni.core.payload.payload_properties import (
    BodyMass,
    PayloadCalculationError,
    PayloadProperties,
    body_in_frame,
    combine_bodies,
    compute_stage_payload_properties,
    plausibility_warnings,
)
from wandelbots.omni.core.payload.rigid_body_mass import (
    RigidBodyMassProperties,
    find_rigid_bodies,
)

QUARTER_TURN_ABOUT_Z = Gf.Quatd(
    np.cos(np.pi / 4), Gf.Vec3d(0.0, 0.0, np.sin(np.pi / 4))
)


def point_body(path: str, mass: float, position) -> BodyMass:
    return BodyMass(
        path, mass, np.asarray(position, dtype=np.float64), np.zeros((3, 3))
    )


class TestCombineBodies(omni.kit.test.AsyncTestCase):
    async def test_two_point_masses(self):
        mass, center, inertia = combine_bodies(
            [
                point_body("/a", 1.0, (2.0, 0.0, 0.0)),
                point_body("/b", 1.0, (-2.0, 0, 0)),
            ]
        )
        self.assertAlmostEqual(2.0, mass)
        np.testing.assert_allclose([0.0, 0.0, 0.0], center)
        # Two unit masses at distance 2 from the centre: Iyy = Izz = 2 m d^2.
        np.testing.assert_allclose(np.diag([0.0, 8.0, 8.0]), inertia, atol=1e-12)

    async def test_own_inertia_adds_to_the_offset_term(self):
        body = BodyMass("/a", 2.0, np.array([0.0, 3.0, 0.0]), np.diag([1.0, 2.0, 3.0]))
        mass, center, inertia = combine_bodies(
            [body, point_body("/b", 2.0, (0, -3, 0))]
        )
        self.assertAlmostEqual(4.0, mass)
        np.testing.assert_allclose([0.0, 0.0, 0.0], center)
        # Offsets of 3 along y move Ixx and Izz by m d^2 = 18 per body.
        np.testing.assert_allclose(np.diag([37.0, 2.0, 39.0]), inertia, atol=1e-12)

    async def test_massless_bodies_are_rejected(self):
        with self.assertRaises(PayloadCalculationError):
            combine_bodies([point_body("/a", 0.0, (0, 0, 0))])


class TestBodyInFrame(omni.kit.test.AsyncTestCase):
    def _physx_body(self, principal_axes=Gf.Quatd.GetIdentity()):
        return RigidBodyMassProperties(
            prim_path="/body",
            mass=2.0,
            center_of_mass=Gf.Vec3d(1.0, 0.0, 0.0),
            diagonal_inertia=Gf.Vec3d(1.0, 2.0, 3.0),
            principal_axes=principal_axes,
        )

    async def test_translation_moves_the_centre_only(self):
        body = body_in_frame(
            self._physx_body(), Gf.Matrix4d().SetTranslate(Gf.Vec3d(5.0, 0.0, 0.0))
        )
        np.testing.assert_allclose([6.0, 0.0, 0.0], body.center_of_mass)
        np.testing.assert_allclose(np.diag([1.0, 2.0, 3.0]), body.inertia_tensor)

    async def test_principal_axes_rotate_the_inertia_into_the_body_frame(self):
        body = body_in_frame(self._physx_body(QUARTER_TURN_ABOUT_Z), Gf.Matrix4d(1.0))
        np.testing.assert_allclose(
            np.diag([2.0, 1.0, 3.0]), body.inertia_tensor, atol=1e-12
        )

    async def test_frame_rotation_turns_centre_and_inertia(self):
        body_to_frame = Gf.Matrix4d().SetRotate(QUARTER_TURN_ABOUT_Z)
        body = body_in_frame(self._physx_body(), body_to_frame)
        np.testing.assert_allclose([0.0, 1.0, 0.0], body.center_of_mass, atol=1e-12)
        np.testing.assert_allclose(
            np.diag([2.0, 1.0, 3.0]), body.inertia_tensor, atol=1e-12
        )


def payload_of(mass: float, extent_millimeters, moments) -> PayloadProperties:
    return PayloadProperties(
        mass=mass,
        center_of_mass=(0.0, 0.0, 0.0),
        inertia_tensor=np.diag(moments),
        extent_millimeters=tuple(extent_millimeters),
    )


class TestPlausibilityWarnings(omni.kit.test.AsyncTestCase):
    async def test_a_real_gripper_passes(self):
        # 65 kg aluminium gripper, 0.73 m wide: moments a few kg m2.
        self.assertEqual(
            [],
            plausibility_warnings(payload_of(64.8, (730, 610, 610), (4.9, 3.0, 4.9))),
        )

    async def test_metre_geometry_on_a_centimetre_stage_is_too_dense(self):
        # The same gripper read as centimetres: a 7 mm tool of 65 kg.
        warnings = plausibility_warnings(
            payload_of(64.8, (7.3, 6.1, 6.1), (4.9e-4, 3.0e-4, 4.9e-4))
        )
        self.assertEqual(1, len(warnings))
        self.assertIn("denser than any material", warnings[0])
        self.assertIn("metersPerUnit", warnings[0])

    async def test_too_little_mass_for_the_size_is_lighter_than_air(self):
        warnings = plausibility_warnings(
            payload_of(0.5, (2000, 2000, 2000), (0.5, 0.5, 0.5))
        )
        self.assertEqual(1, len(warnings))
        self.assertIn("lighter than air", warnings[0])

    async def test_authored_tiny_inertia_is_flagged(self):
        # 0.004 kg m2 for a 120 kg cart of about a metre.
        warnings = plausibility_warnings(
            payload_of(120.0, (1000, 800, 1200), (0.004, 0.004, 0.004))
        )
        self.assertEqual(1, len(warnings))
        self.assertIn("is tiny", warnings[0])

    async def test_inertia_needing_mass_outside_the_bodies_is_flagged(self):
        # 14.5 kg m2 for a 12 kg link of 0.5 m: its mass would sit a metre away.
        warnings = plausibility_warnings(
            payload_of(12.0, (300, 300, 500), (14.5, 14.5, 14.5))
        )
        self.assertEqual(1, len(warnings))
        self.assertIn("outside", warnings[0])

    async def test_bodies_without_geometry_are_not_judged(self):
        self.assertEqual(
            [], plausibility_warnings(payload_of(5.0, (0, 0, 0), (1, 1, 1)))
        )


def define_rigid_box(
    stage: Usd.Stage,
    path: str,
    side: float,
    mass: float,
    translation=(0.0, 0.0, 0.0),
    rotation_z_degrees: float = 0.0,
    scale=(1.0, 1.0, 1.0),
) -> Usd.Prim:
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(side)
    cube.AddTranslateOp().Set(Gf.Vec3d(*translation))
    cube.AddRotateZOp().Set(rotation_z_degrees)
    cube.AddScaleOp().Set(Gf.Vec3d(*scale))
    prim = cube.GetPrim()
    UsdPhysics.CollisionAPI.Apply(prim)
    UsdPhysics.RigidBodyAPI.Apply(prim)
    UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(mass)
    return prim


def define_xform(
    stage: Usd.Stage,
    path: str,
    translation=(0.0, 0.0, 0.0),
    rotation_z_degrees: float = 0.0,
    scale: float = 1.0,
) -> Usd.Prim:
    xform = UsdGeom.Xform.Define(stage, path)
    xform.AddTranslateOp().Set(Gf.Vec3d(*translation))
    xform.AddRotateZOp().Set(rotation_z_degrees)
    xform.AddScaleOp().Set(Gf.Vec3d(scale, scale, scale))
    return xform.GetPrim()


def box_inertia(mass: float, sides_meters) -> list[float]:
    a, b, c = sides_meters
    return [
        mass * (b**2 + c**2) / 12.0,
        mass * (a**2 + c**2) / 12.0,
        mass * (a**2 + b**2) / 12.0,
    ]


CUBE_SIDE_CENTIMETRES = 10.0


class TestComputeStagePayloadProperties(omni.kit.test.AsyncTestCase):
    """Runs the PhysX property query, so it needs the context stage."""

    async def setUp(self):
        await omni.usd.get_context().new_stage_async()
        self.stage = omni.usd.get_context().get_stage()
        UsdGeom.SetStageMetersPerUnit(self.stage, 0.01)

    async def tearDown(self):
        await omni.usd.get_context().new_stage_async()

    async def test_finds_only_rigid_bodies(self):
        define_rigid_box(self.stage, "/World/box", CUBE_SIDE_CENTIMETRES, 1.0)
        define_xform(self.stage, "/World/frame")
        self.assertEqual(
            ["/World/box"],
            [prim.GetPath().pathString for prim in find_rigid_bodies(self.stage)],
        )

    async def test_single_cube_in_millimetres_and_kg_m2_of_the_reference_frame(self):
        define_rigid_box(
            self.stage,
            "/World/cube",
            CUBE_SIDE_CENTIMETRES,
            3.0,
            translation=(100.0, 0, 0),
        )
        # Seen from a frame at x = 50 cm turned 90 degrees about Z, the cube's
        # centre (100, 0, 0) cm sits at (0, -50, 0) cm.
        reference = define_xform(
            self.stage,
            "/World/flange",
            translation=(50.0, 0.0, 0.0),
            rotation_z_degrees=90.0,
        )

        properties = await compute_stage_payload_properties(self.stage, reference)

        self.assertAlmostEqual(3.0, properties.mass, places=6)
        np.testing.assert_allclose(
            [0.0, -500.0, 0.0], properties.center_of_mass, atol=1e-3
        )
        np.testing.assert_allclose(
            box_inertia(3.0, (0.1, 0.1, 0.1)), properties.moment_of_inertia, rtol=1e-5
        )
        np.testing.assert_allclose([0.0] * 3, properties.products_of_inertia, atol=1e-9)
        self.assertEqual(("/World/cube",), properties.body_paths)
        np.testing.assert_allclose(
            [100.0, 100.0, 100.0], properties.extent_millimeters, atol=1e-3
        )

    async def test_two_bodies_combine_about_the_common_centre(self):
        define_rigid_box(
            self.stage,
            "/World/left",
            CUBE_SIDE_CENTIMETRES,
            1.0,
            translation=(-20.0, 0, 0),
        )
        define_rigid_box(
            self.stage,
            "/World/right",
            CUBE_SIDE_CENTIMETRES,
            1.0,
            translation=(20.0, 0, 0),
        )
        reference = define_xform(self.stage, "/World/flange")

        properties = await compute_stage_payload_properties(self.stage, reference)

        self.assertAlmostEqual(2.0, properties.mass, places=6)
        np.testing.assert_allclose(
            [0.0, 0.0, 0.0], properties.center_of_mass, atol=1e-3
        )
        cube = box_inertia(1.0, (0.1, 0.1, 0.1))[0]
        # Each cube is 0.2 m off the common centre along x: parallel axis adds
        # m d^2 to Iyy and Izz only.
        expected = [2 * cube, 2 * (cube + 1.0 * 0.2**2), 2 * (cube + 1.0 * 0.2**2)]
        np.testing.assert_allclose(expected, properties.moment_of_inertia, rtol=1e-5)
        # Two 10 cm cubes centred 40 cm apart span 50 cm along x.
        np.testing.assert_allclose(
            [500.0, 100.0, 100.0], properties.extent_millimeters, atol=1e-3
        )

    async def test_rotated_scaled_box_keeps_its_inertia_in_the_reference_axes(self):
        # A 10 x 20 x 30 cm box turned 90 degrees about Z swaps its x and y
        # moments as seen from the unrotated reference frame.
        define_rigid_box(
            self.stage,
            "/World/box",
            CUBE_SIDE_CENTIMETRES,
            6.0,
            rotation_z_degrees=90.0,
            scale=(1.0, 2.0, 3.0),
        )
        reference = define_xform(self.stage, "/World/flange")

        properties = await compute_stage_payload_properties(self.stage, reference)

        body_frame = box_inertia(6.0, (0.1, 0.2, 0.3))
        np.testing.assert_allclose(
            [body_frame[1], body_frame[0], body_frame[2]],
            properties.moment_of_inertia,
            rtol=1e-5,
        )
        np.testing.assert_allclose([0.0] * 3, properties.products_of_inertia, atol=1e-9)

    async def test_mass_units_of_the_stage_are_converted_to_kilograms(self):
        UsdPhysics.SetStageKilogramsPerUnit(self.stage, 0.001)
        define_rigid_box(self.stage, "/World/cube", CUBE_SIDE_CENTIMETRES, 3000.0)
        reference = define_xform(self.stage, "/World/flange")

        properties = await compute_stage_payload_properties(self.stage, reference)

        self.assertAlmostEqual(3.0, properties.mass, places=6)
        np.testing.assert_allclose(
            box_inertia(3.0, (0.1, 0.1, 0.1)), properties.moment_of_inertia, rtol=1e-5
        )

    async def test_scale_on_the_reference_frame_is_ignored(self):
        define_rigid_box(
            self.stage,
            "/World/cube",
            CUBE_SIDE_CENTIMETRES,
            3.0,
            translation=(100.0, 0, 0),
        )
        reference = define_xform(
            self.stage, "/World/flange", translation=(50.0, 0.0, 0.0), scale=2.0
        )

        properties = await compute_stage_payload_properties(self.stage, reference)

        np.testing.assert_allclose(
            [500.0, 0.0, 0.0], properties.center_of_mass, atol=1e-3
        )

    async def test_stage_without_rigid_bodies_is_rejected(self):
        reference = define_xform(self.stage, "/World/flange")
        with self.assertRaises(PayloadCalculationError):
            await compute_stage_payload_properties(self.stage, reference)

    async def test_to_payload_carries_the_planner_fields(self):
        define_rigid_box(self.stage, "/World/cube", CUBE_SIDE_CENTIMETRES, 3.0)
        reference = define_xform(self.stage, "/World/flange")

        model = (
            await compute_stage_payload_properties(self.stage, reference)
        ).to_payload("gripper_with_part")

        self.assertEqual("gripper_with_part", model.name)
        self.assertAlmostEqual(3.0, model.payload, places=6)
        np.testing.assert_allclose([0.0, 0.0, 0.0], model.center_of_mass, atol=1e-3)
        np.testing.assert_allclose(
            box_inertia(3.0, (0.1, 0.1, 0.1)), model.moment_of_inertia, rtol=1e-5
        )
