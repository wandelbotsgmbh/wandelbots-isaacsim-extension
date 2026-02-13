import omni.usd
from pxr import Usd
from pxr import UsdGeom
from pxr import Vt, Gf
import numpy as np
from numpy.typing import NDArray
from pyhull.convex_hull import ConvexHull

# Type aliases for mesh geometry
Vertex = tuple[float, float, float]
Edge = tuple[Vertex, Vertex]
Triangle = tuple[Vertex, Vertex, Vertex]
Face = list[Vertex]


class MeshUtils:
    @staticmethod
    def merge_prim_meshes(
        source_prim: Usd.Prim,
        target_path: str,
        mesh_offset_transform: Gf.Matrix4d = Gf.Matrix4d().SetIdentity(),
    ) -> UsdGeom.Mesh:
        stage: Usd.Stage = source_prim.GetStage()

        target_prim: UsdGeom.Mesh = UsdGeom.Mesh.Define(stage, target_path)

        # Collect all mesh data from the prim tree
        all_vertices = []
        all_indices = []
        all_face_vertex_counts = []

        def collect_mesh_data(prim: Usd.Prim, vertex_offset: int) -> int:
            """Recursively collect mesh data and return updated vertex offset"""

            # Early return if not a mesh - process children with current offset
            if not prim.IsA(UsdGeom.Mesh):
                current_offset = vertex_offset
                for child in prim.GetChildren():
                    current_offset = collect_mesh_data(child, current_offset)
                return current_offset

            mesh = UsdGeom.Mesh(prim)

            # Get vertices - early return if invalid
            vertices_attr = mesh.GetPointsAttr()
            if not vertices_attr.IsValid():
                return vertex_offset

            vertices = vertices_attr.Get()
            if not vertices:
                return vertex_offset

            world_transform = omni.usd.get_world_transform_matrix(prim)
            prim_scale_transform = world_transform * mesh_offset_transform

            for vertex_idx in range(len(vertices)):
                scaled_vertex = prim_scale_transform.Transform(vertices[vertex_idx])
                vertices[vertex_idx] = [
                    scaled_vertex[0],
                    scaled_vertex[1],
                    scaled_vertex[2],
                ]

            all_vertices.extend(vertices)
            vertex_count = len(vertices)

            # Get face vertex indices
            indices_attr = mesh.GetFaceVertexIndicesAttr()
            if not indices_attr.IsValid():
                return vertex_offset + vertex_count

            indices = indices_attr.Get()
            if not indices:
                return vertex_offset + vertex_count

            # Convert to numpy array and offset indices efficiently
            indices_array = np.array(indices, dtype=np.int32)
            offset_indices = indices_array + vertex_offset
            all_indices.extend(offset_indices.tolist())

            # Get face vertex counts (handles quads, triangles, etc.)
            face_counts_attr = mesh.GetFaceVertexCountsAttr()
            if face_counts_attr.IsValid():
                face_counts = face_counts_attr.Get()
                if face_counts:
                    all_face_vertex_counts.extend(face_counts)

            current_offset = vertex_offset + vertex_count

            # Recursively process children
            for child in prim.GetChildren():
                current_offset = collect_mesh_data(child, current_offset)

            return current_offset

        # Iterate through the whole tree of the source prim
        collect_mesh_data(source_prim, 0)

        # Set the merged mesh data on target prim
        if all_vertices and all_indices:
            target_mesh = UsdGeom.Mesh(target_prim)

            # Set vertices
            points_attr = target_mesh.CreatePointsAttr()
            points_attr.Set(Vt.Vec3fArray(all_vertices))

            # Set indices
            indices_attr = target_mesh.CreateFaceVertexIndicesAttr()
            indices_attr.Set(Vt.IntArray(all_indices))

            # Set face vertex counts using actual counts from source meshes
            if all_face_vertex_counts:
                counts_attr = target_mesh.CreateFaceVertexCountsAttr()
                counts_attr.Set(Vt.IntArray(all_face_vertex_counts))
            else:
                # Fallback to triangles if no face counts found (shouldn't happen)
                face_count = len(all_indices) // 3
                face_counts = [3] * face_count
                counts_attr = target_mesh.CreateFaceVertexCountsAttr()
                counts_attr.Set(Vt.IntArray(face_counts))
        return target_prim

    @staticmethod
    def triangulate_convex_hull(
        hull_vertices: list[list[float]],
    ) -> tuple[list[list[float]], list[list[int]], list[list[float]]]:
        """
        Compute convex hull triangulation from a point cloud using pyhull.

        Args:
            hull_vertices: List of 3D points.

        Returns:
            Tuple of (vertices, faces, normals) where:
            - vertices: Original vertex list
            - faces: List of triangle indices (3 indices per face)
            - normals: List of outward-pointing normals per face
        """
        if len(hull_vertices) < 4:
            return hull_vertices, [], []

        points = np.array(hull_vertices)
        hull = ConvexHull(hull_vertices)

        faces: list[list[int]] = []
        normals: list[list[float]] = []

        for vertex_indices in hull.vertices:
            face = list(vertex_indices)

            # Compute face normal from vertices
            v0, v1, v2 = points[face]
            e1 = v1 - v0
            e2 = v2 - v0
            normal = np.cross(e1, e2)
            norm = np.linalg.norm(normal)
            if norm > 0:
                normal = normal / norm

            # Compute centroid of hull for outward normal check
            centroid = points.mean(axis=0)
            face_center = (v0 + v1 + v2) / 3
            outward = face_center - centroid

            # Flip winding if normal points inward
            if np.dot(normal, outward) < 0:
                face = [face[0], face[2], face[1]]
                normal = -normal

            faces.append(face)
            normals.append(normal.tolist())

        return hull_vertices, faces, normals

    @staticmethod
    def _parse_vertices_into_triangles(vertices: list[Vertex]) -> list[Triangle]:
        """Parse flat vertex list into triangle tuples."""
        # Ensure vertex count is a multiple of 3 and handle empty case
        if len(vertices) % 3 != 0:
            vertices = vertices[: len(vertices) // 3 * 3]
        if not vertices:
            return []

        # Reshape flat list to triangles: [v0, v1, v2, v3, ...] -> [(v0,v1,v2), (v3,v4,v5), ...]
        # Array shape: (num_vertices,) -> (num_triangles, 3 vertices, 3 coords)
        arr = np.array(vertices, dtype=np.float64).reshape(-1, 3, 3)
        return [tuple(tuple(v) for v in tri) for tri in arr]

    @staticmethod
    def _calculate_triangle_normals(
        triangles: list[Triangle],
    ) -> list[NDArray[np.floating]]:
        """Calculate normalized normal vectors for each triangle."""
        if not triangles:
            return []

        # Convert triangles to array: shape (num_triangles, 3 vertices, 3 coords)
        tri_array = np.array(triangles, dtype=np.float64)

        # Compute edge vectors for all triangles: e1 = v1-v0, e2 = v2-v0
        # Shape: (num_triangles, 3 coords)
        e1 = tri_array[:, 1, :] - tri_array[:, 0, :]
        e2 = tri_array[:, 2, :] - tri_array[:, 0, :]

        # Compute cross products and normalize all at once
        # Shape: (num_triangles, 3 coords)
        normals = np.cross(e1, e2)
        norms = np.linalg.norm(normals, axis=1, keepdims=True)
        norms = np.where(norms > 0, norms, 1.0)  # Avoid division by zero
        normals = normals / norms

        return list(normals)

    @staticmethod
    def _build_edge_to_triangles_map(
        triangles: list[Triangle],
    ) -> dict[Edge, list[int]]:
        """Build mapping from edges to triangle indices that share them."""
        edge_to_triangles: dict[Edge, list[int]] = {}
        for tri_idx, tri in enumerate(triangles):
            edges = [
                tuple(sorted([tri[0], tri[1]])),
                tuple(sorted([tri[1], tri[2]])),
                tuple(sorted([tri[2], tri[0]])),
            ]
            for edge in edges:
                if edge not in edge_to_triangles:
                    edge_to_triangles[edge] = []
                edge_to_triangles[edge].append(tri_idx)
        return edge_to_triangles

    @staticmethod
    def _group_triangles_by_bfs(
        triangles: list[Triangle],
        triangle_normals: list[NDArray[np.floating]],
        edge_to_triangles: dict[Edge, list[int]],
        normal_threshold: float,
    ) -> list[list[int]]:
        """Group triangles using BFS based on connectivity and normal similarity."""
        groups: list[list[int]] = []
        used: set[int] = set()

        for start_idx in range(len(triangles)):
            if start_idx in used:
                continue

            # BFS to find connected coplanar triangles
            group: list[int] = []
            queue: list[int] = [start_idx]
            used.add(start_idx)

            while queue:
                current_idx = queue.pop(0)
                group.append(current_idx)
                current_normal = triangle_normals[current_idx]
                current_tri = triangles[current_idx]

                # Check all edges of current triangle
                edges = [
                    tuple(sorted([current_tri[0], current_tri[1]])),
                    tuple(sorted([current_tri[1], current_tri[2]])),
                    tuple(sorted([current_tri[2], current_tri[0]])),
                ]

                for edge in edges:
                    # Find triangles sharing this edge
                    for neighbor_idx in edge_to_triangles[edge]:
                        if neighbor_idx == current_idx or neighbor_idx in used:
                            continue

                        # Check if normals are similar
                        neighbor_normal = triangle_normals[neighbor_idx]
                        normal_similarity = np.dot(current_normal, neighbor_normal)
                        if normal_similarity >= normal_threshold:
                            queue.append(neighbor_idx)
                            used.add(neighbor_idx)

            groups.append(group)

        return groups

    @staticmethod
    def _group_triangles_by_connectivity(
        vertices: list[Vertex],
        normal_threshold: float = 0.99,
    ) -> tuple[list[list[int]], list[Triangle]]:
        """
        Internal helper to group triangle indices by connectivity and normal similarity.

        Args:
            vertices: Flat list of vertices, every 3 vertices form a triangle
            normal_threshold: Cosine similarity threshold for merging

        Returns:
            Tuple of (groups, triangles) where:
            - groups: List of groups, each group is a list of triangle indices
            - triangles: List of triangles, each triangle is 3 vertices
        """
        if len(vertices) < 3:
            return [], []

        triangles = MeshUtils._parse_vertices_into_triangles(vertices)
        triangle_normals = MeshUtils._calculate_triangle_normals(triangles)
        edge_to_triangles = MeshUtils._build_edge_to_triangles_map(triangles)
        groups = MeshUtils._group_triangles_by_bfs(
            triangles, triangle_normals, edge_to_triangles, normal_threshold
        )

        return groups, triangles

    @staticmethod
    def _find_boundary_edges(group: list[int], triangles: list[Triangle]) -> list[Edge]:
        """Find edges that appear only once in a group of triangles (boundary edges)."""
        edge_count: dict[Edge, int] = {}
        for tri_idx in group:
            tri = triangles[tri_idx]

            # Create edges
            edges = [
                tuple(sorted([tri[0], tri[1]])),
                tuple(sorted([tri[1], tri[2]])),
                tuple(sorted([tri[2], tri[0]])),
            ]

            for edge in edges:
                edge_count[edge] = edge_count.get(edge, 0) + 1

        # Boundary edges are those that appear only once
        boundary_edges: list[Edge] = []
        for edge, count in edge_count.items():
            if count == 1:
                boundary_edges.append(edge)

        return boundary_edges

    @staticmethod
    def _order_boundary_vertices(boundary_edges: list[Edge]) -> list[Vertex]:
        """Order boundary edges into a connected path of vertices."""
        if not boundary_edges:
            return []

        # Build adjacency map for boundary edges
        adjacency: dict[Vertex, list[Vertex]] = {}
        for edge in boundary_edges:
            v1, v2 = edge
            if v1 not in adjacency:
                adjacency[v1] = []
            if v2 not in adjacency:
                adjacency[v2] = []
            adjacency[v1].append(v2)
            adjacency[v2].append(v1)

        # Start from any vertex and follow the path
        start_vertex = boundary_edges[0][0]
        ordered_vertices: list[Vertex] = [start_vertex]
        current = start_vertex
        previous: Vertex | None = None

        while True:
            # Find next vertex (the one that's not the previous)
            neighbors = adjacency[current]
            next_vertex: Vertex | None = None
            for neighbor in neighbors:
                if neighbor != previous:
                    next_vertex = neighbor
                    break

            if next_vertex is None or next_vertex == start_vertex:
                break

            ordered_vertices.append(next_vertex)
            previous = current
            current = next_vertex

        return ordered_vertices

    @staticmethod
    def _compute_polygon_normal(vertices: list[Vertex]) -> NDArray[np.floating]:
        """Compute the normal of a polygon using the Newell method (robust for non-planar polygons)."""
        if len(vertices) < 3:
            return np.array([0.0, 0.0, 1.0])

        # Convert to array and create circular-shifted version for edge calculations
        # v: [v0, v1, v2, ...], v_next: [v1, v2, v0, ...]
        # Both shape: (num_vertices, 3 coords)
        v = np.array(vertices, dtype=np.float64)
        v_next = np.roll(v, -1, axis=0)

        # Newell method: sum contributions from all edges
        # Result shape: (3,) representing (nx, ny, nz)
        normal = np.array(
            [
                np.sum((v[:, 1] - v_next[:, 1]) * (v[:, 2] + v_next[:, 2])),
                np.sum((v[:, 2] - v_next[:, 2]) * (v[:, 0] + v_next[:, 0])),
                np.sum((v[:, 0] - v_next[:, 0]) * (v[:, 1] + v_next[:, 1])),
            ]
        )

        norm = np.linalg.norm(normal)
        if norm > 0:
            normal = normal / norm
        return normal

    @staticmethod
    def _fix_winding_order(
        ordered_vertices: list[Vertex], group: list[int], triangles: list[Triangle]
    ) -> list[Vertex]:
        """Ensure boundary vertices have consistent winding order with face normal."""
        if len(ordered_vertices) < 3:
            return ordered_vertices

        # Compute average normal from all triangles in the group
        # Array shape: (num_group_triangles, 3 vertices, 3 coords)
        group_triangles = np.array([triangles[i] for i in group], dtype=np.float64)
        e1 = group_triangles[:, 1, :] - group_triangles[:, 0, :]
        e2 = group_triangles[:, 2, :] - group_triangles[:, 0, :]
        tri_normals = np.cross(e1, e2)  # Shape: (num_group_triangles, 3)
        avg_normal = np.sum(tri_normals, axis=0)  # Sum to get average direction

        norm = np.linalg.norm(avg_normal)
        if norm > 0:
            avg_normal = avg_normal / norm

        # Compare boundary winding with face normal and reverse if needed
        boundary_normal = MeshUtils._compute_polygon_normal(ordered_vertices)
        if np.dot(boundary_normal, avg_normal) < 0:
            return list(reversed(ordered_vertices))

        return ordered_vertices

    @staticmethod
    def get_boundary_edges_from_triangles(
        vertices: list[Vertex],
        normal_threshold: float = 0.99,
    ) -> list[Face]:
        """
        Merge coplanar triangles and return their boundary vertices in clockwise order.

        Groups triangles that share an edge and have similar normals,
        then returns the boundary vertices ordered to form a closed polygon.

        Args:
            vertices: Flat list of vertices, every 3 vertices form a triangle
            normal_threshold: Cosine similarity threshold for merging (0.99 = ~8 degrees)

        Returns:
            List of face boundaries, where each boundary is a list of vertices in clockwise order
        """
        groups, triangles = MeshUtils._group_triangles_by_connectivity(
            vertices, normal_threshold
        )

        # For each group, find boundary edges and order them
        face_boundaries: list[Face] = []

        for group in groups:
            boundary_edges = MeshUtils._find_boundary_edges(group, triangles)
            if not boundary_edges:
                continue

            ordered_vertices = MeshUtils._order_boundary_vertices(boundary_edges)
            ordered_vertices = MeshUtils._fix_winding_order(
                ordered_vertices, group, triangles
            )

            face_boundaries.append(ordered_vertices)

        return face_boundaries
