import carb
import omni.ui as ui
import omni.ui_scene as ui_scene
import omni.usd
from omni.kit.viewport.window import ViewportWindow

from wandelbots.omni.ui.overlay.manipulators import (
    MotionGroupMesh,
    create_from_collider,
    ManipulatorMesh,
)
from wandelbots.omni.ui.overlay.overlay import ViewportOverlay
from wandelbots.omni.utils.math import (
    nova_pose_to_scene_matrix,
    numpy_to_scene_matrix44,
)
from wandelbots.omni.manipulators import compute_forward_kinematics_chain
from wandelbots.omni.utils.scene import SceneUtils

ROBOT_OVERLAY_NAME = "RobotOverlay"

# Semi-transparent red
_OVERLAY_COLOR = [1.0, 0.2, 0.2, 0.3]


class RobotOverlay(ViewportOverlay):
    """Renders a semi-transparent robot ghost at given joint positions for a motion group prim.

    Call ``show()`` to render and ``hide()`` to remove, keyed by motion group prim path.
    """

    def __init__(self, name: str):
        self.name = name
        self._viewport: ViewportWindow | None = None
        self._scene_view: ui_scene.SceneView | None = None
        self._meshes: dict[str, MotionGroupMesh] = {}
        self._mesh_colors: dict[str, list[float]] = {}
        self._tool_meshes: dict[str, list[ManipulatorMesh]] = {}

    def attach_to_viewport(self, viewport: ViewportWindow):
        self._viewport = viewport
        if not self._viewport:
            carb.log_warn(
                f"Overlay '{self.name}' could not be attached to viewport: No viewport provided."
            )
            return
        with self._viewport.get_frame(self.name):
            view_vstack = ui.VStack(content_clipping=False)
            with view_vstack:
                self._scene_view = ui_scene.SceneView()
                with self._scene_view.scene:
                    pass
        self._viewport.viewport_api.add_scene_view(self._scene_view)

    async def show(
        self,
        motion_group_prim_path: str,
        joint_positions: list[float],
        color: list[float] | None = None,
        tool_colliders: dict | None = None,
        filled: bool = True,
    ) -> None:
        """Render a robot ghost at the given joint positions.

        The mesh is created and loaded on first call, then cached for subsequent updates.

        Args:
            motion_group_prim_path: USD prim path of the motion group to render.
            joint_positions: Joint positions in radians.
            color: Optional RGBA color list. Defaults to _OVERLAY_COLOR.
            tool_colliders: Optional dict of tool collider ID → Collider objects to render at flange.
            filled: Whether to render as solid (True) or wireframe (False).
        """
        if self._scene_view is None:
            carb.log_warn(f"Overlay '{self.name}' not attached to a viewport yet.")
            return

        stage = omni.usd.get_context().get_stage()
        if not stage:
            return

        mesh_color = color if color else _OVERLAY_COLOR

        # Recreate mesh if color or filled mode changed
        if motion_group_prim_path in self._meshes:
            existing_mesh = self._meshes[motion_group_prim_path]
            if (
                self._mesh_colors.get(motion_group_prim_path) != mesh_color
                or existing_mesh.filled != filled
            ):
                old_mesh = self._meshes.pop(motion_group_prim_path)
                old_mesh.visible = False
                del self._mesh_colors[motion_group_prim_path]

        if motion_group_prim_path not in self._meshes:
            motion_group_prim = stage.GetPrimAtPath(motion_group_prim_path)
            if not motion_group_prim.IsValid():
                carb.log_warn(
                    f"RobotOverlay: Motion group prim not valid: {motion_group_prim_path}"
                )
                return
            mesh = MotionGroupMesh(
                motion_group_prim=motion_group_prim,
                color=mesh_color,
                filled=filled,
            )
            with self._scene_view.scene:
                await mesh.load_meshes()
            self._meshes[motion_group_prim_path] = mesh
            self._mesh_colors[motion_group_prim_path] = mesh_color

        mesh = self._meshes[motion_group_prim_path]
        mesh.set_joint_values(joint_positions)
        mesh.visible = True

        # Render tool colliders at the flange (last FK link)
        self._update_tool_colliders(
            motion_group_prim_path,
            mesh,
            joint_positions,
            tool_colliders,
            mesh_color,
            filled,
        )

    def _update_tool_colliders(
        self,
        motion_group_prim_path: str,
        mesh: MotionGroupMesh,
        joint_positions: list[float],
        tool_colliders: dict | None,
        color: list[float],
        filled: bool = True,
    ) -> None:
        # Clear old tool meshes
        old_tool_meshes = self._tool_meshes.pop(motion_group_prim_path, [])
        for tool_mesh in old_tool_meshes:
            tool_mesh.visible = False

        if not tool_colliders or not mesh.motion_group_description:
            return
        if self._scene_view is None:
            return

        stage_units = SceneUtils.get_stage_units()
        unit_factor = stage_units / 1000.0
        dh_parameters = mesh.motion_group_description.dh_parameters

        fk_chain = [
            numpy_to_scene_matrix44(m)
            for m in compute_forward_kinematics_chain(
                dh_parameters=dh_parameters,
                dh_unit_to_stage_unit_factor=unit_factor,
                joint_values_rad=joint_positions,
            )
        ]

        # Flange is the last transform in the FK chain
        base_transform = mesh.motion_group_transform
        flange_transform = base_transform * fk_chain[-1]

        new_tool_meshes: list[ManipulatorMesh] = []
        with self._scene_view.scene:
            for collider_id, collider in tool_colliders.items():
                collider_pose = list(collider.pose.position) + list(
                    collider.pose.orientation
                    if collider.pose.orientation
                    else [0, 0, 0]
                )
                local_transform = nova_pose_to_scene_matrix(
                    collider_pose, stage_units
                ) * ui_scene.Matrix44.get_scale_matrix(
                    unit_factor, unit_factor, unit_factor
                )
                world_transform = flange_transform * local_transform
                tool_mesh = create_from_collider(
                    collider=collider,
                    transform=world_transform,
                    color=color,
                    filled=filled,
                    visible=True,
                )
                if tool_mesh:
                    new_tool_meshes.append(tool_mesh)

        self._tool_meshes[motion_group_prim_path] = new_tool_meshes

    def hide(self, motion_group_prim_path: str) -> None:
        """Hide and discard the robot ghost for a motion group prim."""
        mesh = self._meshes.pop(motion_group_prim_path, None)
        self._mesh_colors.pop(motion_group_prim_path, None)
        if mesh is not None:
            mesh.visible = False
        # Clean up tool meshes
        tool_meshes = self._tool_meshes.pop(motion_group_prim_path, [])
        for tool_mesh in tool_meshes:
            tool_mesh.visible = False

    def __del__(self):
        carb.log_verbose(f"Overlay '{self.name}' detached from viewport.")
        if self._viewport and self._scene_view:
            self._viewport.viewport_api.remove_scene_view(self._scene_view)
            self._viewport = None
            self._scene_view = None
