import omni.kit.test
import math
import numpy as np
from wandelbots.omni.utils.math import (
    quat_to_rotvec,
    rotvec_to_quat,
    rotvec_to_matrix,
    matrix_to_rotvec,
    euler_to_rotvec,
    pose_to_matrix,
    matrix_to_pose,
    compose_rotvecs,
    nova_pose_to_scene_matrix,
)
import omni.ui.scene as sc


class TestMathUtils(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        pass

    async def tearDown(self):
        pass

    # ==================== quat_to_rotvec tests ====================

    async def test_quat_to_rotvec_identity(self):
        """Identity quaternion should give zero rotation vector."""
        result = quat_to_rotvec(0, 0, 0, 1)
        self.assertEqual(result, [0.0, 0.0, 0.0])

    async def test_quat_to_rotvec_90deg_x(self):
        """90 degree rotation around X axis."""
        # Quaternion for 90 deg around X: (sin(45°), 0, 0, cos(45°))
        half_angle = math.radians(45)
        x, y, z, w = math.sin(half_angle), 0, 0, math.cos(half_angle)
        result = quat_to_rotvec(x, y, z, w)
        expected = [math.radians(90), 0.0, 0.0]
        for i in range(3):
            self.assertAlmostEqual(result[i], expected[i], places=6)

    async def test_quat_to_rotvec_90deg_y(self):
        """90 degree rotation around Y axis."""
        half_angle = math.radians(45)
        x, y, z, w = 0, math.sin(half_angle), 0, math.cos(half_angle)
        result = quat_to_rotvec(x, y, z, w)
        expected = [0.0, math.radians(90), 0.0]
        for i in range(3):
            self.assertAlmostEqual(result[i], expected[i], places=6)

    async def test_quat_to_rotvec_90deg_z(self):
        """90 degree rotation around Z axis."""
        half_angle = math.radians(45)
        x, y, z, w = 0, 0, math.sin(half_angle), math.cos(half_angle)
        result = quat_to_rotvec(x, y, z, w)
        expected = [0.0, 0.0, math.radians(90)]
        for i in range(3):
            self.assertAlmostEqual(result[i], expected[i], places=6)

    async def test_quat_to_rotvec_negative_w(self):
        """Quaternion with negative w should still work (double cover)."""
        half_angle = math.radians(45)
        # Negated quaternion represents same rotation
        x, y, z, w = -math.sin(half_angle), 0, 0, -math.cos(half_angle)
        result = quat_to_rotvec(x, y, z, w)
        expected = [math.radians(90), 0.0, 0.0]
        for i in range(3):
            self.assertAlmostEqual(result[i], expected[i], places=6)

    async def test_quat_to_rotvec_unnormalized(self):
        """Unnormalized quaternion should be handled correctly."""
        half_angle = math.radians(45)
        scale = 2.5
        x, y, z, w = (
            scale * math.sin(half_angle),
            0,
            0,
            scale * math.cos(half_angle),
        )
        result = quat_to_rotvec(x, y, z, w)
        expected = [math.radians(90), 0.0, 0.0]
        for i in range(3):
            self.assertAlmostEqual(result[i], expected[i], places=6)

    # ==================== rotvec_to_quat tests ====================

    async def test_rotvec_to_quat_zero(self):
        """Zero rotation vector should give identity quaternion."""
        result = rotvec_to_quat(0, 0, 0)
        self.assertAlmostEqual(result[0], 0.0, places=6)
        self.assertAlmostEqual(result[1], 0.0, places=6)
        self.assertAlmostEqual(result[2], 0.0, places=6)
        self.assertAlmostEqual(result[3], 1.0, places=6)

    async def test_rotvec_to_quat_90deg_x(self):
        """90 degree rotation around X axis."""
        rotvec = [math.radians(90), 0, 0]
        result = rotvec_to_quat(*rotvec)
        half_angle = math.radians(45)
        expected = [math.sin(half_angle), 0, 0, math.cos(half_angle)]
        for i in range(4):
            self.assertAlmostEqual(result[i], expected[i], places=6)

    async def test_quat_rotvec_roundtrip(self):
        """Converting quat->rotvec->quat should give same quaternion."""
        # Original quaternion (normalized)
        half_angle = math.radians(30)
        axis = np.array([1, 2, 3])
        axis = axis / np.linalg.norm(axis)
        original_quat = [
            axis[0] * math.sin(half_angle),
            axis[1] * math.sin(half_angle),
            axis[2] * math.sin(half_angle),
            math.cos(half_angle),
        ]

        rotvec = quat_to_rotvec(*original_quat)
        result_quat = rotvec_to_quat(*rotvec)

        for i in range(4):
            self.assertAlmostEqual(result_quat[i], original_quat[i], places=6)

    # ==================== rotvec_to_matrix tests ====================

    async def test_rotvec_to_matrix_identity(self):
        """Zero rotation vector should give identity matrix."""
        result = rotvec_to_matrix(0, 0, 0)
        expected = np.eye(3)
        np.testing.assert_array_almost_equal(result, expected)

    async def test_rotvec_to_matrix_90deg_z(self):
        """90 degree rotation around Z axis."""
        result = rotvec_to_matrix(0, 0, math.radians(90))
        # Rotation around Z by 90 deg: [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        expected = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
        np.testing.assert_array_almost_equal(result, expected, decimal=6)

    async def test_rotvec_to_matrix_orthogonal(self):
        """Resulting matrix should be orthogonal (R @ R.T = I)."""
        result = rotvec_to_matrix(0.1, 0.2, 0.3)
        identity = result @ result.T
        np.testing.assert_array_almost_equal(identity, np.eye(3), decimal=6)

    async def test_rotvec_to_matrix_det_one(self):
        """Rotation matrix should have determinant 1."""
        result = rotvec_to_matrix(0.5, -0.3, 0.7)
        det = np.linalg.det(result)
        self.assertAlmostEqual(det, 1.0, places=6)

    # ==================== matrix_to_rotvec tests ====================

    async def test_matrix_to_rotvec_identity(self):
        """Identity matrix should give zero rotation vector."""
        result = matrix_to_rotvec(np.eye(3))
        self.assertAlmostEqual(result[0], 0.0, places=6)
        self.assertAlmostEqual(result[1], 0.0, places=6)
        self.assertAlmostEqual(result[2], 0.0, places=6)

    async def test_matrix_rotvec_roundtrip(self):
        """Converting rotvec->matrix->rotvec should give same rotation vector."""
        original = [0.3, -0.5, 0.7]
        matrix = rotvec_to_matrix(*original)
        result = matrix_to_rotvec(matrix)
        for i in range(3):
            self.assertAlmostEqual(result[i], original[i], places=6)

    async def test_matrix_to_rotvec_180deg(self):
        """180 degree rotation should be handled correctly."""
        # 180 deg around X axis
        matrix = np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], dtype=float)
        result = matrix_to_rotvec(matrix)
        angle = np.sqrt(sum(r * r for r in result))
        self.assertAlmostEqual(angle, math.pi, places=5)
        # Axis should be [1, 0, 0] or [-1, 0, 0]
        axis = [r / angle for r in result]
        self.assertAlmostEqual(abs(axis[0]), 1.0, places=5)
        self.assertAlmostEqual(axis[1], 0.0, places=5)
        self.assertAlmostEqual(axis[2], 0.0, places=5)

    # ==================== euler_to_rotvec tests ====================

    async def test_euler_to_rotvec_zero(self):
        """Zero angles should give zero rotation vector."""
        result = euler_to_rotvec([0, 0, 0], "xyz", degrees=True)
        for i in range(3):
            self.assertAlmostEqual(result[i], 0.0, places=6)

    async def test_euler_to_rotvec_xyz(self):
        """XYZ extrinsic Euler angles conversion."""
        # Single axis rotation for easy verification
        result = euler_to_rotvec([90, 0, 0], "xyz", degrees=True)
        expected = [math.radians(90), 0.0, 0.0]
        for i in range(3):
            self.assertAlmostEqual(result[i], expected[i], places=5)

    async def test_euler_to_rotvec_radians(self):
        """Euler angles in radians."""
        result = euler_to_rotvec([math.pi / 2, 0, 0], "xyz", degrees=False)
        expected = [math.pi / 2, 0.0, 0.0]
        for i in range(3):
            self.assertAlmostEqual(result[i], expected[i], places=5)

    async def test_euler_to_rotvec_zyx(self):
        """ZYX extrinsic Euler angles conversion."""
        # For 'zyx' order, angles are [z_angle, y_angle, x_angle]
        # So [90, 0, 0] means 90 deg rotation around Z
        result = euler_to_rotvec([90, 0, 0], "zyx", degrees=True)
        expected = [0.0, 0.0, math.radians(90)]
        for i in range(3):
            self.assertAlmostEqual(result[i], expected[i], places=5)

    # ==================== pose_to_matrix / matrix_to_pose tests ====================

    async def test_pose_to_matrix_identity(self):
        """Zero pose should give identity matrix."""
        result = pose_to_matrix([0, 0, 0, 0, 0, 0])
        expected = np.eye(4)
        np.testing.assert_array_almost_equal(result, expected)

    async def test_pose_to_matrix_translation_only(self):
        """Pure translation pose."""
        result = pose_to_matrix([1, 2, 3, 0, 0, 0])
        expected = np.eye(4)
        expected[:3, 3] = [1, 2, 3]
        np.testing.assert_array_almost_equal(result, expected)

    async def test_pose_matrix_roundtrip(self):
        """Converting pose->matrix->pose should give same pose."""
        original = [1.5, -2.3, 4.7, 0.1, -0.2, 0.3]
        matrix = pose_to_matrix(original)
        result = matrix_to_pose(matrix)
        for i in range(6):
            self.assertAlmostEqual(result[i], original[i], places=5)

    async def test_matrix_to_pose_rotation_only(self):
        """Pure rotation matrix."""
        # 90 deg around Z
        mat = np.eye(4)
        mat[:3, :3] = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
        result = matrix_to_pose(mat)
        self.assertAlmostEqual(result[0], 0.0, places=6)
        self.assertAlmostEqual(result[1], 0.0, places=6)
        self.assertAlmostEqual(result[2], 0.0, places=6)
        self.assertAlmostEqual(result[3], 0.0, places=5)
        self.assertAlmostEqual(result[4], 0.0, places=5)
        self.assertAlmostEqual(result[5], math.radians(90), places=5)

    # ==================== compose_rotvecs tests ====================

    async def test_compose_rotvecs_identity(self):
        """Composing with identity (zero) rotation should give the same rotation."""
        rotvec = [0.1, 0.2, 0.3]
        result = compose_rotvecs([0, 0, 0], rotvec)
        for i in range(3):
            self.assertAlmostEqual(result[i], rotvec[i], places=6)

    async def test_compose_rotvecs_identity_right(self):
        """Composing with identity on the right should give the same rotation."""
        rotvec = [0.1, 0.2, 0.3]
        result = compose_rotvecs(rotvec, [0, 0, 0])
        for i in range(3):
            self.assertAlmostEqual(result[i], rotvec[i], places=6)

    async def test_compose_rotvecs_same_axis(self):
        """Composing rotations on the same axis should add angles."""
        rotvec1 = [0.1, 0, 0]  # 0.1 rad around X
        rotvec2 = [0.2, 0, 0]  # 0.2 rad around X
        result = compose_rotvecs(rotvec1, rotvec2)
        expected = [0.3, 0, 0]  # 0.3 rad around X
        for i in range(3):
            self.assertAlmostEqual(result[i], expected[i], places=6)

    async def test_compose_rotvecs_90deg_xy(self):
        """Compose 90 deg X then 90 deg Y."""
        rotvec1 = [math.radians(90), 0, 0]  # 90 deg around X
        rotvec2 = [0, math.radians(90), 0]  # 90 deg around Y
        result = compose_rotvecs(rotvec1, rotvec2)
        # The result should be a valid rotation vector
        angle = math.sqrt(sum(r * r for r in result))
        self.assertGreater(angle, 0)
        # Verify by converting back to matrix and checking
        R1 = rotvec_to_matrix(*rotvec1)
        R2 = rotvec_to_matrix(*rotvec2)
        expected_matrix = R1 @ R2
        result_matrix = rotvec_to_matrix(*result)
        np.testing.assert_array_almost_equal(result_matrix, expected_matrix, decimal=6)

    # ==================== nova_pose_to_scene_matrix tests ====================

    async def test_nova_pose_to_scene_matrix_identity(self):
        """Zero pose should give identity matrix."""

        result = nova_pose_to_scene_matrix([0, 0, 0, 0, 0, 0])
        self.assertIsInstance(result, sc.Matrix44)
        # Check diagonal elements are 1
        self.assertAlmostEqual(result[0], 1.0, places=6)
        self.assertAlmostEqual(result[5], 1.0, places=6)
        self.assertAlmostEqual(result[10], 1.0, places=6)
        self.assertAlmostEqual(result[15], 1.0, places=6)
        # Check translation is 0
        self.assertAlmostEqual(result[12], 0.0, places=6)
        self.assertAlmostEqual(result[13], 0.0, places=6)
        self.assertAlmostEqual(result[14], 0.0, places=6)

    async def test_nova_pose_to_scene_matrix_translation(self):
        """Translation should be converted from mm to stage units."""

        # 1000mm = 1m, with default stage_meters_per_unit=0.01 (cm), 1m = 100 stage units
        pose = [1000, 2000, 3000, 0, 0, 0]
        result = nova_pose_to_scene_matrix(pose, stage_meters_per_unit=0.01)
        self.assertIsInstance(result, sc.Matrix44)
        # Translation in stage units (cm): 1000mm = 100cm, 2000mm = 200cm, 3000mm = 300cm
        self.assertAlmostEqual(result[12], 100.0, places=4)
        self.assertAlmostEqual(result[13], 200.0, places=4)
        self.assertAlmostEqual(result[14], 300.0, places=4)

    async def test_nova_pose_to_scene_matrix_translation_meters(self):
        """Translation with stage in meters."""

        # 1000mm = 1m, with stage_meters_per_unit=1.0 (meters), 1m = 1 stage unit
        pose = [1000, 2000, 3000, 0, 0, 0]
        result = nova_pose_to_scene_matrix(pose, stage_meters_per_unit=1.0)
        self.assertIsInstance(result, sc.Matrix44)
        self.assertAlmostEqual(result[12], 1.0, places=4)
        self.assertAlmostEqual(result[13], 2.0, places=4)
        self.assertAlmostEqual(result[14], 3.0, places=4)

    async def test_nova_pose_to_scene_matrix_rotation_90z(self):
        """90 degree rotation around Z axis."""

        pose = [0, 0, 0, 0, 0, math.radians(90)]
        result = nova_pose_to_scene_matrix(pose)
        self.assertIsInstance(result, sc.Matrix44)
        # Rotation matrix for 90 deg around Z (column-major):
        # [0, 1, 0, 0,  -1, 0, 0, 0,  0, 0, 1, 0,  0, 0, 0, 1]
        self.assertAlmostEqual(result[0], 0.0, places=5)
        self.assertAlmostEqual(result[1], 1.0, places=5)
        self.assertAlmostEqual(result[4], -1.0, places=5)
        self.assertAlmostEqual(result[5], 0.0, places=5)
        self.assertAlmostEqual(result[10], 1.0, places=5)

    async def test_nova_pose_to_scene_matrix_combined(self):
        """Combined translation and rotation."""

        pose = [1000, 0, 0, 0, 0, math.radians(90)]
        result = nova_pose_to_scene_matrix(pose, stage_meters_per_unit=0.01)
        self.assertIsInstance(result, sc.Matrix44)
        # Translation: 1000mm = 100cm
        self.assertAlmostEqual(result[12], 100.0, places=4)
        self.assertAlmostEqual(result[13], 0.0, places=4)
        self.assertAlmostEqual(result[14], 0.0, places=4)
        # Rotation 90 deg around Z
        self.assertAlmostEqual(result[0], 0.0, places=5)
        self.assertAlmostEqual(result[1], 1.0, places=5)
