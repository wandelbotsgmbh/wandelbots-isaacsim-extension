import asyncio
import os
import tempfile
import time
from pathlib import Path

import carb
import omni.usd
from omni.kit.async_engine import run_coroutine
from pxr import Usd
from pxr import UsdGeom
from pxr import Vt, Gf
import numpy as np
from numpy.typing import NDArray
from pyhull.convex_hull import ConvexHull

from wandelbots.omni.utils import watertight_mesh
from wandelbots.omni.utils.base import get_extension_root, get_kit_python_executable
from wandelbots.omni.utils.watertight_mesh import SubMesh

# Type aliases for mesh geometry
Vertex = tuple[float, float, float]
Edge = tuple[Vertex, Vertex]
Triangle = tuple[Vertex, Vertex, Vertex]
Face = list[Vertex]

# Each worker subprocess spawns its own CoACD process pool sized to nearly
# all cores (see watertight_mesh._decompose_concave_shells), so two builds
# running at once oversubscribe the CPU and are both slower than queued -
# this serializes every ghost mesh build (preview and refine alike) across
# the whole extension, one worker subprocess at a time.
_mesh_build_slot = asyncio.Semaphore(1)


async def _build_watertight_in_subprocess(
    submeshes: list[SubMesh], decompose: bool = True
) -> SubMesh | None:
    """Run build_watertight_mesh in a separate process.

    Its native deps (manifold3d, coacd) are nanobind/ctypes libraries that
    must never load into the Kit process: a Kit extension hot reload purges
    their sys.modules entries and the following re-import re-initializes
    nanobind, which aborts the whole app. In a short-lived worker process
    they load freshly every time, and a native crash cannot take Kit down.
    """
    dependency_path = str(get_extension_root() / "pip_prebundle")
    worker_script = Path(__file__).with_name("watertight_mesh.py")
    with tempfile.TemporaryDirectory(prefix="wb_ghost_mesh_") as exchange_dir:
        input_path = str(Path(exchange_dir) / "input.npz")
        output_path = str(Path(exchange_dir) / "output.npz")
        watertight_mesh.write_submeshes(input_path, submeshes, decompose=decompose)

        async with _mesh_build_slot:
            process = await asyncio.create_subprocess_exec(
                get_kit_python_executable(),
                # -P: do not prepend the script dir to sys.path - this package
                # has modules shadowing the stdlib (math.py), which would break
                # numpy inside the worker.
                "-P",
                str(worker_script),
                # The dependency path travels as an argument AND a dedicated
                # env var (see _prepare_worker_environment): debugger
                # subprocess injection (pydevd) rewrites PYTHONPATH and argv,
                # so the worker needs redundant channels.
                dependency_path,
                input_path,
                output_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Kit's process env carries interpreter-affecting variables
                # (the Isaac launcher exports PYTHONPATH; LD_LIBRARY_PATH
                # points at Kit's libs) that break the worker's numpy native
                # modules. Strip those; the worker resolves its deps itself.
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key not in ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH")
                }
                | {"WANDELBOTS_MESH_WORKER_DEPS": dependency_path},
            )
            _, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(
                f"mesh worker exited with {process.returncode}: "
                f"{stderr.decode(errors='replace')[-1500:]}"
            )

        with np.load(output_path) as result:
            if "empty" in result:
                return None
            return result["vertices"], result["triangles"]


