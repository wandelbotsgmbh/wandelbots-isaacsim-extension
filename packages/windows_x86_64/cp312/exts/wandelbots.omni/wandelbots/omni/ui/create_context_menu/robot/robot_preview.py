"""Viewport overlay that shows a translucent collision-mesh preview of the
currently selected robot model *before* the user clicks Confirm."""

from __future__ import annotations

import asyncio

import carb
import omni.ui as ui
import omni.ui.scene as sc
import omni.ui_scene as ui_scene
import omni.usd
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models
from omni.kit.async_engine import run_coroutine
from omni.kit.viewport.utility import get_active_viewport_window
from omni.kit.viewport.window import ViewportWindow
from pxr import Gf

from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVACloudInstance, NOVAInstance
from wandelbots.omni.manipulators.utils import compute_forward_kinematics_chain
from .model_base_offsets import MODEL_BASE_OFFSETS
from wandelbots.omni.ui.overlay.manipulators.manipulator_mesh import (
    ManipulatorMesh,
    create_from_collider,
)
from wandelbots.omni.utils.math import (
    nova_pose_to_scene_matrix,
    numpy_to_scene_matrix44,
)
from wandelbots.omni.utils.scene import SceneUtils

_DEFAULT_PREVIEW_COLOR: list[float] = [0.6, 0.4, 1.0, 0.12]
_FRAME_NAME = "robot_spawn_preview"


