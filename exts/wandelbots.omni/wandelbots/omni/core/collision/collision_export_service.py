from typing import Literal, cast

import carb
import omni.physx.bindings._physx as physx_bindings
import omni.usd
from pxr import UsdUtils, Usd
import pydantic
import omni.timeline
from pxr import Sdf
import wandelbots.omni.core.collision.shapes as collision_shapes
from wandelbots.omni.utils.prims import Pose, PrimUtils, WSPose


class SphereSweepParameters(pydantic.BaseModel):
    sweep_type: Literal["sphere"]
    radius: float = pydantic.Field(10.0, description="Radius [stage_units]")
    position: list[float] = pydantic.Field(
        [0.0, 0.0, 0.0],
        min_length=3,
        max_length=3,
        description="Position of the sphere sweep [stage_units]",
    )
    direction: list[float] = pydantic.Field(
        [0.0, 0.0, -1.0],
        min_length=3,
        max_length=3,
        description="Direction of the sphere sweep",
    )
    max_distance: float = pydantic.Field(
        0.0, description="Sweep distance [stage_units]"
    )


class BoxSweepParameters(pydantic.BaseModel):
    sweep_type: Literal["box"]
    half_extent: list[float] = pydantic.Field(
        [5.0, 5.0, 5.0],
        min_length=3,
        max_length=3,
        description="Half extent of the box [stage_units]",
    )
    sphere_radius: float = 0.5
    position: list[float] = pydantic.Field(
        [0.0, 0.0, 0.0],
        min_length=3,
        max_length=3,
        description="Position of the box sweep [stage_units]",
    )
    rotation: list[float] = pydantic.Field(
        [0.0, 0.0, 0.0, 1.0],
        min_length=4,
        max_length=4,
        description="Rotation of the box sweep in quaternion format",
    )
    direction: list[float] = pydantic.Field(
        [0.0, 0.0, -1.0],
        min_length=3,
        max_length=3,
        description="Direction of the box sweep",
    )
    max_distance: float = pydantic.Field(
        0.0, description="Sweep distance [stage_units]"
    )


SweepParameters = SphereSweepParameters | BoxSweepParameters


