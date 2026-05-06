import carb
import isaacsim.core.utils.stage as stage_utils
import wandelbots.usd as wb_schema  # type: ignore
from pxr import UsdPhysics, Usd
import math
import wandelbots_api_client.v2 as wb
import numpy as np
from .motion_group import (
    MotionGroup,
    get_root_articulation_path,
    find_physx_articulation_path,
)
from .articulation_cache import get_articulation_cache
from usd.schema.isaac import robot_schema
from usd.schema.isaac.robot_schema import utils as robot_schema_utils


def get_scene_motion_group_prim_paths(include_prims_without_api=True) -> list[str]:
    """Returns all prim paths with articulation root or motion group api.
    Can be used to discover potential robots

    Returns:
        list[str]: Paths to prims with articulation root or motion group api
    """
    stage: Usd.Stage = stage_utils.get_current_stage()
    if stage is None:
        return []

    def _filter(prim: Usd.Prim) -> bool:
        if prim.HasAPI(wb_schema.MotionGroupAPI):
            return True
        if prim.HasAPI(wb_schema.ToolAPI):
            return False
        if include_prims_without_api and prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            return True
        return False

    return [prim.GetPrimPath().pathString for prim in stage.Traverse() if _filter(prim)]


def dh_transform_matrix(
    a: float, alpha: float, d: float, theta: float, unit_factor: float
) -> np.ndarray:
    """Compute DH transformation matrix as a 4x4 numpy array.

    Standard DH convention: T = Rz(theta) * Tz(d) * Tx(a) * Rx(alpha)

    Args:
        a: Link length (mm)
        alpha: Link twist (radians)
        d: Link offset (mm)
        theta: Joint angle (radians)
        unit_factor: Conversion factor from mm to stage units

    Returns:
        4x4 homogeneous transformation matrix.
    """
    ca = math.cos(alpha)
    sa = math.sin(alpha)
    ct = math.cos(theta)
    st = math.sin(theta)

    # Apply unit conversion to distance parameters
    a_scaled = a * unit_factor
    d_scaled = d * unit_factor

    return np.array(
        [
            [ct, -st * ca, st * sa, a_scaled * ct],
            [st, ct * ca, -ct * sa, a_scaled * st],
            [0.0, sa, ca, d_scaled],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def compute_forward_kinematics_chain(
    dh_parameters: list[wb.models.DHParameter],
    joint_values_rad: list[float],
    dh_unit_to_stage_unit_factor: float,
) -> list[np.ndarray]:
    world_T = np.eye(4)
    results = [world_T.copy()]

    for i, dh_param in enumerate(dh_parameters):
        theta = joint_values_rad[i] if i < len(joint_values_rad) else 0.0
        theta = -theta if dh_param.reverse_rotation_direction else theta
        if dh_param.theta is not None:
            theta += dh_param.theta

        Ti = dh_transform_matrix(
            dh_param.a if dh_param.a is not None else 0.0,
            dh_param.alpha if dh_param.alpha is not None else 0.0,
            dh_param.d if dh_param.d is not None else 0.0,
            theta,
            dh_unit_to_stage_unit_factor,
        )
        world_T = world_T @ Ti
        results.append(world_T.copy())

    return results


def get_link_0_from_motion_group_prim(
    motion_group_prim: Usd.Prim,
    fallback_to_motion_group: bool = True,
) -> Usd.Prim | None:
    if not motion_group_prim or not motion_group_prim.IsValid():
        return motion_group_prim if fallback_to_motion_group else None

    if not motion_group_prim.HasAPI(wb_schema.MotionGroupAPI):
        return motion_group_prim if fallback_to_motion_group else None

    link_0_path = motion_group_prim.GetPath().AppendPath("link_0")
    link_0_prim = motion_group_prim.GetStage().GetPrimAtPath(link_0_path)

    if link_0_prim.IsValid() and link_0_prim.HasAPI(UsdPhysics.RigidBodyAPI):
        carb.log_verbose(
            f"Using link_0 at {link_0_path} for motion group pose updates."
        )
        return link_0_prim

    carb.log_verbose(
        f"link_0 at {link_0_path} is unavailable or missing RigidBodyAPI, "
        f"using motion group {motion_group_prim.GetPath()}"
    )
    return motion_group_prim if fallback_to_motion_group else None


def _get_articulation_group_joints(
    articulation_root: Usd.Prim,
) -> list[str]:
    articulation_handle = get_articulation_cache().get_articulation(
        find_physx_articulation_path(articulation_root)
    )
    return articulation_handle.articulation._articulation_view._dof_paths[0]


def _apply_isaac_robot_schema(motion_group_prim: Usd.Prim) -> None:
    carb.log_info(
        f"IsaacRobotAPI not found on {motion_group_prim.GetPath()}, "
        "auto-applying Isaac robot schema (IsaacRobotAPI / IsaacLinkAPI / IsaacJointAPI)."
    )
    robot_schema.ApplyRobotAPI(motion_group_prim)

    links_rel = motion_group_prim.GetRelationship(
        robot_schema.Relations.ROBOT_LINKS.name
    )
    joints_rel = motion_group_prim.GetRelationship(
        robot_schema.Relations.ROBOT_JOINTS.name
    )

    for prim in Usd.PrimRange(motion_group_prim):
        if prim == motion_group_prim:
            continue
        if UsdPhysics.RigidBodyAPI(prim) and "link_" in prim.GetPrimPath().pathString:
            robot_schema.ApplyLinkAPI(prim)
            links_rel.AddTarget(prim.GetPath())
        if UsdPhysics.Joint(prim) and "joint_" in prim.GetPrimPath().pathString:
            robot_schema.ApplyJointAPI(prim)
            joints_rel.AddTarget(prim.GetPath())


def get_motion_group_current_joint_positions(
    motion_group_prim: Usd.Prim,
) -> list[float] | None:
    try:
        usd_root_path = get_root_articulation_path(motion_group_prim)
        usd_root_prim = motion_group_prim.GetStage().GetPrimAtPath(usd_root_path)
        handle = get_articulation_cache().get_articulation(
            find_physx_articulation_path(usd_root_prim)
        )
        articulation = handle.articulation
        if articulation and articulation.is_valid():
            return [float(x) for x in articulation.get_joint_positions()]
    except Exception:
        pass
    return None


def get_articulation_joint_indices(motion_group: MotionGroup) -> list[int]:
    stage = stage_utils.get_current_stage()
    if stage is None:
        carb.log_warn("No stage available, using sequential indices")
        return list(range(motion_group.num_dof))

    motion_group_prim_path_obj = motion_group.configuration.prim_path
    motion_group_prim = stage.GetPrimAtPath(motion_group_prim_path_obj)

    articulation_root_path = get_root_articulation_path(motion_group_prim)
    articulation_root = stage.GetPrimAtPath(articulation_root_path)

    ordered_joints = _get_articulation_group_joints(articulation_root)

    robot_joints = robot_schema_utils.GetAllRobotJoints(
        motion_group_prim.GetStage(), motion_group_prim
    )
    if not robot_joints:
        _apply_isaac_robot_schema(motion_group_prim)
        robot_joints = robot_schema_utils.GetAllRobotJoints(
            motion_group_prim.GetStage(), motion_group_prim
        )

    joint_indices = []
    robot_joint_paths = {joint.GetPrimPath().pathString for joint in robot_joints}
    for idx, joint_path in enumerate(ordered_joints):
        if joint_path in robot_joint_paths:
            joint_indices.append(idx)

    return joint_indices
