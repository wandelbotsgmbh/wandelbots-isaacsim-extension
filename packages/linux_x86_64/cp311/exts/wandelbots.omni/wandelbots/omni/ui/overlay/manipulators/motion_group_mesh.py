import carb
import omni.ui.scene as sc
import omni.usd
import wandelbots_api_client.v2 as wb
import wandelbots_api_client.v2.models as wb_models
from wandelbots.omni.utils.math import (
    nova_pose_to_scene_matrix,
    numpy_to_scene_matrix44,
)
from pxr import Usd
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    compute_forward_kinematics_chain,
    get_motion_group_configuration_from_prim,
)
from wandelbots.omni.manipulators.utils import get_link_0_from_motion_group_prim
from wandelbots.omni.utils.prims import PrimUtils, Pose
from wandelbots.omni.utils.scene import SceneUtils
from .manipulator_mesh import create_from_collider, ManipulatorMesh
import wandelbots.omni.ui.colors as color_utils


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

        # Store path instead of prim reference to prevent stale references
        self._motion_group_path: str = motion_group_prim.GetPath().pathString
        self._motion_group_configuration: MotionGroupConfiguration = motion_group
        self._motion_group_description: wb_models.MotionGroupDescription | None = None

        self._stage_meters_per_unit = SceneUtils.get_stage_units()
        self._unit_factor = self._stage_meters_per_unit / 1000.0  # mm to stage units

        stage = motion_group_prim.GetStage()
        reference_prim = get_link_0_from_motion_group_prim(motion_group_prim)
        reference_prim_path = reference_prim.GetPath().pathString
        motion_group_pose: Pose = PrimUtils.get_prim_pose(
            reference_prim_path,
            "world",
            stage=stage,
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

    async def _fetch_motion_group_description(
        self,
    ) -> wb_models.MotionGroupDescription:
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
        return motion_group_description

    async def _fetch_motion_collision_model(
        self, motion_group_model: str
    ) -> list[dict[str, wb_models.Collider]]:
        async with (
            self._motion_group_configuration.motion_stream_configuration.get_api_client() as client
        ):
            collision_model = await wb.MotionGroupModelsApi(
                client
            ).get_motion_group_collision_model(motion_group_model=motion_group_model)
        return collision_model

    async def load_meshes(
        self, link_chain_colliders: list[dict[str, wb_models.Collider]] | None = None
    ):
        motion_group_description = await self._fetch_motion_group_description()
        if not link_chain_colliders:
            link_chain_colliders = await self._fetch_motion_collision_model(
                motion_group_description.motion_group_model
            )
        self._motion_group_description = motion_group_description
        self._joint_values = [0.0] * len(self.dh_parameters)

        carb.log_verbose(
            f"Building motion group {motion_group_description.motion_group_model}: "
            f"{len(self.dh_parameters)} joints, "
            f"{len(link_chain_colliders)} links"
        )

        for link_idx, link in enumerate(link_chain_colliders):
            for collider_id, collider in link.items():
                if link_idx > len(self._motion_group_description.dh_parameters):
                    carb.log_warn(
                        f"Link index {link_idx} exceeds joint count {len(self._motion_group_description.dh_parameters)}"
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
                fk_transforms = [
                    numpy_to_scene_matrix44(matrix)
                    for matrix in compute_forward_kinematics_chain(
                        dh_parameters=self._motion_group_description.dh_parameters,
                        dh_unit_to_stage_unit_factor=self._unit_factor,
                        joint_values_rad=self._joint_values,
                    )
                ]
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
        # Query stage fresh each time using stored path (prevents stale prim references)
        stage = omni.usd.get_context().get_stage()
        motion_group_prim = stage.GetPrimAtPath(self._motion_group_path)

        # If prim no longer exists, skip update silently
        if not motion_group_prim.IsValid():
            carb.log_verbose(
                f"Motion group prim at {self._motion_group_path} is no longer valid, skipping joint update"
            )
            return

        reference_prim = get_link_0_from_motion_group_prim(motion_group_prim)
        reference_prim_path = reference_prim.GetPath().pathString
        motion_group_pose: Pose = PrimUtils.get_prim_pose(
            reference_prim_path,
            "world",
            stage=stage,
        )
        self._motion_group_transform = nova_pose_to_scene_matrix(
            motion_group_pose.pose, self._stage_meters_per_unit
        )
        self._joint_values = joint_values
        self._update_transforms()

    def _update_transforms(self):
        """Update all mesh transforms based on current joint values."""
        fk_transforms = [
            numpy_to_scene_matrix44(matrix)
            for matrix in compute_forward_kinematics_chain(
                dh_parameters=self._motion_group_description.dh_parameters,
                dh_unit_to_stage_unit_factor=self._unit_factor,
                joint_values_rad=self._joint_values,
            )
        ]

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

    @property
    def filled(self) -> bool:
        return self._filled

    @filled.setter
    def filled(self, value: bool):
        self._filled = value
        for _, _, mesh in self._link_meshes:
            mesh.filled = value
