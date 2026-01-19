import math
import numpy as np
import omni.ui.scene as sc


def quat_to_rotvec(x: float, y: float, z: float, w: float) -> list[float]:
    """Convert quaternion (x, y, z, w) to rotation vector (axis-angle representation).

    Args:
        x: Quaternion x component (imaginary)
        y: Quaternion y component (imaginary)
        z: Quaternion z component (imaginary)
        w: Quaternion w component (real/scalar)

    Returns:
        Rotation vector [rx, ry, rz] where the vector direction is the rotation axis
        and the magnitude is the rotation angle in radians.
    """
    # Normalize quaternion
    norm = np.sqrt(x * x + y * y + z * z + w * w)
    if norm < 1e-10:
        return [0.0, 0.0, 0.0]
    x, y, z, w = x / norm, y / norm, z / norm, w / norm

    # Ensure w is positive (choose the shorter rotation path)
    if w < 0:
        x, y, z, w = -x, -y, -z, -w

    # Compute angle using arctan2 to handle numerical issues
    sin_half_angle_sq = x * x + y * y + z * z
    if sin_half_angle_sq < 1e-10:
        # No rotation
        return [0.0, 0.0, 0.0]

    sin_half_angle = np.sqrt(sin_half_angle_sq)
    angle = 2.0 * np.arctan2(sin_half_angle, w)

    # Compute axis (normalized) and scale by angle
    axis_scale = angle / sin_half_angle
    return [float(x * axis_scale), float(y * axis_scale), float(z * axis_scale)]


def rotvec_to_quat(rx: float, ry: float, rz: float) -> list[float]:
    """Convert rotation vector (axis-angle) to quaternion (x, y, z, w).

    Args:
        rx: Rotation vector x component
        ry: Rotation vector y component
        rz: Rotation vector z component

    Returns:
        Quaternion [x, y, z, w] where w is the scalar component.
    """
    angle = np.sqrt(rx * rx + ry * ry + rz * rz)
    if angle < 1e-10:
        return [0.0, 0.0, 0.0, 1.0]

    half_angle = angle / 2.0
    s = np.sin(half_angle) / angle
    return [float(rx * s), float(ry * s), float(rz * s), float(np.cos(half_angle))]


def rotvec_to_matrix(rx: float, ry: float, rz: float) -> np.ndarray:
    """Convert rotation vector (axis-angle) to 3x3 rotation matrix.

    Uses Rodrigues' rotation formula.

    Args:
        rx: Rotation vector x component
        ry: Rotation vector y component
        rz: Rotation vector z component

    Returns:
        3x3 rotation matrix as numpy array.
    """
    angle = np.sqrt(rx * rx + ry * ry + rz * rz)
    if angle < 1e-10:
        return np.eye(3)

    # Normalize axis
    ax, ay, az = rx / angle, ry / angle, rz / angle

    # Rodrigues' formula: R = I + sin(θ)K + (1-cos(θ))K²
    # where K is the skew-symmetric matrix of the axis
    c = np.cos(angle)
    s = np.sin(angle)
    t = 1.0 - c

    return np.array(
        [
            [t * ax * ax + c, t * ax * ay - s * az, t * ax * az + s * ay],
            [t * ax * ay + s * az, t * ay * ay + c, t * ay * az - s * ax],
            [t * ax * az - s * ay, t * ay * az + s * ax, t * az * az + c],
        ]
    )


def matrix_to_rotvec(R: np.ndarray) -> list[float]:
    """Convert a 3x3 rotation matrix to rotation vector (axis-angle).

    Args:
        R: 3x3 rotation matrix as numpy array.

    Returns:
        Rotation vector [rx, ry, rz] in radians.
    """
    # Use Rodrigues' formula inverse
    # angle = arccos((trace(R) - 1) / 2)
    trace = R[0, 0] + R[1, 1] + R[2, 2]
    cos_angle = (trace - 1.0) / 2.0
    cos_angle = np.clip(cos_angle, -1.0, 1.0)
    angle = math.acos(cos_angle)

    if abs(angle) < 1e-10:
        # No rotation
        return [0.0, 0.0, 0.0]

    if abs(angle - math.pi) < 1e-6:
        # 180 degree rotation - need to extract axis from R
        # Find the column of (R + I) with largest norm
        RpI = R + np.eye(3)
        norms = [np.linalg.norm(RpI[:, i]) for i in range(3)]
        max_idx = int(np.argmax(norms))
        axis = RpI[:, max_idx]
        axis = axis / np.linalg.norm(axis)
        return [float(axis[0] * angle), float(axis[1] * angle), float(axis[2] * angle)]

    # General case: axis from skew-symmetric part
    axis = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    axis = axis / (2.0 * math.sin(angle))
    return [float(axis[0] * angle), float(axis[1] * angle), float(axis[2] * angle)]


