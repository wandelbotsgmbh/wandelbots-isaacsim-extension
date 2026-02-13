"""Motion group collision mesh generation for link chain visualization."""

import math
import carb
import omni.ui.scene as sc
import wandelbots_api_client.v2 as wb
import wandelbots_api_client.v2.models as wb_models
from wandelbots.omni.utils.math import nova_pose_to_scene_matrix
from pxr import Usd
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    get_motion_group_configuration_from_prim,
)
from wandelbots.omni.utils.prims import PrimUtils, Pose
from wandelbots.omni.utils.scene import SceneUtils
from .manipulator_mesh import create_from_collider, ManipulatorMesh
import wandelbots.omni.ui.colors as color_utils


def _dh_transform(
    a: float, alpha: float, d: float, theta: float, unit_factor: float
) -> sc.Matrix44:
    """Compute DH transformation matrix.

    Standard DH convention: T = Rz(theta) * Tz(d) * Tx(a) * Rx(alpha)

    Args:
        a: Link length (mm)
        alpha: Link twist (radians)
        d: Link offset (mm)
        theta: Joint angle (radians)
        unit_factor: Conversion factor from mm to stage units

    Returns:
        sc.Matrix44 transformation matrix in column-major order.
    """
    ca = math.cos(alpha)
    sa = math.sin(alpha)
    ct = math.cos(theta)
    st = math.sin(theta)

    # Apply unit conversion to distance parameters
    a_scaled = a * unit_factor
    d_scaled = d * unit_factor

    # Matrix is column-major: [col0, col1, col2, col3]
    return sc.Matrix44(
        ct,
        st,
        0.0,
        0.0,  # column 0 (X-axis)
        -st * ca,
        ct * ca,
        sa,
        0.0,  # column 1 (Y-axis)
        st * sa,
        -ct * sa,
        ca,
        0.0,  # column 2 (Z-axis)
        a_scaled * ct,
        a_scaled * st,
        d_scaled,
        1.0,  # column 3 (translation)
    )


