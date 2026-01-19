import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Depends, Query
from wandelbots.omni.core.collision.collision_export_service import (
    CollisionExportService,
    get_collision_export_service,
    SweepParameters,
)
from wandelbots.omni.core.collision.shapes import Collider
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.scene import SceneUtils

collision_world_router = APIRouter(
    prefix="/physics/collision", tags=["Collision World"]
)

CollisionServiceDep = Annotated[
    CollisionExportService, Depends(get_collision_export_service)
]


def validate_prim_path_reference(
    relative_to_prim: str | None = Query(
        None,
        description="Prim path to which the collision object poses are relative to. If not provided, world coordinates are used.",
    ),
) -> str | None:
    if not relative_to_prim:
        return None
    if not PrimUtils.is_prim_valid(relative_to_prim):
        raise HTTPException(404, detail=f"Invalid prim path: {relative_to_prim}")
    return relative_to_prim


PrimPath = Annotated[str | None, Depends(validate_prim_path_reference)]


@collision_world_router.post(
    path="/sweep",
    operation_id="sweep_collisions",
    response_model=dict[str, Collider],
    description="Performs a collision sweep with the specified volume and returns the colliders that were hit.",
)
async def sweep_collisions(
    sweep_arguments: SweepParameters,
    collision_export_service: CollisionServiceDep,
    relative_to_prim: PrimPath,
):
    # The collision sweep requires the simulation to be running otherwise it will return an empty result.
    timeline, was_playing = SceneUtils.check_simulation()

    if not timeline.is_playing():
        timeline.play()

    while timeline.is_stopped():
        # waiting for the timeline to start
        await asyncio.sleep(0.1)

    try:
        reference_pose = None
        if relative_to_prim:
            if not PrimUtils.is_prim_valid(relative_to_prim):
                raise HTTPException(
                    404, detail=f"Invalid prim path: {relative_to_prim}"
                )
            reference_pose = PrimUtils.get_prim_pose(
                relative_to_prim, coordinate_system="world"
            )

        return collision_export_service.collision_sweep(
            sweep_arguments, stage=None, reference_prim_pose=reference_pose
        )
    finally:
        if not was_playing and timeline.is_playing():
            timeline.stop()
