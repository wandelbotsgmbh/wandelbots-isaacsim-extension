import math
import omni.kit.test
import numpy as np
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.math import euler_to_rotvec, pose_to_matrix, matrix_to_pose
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

    # --- set_relative_pose (SE(3) composition) ---------------------------------

    def _define_posed_xform(self, stage, path, initial_pose):
        """Create a top-level Xform with translate+orient ops at initial_pose.

        translate/orient ops must exist for set_prim_pose to write to them.
        """
        prim: UsdGeom.Xform = UsdGeom.Xform.Define(stage, path)
        prim.AddTranslateOp()
        prim.AddOrientOp()
        PrimUtils.set_prim_pose(path, WSPose(pose=initial_pose), stage)
        return path

    def _se3_reference(self, current_pose, relative_pose, object_first):
        """Independent SE(3) composition reference (not via set_relative_pose)."""
        t_current = pose_to_matrix(current_pose)
        t_relative = pose_to_matrix(relative_pose)
        t_new = t_current @ t_relative if object_first else t_relative @ t_current
        return matrix_to_pose(t_new)

    def _assert_pose_matrices_close(self, expected_pose, actual_pose, places=3):
        """Compare poses via their 4x4 matrices to avoid rotvec representation ambiguity."""
        expected_matrix = pose_to_matrix(expected_pose)
        actual_matrix = pose_to_matrix(actual_pose)
        for row in range(4):
            for col in range(4):
                self.assertAlmostEqual(
                    expected_matrix[row, col],
                    actual_matrix[row, col],
                    places=places,
                )

    async def test_set_relative_pose_object_first_rotates_translation_into_local_frame(
        self,
    ):
        # Regression test for the reported bug: with object_first=True the relative
        # translation must be rotated into the object's local frame (R_obj * t_rel),
        # not added/subtracted in raw world coordinates.
        with self.use_test_stage() as stage:
            # Object rotated 90 deg about Z, at the origin.
            path = self._define_posed_xform(
                stage, "/Target", [0, 0, 0, 0, 0, math.pi / 2]
            )
            # Relative pose: 100 mm along the object's local +X, no rotation.
            relative = [100, 0, 0, 0, 0, 0]

            PrimUtils.set_relative_pose(
                path, WSPose(pose=relative), object_first=True, stage=stage
            )

            actual = PrimUtils.get_prim_pose(
                path, coordinate_system="local", rotation_type="cartesian", stage=stage
            ).pose

            # Local +X of a 90 deg-about-Z object points along world +Y.
            self.assertAlmostEqual(actual[0], 0.0, places=2)
            self.assertAlmostEqual(actual[1], 100.0, places=2)
            self.assertAlmostEqual(actual[2], 0.0, places=2)
            # The old (buggy) behavior produced t_obj - t_rel = [-100, 0, 0]; assert we
            # are nowhere near that.
            self.assertGreater(actual[1], 50.0)

    async def test_set_relative_pose_world_first_keeps_world_translation(self):
        # object_first=False applies the relative pose in the world frame (T_rel ∘ T_obj).
        with self.use_test_stage() as stage:
            path = self._define_posed_xform(
                stage, "/Target", [0, 0, 0, 0, 0, math.pi / 2]
            )
            relative = [100, 0, 0, 0, 0, 0]

            PrimUtils.set_relative_pose(
                path, WSPose(pose=relative), object_first=False, stage=stage
            )

            actual = PrimUtils.get_prim_pose(
                path, coordinate_system="local", rotation_type="cartesian", stage=stage
            ).pose

            # Relative rotation is identity, so the translation stays in world +X.
            self.assertAlmostEqual(actual[0], 100.0, places=2)
            self.assertAlmostEqual(actual[1], 0.0, places=2)
            self.assertAlmostEqual(actual[2], 0.0, places=2)

    async def test_set_relative_pose_matches_se3_reference(self):
        # General case with non-trivial translation and rotation on both operands,
        # checked against an independent matrix-composition reference, both orders.
        for object_first in (True, False):
            with self.use_test_stage() as stage:
                initial = [100, 200, 300] + euler_to_rotvec([10, 20, 30], "xyz")
                relative = [40, -50, 60] + euler_to_rotvec([15, -25, 35], "xyz")
                path = self._define_posed_xform(stage, "/Target", initial)

                current = PrimUtils.get_prim_pose(
                    path,
                    coordinate_system="local",
                    rotation_type="cartesian",
                    stage=stage,
                ).pose
                expected = self._se3_reference(current, relative, object_first)

                PrimUtils.set_relative_pose(
                    path, WSPose(pose=relative), object_first=object_first, stage=stage
                )
                actual = PrimUtils.get_prim_pose(
                    path,
                    coordinate_system="local",
                    rotation_type="cartesian",
                    stage=stage,
                ).pose

                self._assert_pose_matrices_close(expected, actual)

    async def test_set_relative_pose_rotation_order_depends_on_object_first(self):
        # Non-commuting rotations: the resulting orientation must differ between the
        # two orders, proving composition order is honored.
        relative = [0, 0, 0] + [0, math.pi / 2, 0]  # 90 deg about Y
        initial = [0, 0, 0] + [math.pi / 2, 0, 0]  # 90 deg about X
        results = {}
        for object_first in (True, False):
            with self.use_test_stage() as stage:
                path = self._define_posed_xform(stage, "/Target", initial)
                PrimUtils.set_relative_pose(
                    path, WSPose(pose=relative), object_first=object_first, stage=stage
                )
                results[object_first] = PrimUtils.get_prim_pose(
                    path,
                    coordinate_system="local",
                    rotation_type="cartesian",
                    stage=stage,
                ).pose

        rot_true = pose_to_matrix(results[True])[:3, :3]
        rot_false = pose_to_matrix(results[False])[:3, :3]
        self.assertFalse(np.allclose(rot_true, rot_false, atol=1e-3))

    async def test_set_relative_pose_identity_is_noop(self):
        with self.use_test_stage() as stage:
            initial = [100, 200, 300] + euler_to_rotvec([10, 20, 30], "xyz")
            path = self._define_posed_xform(stage, "/Target", initial)
            before = PrimUtils.get_prim_pose(
                path, coordinate_system="local", rotation_type="cartesian", stage=stage
            ).pose

            PrimUtils.set_relative_pose(
                path, WSPose(pose=[0, 0, 0, 0, 0, 0]), object_first=True, stage=stage
            )

            after = PrimUtils.get_prim_pose(
                path, coordinate_system="local", rotation_type="cartesian", stage=stage
            ).pose
            self._assert_pose_matrices_close(before, after)

    async def test_set_relative_pose_inverse_returns_to_origin(self):
        # Applying a relative transform then its inverse (same order) returns the
        # original pose.
        with self.use_test_stage() as stage:
            initial = [100, 200, 300] + euler_to_rotvec([10, 20, 30], "xyz")
            relative = [40, -50, 60] + euler_to_rotvec([15, -25, 35], "xyz")
            path = self._define_posed_xform(stage, "/Target", initial)
            before = PrimUtils.get_prim_pose(
                path, coordinate_system="local", rotation_type="cartesian", stage=stage
            ).pose

            relative_inverse = matrix_to_pose(np.linalg.inv(pose_to_matrix(relative)))

            PrimUtils.set_relative_pose(
                path, WSPose(pose=relative), object_first=True, stage=stage
            )
            PrimUtils.set_relative_pose(
                path, WSPose(pose=relative_inverse), object_first=True, stage=stage
            )

            after = PrimUtils.get_prim_pose(
                path, coordinate_system="local", rotation_type="cartesian", stage=stage
            ).pose
            self._assert_pose_matrices_close(before, after)
