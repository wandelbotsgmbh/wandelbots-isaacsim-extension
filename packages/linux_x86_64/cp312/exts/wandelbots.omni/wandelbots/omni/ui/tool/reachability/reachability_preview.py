"""Viewport overlay for previewing robot collision meshes at target poses."""

from __future__ import annotations

import asyncio
from typing import Optional

import carb
import omni.ui as ui
import omni.ui.scene as sc
import omni.ui_scene as ui_scene
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models
from omni.kit.viewport.utility import get_active_viewport_window
from omni.kit.viewport.window import ViewportWindow

from wandelbots.omni.instances.models import NOVACloudInstance, NOVAInstance
from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.manipulators.utils import compute_forward_kinematics_chain
from wandelbots.omni.reachability.model_base_offsets import MODEL_BASE_OFFSETS
from wandelbots.omni.reachability.reachability_service import ReachabilityResult
from wandelbots.omni.ui.overlay.manipulators.manipulator_mesh import (
    ManipulatorMesh,
    create_from_collider,
)
from wandelbots.omni.utils.math import (
    nova_pose_to_scene_matrix,
    numpy_to_scene_matrix44,
)
from wandelbots.omni.utils.scene import SceneUtils


class ReachabilityPreview:
    """Renders ghost collision meshes in the viewport for a selected reachability result."""

    def __init__(self) -> None:
        self._viewport: ViewportWindow | None = None
        self._scene_view: ui_scene.SceneView | None = None
        self._vstack: ui.VStack | None = None
        self._meshes: list[ManipulatorMesh] = []
        self._active_model: str | None = None
        self._frame_name = "reachability_preview"
        # Cache: model_name -> (dh_parameters, collision_model)
        self._model_cache: dict[str, tuple] = {}
        # State for recreating the preview (e.g. after color change)
        self._last_result: ReachabilityResult | None = None
        self._last_mounting_pose: list[float] | None = None
        self._last_color: list[float] | None = None

    def _ensure_scene(self) -> bool:
        if self._scene_view is not None:
            return True
        viewport = get_active_viewport_window()
        if viewport is None:
            carb.log_warn("No active viewport for reachability preview")
            return False
        self._viewport = viewport
        with viewport.get_frame(self._frame_name):
            self._vstack = ui.VStack(content_clipping=False)
            with self._vstack:
                self._scene_view = ui_scene.SceneView()
                with self._scene_view.scene:
                    pass
        viewport.viewport_api.add_scene_view(self._scene_view)
        return True

    async def show_preview(
        self,
        result: ReachabilityResult,
        instance: NOVAInstance,
        mounting_pose: Optional[list[float]],
        color: list[float] | None = None,
        force: bool = False,
    ) -> None:
        if not result.joint_solutions:
            return
        if not force and self._active_model == result.model_name:
            return

        self._clear_meshes()
        self._active_model = result.model_name
        self._last_result = result
        self._last_mounting_pose = mounting_pose
        self._last_color = color

        if not self._ensure_scene():
            return

        model_name = result.model_name

        # Use cached kinematic + collision data if available
        if model_name in self._model_cache:
            dh_parameters, collision_model = self._model_cache[model_name]
        else:
            api_client = self._make_api_client(instance)
            if api_client is None:
                return
            try:
                models_api = wb_v2.MotionGroupModelsApi(api_client)

                kinematic_model: wb_v2_models.KinematicModel = await asyncio.wait_for(
                    models_api.get_motion_group_kinematic_model(
                        motion_group_model=model_name
                    ),
                    timeout=2.0,
                )
                dh_parameters = kinematic_model.dh_parameters

                collision_model: list[
                    dict[str, wb_v2_models.Collider]
                ] = await asyncio.wait_for(
                    models_api.get_motion_group_collision_model(
                        motion_group_model=model_name
                    ),
                    timeout=2.0,
                )

                self._model_cache[model_name] = (dh_parameters, collision_model)
            except Exception as exc:
                carb.log_warn(f"Failed to fetch model data for preview: {exc}")
                return
            finally:
                try:
                    await api_client.close()
                except Exception as exc:
                    carb.log_warn(f"Error closing preview API client: {exc}")

        self._build_meshes(result, dh_parameters, collision_model, mounting_pose, color)

    def _build_meshes(
        self,
        result: ReachabilityResult,
        dh_parameters,
        collision_model,
        mounting_pose: Optional[list[float]],
        color: list[float] | None = None,
    ) -> None:
        """Build the scene meshes from cached model data."""
        if not self._scene_view:
            return

        try:
            stage_units = SceneUtils.get_stage_units()
            unit_factor = stage_units / 1000.0

            # Build base transform: mounting pose + base offset from lookup.
            base_transform = sc.Matrix44()
            if mounting_pose:
                base_transform = nova_pose_to_scene_matrix(mounting_pose, stage_units)

            base_offset = MODEL_BASE_OFFSETS.get(result.model_name, 0.0)
            if base_offset != 0.0:
                offset_pose = [0, 0, base_offset * 1000.0, 0, 0, 0]
                base_transform = base_transform * nova_pose_to_scene_matrix(
                    offset_pose, stage_units
                )

            mesh_color = color if color else [0.4, 1.0, 0.4, 0.15]

            with self._scene_view.scene:
                for pose_idx, joint_values in enumerate(result.joint_solutions):
                    if not joint_values:
                        continue

                    fk_chain = [
                        numpy_to_scene_matrix44(m)
                        for m in compute_forward_kinematics_chain(
                            dh_parameters=dh_parameters,
                            dh_unit_to_stage_unit_factor=unit_factor,
                            joint_values_rad=joint_values,
                        )
                    ]

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

                            link_transform = base_transform * fk_chain[link_idx]
                            world_transform = link_transform * local_transform

                            mesh = create_from_collider(
                                collider=collider,
                                transform=world_transform,
                                color=mesh_color,
                                filled=True,
                                visible=True,
                            )
                            if mesh:
                                self._meshes.append(mesh)

            carb.log_info(
                f"Reachability preview: {len(self._meshes)} meshes for "
                f"'{result.model_name}' at {len(result.joint_solutions)} poses"
            )

        except Exception as exc:
            carb.log_warn(f"Failed to create reachability preview: {exc}")
            self._clear_meshes()

    def update_color(self, color: list[float]) -> None:
        """Update the color by rebuilding the meshes with the new color."""
        self._last_color = color
        if self._last_result is None or not self._last_result.joint_solutions:
            return
        model_name = self._last_result.model_name
        if model_name not in self._model_cache:
            return
        self._clear_meshes()
        dh_parameters, collision_model = self._model_cache[model_name]
        self._build_meshes(
            self._last_result,
            dh_parameters,
            collision_model,
            self._last_mounting_pose,
            color,
        )

    def _clear_meshes(self) -> None:
        """Remove rendered meshes without resetting cached state."""
        for mesh in self._meshes:
            mesh.visible = False
        self._meshes.clear()
        if self._scene_view is not None:
            self._scene_view.scene.clear()

    def clear(self) -> None:
        self._clear_meshes()
        self._active_model = None
        self._last_result = None
        self._last_mounting_pose = None

    def destroy(self) -> None:
        self.clear()
        self._model_cache.clear()
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