class MotionGroupMesh:
    """Manages collision meshes for a motion group's link chain.

    This class creates and updates collision meshes based on joint values,
    allowing real-time visualization of the robot's collision geometry.
    """

    def __init__(
        self,
        motion_group_prim: Usd.Prim,
        color: color_utils.ColorRGBA = [0.4, 1.0, 0.4, 0.15],
        filled: bool = True,
    ):
        motion_group = get_motion_group_configuration_from_prim(motion_group_prim)
        if motion_group is None:
            raise ValueError(
                f"Prim {motion_group_prim.GetPath()} is not a valid motion group"
            )

        self._motion_group_prim = motion_group_prim
        self._motion_group_configuration: MotionGroupConfiguration = motion_group
        self._motion_group_description: wb_models.MotionGroupDescription | None = None

        self._stage_meters_per_unit = SceneUtils.get_stage_units()
        self._unit_factor = self._stage_meters_per_unit / 1000.0  # mm to stage units

        motion_group_pose: Pose = PrimUtils.get_prim_pose(
            self._motion_group_prim.GetPrimPath().pathString,
            "world",
            stage=self._motion_group_prim.GetStage(),
        )
        self._motion_group_transform = nova_pose_to_scene_matrix(
            motion_group_pose.pose, self._stage_meters_per_unit
        )

        self._color = color
        self._filled = filled

        # Store joint values (initialized to zero - DH theta offsets are added during FK)
        self._joint_values: list[float] = []

        # Store meshes and their link indices for updates
        # (link_idx, local_transform, mesh)
        self._link_meshes: list[tuple[int, sc.Matrix44, ManipulatorMesh]] = []

    def _compute_forward_kinematics(
        self, joint_values: list[float]
    ) -> list[sc.Matrix44]:
        world_T = sc.Matrix44()
        results = [world_T]

        for i, dh_param in enumerate(self.dh_parameters):
            # Joint value + DH theta offset
            theta = joint_values[i] if i < len(joint_values) else 0.0
            theta = -theta if dh_param.reverse_rotation_direction else theta
            if dh_param.theta is not None:
                theta += dh_param.theta

            Ti = _dh_transform(
                dh_param.a if dh_param.a is not None else 0.0,
                dh_param.alpha if dh_param.alpha is not None else 0.0,
                dh_param.d if dh_param.d is not None else 0.0,
                theta,
                self._unit_factor,
            )
            world_T = world_T * Ti
            results.append(world_T)

        return results

    async def _fetch_motion_group_data(
        self,
    ) -> tuple[wb_models.MotionGroupDescription, list[dict[str, wb_models.Collider]]]:
        async with (
            self._motion_group_configuration.motion_stream_configuration.get_api_client() as client
        ):
            motion_group_description = await wb.MotionGroupApi(
                client
            ).get_motion_group_description(
                cell=self._motion_group_configuration.motion_stream_configuration.cell,
                controller=self._motion_group_configuration.motion_stream_configuration.controller,
                motion_group=self._motion_group_configuration.motion_stream_configuration.motion_group,
            )
            collision_model = await wb.MotionGroupModelsApi(
                client
            ).get_motion_group_collision_model(
                motion_group_model=motion_group_description.motion_group_model
            )
        return motion_group_description, collision_model

    async def load_meshes(self):
        (
            motion_group_description,
            collision_model,
        ) = await self._fetch_motion_group_data()
        self._motion_group_description = motion_group_description
        self._joint_values = [0.0] * len(self.dh_parameters)

        carb.log_verbose(
            f"Building motion group {motion_group_description.motion_group_model}: "
            f"{len(self.dh_parameters)} joints, "
            f"{len(collision_model)} links"
        )

        for link_idx, link in enumerate(collision_model):
            for collider_id, collider in link.items():
                if link_idx > len(self.dh_parameters):
                    carb.log_warn(
                        f"Link index {link_idx} exceeds joint count {len(self.dh_parameters)}"
                    )
                    continue

                # Mesh pose: position in mm, orientation as rotation vector
                mesh_pose = list(collider.pose.position) + list(
                    collider.pose.orientation
                    if collider.pose.orientation
                    else [0, 0, 0]
                )

                # Mesh local transform + scale for vertices (in mm)
                local_transform = nova_pose_to_scene_matrix(
                    mesh_pose, self._stage_meters_per_unit
                ) * sc.Matrix44.get_scale_matrix(
                    self._unit_factor, self._unit_factor, self._unit_factor
                )

                # Compute initial world transform
                fk_transforms = self._compute_forward_kinematics(self._joint_values)
                link_transform = self._motion_group_transform * fk_transforms[link_idx]
                world_transform = link_transform * local_transform

                mesh = create_from_collider(
                    collider=collider,
                    transform=world_transform,
                    color=self._color,
                    filled=self._filled,
                    visible=False,
                )

                if mesh:
                    self._link_meshes.append((link_idx, local_transform, mesh))
                else:
                    carb.log_warn(
                        f"Collider {collider_id} with shape type "
                        f"'{collider.shape.actual_instance.shape_type}' is not supported"
                    )

    def set_joint_values(self, joint_values: list[float]):
        """Update the joint values and refresh all mesh transforms.

        Args:
            joint_values: List of joint angles in radians
        """
        self._joint_values = joint_values
        self._update_transforms()

    def _update_transforms(self):
        """Update all mesh transforms based on current joint values."""
        fk_transforms = self._compute_forward_kinematics(self._joint_values)

        for link_idx, local_transform, mesh in self._link_meshes:
            if link_idx < len(fk_transforms):
                link_transform = self._motion_group_transform * fk_transforms[link_idx]
                world_transform = link_transform * local_transform
                mesh.set_transform(world_transform)

    def set_motion_group_transform(self, transform: sc.Matrix44):
        """Update the base transform of the motion group.

        Args:
            transform: New base transform for the motion group
        """
        self._motion_group_transform = transform
        self._update_transforms()

    @property
    def joint_values(self) -> list[float]:
        return self._joint_values.copy()

    @property
    def joint_count(self) -> int:
        return len(self.dh_parameters)

    @property
    def meshes(self) -> list[ManipulatorMesh]:
        return [mesh for _, _, mesh in self._link_meshes]

    @property
    def visible(self) -> bool:
        return all(mesh.visible for mesh in self.meshes)

    @visible.setter
    def visible(self, value: bool):
        for mesh in self.meshes:
            mesh.visible = value

    @property
    def motion_group_configuration(self) -> MotionGroupConfiguration:
        return self._motion_group_configuration

    @property
    def motion_group_description(self) -> wb_models.MotionGroupDescription:
        """Get the motion group description."""
        return self._motion_group_description

    @property
    def dh_parameters(self) -> list[wb_models.DHParameter]:
        """Get the DH parameters of the motion group."""
        return (
            self._motion_group_description.dh_parameters
            if self._motion_group_description
            else []
        )

    @property
    def color(self) -> color_utils.ColorRGBA:
        return self._color

    @color.setter
    def color(self, value: color_utils.ColorRGBA):
        self._color = value
        for _, _, mesh in self._link_meshes:
            mesh.color = value
