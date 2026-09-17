"""Viewport overlay for previewing robot collision meshes at target poses."""

from __future__ import annotations

import asyncio
from typing import Optional

import carb
import omni.ui as ui
import omni.ui.scene as sc
import omni.ui_scene as ui_scene
import pydantic
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models
from omni.kit.viewport.utility import get_active_viewport_window
from omni.kit.viewport.window import ViewportWindow
from pxr import Tf, Usd

from wandelbots.omni.instances.models import NOVAInstance
from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.manipulators.motion_group import (
    get_motion_group_configuration_from_prim,
)
from wandelbots.omni.manipulators.model_base_offsets import MODEL_BASE_OFFSETS
from wandelbots.omni.manipulators.utils import compute_forward_kinematics_chain
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


class _ToolMeshManipulator(sc.Manipulator):
    """Lightweight triangle-soup renderer for the attached tool's mesh.

    Unlike ManipulatorMesh it does not merge coplanar faces: that merge is a
    BFS that hangs the app on a full-detail tool mesh, so the triangles are
    drawn as they are, in one sc.PolygonMesh call.
    """

    def __init__(
        self,
        transform: sc.Transform,
        vertices: list[tuple[float, float, float]],
        color: list[float],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._transform = transform
        self._vertices = vertices
        self._color = color

    def on_build(self):
        if not self._vertices:
            return
        triangle_count = len(self._vertices) // 3
        with sc.Transform(transform=self._transform):
            sc.PolygonMesh(
                positions=self._vertices,
                colors=[self._color] * len(self._vertices),
                vertex_counts=[3] * triangle_count,
                vertex_indices=list(range(triangle_count * 3)),
            )


class ReachabilityPreview:
    """Renders ghost collision meshes in the viewport for a selected reachability result."""

    def __init__(self, frame_name: str = "reachability_preview") -> None:
        self._viewport: ViewportWindow | None = None
        self._scene_view: ui_scene.SceneView | None = None
        self._vstack: ui.VStack | None = None
        self._meshes_per_pose: list[list[ManipulatorMesh]] = []
        self._active_model: str | None = None
        self._frame_name = frame_name
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
        instance: NOVAInstance | None = None,
        mounting_pose: Optional[list[float]] = None,
        color: list[float] | None = None,
        force: bool = False,
        motion_group_prim: Usd.Prim | None = None,
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
            api_client = self._resolve_api_client(instance, motion_group_prim)
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
            unit_factor = SceneUtils.millimeters_to_stage_value(1.0)

            base_offset = MODEL_BASE_OFFSETS.get(result.model_name, 0.0)
            offset_transform = None
            if base_offset != 0.0:
                offset_pose = [0, 0, base_offset * 1000.0, 0, 0, 0]
                offset_transform = nova_pose_to_scene_matrix(offset_pose, stage_units)

            def base_transform_for(pose_index: int) -> sc.Matrix44:
                """Base transform: per-pose mounting (multi-base analysis)
                falls back to the single mounting pose, plus the model's
                kinematic base offset."""
                pose = mounting_pose
                per_pose = result.per_pose_mounting_poses
                if per_pose and pose_index < len(per_pose) and per_pose[pose_index]:
                    pose = per_pose[pose_index]
                transform = (
                    nova_pose_to_scene_matrix(pose, stage_units)
                    if pose
                    else sc.Matrix44()
                )
                if offset_transform is not None:
                    transform = transform * offset_transform
                return transform

            mesh_color = color if color else [0.4, 1.0, 0.4, 0.15]

            self._meshes_per_pose = []
            with self._scene_view.scene:
                for pose_index, joint_values in enumerate(result.joint_solutions):
                    pose_meshes = self._build_single_pose_meshes(
                        joint_values,
                        dh_parameters,
                        collision_model,
                        base_transform_for(pose_index),
                        stage_units,
                        unit_factor,
                        mesh_color,
                        result.tool_mesh_vertices,
                    )
                    self._meshes_per_pose.append(pose_meshes)

            total = sum(len(p) for p in self._meshes_per_pose)
            carb.log_info(
                f"Reachability preview: {total} meshes for "
                f"'{result.model_name}' at {len(result.joint_solutions)} poses"
            )

        except Exception as exc:
            carb.log_warn(f"Failed to create reachability preview: {exc}")
            self._clear_meshes()

    def _build_single_pose_meshes(
        self,
        joint_values: list[float],
        dh_parameters,
        collision_model,
        base_transform,
        stage_units: float,
        unit_factor: float,
        mesh_color: list[float],
        tool_mesh_vertices: Optional[list[tuple[float, float, float]]] = None,
    ) -> list[ManipulatorMesh]:
        """Build and return meshes for one pose. Must be called within a scene context."""
        if not joint_values:
            return []
        pose_meshes: list[ManipulatorMesh] = []
        fk_chain = [
            numpy_to_scene_matrix44(m)
            for m in compute_forward_kinematics_chain(
                dh_parameters=dh_parameters,
                dh_unit_to_stage_unit_factor=unit_factor,
                joint_values_rad=joint_values,
            )
        ]
        for link_index, link in enumerate(collision_model):
            if link_index >= len(fk_chain):
                break
            for _collider_id, collider in link.items():
                mesh_pose = list(collider.pose.position) + list(
                    collider.pose.orientation
                    if collider.pose.orientation
                    else [0, 0, 0]
                )
                local_transform = nova_pose_to_scene_matrix(
                    mesh_pose, stage_units
                ) * sc.Matrix44.get_scale_matrix(unit_factor, unit_factor, unit_factor)
                link_transform = base_transform * fk_chain[link_index]
                world_transform = link_transform * local_transform
                mesh = create_from_collider(
                    collider=collider,
                    transform=world_transform,
                    color=mesh_color,
                    filled=True,
                    visible=True,
                )
                if mesh:
                    pose_meshes.append(mesh)

        if tool_mesh_vertices:
            # fk_chain[-1] is the flange frame; the tool mesh (meters,
            # relative to its own root - see AttachToolWidget) anchors
            # there directly, needing only a unit scale.
            flange_transform = base_transform * fk_chain[-1]
            scale = 1.0 / stage_units if stage_units else 1.0
            scale_matrix = sc.Matrix44.get_scale_matrix(scale, scale, scale)
            mesh = _ToolMeshManipulator(
                transform=flange_transform * scale_matrix,
                vertices=tool_mesh_vertices,
                color=mesh_color,
                visible=True,
            )
            pose_meshes.append(mesh)
        return pose_meshes

    def set_pose_visible(self, pose_index: int, visible: bool) -> None:
        """Show or hide all meshes for a single target pose."""
        if pose_index < len(self._meshes_per_pose):
            for mesh in self._meshes_per_pose[pose_index]:
                mesh.visible = visible

    def update_pose_joint_config(
        self, pose_index: int, joint_values: list[float]
    ) -> None:
        """Swap the rendered joint configuration for a single pose."""
        if not self._scene_view or self._last_result is None:
            return
        model_name = self._last_result.model_name
        if model_name not in self._model_cache:
            return
        # Ensure the per-pose list is long enough
        while len(self._meshes_per_pose) <= pose_index:
            self._meshes_per_pose.append([])
        # Clear old meshes for this pose
        for mesh in self._meshes_per_pose[pose_index]:
            mesh.visible = False
        self._meshes_per_pose[pose_index].clear()
        if not joint_values:
            return
        stage_units = SceneUtils.get_stage_units()
        unit_factor = SceneUtils.millimeters_to_stage_value(1.0)
        base_transform = sc.Matrix44()
        if self._last_mounting_pose:
            base_transform = nova_pose_to_scene_matrix(
                self._last_mounting_pose, stage_units
            )
        base_offset = MODEL_BASE_OFFSETS.get(model_name, 0.0)
        if base_offset != 0.0:
            offset_pose = [0, 0, base_offset * 1000.0, 0, 0, 0]
            base_transform = base_transform * nova_pose_to_scene_matrix(
                offset_pose, stage_units
            )
        mesh_color = self._last_color if self._last_color else [0.4, 1.0, 0.4, 0.15]
        dh_parameters, collision_model = self._model_cache[model_name]
        with self._scene_view.scene:
            self._meshes_per_pose[pose_index] = self._build_single_pose_meshes(
                joint_values,
                dh_parameters,
                collision_model,
                base_transform,
                stage_units,
                unit_factor,
                mesh_color,
                self._last_result.tool_mesh_vertices if self._last_result else None,
            )

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
        for pose_meshes in self._meshes_per_pose:
            for mesh in pose_meshes:
                mesh.visible = False
        self._meshes_per_pose.clear()
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

    def _resolve_api_client(
        self,
        instance: NOVAInstance | None,
        motion_group_prim: Usd.Prim | None,
    ) -> wb_v2.ApiClient | None:
        """Create an API client from an instance or motion group prim."""
        if instance is not None:
            return get_instances_api().create_api_client_for_instance(instance)
        if motion_group_prim is None:
            return None
        try:
            config = get_motion_group_configuration_from_prim(motion_group_prim)
            if config is None:
                return None
            return config.motion_stream_configuration.get_api_client()
        except (pydantic.ValidationError, Tf.ErrorException) as exc:
            carb.log_warn(f"Failed to create API client from prim: {exc}")
            return None
