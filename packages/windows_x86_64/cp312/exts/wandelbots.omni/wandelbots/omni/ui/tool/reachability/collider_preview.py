"""Viewport overlay for previewing the collider sweep sphere and found colliders."""

from __future__ import annotations

import math

import carb
import omni.ui as ui
import omni.ui.scene as sc
import omni.ui_scene as ui_scene
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models
from omni.kit.viewport.utility import get_active_viewport_window
from omni.kit.viewport.window import ViewportWindow

from wandelbots.omni.ui.overlay.manipulators.manipulator_mesh import (
    ManipulatorMesh,
    create_from_collider,
)
from wandelbots.omni.utils.math import nova_pose_to_scene_matrix
from wandelbots.omni.utils.scene import SceneUtils


def _generate_sphere_wireframe(
    center: tuple[float, float, float],
    radius: float,
    segments: int = 32,
    rings: int = 16,
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """Generate line segments for a wireframe sphere (latitude + longitude arcs)."""
    lines: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    cx, cy, cz = center

    for i in range(1, rings):
        theta = math.pi * i / rings
        r = radius * math.sin(theta)
        z = cz + radius * math.cos(theta)
        for j in range(segments):
            phi1 = 2 * math.pi * j / segments
            phi2 = 2 * math.pi * ((j + 1) % segments) / segments
            p1 = (cx + r * math.cos(phi1), cy + r * math.sin(phi1), z)
            p2 = (cx + r * math.cos(phi2), cy + r * math.sin(phi2), z)
            lines.append((p1, p2))

    half_seg = max(segments // 2, 4)
    for j in range(half_seg):
        phi = 2 * math.pi * j / half_seg
        for i in range(rings):
            theta1 = math.pi * i / rings
            theta2 = math.pi * (i + 1) / rings
            p1 = (
                cx + radius * math.sin(theta1) * math.cos(phi),
                cy + radius * math.sin(theta1) * math.sin(phi),
                cz + radius * math.cos(theta1),
            )
            p2 = (
                cx + radius * math.sin(theta2) * math.cos(phi),
                cy + radius * math.sin(theta2) * math.sin(phi),
                cz + radius * math.cos(theta2),
            )
            lines.append((p1, p2))

    return lines


class ColliderPreview:
    """Renders a wireframe sweep sphere and found collider meshes in the viewport."""

    def __init__(self) -> None:
        self._viewport: ViewportWindow | None = None
        self._scene_view: ui_scene.SceneView | None = None
        self._vstack: ui.VStack | None = None
        self._meshes: list[ManipulatorMesh] = []
        self._frame_name = "collider_sweep_preview"

    def _ensure_scene(self) -> bool:
        if self._scene_view is not None:
            return True
        viewport = get_active_viewport_window()
        if viewport is None:
            carb.log_warn("No active viewport for collider preview")
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

    def show(
        self,
        center_world_mm: tuple[float, float, float],
        radius_m: float,
        colliders: dict[str, wb_v2_models.Collider],
        collider_color: list[float] | None = None,
    ) -> None:
        """Draw the sweep sphere and collider meshes.

        Args:
            center_world_mm: Sphere center in workspace units (mm).
            radius_m: Sweep radius in meters.
            colliders: NOVA-format colliders found by the sweep.
            collider_color: RGBA color for collider meshes. Defaults to orange.
        """
        self.clear()
        if not self._ensure_scene():
            return

        stage_units = SceneUtils.get_stage_units()
        factor = stage_units / 1000.0
        center = (
            center_world_mm[0] * factor,
            center_world_mm[1] * factor,
            center_world_mm[2] * factor,
        )
        radius_stage = radius_m * stage_units

        sphere_color = [0.3, 0.7, 1.0, 0.4]
        fill_color = collider_color if collider_color else [1.0, 0.5, 0.2, 0.3]

        with self._scene_view.scene:
            lines = _generate_sphere_wireframe(
                center, radius_stage, segments=48, rings=24
            )
            for p1, p2 in lines:
                sc.Line(p1, p2, color=sphere_color)

            for _name, collider in colliders.items():
                pose_vals = list(collider.pose.position) + list(
                    collider.pose.orientation
                    if collider.pose.orientation
                    else [0, 0, 0]
                )
                transform = nova_pose_to_scene_matrix(pose_vals, stage_units)
                scale = sc.Matrix44.get_scale_matrix(factor, factor, factor)
                world_transform = transform * scale

                if isinstance(collider.shape.actual_instance, wb_v2.models.Plane):
                    self._draw_plane(collider, center, radius_stage, fill_color)
                    continue

                mesh = create_from_collider(
                    collider=collider,
                    transform=world_transform,
                    color=fill_color,
                    filled=True,
                    visible=True,
                )
                if mesh:
                    self._meshes.append(mesh)

        carb.log_info(
            f"Collider preview: sphere r={radius_m}m, "
            f"{len(self._meshes)} collider meshes"
        )

    def _draw_plane(
        self,
        collider: wb_v2_models.Collider,
        center: tuple[float, float, float],
        radius_stage: float,
        color: list[float],
    ) -> None:
        """Render a plane collider as a flat quad clamped to the sweep sphere diameter."""
        plane_z = collider.pose.position[2] * (SceneUtils.get_stage_units() / 1000.0)
        half = radius_stage * 2.0
        cx, cy = center[0], center[1]
        corners = [
            (cx - half, cy - half, plane_z),
            (cx + half, cy - half, plane_z),
            (cx + half, cy + half, plane_z),
            (cx - half, cy + half, plane_z),
        ]
        sc.PolygonMesh(
            positions=corners,
            colors=[color] * 4,
            vertex_counts=[4],
            vertex_indices=[0, 1, 2, 3],
        )
        for i in range(4):
            sc.Line(corners[i], corners[(i + 1) % 4], color=color)

    def clear(self) -> None:
        for mesh in self._meshes:
            mesh.visible = False
        self._meshes.clear()
        if self._scene_view is not None:
            self._scene_view.scene.clear()

    def destroy(self) -> None:
        self.clear()
        if self._viewport is not None and self._scene_view is not None:
            try:
                self._viewport.viewport_api.remove_scene_view(self._scene_view)
            except Exception as exc:
                carb.log_warn(f"Error removing collider preview scene view: {exc}")
        self._scene_view = None
        self._vstack = None
        self._viewport = None
