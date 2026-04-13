import carb
import omni.ui as ui
import omni.ui_scene as ui_scene
import omni.usd
from omni.kit.viewport.window import ViewportWindow

from wandelbots.omni.ui.overlay.manipulators import MotionGroupMesh
from wandelbots.omni.ui.overlay.overlay import ViewportOverlay

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
        self, motion_group_prim_path: str, joint_positions: list[float]
    ) -> None:
        """Render a robot ghost at the given joint positions.

        The mesh is created and loaded on first call, then cached for subsequent updates.

        Args:
            motion_group_prim_path: USD prim path of the motion group to render.
            joint_positions: Joint positions in radians.
        """
        if self._scene_view is None:
            carb.log_warn(f"Overlay '{self.name}' not attached to a viewport yet.")
            return

        stage = omni.usd.get_context().get_stage()
        if not stage:
            return

        if motion_group_prim_path not in self._meshes:
            motion_group_prim = stage.GetPrimAtPath(motion_group_prim_path)
            if not motion_group_prim.IsValid():
                carb.log_warn(
                    f"RobotOverlay: Motion group prim not valid: {motion_group_prim_path}"
                )
                return
            mesh = MotionGroupMesh(
                motion_group_prim=motion_group_prim,
                color=_OVERLAY_COLOR,
                filled=True,
            )
            with self._scene_view.scene:
                await mesh.load_meshes()
            self._meshes[motion_group_prim_path] = mesh

        mesh = self._meshes[motion_group_prim_path]
        mesh.set_joint_values(joint_positions)
        mesh.visible = True

    def hide(self, motion_group_prim_path: str) -> None:
        """Hide and discard the robot ghost for a motion group prim."""
        mesh = self._meshes.pop(motion_group_prim_path, None)
        if mesh is not None:
            mesh.visible = False

    def __del__(self):
        carb.log_verbose(f"Overlay '{self.name}' detached from viewport.")
        if self._viewport and self._scene_view:
            self._viewport.viewport_api.remove_scene_view(self._scene_view)
            self._viewport = None
            self._scene_view = None
