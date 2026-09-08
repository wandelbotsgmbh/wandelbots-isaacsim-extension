import carb
import wandelbots_api_client.v2 as nova_api
from typing import cast
from pxr import Sdf, Usd
import wandelbots.omni.core.collision.shapes as collision_shapes
from wandelbots.omni.usd.schema_utils import SchemaUtils

CARB_SETTINGS_PREFIX = "/persistent/exts/wandelbots.omni/collision_world"


def validate_setup_matches_motion_group(
    collision_setup: nova_api.models.CollisionSetup,
    motion_group_prim: Usd.Prim,
) -> list[str]:
    """Human-readable warnings about robot-mounted content (tool colliders,
    link extras) that does not originate from the given motion group; empty
    when everything matches.

    Stored collider ids double as provenance: tool ids are absolute stage paths
    of the swept tool subtree, link extras ids are paths relative to the
    exporting motion group (e.g. 'link_4/Cube'). Comparing them against the
    selected robot catches loading or planning a setup with the wrong robot.
    This is a heuristic over stage paths, not proof - a same-model twin robot or
    a different stage is legitimate - so callers warn, never block.
    """
    if motion_group_prim is None or not motion_group_prim.IsValid():
        return []
    return _tool_provenance_warnings(
        collision_setup.tool, motion_group_prim
    ) + _link_extras_provenance_warnings(collision_setup.link_chain, motion_group_prim)


def _tool_provenance_warnings(
    tool_colliders: dict[str, nova_api.models.Collider] | None,
    motion_group_prim: Usd.Prim,
) -> list[str]:
    if not tool_colliders:
        return []

    linked_tool_paths = [
        tool.GetPath().pathString
        for tool in SchemaUtils.list_motion_group_tools(motion_group_prim)
        if tool and tool.IsValid()
    ]
    if not linked_tool_paths:
        return [
            "the setup contains tool colliders, but the selected motion "
            "group has no linked tool"
        ]

    mismatched = [
        collider_id
        for collider_id in tool_colliders
        if not any(
            collider_id == path or collider_id.startswith(path + "/")
            for path in linked_tool_paths
        )
    ]
    if not mismatched:
        return []
    return [
        f"{len(mismatched)} of {len(tool_colliders)} tool colliders do not "
        f"originate from the selected motion group's tool "
        f"({', '.join(linked_tool_paths)}), e.g. '{mismatched[0]}'"
    ]


def _link_extras_provenance_warnings(
    link_chain: list[dict[str, nova_api.models.Collider]] | None,
    motion_group_prim: Usd.Prim,
) -> list[str]:
    if not link_chain:
        return []

    motion_group_path = motion_group_prim.GetPath().pathString
    stage = motion_group_prim.GetStage()
    missing = [
        collider_id
        for link_extras in link_chain
        for collider_id in link_extras or {}
        if not _link_extra_exists(stage, motion_group_path, collider_id)
    ]
    if not missing:
        return []
    return [
        f"{len(missing)} link collider(s) not found under the selected motion "
        f"group, e.g. '{motion_group_path}/{missing[0]}'"
    ]


def _link_extra_exists(
    stage: Usd.Stage, motion_group_path: str, collider_id: str
) -> bool:
    """Does a stored link extra still exist under the selected motion group?"""
    if "/" not in collider_id:
        # Legacy id stripped to the bare prim name - carries no provenance,
        # nothing to verify.
        carb.log_verbose(
            f"Link extra '{collider_id}' has no path - cannot verify its origin."
        )
        return True

    full_path = f"{motion_group_path}/{collider_id}"
    if not Sdf.Path.IsValidPathString(full_path):
        return False
    if stage.GetPrimAtPath(full_path).IsValid():
        return True

    # Leaf prims of merged collision meshes can be transient; accept when the
    # parent still exists under the selected robot, but never accept a bare
    # link-level match.
    parent_path = full_path.rsplit("/", 1)[0]
    return (
        parent_path != motion_group_path and stage.GetPrimAtPath(parent_path).IsValid()
    )


def merge_link_chain_extras(
    canonical: list[dict[str, nova_api.models.Collider]],
    stored: list[dict[str, nova_api.models.Collider]] | None,
) -> list[dict[str, nova_api.models.Collider]]:
    """Merge stored link-chain extras onto the canonical NOVA collision model
    and return a new list; neither input is mutated.

    Stored setups hold only the equipment attached to robot links, so the
    robot's own geometry always comes from the canonical model: on an id
    collision the canonical entry wins. Canonical models are truncated after
    the last link with geometry, so extras stored beyond that length
    (flange-mounted equipment) extend the chain instead of being dropped.
    """
    merged = [dict(link) for link in canonical]
    if not stored:
        return merged
    if len(stored) > len(merged):
        carb.log_info(
            f"Stored link chain has {len(stored)} links but the canonical model "
            f"has {len(merged)}; extending for the flange-side extras."
        )
        merged.extend({} for _ in range(len(stored) - len(merged)))
    for index, extras in enumerate(stored):
        if not extras:
            continue
        for collider_id, collider in extras.items():
            if collider_id in merged[index]:
                carb.log_verbose(
                    f"Link {index}: stored collider '{collider_id}' duplicates a "
                    "canonical model entry; keeping the canonical one."
                )
                continue
            merged[index][collider_id] = collider
    return merged


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
