from typing import cast
import omni.ui.scene as sc
import wandelbots_api_client.v2 as wb
from wandelbots.omni.utils.mesh import MeshUtils
import wandelbots.omni.ui.colors as color_utils


class ManipulatorMesh(sc.Manipulator):
    def __init__(
        self,
        transform: sc.Transform,
        vertices: list[tuple[float, float, float]],
        color: color_utils.ColorRGBA = [1.0, 1.0, 1.0, 0.15],
        filled: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._angle = 0
        self._transform = transform
        self._vertices = vertices
        self._face_boundaries = MeshUtils.get_boundary_edges_from_triangles(
            self._vertices
        )
        self._color = color
        self._color_array = self._calculate_color_array()
        self._filled = filled
        self._rendered = False
        self._scene_transform: sc.Transform | None = None

    def on_build(self):
        with sc.Transform(transform=self._transform) as self._scene_transform:
            if self._filled:
                # Render each face boundary as a filled polygon
                for boundary_idx, boundary_vertices in enumerate(self._face_boundaries):
                    sc.PolygonMesh(
                        positions=boundary_vertices,
                        colors=self._color_array[boundary_idx],
                        vertex_counts=[len(boundary_vertices)],
                        vertex_indices=[i for i in range(len(boundary_vertices))],
                    )
            else:
                # Render each face boundary as wireframe edges
                for boundary_vertices in self._face_boundaries:
                    for i in range(len(boundary_vertices)):
                        v1 = boundary_vertices[i]
                        v2 = boundary_vertices[(i + 1) % len(boundary_vertices)]
                        sc.Line(v1, v2, color=self._color)

        # Redraw all
        if not self._rendered:
            self.invalidate()
            self._rendered = True

    def set_transform(self, transform: sc.Matrix44):
        self._transform = transform
        self.invalidate()

    @property
    def color(self) -> color_utils.ColorRGBA:
        return self._color

    @color.setter
    def color(self, value: color_utils.ColorRGBA):
        self._color = value
        self._color_array = self._calculate_color_array()
        self.invalidate()

    def _calculate_color_array(self) -> list[list[color_utils.ColorRGBA]]:
        return [[self._color for _ in face] for face in self._face_boundaries]


def create_from_collider(
    collider: wb.models.Collider,
    transform: sc.Transform,
    color: color_utils.ColorRGBA = [1.0, 1.0, 1.0, 0.15],
    filled: bool = False,
    visible: bool = True,
) -> ManipulatorMesh | None:
    if isinstance(collider.shape.actual_instance, wb.models.ConvexHull):
        convex_hull = cast(wb.models.ConvexHull, collider.shape.actual_instance)

        # Triangulate the convex hull vertices
        vertices, faces, _ = MeshUtils.triangulate_convex_hull(convex_hull.vertices)

        # Convert indexed faces to flat vertex list for rendering
        triangulated_vertices = []
        for face in faces:
            for idx in face:
                triangulated_vertices.append(tuple(vertices[idx]))

        return ManipulatorMesh(
            transform=transform,
            vertices=triangulated_vertices,
            color=color,
            filled=filled,
            visible=visible,
        )

    return None
