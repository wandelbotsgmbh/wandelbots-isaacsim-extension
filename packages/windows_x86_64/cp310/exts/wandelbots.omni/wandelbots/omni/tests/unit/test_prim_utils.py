import omni.kit.test
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.math import euler_to_rotvec
from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.tests.stage_utils import use_stage
from pxr import UsdGeom, Usd, UsdPhysics, Gf
import omni.usd
import omni.ui.scene as sc
from contextlib import contextmanager


class TestPrimUtils(omni.kit.test.AsyncTestCase):
    # Before running each test
    async def setUp(self):
        pass

    # After running each test
    async def tearDown(self):
        pass

    @contextmanager
    def use_test_stage(self):
        stage: Usd.Stage = Usd.Stage.CreateInMemory("TestPrimUtils")
        self.assertIsNotNone(stage)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        self.assertEqual(1, UsdGeom.GetStageMetersPerUnit(stage))
        with use_stage(stage):
            yield stage

    async def test_world_prim_pose_is_zero(self):
        with self.use_test_stage() as stage:
            target_prim: UsdGeom.Xform = UsdGeom.Xform.Define(
                stage,
                "/TargetXform",
            )

            self.assertListEqual(
                [0, 0, 0, 0, 0, 0],
                PrimUtils.get_prim_pose(
                    target_prim.GetPath().pathString,
                    coordinate_system="world",
                    stage=stage,
                ).pose,
            )

    async def test_prim_pose_is_rotated_zyx(self):
        with self.use_test_stage() as stage:
            target_prim: UsdGeom.Xform = UsdGeom.Xform.Define(
                stage,
                "/TargetXform",
            )
            target_prim.AddTranslateOp().Set(value=(1, 2, 3))
            target_prim.AddRotateZYXOp().Set(value=(10, 20, 30))

            # RotateZYXOp.Set(value=(10, 20, 30)) stores angles as (X, Y, Z) = (10, 20, 30)
            # For extrinsic 'zyx', we need [z, y, x] order = [30, 20, 10]
            rotvec = euler_to_rotvec([30, 20, 10], "zyx", degrees=True)
            ref_pose = [1000, 2000, 3000] + rotvec
            actual_pose = PrimUtils.get_prim_pose(
                target_prim.GetPath().pathString, coordinate_system="world", stage=stage
            ).pose

            for p_idx in range(len(ref_pose)):
                self.assertAlmostEqual(
                    ref_pose[p_idx],
                    actual_pose[p_idx],
                    places=5,
                )

    async def test_prim_pose_is_rotated_xzy(self):
        with self.use_test_stage() as stage:
            target_prim: UsdGeom.Xform = UsdGeom.Xform.Define(
                stage,
                "/TargetXform",
            )
            target_prim.AddTranslateOp().Set(value=(1, 2, 3))
            target_prim.AddRotateXZYOp().Set(value=(10, 20, 30))

            # RotateXZYOp.Set(value=(10, 20, 30)) stores angles as (X, Y, Z) = (10, 20, 30)
            # For extrinsic 'xzy', we need [x, z, y] order = [10, 30, 20]
            rotvec = euler_to_rotvec([10, 30, 20], "xzy", degrees=True)
            ref_pose = [1000, 2000, 3000] + rotvec
            actual_pose = PrimUtils.get_prim_pose(
                target_prim.GetPath().pathString, coordinate_system="world", stage=stage
            ).pose

            for p_idx in range(len(ref_pose)):
                self.assertAlmostEqual(
                    ref_pose[p_idx],
                    actual_pose[p_idx],
                    places=5,
                )

    async def test_prim_pose_is_rotated_xyz(self):
        with self.use_test_stage() as stage:
            target_prim: UsdGeom.Xform = UsdGeom.Xform.Define(
                stage,
                "/TargetXform",
            )
            target_prim.AddTranslateOp().Set(value=(1, 2, 3))
            target_prim.AddRotateXYZOp().Set(value=(10, 20, 30))

            # XYZ(10, 20, 30) -> USD uses extrinsic convention
            rotvec = euler_to_rotvec([10, 20, 30], "xyz", degrees=True)
            ref_pose = [1000, 2000, 3000] + rotvec
            actual_pose = PrimUtils.get_prim_pose(
                target_prim.GetPath().pathString, coordinate_system="world", stage=stage
            ).pose

            for p_idx in range(len(ref_pose)):
                self.assertAlmostEqual(
                    ref_pose[p_idx],
                    actual_pose[p_idx],
                    places=5,
                )

    async def test_prim_pose_is_rotated_yxz(self):
        with self.use_test_stage() as stage:
            target_prim: UsdGeom.Xform = UsdGeom.Xform.Define(
                stage,
                "/TargetXform",
            )
            target_prim.AddTranslateOp().Set(value=(1, 2, 3))
            target_prim.AddRotateYXZOp().Set(value=(10, 20, 30))

            # RotateYXZOp.Set(value=(10, 20, 30)) stores angles as (X, Y, Z) = (10, 20, 30)
            # For extrinsic 'yxz', we need [y, x, z] order = [20, 10, 30]
            rotvec = euler_to_rotvec([20, 10, 30], "yxz", degrees=True)
            ref_pose = [1000, 2000, 3000] + rotvec
            actual_pose = PrimUtils.get_prim_pose(
                target_prim.GetPath().pathString, coordinate_system="world", stage=stage
            ).pose

            for p_idx in range(len(ref_pose)):
                self.assertAlmostEqual(
                    ref_pose[p_idx],
                    actual_pose[p_idx],
                    places=5,
                )

    async def test_scene_matrix(self):
        unit_factor = 1.0 / 1000.0  # assuming stage units are in millimeters
        ref_pose = [1000, 2000, 3000, 0.1, 0.2, 0.3]  # rotation vector values
        transform = (
            sc.Matrix44.get_translation_matrix(
                ref_pose[0] * unit_factor,
                ref_pose[1] * unit_factor,
                ref_pose[2] * unit_factor,
            )
            * sc.Matrix44.get_rotation_matrix(
                ref_pose[3],
                ref_pose[4],
                ref_pose[5],
            )
            * sc.Matrix44.get_scale_matrix(unit_factor, unit_factor, unit_factor)
        )

        transform = sc.Matrix44() * transform

    async def test_rigid_body_get_local_pose(self):
        with self.use_test_stage() as test_stage:
            rigid_body_path = "/World/Cube"
            rigid_body_cube: UsdGeom.Cube = UsdGeom.Cube.Define(
                test_stage, rigid_body_path
            )
            UsdPhysics.RigidBodyAPI.Apply(rigid_body_cube.GetPrim())

            rigid_body_cube.AddTranslateOp().Set(value=(0.1, 0.2, 0.3))
            rigid_body_cube.AddOrientOp().Set(value=Gf.Quatf(1, 0, 0, 0))

            actual_pose = PrimUtils.get_prim_pose(
                rigid_body_path,
                coordinate_system="local",
                rotation_type="cartesian",
                stage=test_stage,
            )

            expected_position = [100, 200, 300]
            for position_idx in range(3):
                self.assertAlmostEqual(
                    expected_position[position_idx],
                    actual_pose.pose[position_idx],
                    places=1,
                )

    async def test_rigid_body_set_local_pose(self):
        with self.use_test_stage() as test_stage:
            rigid_body_path = "/World/Cube"
            rigid_body_cube: UsdGeom.Cube = UsdGeom.Cube.Define(
                test_stage, rigid_body_path
            )
            UsdPhysics.RigidBodyAPI.Apply(rigid_body_cube.GetPrim())

            pose_to_set = WSPose(pose=[100, 200, 300, 0.1, 0.2, 0.3])
            PrimUtils.set_prim_pose(rigid_body_path, pose_to_set, test_stage)

            rigid_body_prim = rigid_body_cube.GetPrim()
            translate_attr = rigid_body_prim.GetAttribute("xformOp:translate")
            orient_attr = rigid_body_prim.GetAttribute("xformOp:orient")

            self.assertIsNotNone(translate_attr)
            self.assertIsNotNone(orient_attr)

            actual_translation = translate_attr.Get()
            self.assertAlmostEqual(actual_translation[0], 0.1, places=3)
            self.assertAlmostEqual(actual_translation[1], 0.2, places=3)
            self.assertAlmostEqual(actual_translation[2], 0.3, places=3)

    async def test_rigid_body_get_world_pose(self):
        with self.use_test_stage() as test_stage:
            parent_xform_path = "/World/Parent"
            parent_xform: UsdGeom.Xform = UsdGeom.Xform.Define(
                test_stage, parent_xform_path
            )
            parent_xform.AddTranslateOp().Set(value=(1, 2, 3))

            rigid_body_path = "/World/Parent/Cube"
            rigid_body_cube: UsdGeom.Cube = UsdGeom.Cube.Define(
                test_stage, rigid_body_path
            )
            UsdPhysics.RigidBodyAPI.Apply(rigid_body_cube.GetPrim())

            rigid_body_cube.AddTranslateOp().Set(value=(0.1, 0.2, 0.3))
            rigid_body_cube.AddOrientOp().Set(value=Gf.Quatf(1, 0, 0, 0))

            actual_world_pose = PrimUtils.get_prim_pose(
                rigid_body_path,
                coordinate_system="world",
                rotation_type="cartesian",
                stage=test_stage,
            )

            expected_world_position = [1100, 2200, 3300]
            for position_idx in range(3):
                self.assertAlmostEqual(
                    expected_world_position[position_idx],
                    actual_world_pose.pose[position_idx],
                    places=1,
                )
