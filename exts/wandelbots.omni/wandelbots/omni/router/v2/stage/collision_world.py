import asyncio
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Depends
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


def validate_prim_path_body(
    prim_path: str = Body(..., description="Prim path of the object"),
) -> str:
    if not PrimUtils.is_prim_valid(prim_path):
        raise HTTPException(404, detail=f"Invalid prim path: {prim_path}")
    return prim_path


@collision_world_router.post(
    path="/sweep",
    operation_id="sweep_collisions",
    response_model=dict[str, Collider],
    description="Performs a collision sweep with the specified volume and returns the colliders that were hit.",
)
async def sweep_collisions(
    sweep_arguments: SweepParameters,
    collision_export_service: CollisionServiceDep,
):
    # The collision sweep requires the simulation to be running otherwise it will return an empty result.
    timeline, was_playing = SceneUtils.check_simulation()

    if not timeline.is_playing():
        timeline.play()

    while timeline.is_stopped():
        # waiting for the timeline to start
        await asyncio.sleep(0.1)

    try:
        return collision_export_service.collision_sweep(sweep_arguments)
    finally:
        if not was_playing and timeline.is_playing():
            timeline.stop()