class MeshUtils:
    @staticmethod
    def iter_source_meshes(source_prim: Usd.Prim):
        """Yield (prim, points, indices, counts) for every UsdGeom.Mesh with
        valid geometry under source_prim.

        The one traversal shared by every ghost-mesh consumer that needs raw
        mesh data: the submesh collector below and the cache's geometry
        stamp (GhostMeshCache.compute_source_stamp) both walk this same set,
        so a mesh that would not affect the built geometry (missing/empty
        attributes) does not affect the cache key either.
        """
        for prim in Usd.PrimRange(source_prim):
            if not prim.IsA(UsdGeom.Mesh):
                continue
            mesh = UsdGeom.Mesh(prim)
            points = mesh.GetPointsAttr().Get()
            counts = mesh.GetFaceVertexCountsAttr().Get()
            indices = mesh.GetFaceVertexIndicesAttr().Get()
            if not points or not counts or not indices:
                continue
            yield prim, points, indices, counts

    @staticmethod
    def _collect_triangulated_submeshes(
        source_prim: Usd.Prim,
        mesh_offset_transform: Gf.Matrix4d,
    ) -> list[SubMesh]:
        """Collect every mesh under source_prim as world-transformed triangles."""
        submeshes: list[SubMesh] = []
        for prim, points, indices, counts in MeshUtils.iter_source_meshes(source_prim):
            transform = (
                omni.usd.get_world_transform_matrix(prim) * mesh_offset_transform
            )
            # Gf matrices are row-major with row vectors: transformed = p * M
            vertices = np.asarray(points, dtype=np.float64)
            matrix = np.asarray(transform, dtype=np.float64)
            vertices = vertices @ matrix[:3, :3] + matrix[3, :3]

            # Fan-triangulate polygonal faces (quads/ngons) into triangles
            counts_array = np.asarray(counts, dtype=np.int64)
            indices_array = np.asarray(indices, dtype=np.int32)
            if np.all(counts_array == 3):
                triangles = indices_array.reshape(-1, 3)
            else:
                triangles_per_face = np.maximum(counts_array - 2, 0)
                face_starts = np.concatenate(([0], np.cumsum(counts_array[:-1])))
                face_start_per_triangle = np.repeat(face_starts, triangles_per_face)
                fan_starts = np.concatenate(([0], np.cumsum(triangles_per_face[:-1])))
                index_in_fan = np.arange(triangles_per_face.sum()) - np.repeat(
                    fan_starts, triangles_per_face
                )
                triangles = np.stack(
                    (
                        indices_array[face_start_per_triangle],
                        indices_array[face_start_per_triangle + index_in_fan + 1],
                        indices_array[face_start_per_triangle + index_in_fan + 2],
                    ),
                    axis=1,
                )
            if len(triangles):
                submeshes.append((vertices, triangles))
        return submeshes

    @staticmethod
    def fill_ghost_mesh_progressively(
        source_prim: Usd.Prim,
        target_path: str,
        mesh_offset_transform: Gf.Matrix4d = Gf.Matrix4d().SetIdentity(),
    ) -> asyncio.Task:
        """Fill the mesh prim at target_path in two background passes.

        A hull-only preview (concave openings temporarily filled) lands
        within seconds; the full CoACD build then replaces it in place, both
        in worker processes (see _build_watertight_in_subprocess). Geometry
        is collected synchronously here, paired with the offset transform
        computed from the same frame's world transforms - so the baked
        result is immune to the robot moving while the builds run.

        The returned task resolves to the refined UsdGeom.Mesh, or None when
        the scene changed, the preview fell back to the verbatim merge, or
        the refinement failed (the preview then stays).
        """
        submeshes = MeshUtils._collect_triangulated_submeshes(
            source_prim, mesh_offset_transform
        )
        return run_coroutine(
            MeshUtils._fill_ghost_mesh(
                source_prim, target_path, mesh_offset_transform, submeshes
            )
        )

    @staticmethod
    async def _fill_ghost_mesh(
        source_prim: Usd.Prim,
        target_path: str,
        mesh_offset_transform: Gf.Matrix4d,
        submeshes: list[SubMesh],
    ) -> UsdGeom.Mesh | None:
        stage = source_prim.GetStage()
        if not submeshes:
            carb.log_warn(
                f"No mesh geometry found under {source_prim.GetPath()}; "
                f"filling {target_path} with the verbatim merge."
            )
            MeshUtils.merge_prim_meshes(source_prim, target_path, mesh_offset_transform)
            return None

        source_vertex_count = sum(len(vertices) for vertices, _ in submeshes)

        def scene_changed() -> bool:
            return stage.expired or not stage.GetPrimAtPath(target_path)

        # Preview pass: hulls only, so the ghost has a body within seconds.
        started = time.monotonic()
        try:
            preview = await _build_watertight_in_subprocess(submeshes, decompose=False)
        except Exception as error:  # includes worker startup failures
            carb.log_warn(f"Ghost mesh preview for {target_path} failed: {error}")
            preview = None
        if scene_changed():
            carb.log_verbose(f"Scene changed while building {target_path}; aborting.")
            return None
        if preview is None:
            if source_prim:
                MeshUtils.merge_prim_meshes(
                    source_prim, target_path, mesh_offset_transform
                )
            return None
        MeshUtils._write_triangle_mesh(stage, target_path, *preview)
        carb.log_info(
            f"Ghost mesh preview for {target_path}: "
            f"{source_vertex_count} -> {len(preview[0])} vertices "
            f"in {time.monotonic() - started:.1f}s"
        )

        # Refinement pass: the full CoACD build replaces the preview.
        started = time.monotonic()
        try:
            refined = await _build_watertight_in_subprocess(submeshes, decompose=True)
        except Exception as error:
            carb.log_warn(
                f"Ghost mesh refinement for {target_path} failed: {error}; "
                f"keeping the preview mesh."
            )
            return None
        if scene_changed():
            carb.log_verbose(f"Scene changed while refining {target_path}; aborting.")
            return None
        if refined is None:
            carb.log_warn(
                f"Ghost mesh refinement for {target_path} produced no valid mesh; "
                f"keeping the preview mesh."
            )
            return None
        target_mesh = MeshUtils._write_triangle_mesh(stage, target_path, *refined)
        carb.log_info(
            f"Ghost mesh refined for {target_path}: "
            f"{source_vertex_count} -> {len(refined[0])} vertices "
            f"({100 * len(refined[0]) / max(source_vertex_count, 1):.1f}%) "
            f"in {time.monotonic() - started:.1f}s"
        )
        return target_mesh

    @staticmethod
    def _write_triangle_mesh(
        stage: Usd.Stage,
        target_path: str,
        vertices: NDArray,
        triangles: NDArray,
    ) -> UsdGeom.Mesh:
        target_mesh = UsdGeom.Mesh.Define(stage, target_path)
        target_mesh.CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(vertices))
        target_mesh.CreateFaceVertexIndicesAttr().Set(
            Vt.IntArray.FromNumpy(triangles.reshape(-1))
        )
        target_mesh.CreateFaceVertexCountsAttr().Set(
            Vt.IntArray.FromNumpy(np.full(len(triangles), 3, dtype=np.int32))
        )
        return target_mesh

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

        def children_of(prim: Usd.Prim):
            # Instance proxies, because GetChildren stops at an instance and the
            # meshes of an instanced tool live in its prototype. Without this an
            # instanced tool merges to an empty mesh.
            return prim.GetFilteredChildren(Usd.TraverseInstanceProxies())

        def collect_mesh_data(prim: Usd.Prim, vertex_offset: int) -> int:
            """Recursively collect mesh data and return updated vertex offset"""

            # Early return if not a mesh - process children with current offset
            if not prim.IsA(UsdGeom.Mesh):
                current_offset = vertex_offset
                for child in children_of(prim):
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
            for child in children_of(prim):
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
        else:
            # An empty result is never what the caller wanted, and it used to be
            # returned silently - a ghost object then appeared with no geometry.
            carb.log_warn(
                f"No mesh geometry found under {source_prim.GetPath()}, so "
                f"{target_path} is empty."
            )
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