class RobotPreview:
    """Renders transparent collision meshes in the viewport for a robot model
    at its zero-joint-angle pose so the user can see what the robot looks like
    before confirming the spawn."""

    def __init__(self) -> None:
        self._viewport: ViewportWindow | None = None
        self._scene_view: ui_scene.SceneView | None = None
        self._vstack: ui.VStack | None = None
        self._meshes: list[ManipulatorMesh] = []
        self._active_model: str | None = None
        self._active_prim_path: str | None = None
        self._preview_task: asyncio.Task | None = None
        self._color: list[float] = list(_DEFAULT_PREVIEW_COLOR)

    @property
    def color(self) -> list[float]:
        return self._color

    @color.setter
    def color(self, value: list[float]) -> None:
        self._color = list(value)
        for mesh in self._meshes:
            mesh.color = self._color

    def _ensure_scene(self) -> bool:
        if self._scene_view is not None:
            return True
        viewport = get_active_viewport_window()
        if viewport is None:
            carb.log_warn("No active viewport for robot preview")
            return False
        self._viewport = viewport
        with viewport.get_frame(_FRAME_NAME):
            self._vstack = ui.VStack(content_clipping=False)
            with self._vstack:
                self._scene_view = ui_scene.SceneView()
                with self._scene_view.scene:
                    pass
        viewport.viewport_api.add_scene_view(self._scene_view)
        return True

    def request_preview(
        self,
        model_name: str | None,
        instance: NOVAInstance | None,
        prim_path: str | None = None,
    ) -> None:
        if model_name == self._active_model and prim_path == self._active_prim_path:
            return

        if self._preview_task is not None:
            self._preview_task.cancel()
            self._preview_task = None

        if model_name is None or instance is None:
            self.clear()
            return

        self._preview_task = run_coroutine(
            self._show_preview(model_name, instance, prim_path)
        )

    async def _show_preview(
        self,
        model_name: str,
        instance: NOVAInstance,
        prim_path: str | None = None,
    ) -> None:
        self.clear()
        self._active_model = model_name
        self._active_prim_path = prim_path

        if not self._ensure_scene():
            return

        api_client = self._make_api_client(instance)
        if api_client is None:
            return

        try:
            models_api = wb_v2.MotionGroupModelsApi(api_client)

            kinematic_model: wb_v2_models.KinematicModel = (
                await models_api.get_motion_group_kinematic_model(
                    motion_group_model=model_name
                )
            )
            dh_parameters = kinematic_model.dh_parameters

            collision_model: list[
                dict[str, wb_v2_models.Collider]
            ] = await models_api.get_motion_group_collision_model(
                motion_group_model=model_name
            )

            stage_units = SceneUtils.get_stage_units()
            unit_factor = stage_units / 1000.0

            base_transform = self._compute_base_transform(
                prim_path, model_name, stage_units
            )

            zero_joints = [0.0] * len(dh_parameters)
            fk_chain = [
                numpy_to_scene_matrix44(m)
                for m in compute_forward_kinematics_chain(
                    dh_parameters=dh_parameters,
                    dh_unit_to_stage_unit_factor=unit_factor,
                    joint_values_rad=zero_joints,
                )
            ]

            with self._scene_view.scene:
                for link_idx, link in enumerate(collision_model):
                    if link_idx >= len(fk_chain):
                        break
                    for _collider_id, collider in link.items():
                        mesh_pose = list(collider.pose.position) + list(
                            collider.pose.orientation
                            if collider.pose.orientation
                            else [0, 0, 0]
                        )
                        local_transform = nova_pose_to_scene_matrix(
                            mesh_pose, stage_units
                        ) * sc.Matrix44.get_scale_matrix(
                            unit_factor, unit_factor, unit_factor
                        )

                        world_transform = (
                            base_transform * fk_chain[link_idx] * local_transform
                        )

                        mesh = create_from_collider(
                            collider=collider,
                            transform=world_transform,
                            color=self._color,
                            filled=True,
                            visible=True,
                        )
                        if mesh:
                            self._meshes.append(mesh)

            carb.log_info(
                f"Robot preview: {len(self._meshes)} meshes for '{model_name}'"
            )

        except asyncio.CancelledError:
            self.clear()
        except Exception as exc:
            carb.log_warn(f"Failed to create robot preview: {exc}")
            self.clear()
        finally:
            self._preview_task = None
            try:
                await api_client.close()
            except Exception:
                pass

    def clear(self) -> None:
        for mesh in self._meshes:
            mesh.visible = False
        self._meshes.clear()
        self._active_model = None
        self._active_prim_path = None

        if self._scene_view is not None:
            self._scene_view.scene.clear()

    def destroy(self) -> None:
        if self._preview_task is not None:
            self._preview_task.cancel()
            self._preview_task = None

        self.clear()

        if self._viewport is not None and self._scene_view is not None:
            try:
                self._viewport.viewport_api.remove_scene_view(self._scene_view)
            except Exception as exc:
                carb.log_warn(f"Error removing preview scene view: {exc}")

        self._scene_view = None
        self._vstack = None
        self._viewport = None

    def _make_api_client(self, instance: NOVAInstance) -> wb_v2.ApiClient | None:
        if isinstance(instance, NOVACloudInstance):
            token = get_instances_api().get_auth_token_from_host(instance.host)
            return instance.create_api_client(token=token)
        return instance.create_api_client()

    def _compute_base_transform(
        self,
        prim_path: str | None,
        model_name: str,
        stage_units: float,
    ) -> sc.Matrix44:
        base_matrix = sc.Matrix44()

        if prim_path:
            stage = omni.usd.get_context().get_stage()
            if stage:
                prim = stage.GetPrimAtPath(prim_path)
                if prim.IsValid():
                    world_mtx: Gf.Matrix4d = omni.usd.get_world_transform_matrix(prim)
                    flat = []
                    for col in range(4):
                        for row in range(4):
                            flat.append(world_mtx[col][row])
                    base_matrix = sc.Matrix44(*flat)

        base_offset = MODEL_BASE_OFFSETS.get(model_name, 0.0)
        if base_offset != 0.0:
            offset_pose = [0, 0, base_offset * 1000.0, 0, 0, 0]
            base_matrix = base_matrix * nova_pose_to_scene_matrix(
                offset_pose, stage_units
            )

        return base_matrix