class CollisionExportService:
    def __init__(self):
        carb.log_verbose("Acquire physx interfaces")
        # acquire the physx interfaces takes some time, so we do it once here
        self._physx_cooking = physx_bindings.acquire_physx_cooking_interface()
        self._physx_sweep = physx_bindings.acquire_physx_scene_query_interface()
        self._physx_query = physx_bindings.acquire_physx_property_query_interface()

    def __del__(self):
        carb.log_verbose("Release physx interfaces")
        physx_bindings.release_physx_cooking_interface(self._physx_cooking)
        physx_bindings.release_physx_scene_query_interface(self._physx_sweep)
        physx_bindings.release_physx_property_query_interface(self._physx_query)

    def collision_sweep(
        self,
        sweep_args: SweepParameters,
        stage: Usd.Stage = None,
        reference_prim_pose: Pose = None,
    ) -> dict[str, collision_shapes.Collider]:
        if not omni.timeline.get_timeline_interface().is_playing():
            raise RuntimeError(
                "Timeline is not playing. Please start the timeline before performing a collision sweep."
            )

        stage: Usd.Stage = stage if stage else omni.usd.get_context().get_stage()
        stage_id: int = UsdUtils.StageCache.Get().GetId(stage).ToLongInt()

        carb.log_verbose(f"collision_sweep on stage id={stage_id} {sweep_args}")

        visited_colliders: set[int] = set()
        colliders: dict[str, collision_shapes.Collider] = dict()

        def on_sweep_hit(hit: physx_bindings.SweepHit) -> bool:
            prim = stage.GetPrimAtPath(hit.collision)
            prim_path: str = cast(Sdf.Path, prim.GetPath()).pathString
            prim_id = hit.collision_encoded[0]
            carb.log_verbose(f"on_sweep_hit collider {prim_id} {prim.GetPath()}")
            if prim_id in visited_colliders:
                return True

            carb.log_verbose(
                f"Collision will be added to list collision={hit.collision} prim_id={prim_id}"
            )

            # Unknown usually stands for primitive shape like colliders
            collision_approximation = "unknown"

            if prim.HasAttribute("physics:approximation"):
                collision_approximation = prim.GetAttributeAtPath(
                    f"{prim_path}.physics:approximation"
                ).Get(Usd.TimeCode.Default())
            carb.log_verbose(
                f"Collider PrimType path={prim_path} type={prim.GetTypeName()} approx={collision_approximation}"
            )

            # Generate collider based on the prim type
            if prim.GetTypeName() == "Sphere":
                colliders[prim_path] = collision_shapes.sphere_to_collider(prim)
            elif prim.GetTypeName() == "Cube":
                colliders[prim_path] = collision_shapes.cube_to_collider(
                    prim,
                )
            elif prim.GetTypeName() == "Cylinder":
                collider = collision_shapes.cylinder_to_collider(prim)
                if collider is None:
                    carb.log_warn(
                        f"Unsupported cylinder collider {prim.GetPath()} with type '{prim.GetTypeName()}/{collision_approximation}' for export."
                    )
                    return True
                colliders[prim_path] = collider
            elif prim.GetTypeName() == "Capsule":
                collider = collision_shapes.capsule_to_collider(prim)
                if collider is None:
                    carb.log_warn(
                        f"Unsupported capsule collider {prim.GetPath()} with type '{prim.GetTypeName()}/{collision_approximation}' for export."
                    )
                    return True
                colliders[prim_path] = collider

            elif prim.GetTypeName() == "Plane":
                collider = collision_shapes.plane_to_collider(prim)
                if collider is None:
                    carb.log_warn(
                        f"Unsupported plane collider {prim.GetPath()} with type '{prim.GetTypeName()}/{collision_approximation}' for export."
                    )
                    return True
                colliders[prim_path] = collider
            elif (
                collision_approximation == "convexHull"
                or collision_approximation == "convexDecomposition"
            ):
                convex_colliders = collision_shapes.get_convex_hull_colliders(
                    self._physx_cooking, stage_id, prim, prim_id
                )

                if convex_colliders is None or len(convex_colliders) == 0:
                    carb.log_warn(
                        f"Could not fallback to convex hull {prim.GetPath()} collision type '{prim.GetTypeName()}/{collision_approximation}' is not supported for export."
                    )
                    return True

                colliders.update(
                    [
                        (f"{prim_path}/{hull_index}", collider)
                        for (
                            prim_path,
                            hull_index,
                        ), collider in convex_colliders.items()
                    ]
                )
            else:
                carb.log_warn(
                    f"Unsupported collider {prim.GetPath()} with type '{prim.GetTypeName()}/{collision_approximation}' for export."
                )
                return True
            return True

        if sweep_args.sweep_type == "sphere":
            self._physx_sweep.sweep_sphere_all(
                sweep_args.radius,
                carb.Float3(*sweep_args.position),
                carb.Float3(*sweep_args.direction),
                sweep_args.max_distance,
                on_sweep_hit,
            )
        elif sweep_args.sweep_type == "box":
            self._physx_sweep.sweep_box_all(
                halfExtent=sweep_args.half_extent,
                pos=carb.Float3(*sweep_args.position),
                dir=carb.Float3(*sweep_args.direction),
                rot=carb.Float4(*sweep_args.rotation),
                distance=sweep_args.max_distance,
                reportFn=on_sweep_hit,
            )
        else:
            raise ValueError(f"Unknown sweep type: {sweep_args.sweep_type}")
        return {
            prim_path: collision_shapes.Collider(
                shape=collider.shape,
                pose=PrimUtils.get_relative_pose(
                    reference_prim_pose,
                    WSPose(pose=collider.pose.position + collider.pose.orientation),
                ).to_nova_pose()
                if reference_prim_pose
                else collider.pose,
            )
            for prim_path, collider in colliders.items()
            if collider is not None
        }


_collision_export_service = CollisionExportService()


def get_collision_export_service() -> CollisionExportService:
    return _collision_export_service
