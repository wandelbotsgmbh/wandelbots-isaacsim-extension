import pydantic
from typing import Literal, cast

import carb
from pxr import Usd
import wandelbots_api_client.v2.models as nova_models
from pxr import Sdf, Gf
import omni.physx.bindings._physx as physx_bindings
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.datatypes import WSPose


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
    )


def sphere_to_collider(prim: Usd.Prim) -> Collider:
    prim_path: str = cast(Sdf.Path, prim.GetPath()).pathString
    radius = prim.GetAttributeAtPath(f"{prim_path}.radius").Get(Usd.TimeCode.Default())
    return Collider(
        shape=Sphere(
            shape_type="sphere", radius=SceneUtils.value_to_millimeters(radius)
        ),
        pose=cast(
            WSPose,
            PrimUtils.get_prim_pose(
                prim_path, rotation_type="cartesian", coordinate_system="world"
            ),
        ).to_nova_pose(),
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
    if scale[0] != scale[1] or scale[0] != scale[2]:
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
        return True

    _, _, scale = PrimUtils.get_world_transform_xform(prim)
    if scale[0] != scale[1] or scale[0] != scale[2]:
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
    )


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
            )

    physx_cooking_instance.request_convex_collision_representation(
        stage_id,
        prim_id,
        False,
        lambda _result, convexes: on_convex_result(convexes),
    )
    return colliders
