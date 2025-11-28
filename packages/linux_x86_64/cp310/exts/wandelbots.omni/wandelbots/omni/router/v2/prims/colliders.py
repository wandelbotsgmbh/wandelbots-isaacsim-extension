from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, status, Depends
from wandelbots.omni.core.collision.collision_export_service import (
    CollisionExportService,
    get_collision_export_service,
)
from wandelbots.omni.utils.prims import PrimUtils

colliders_router = APIRouter(prefix="/prims/physics/colliders", tags=["Prims"])

CollisionServiceDep = Annotated[
    CollisionExportService, Depends(get_collision_export_service)
]


def validate_prim_path_body(
    prim_path: str = Body(..., description="Prim path of the object"),
) -> str:
    if not PrimUtils.is_prim_valid(prim_path):
        raise HTTPException(404, detail=f"Invalid prim path: {prim_path}")
    return prim_path


@colliders_router.patch(
    path="/",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="update_colliders",
    response_model=None,
    responses={
        204: {"description": "Collider state updated successfully"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Could not update collider state"},
    },
)
async def update_colliders(
    prim_path: str = Depends(validate_prim_path_body),
    enable: bool = Body(
        ..., description="Set to true to enable collider, false to disable"
    ),
) -> None:
    """
    Enable or disable the colliders on a prim.
    """
    try:
        prim = PrimUtils.get_prim(prim_path)
        prim.GetAttribute("physics:collisionEnabled").Set(True if enable else False)
    except Exception as e:
        raise HTTPException(500, f"Could not enable collider of prim at path: {e}")
