import pydantic
from typing import Literal, cast

import carb
from pxr import Usd, UsdGeom
import wandelbots_api_client.v2.models as nova_models
from pxr import Sdf, Gf
import omni.physx.bindings._physx as physx_bindings
from wandelbots.omni.core.collision.authored_geometry import (
    collider_points_local,
    convex_hull_points_and_faces,
)
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.datatypes import WSPose


# Scales come out of a matrix decomposition, so they are never exactly equal.
_SCALE_TOLERANCE = 1e-6


def _is_uniform_scale(scale) -> bool:
    return Gf.IsClose(scale[0], scale[1], _SCALE_TOLERANCE) and Gf.IsClose(
        scale[0], scale[2], _SCALE_TOLERANCE
    )


class ConvexHull(pydantic.BaseModel):
    shape_type: Literal["convex_hull"] = "convex_hull"
    vertices: list[list[float]]


class Sphere(pydantic.BaseModel):
    shape_type: Literal["sphere"] = "sphere"
    radius: float = pydantic.Field(..., description="Radius of the sphere [mm]")


class Box(pydantic.BaseModel):
    shape_type: Literal["box"] = "box"
    size_x: float = pydantic.Field(..., description="Size in x direction [mm]")
    size_y: float = pydantic.Field(..., description="Size in y direction [mm]")
    size_z: float = pydantic.Field(..., description="Size in z direction [mm]")
    box_type: Literal["FULL"] = "FULL"


class Capsule(pydantic.BaseModel):
    shape_type: Literal["capsule"] = "capsule"
    radius: float = pydantic.Field(..., description="Radius of the capsule [mm]")
    height: float = pydantic.Field(..., description="Height of the capsule [mm]")


class Cylinder(pydantic.BaseModel):
    shape_type: Literal["cylinder"] = "cylinder"
    radius: float = pydantic.Field(..., description="Radius of the cylinder [mm]")
    height: float = pydantic.Field(..., description="Height of the cylinder [mm]")


class Plane(pydantic.BaseModel):
    shape_type: Literal["plane"] = "plane"


CollisionShape = ConvexHull | Sphere | Box | Capsule | Cylinder | Plane


class Collider(pydantic.BaseModel):
    shape: CollisionShape
    pose: nova_models.Pose
    prim_path: str


def plane_to_collider(prim: Usd.Prim) -> Collider | None:
    axis_attribute = prim.GetAttributeAtPath(f"{prim.GetPath().pathString}.axis")
    if axis_attribute.Get(Usd.TimeCode.Default()) != "Z":
        carb.log_warn(
            f"Unsupported axis {axis_attribute.Get(Usd.TimeCode.Default())} for prim {prim.GetPath()}. Expected 'Z'."
        )
        return None

    return Collider(
        shape=Plane(shape_type="plane"),
        pose=cast(
            WSPose,
            PrimUtils.get_prim_pose(
                prim.GetPath().pathString,
                rotation_type="cartesian",
                coordinate_system="world",
            ),
        ).to_nova_pose(),
        prim_path=prim.GetPath().pathString,
    )


def sphere_to_collider(prim: Usd.Prim) -> Collider | None:
    prim_path: str = cast(Sdf.Path, prim.GetPath()).pathString
    radius = prim.GetAttributeAtPath(f"{prim_path}.radius").Get(Usd.TimeCode.Default())

    _, _, scale = PrimUtils.get_world_transform_xform(prim)
    if not _is_uniform_scale(scale):
        # A non-uniformly scaled sphere is an ellipsoid, which NOVA has no
        # primitive for. The caller falls back to points_hull_collider.
        carb.log_warn(
            f"Non-uniform scale {scale} for prim {prim.GetPath()}. Expected uniform scale."
        )
        return None

    return Collider(
        shape=Sphere(
            shape_type="sphere",
            radius=SceneUtils.value_to_millimeters(radius * scale[0]),
        ),
        pose=cast(
            WSPose,
            PrimUtils.get_prim_pose(
                prim_path, rotation_type="cartesian", coordinate_system="world"
            ),
        ).to_nova_pose(),
        prim_path=prim_path,
    )


def cube_to_collider(prim: Usd.Prim) -> Collider:
    prim_path: str = cast(Sdf.Path, prim.GetPath()).pathString
    size = prim.GetAttributeAtPath(f"{prim_path}.size").Get(Usd.TimeCode.Default())
    _, _, scale = PrimUtils.get_world_transform_xform(prim)
    return Collider(
        shape=Box(
            shape_type="box",
            size_x=SceneUtils.value_to_millimeters(size * scale[0]),
            size_y=SceneUtils.value_to_millimeters(size * scale[1]),
            size_z=SceneUtils.value_to_millimeters(size * scale[2]),
            box_type="FULL",
        ),
        pose=cast(
            WSPose,
            PrimUtils.get_prim_pose(
                prim_path, rotation_type="cartesian", coordinate_system="world"
            ),
        ).to_nova_pose(),
        prim_path=prim_path,
    )