def euler_to_rotvec(
    angles: list[float], order: str, degrees: bool = True
) -> list[float]:
    """Convert Euler angles to rotation vector using extrinsic (fixed-frame) convention.

    Args:
        angles: List of 3 angles in the order specified by 'order' parameter
        order: Axis order string like 'xyz', 'zyx', etc. (lowercase for extrinsic)
        degrees: If True, angles are in degrees; otherwise radians

    Returns:
        Rotation vector [rx, ry, rz] in radians.
    """
    if degrees:
        angles = [math.radians(a) for a in angles]

    # Build rotation matrix from extrinsic Euler angles
    def rot_x(a: float) -> np.ndarray:
        c, s = math.cos(a), math.sin(a)
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])

    def rot_y(a: float) -> np.ndarray:
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])

    def rot_z(a: float) -> np.ndarray:
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])

    rot_funcs = {"x": rot_x, "y": rot_y, "z": rot_z}

    # Extrinsic: multiply in order of string (first rotation applied to identity first)
    R = np.eye(3)
    for i, axis in enumerate(order.lower()):
        R = rot_funcs[axis](angles[i]) @ R

    return matrix_to_rotvec(R)


def pose_to_matrix(pose: list[float]) -> np.ndarray:
    """Convert a pose [x, y, z, rx, ry, rz] to a 4x4 transformation matrix.

    Args:
        pose: List of 6 floats [x, y, z, rx, ry, rz] where rotation is a rotation vector.

    Returns:
        4x4 homogeneous transformation matrix.
    """
    mat = np.eye(4)
    mat[:3, :3] = rotvec_to_matrix(pose[3], pose[4], pose[5])
    mat[:3, 3] = pose[:3]
    return mat


def matrix_to_pose(mat: np.ndarray) -> list[float]:
    """Convert a 4x4 transformation matrix to a pose [x, y, z, rx, ry, rz].

    Args:
        mat: 4x4 homogeneous transformation matrix.

    Returns:
        List of 6 floats [x, y, z, rx, ry, rz] where rotation is a rotation vector.
    """
    trans = mat[:3, 3].tolist()
    rotvec = matrix_to_rotvec(mat[:3, :3])
    return trans + rotvec


def compose_rotvecs(rotvec1: list[float], rotvec2: list[float]) -> list[float]:
    """Compose two rotation vectors (apply rotvec2 after rotvec1).

    This is equivalent to scipy's Rotation multiplication: r1 * r2.

    Args:
        rotvec1: First rotation vector [rx, ry, rz]
        rotvec2: Second rotation vector [rx, ry, rz]

    Returns:
        Composed rotation vector [rx, ry, rz] representing rotvec2 applied after rotvec1.
    """
    # Convert to matrices, multiply, convert back
    R1 = rotvec_to_matrix(*rotvec1)
    R2 = rotvec_to_matrix(*rotvec2)
    R_composed = R1 @ R2
    return matrix_to_rotvec(R_composed)


def nova_pose_to_scene_matrix(pose: list[float], stage_meters_per_unit: float = 1.0):
    """Convert a Nova pose to omni.ui.scene.Matrix44.

    Nova poses are [x, y, z, rx, ry, rz] where:
    - Translation is in millimeters
    - Rotation is a rotation vector (axis-angle) in radians

    Args:
        pose: Nova pose [x, y, z, rx, ry, rz] in mm and radians
        stage_meters_per_unit: Stage scale (default 0.01 for cm stages)

    Returns:
        omni.ui.scene.Matrix44 transformation matrix.
    """
    unit_factor = 0.001 / stage_meters_per_unit  # mm -> meters -> stage units
    rot_matrix = rotvec_to_matrix(pose[3], pose[4], pose[5])

    # Matrix44 expects values in column-major order:
    # [m00, m10, m20, m30, m01, m11, m21, m31, m02, m12, m22, m32, m03, m13, m23, m33]
    tx = pose[0] * unit_factor
    ty = pose[1] * unit_factor
    tz = pose[2] * unit_factor

    return sc.Matrix44(
        rot_matrix[0, 0],
        rot_matrix[1, 0],
        rot_matrix[2, 0],
        0.0,
        rot_matrix[0, 1],
        rot_matrix[1, 1],
        rot_matrix[2, 1],
        0.0,
        rot_matrix[0, 2],
        rot_matrix[1, 2],
        rot_matrix[2, 2],
        0.0,
        tx,
        ty,
        tz,
        1.0,
    )
