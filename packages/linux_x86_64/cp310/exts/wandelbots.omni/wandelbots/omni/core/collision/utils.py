import wandelbots_api_client.v2 as nova_api
from typing import cast
import wandelbots.omni.core.collision.shapes as collision_shapes


def to_nova_collider(
    shape: collision_shapes.Collider,
) -> nova_api.models.Collider:
    pose = nova_api.models.Pose(
        position=[
            shape.pose.position[0],
            shape.pose.position[1],
            shape.pose.position[2],
        ],
        orientation=[
            shape.pose.orientation[0],
            shape.pose.orientation[1],
            shape.pose.orientation[2],
        ],
    )

    if isinstance(shape.shape, collision_shapes.Sphere):
        return nova_api.models.Collider(
            shape=nova_api.models.ColliderShape(
                nova_api.models.Sphere(
                    radius=shape.shape.radius,
                    shape_type="sphere",
                )
            ),
            pose=pose,
        )
    elif isinstance(shape.shape, collision_shapes.Box):
        return nova_api.models.Collider(
            shape=nova_api.models.ColliderShape(
                nova_api.models.Box(
                    size_x=shape.shape.size_x,
                    size_y=shape.shape.size_y,
                    size_z=shape.shape.size_z,
                    shape_type="box",
                    box_type="FULL",
                )
            ),
            pose=pose,
        )
    elif isinstance(shape.shape, collision_shapes.Capsule):
        return nova_api.models.Collider(
            shape=nova_api.models.ColliderShape(
                nova_api.models.Capsule(
                    cylinder_height=shape.shape.height,
                    radius=shape.shape.radius,
                    shape_type="capsule",
                )
            ),
            pose=pose,
        )
    elif isinstance(shape.shape, collision_shapes.Cylinder):
        return nova_api.models.Collider(
            shape=nova_api.models.ColliderShape(
                nova_api.models.Cylinder(
                    height=shape.shape.height,
                    radius=shape.shape.radius,
                    shape_type="cylinder",
                )
            ),
            pose=pose,
        )
    elif isinstance(shape.shape, collision_shapes.Plane):
        return nova_api.models.Collider(
            shape=nova_api.models.ColliderShape(
                nova_api.models.Plane(shape_type="plane")
            ),
            pose=pose,
        )
    elif isinstance(shape.shape, collision_shapes.ConvexHull):
        return nova_api.models.Collider(
            shape=nova_api.models.ColliderShape(
                nova_api.models.ConvexHull(
                    shape_type="convex_hull",
                    vertices=cast(
                        collision_shapes.ConvexHull,
                        shape.shape,
                    ).vertices,
                )
            ),
            pose=pose,
        )
    return None