def cylinder_to_collider(prim: Usd.Prim) -> Collider | None:
    prim_path: str = cast(Sdf.Path, prim.GetPath()).pathString
    radius = prim.GetAttributeAtPath(f"{prim_path}.radius").Get(Usd.TimeCode.Default())
    height = prim.GetAttributeAtPath(f"{prim_path}.height").Get(Usd.TimeCode.Default())

    axis = prim.GetAttributeAtPath(f"{prim_path}.axis").Get(Usd.TimeCode.Default())

    if axis != "Z":
        carb.log_warn(
            f"Unsupported axis {axis} for prim {prim.GetPath()}. Expected 'Z'."
        )
        return None

    _, _, scale = PrimUtils.get_world_transform_xform(prim)
    if not _is_uniform_scale(scale):
        carb.log_warn(
            f"Unsupported scale {scale} for prim {prim.GetPath()}. Expected uniform scale."
        )
        return None

    return Collider(
        shape=Cylinder(
            shape_type="cylinder",
            radius=SceneUtils.value_to_millimeters(radius * scale[0]),
            height=SceneUtils.value_to_millimeters(height * scale[0]),
        ),
        pose=cast(
            WSPose,
            PrimUtils.get_prim_pose(
                prim_path, rotation_type="cartesian", coordinate_system="world"
            ),
        ).to_nova_pose(),
        prim_path=prim_path,
    )


def capsule_to_collider(prim: Usd.Prim) -> Collider | None:
    prim_path: str = cast(Sdf.Path, prim.GetPath()).pathString

    radius = prim.GetAttributeAtPath(f"{prim_path}.radius").Get(Usd.TimeCode.Default())
    height = prim.GetAttributeAtPath(f"{prim_path}.height").Get(Usd.TimeCode.Default())

    axis = prim.GetAttributeAtPath(f"{prim_path}.axis").Get(Usd.TimeCode.Default())

    if axis != "Z":
        carb.log_warn(
            f"Unsupported axis {axis} for prim {prim.GetPath()}. Expected 'Z'."
        )
        return None

    _, _, scale = PrimUtils.get_world_transform_xform(prim)
    if not _is_uniform_scale(scale):
        carb.log_warn(
            f"Unsupported scale {scale} for prim {prim.GetPath()}. Expected uniform scale."
        )
        return None

    return Collider(
        shape=Capsule(
            shape_type="capsule",
            radius=SceneUtils.value_to_millimeters(radius * scale[0]),
            height=SceneUtils.value_to_millimeters(height * scale[0]),
        ),
        pose=cast(
            WSPose,
            PrimUtils.get_prim_pose(
                prim_path, rotation_type="cartesian", coordinate_system="world"
            ),
        ).to_nova_pose(),
        prim_path=prim_path,
    )


def _hull_collider_from_local_points(
    prim: Usd.Prim, points_local: list[tuple[float, float, float]]
) -> Collider | None:
    """ConvexHull collider from prim-local points.

    Follows the convention of get_convex_hull_colliders: the world scale is
    baked into the vertices in millimeters and the pose carries rotation and
    translation, so any axis and any scale work.
    """
    if len(points_local) < 4:
        return None
    _, _, scale = PrimUtils.get_world_transform_xform(prim)
    scale_transform = Gf.Matrix4d()
    scale_transform.SetScale(
        Gf.Vec3d(
            SceneUtils.value_to_millimeters(scale[0]),
            SceneUtils.value_to_millimeters(scale[1]),
            SceneUtils.value_to_millimeters(scale[2]),
        )
    )
    vertices = []
    for point in points_local:
        scaled = scale_transform.Transform(Gf.Vec3d(*point))
        vertices.append([scaled[0], scaled[1], scaled[2]])
    return Collider(
        shape=ConvexHull(shape_type="convex_hull", vertices=vertices),
        pose=cast(
            WSPose,
            PrimUtils.get_prim_pose(
                prim.GetPath().pathString,
                rotation_type="cartesian",
                coordinate_system="world",
            ),
        ).to_nova_pose(),
        prim_path=prim.GetPath().pathString,
    )


