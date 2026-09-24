"""DH description of a motion group, read from the NOVA motion-group description.

Only the kinematic constants are kept here - the chain, its joint limits and the
reach they imply. Nothing in this module decides whether a pose is reachable;
that answer always comes from NOVA's inverse kinematics (see
``envelope_service``), never from local forward kinematics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class DHChain:
    """Flat DH description in NOVA units (a/d in mm, angles in rad)."""

    a: np.ndarray  # (J,)
    alpha: np.ndarray  # (J,)
    d: np.ndarray  # (J,)
    theta0: np.ndarray  # (J,) fixed theta offset
    sign: np.ndarray  # (J,) +1, or -1 for reverse_rotation_direction
    lower: np.ndarray  # (J,) joint lower limits [rad]
    upper: np.ndarray  # (J,) joint upper limits [rad]
    # fixed mount->DH-base transform (4x4, mm); identity when the DH chain
    # starts at the mount prim (kinematic_chain_offset null in the description)
    base: np.ndarray | None = None

    @property
    def num_joints(self) -> int:
        return int(self.a.shape[0])

    def to_mount_frame(self, positions_mm: np.ndarray) -> np.ndarray:
        """Transform (N, 3) DH-base-frame positions into the mount frame.

        The mount frame is the motion group's own frame, which is what NOVA
        states its poses in - see the envelope overlay, which draws there.
        """
        if self.base is None or positions_mm.size == 0:
            return positions_mm
        rotation = self.base[:3, :3].astype(positions_mm.dtype)
        translation = self.base[:3, 3].astype(positions_mm.dtype)
        return positions_mm @ rotation.T + translation


def dh_chain_from_description(
    dh_parameters, joint_limits=None, kinematic_chain_offset=None
) -> DHChain:
    """Build a :class:`DHChain` from NOVA ``DHParameter`` list + optional limits.

    ``joint_limits`` is a list of objects with ``.lower_limit`` / ``.upper_limit``
    (NOVA ``LimitRange``); when absent a wide default (+/- pi) is used.
    ``kinematic_chain_offset`` (NOVA ``Pose``, position mm / rotvec rad) is the
    fixed mount->DH-base transform.
    """
    a, alpha, d, theta0, sign, lower, upper = [], [], [], [], [], [], []
    for index, parameter in enumerate(dh_parameters):
        a.append(float(parameter.a) if parameter.a is not None else 0.0)
        alpha.append(float(parameter.alpha) if parameter.alpha is not None else 0.0)
        d.append(float(parameter.d) if parameter.d is not None else 0.0)
        theta0.append(float(parameter.theta) if parameter.theta is not None else 0.0)
        sign.append(
            -1.0 if getattr(parameter, "reverse_rotation_direction", False) else 1.0
        )
        limit = (
            joint_limits[index] if joint_limits and index < len(joint_limits) else None
        )
        if (
            limit is not None
            and limit.lower_limit is not None
            and limit.upper_limit is not None
        ):
            lower.append(float(limit.lower_limit))
            upper.append(float(limit.upper_limit))
        else:
            lower.append(-np.pi)
            upper.append(np.pi)

    def as_float32(values) -> np.ndarray:
        return np.asarray(values, dtype=np.float32)

    base = None
    if kinematic_chain_offset is not None:
        from wandelbots.omni.manipulators.utils import kinematic_chain_offset_matrix

        base = kinematic_chain_offset_matrix(kinematic_chain_offset)
    return DHChain(
        as_float32(a),
        as_float32(alpha),
        as_float32(d),
        as_float32(theta0),
        as_float32(sign),
        as_float32(lower),
        as_float32(upper),
        base=base,
    )
