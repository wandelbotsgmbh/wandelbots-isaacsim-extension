import omni.usd
from pxr import Usd
from pxr import UsdGeom
from pxr import Vt, Gf
import numpy as np


class MeshUtils:
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

        def collect_mesh_data(prim, vertex_offset):
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

            # Set face vertex counts (assuming triangles)
            face_count = len(all_indices) // 3
            face_counts = [3] * face_count
            counts_attr = target_mesh.CreateFaceVertexCountsAttr()
            counts_attr.Set(Vt.IntArray(face_counts))
        return target_prim