def points_hull_collider(
    prim: Usd.Prim, approximation: str | None = None
) -> Collider | None:
    """Convex-hull collider for a prim NOVA has no primitive for: a shape with
    a non-Z axis or a non-uniform scale, a cone, or a mesh approximation
    outside the convex family. The points enclose the authored geometry, so the
    collider never under-reports it.

    A mesh point cloud is reduced to its hull vertices first; a shape shell and
    a bounding box are already minimal.
    """
    points = collider_points_local(prim, Usd.TimeCode.Default(), approximation)
    if not points:
        return None
    if prim.IsA(UsdGeom.Mesh) and approximation not in (
        "boundingCube",
        "boundingSphere",
    ):
        points, _ = convex_hull_points_and_faces(points)
        if not points:
            return None
    return _hull_collider_from_local_points(prim, points)


def triangulate_polygon(
    vertices: list[Gf.Vec3f],
) -> list[list[Gf.Vec3f]]:
    # triangle
    if len(vertices) == 3:
        return vertices

    # polygon
    # create a triangle fan from polygon (the vertices are in order)
    polygon_vertices = []
    polygon_center = Gf.Vec3f(0, 0, 0)
    for vertex in vertices:
        polygon_center += vertex
    polygon_center = polygon_center / len(vertices)

    for vertex_index in range(len(vertices)):
        vertex_a = vertices[vertex_index]
        vertex_b = vertices[(vertex_index + 1) % len(vertices)]
        polygon_vertices += [vertex_a, vertex_b, polygon_center]
    return polygon_vertices


def triangulate_convex_hull(
    convex_hull: physx_bindings.PhysxConvexMeshData,
) -> list[Gf.Vec3f]:
    mesh_vertices = []
    for polygon in convex_hull.polygons:
        offset = polygon.index_base
        indices = [
            i for i in convex_hull.indices[offset : offset + polygon.num_vertices]
        ]
        hull_vertices = [convex_hull.vertices[index] for index in indices]
        hull_vertices = [
            Gf.Vec3f(vertex[0], vertex[1], vertex[2]) for vertex in hull_vertices
        ]
        mesh_vertices += triangulate_polygon(hull_vertices)
    return mesh_vertices


def get_convex_hull_vertex_count(
    physx_cooking_instance: physx_bindings.PhysXCooking,
    stage_id: int,
    prim: Usd.Prim,
    prim_id: int,
) -> int:
    """Return the unique convex-hull vertex count cooked by PhysX for a prim.

    Sums vertices across hulls, so a ``convexDecomposition`` prim reports the total
    of all its hulls while a ``convexHull`` prim reports its single hull. The cook
    runs synchronously (``False``), matching ``get_convex_hull_colliders``.
    """
    total = {"n": 0}

    def _on_result(convexes: list[physx_bindings.PhysxConvexMeshData]):
        total["n"] = sum(len(hull.vertices) for hull in convexes)

    physx_cooking_instance.request_convex_collision_representation(
        stage_id,
        prim_id,
        False,
        lambda _result, convexes: _on_result(convexes),
    )
    return total["n"]


def get_convex_hull_colliders(
    physx_cooking_instance: physx_bindings.PhysXCooking,
    stage_id: int,
    prim: Usd.Prim,
    prim_id: int,
):
    """Get the convex hull colliders for a given prim.

    Returns a dictionary of colliders with the key being a tuple of (prim_path, hull_index).
    """
    colliders: dict[tuple[str, int], Collider] = {}

    def on_convex_result(convexes: list[physx_bindings.PhysxConvexMeshData]):
        carb.log_verbose(f"Convex result: shape_count={len(convexes)}")

        _, _, scale = PrimUtils.get_world_transform_xform(prim)

        prim_scale_transform = Gf.Matrix4d()
        prim_scale_transform.SetScale(
            Gf.Vec3d(
                SceneUtils.value_to_millimeters(scale[0]),
                SceneUtils.value_to_millimeters(scale[1]),
                SceneUtils.value_to_millimeters(scale[2]),
            )
        )

        for hull_index, hull in enumerate(convexes):
            mesh_vertices = triangulate_convex_hull(hull)

            if len(mesh_vertices) == 0:
                continue

            for vertex_idx in range(len(mesh_vertices)):
                scaled_vertex = prim_scale_transform.Transform(
                    mesh_vertices[vertex_idx]
                )
                mesh_vertices[vertex_idx] = [
                    scaled_vertex[0],
                    scaled_vertex[1],
                    scaled_vertex[2],
                ]

            colliders[(prim.GetPath().pathString, hull_index)] = Collider(
                shape=ConvexHull(shape_type="convex_hull", vertices=mesh_vertices),
                pose=cast(
                    WSPose,
                    PrimUtils.get_prim_pose(
                        prim.GetPath().pathString,
                        rotation_type="cartesian",
                        coordinate_system="world",
                    ),
                ).to_nova_pose(),
                prim_path=prim.GetPath().pathString,
            )

    physx_cooking_instance.request_convex_collision_representation(
        stage_id,
        prim_id,
        False,
        lambda _result, convexes: on_convex_result(convexes),
    )
    return colliders
