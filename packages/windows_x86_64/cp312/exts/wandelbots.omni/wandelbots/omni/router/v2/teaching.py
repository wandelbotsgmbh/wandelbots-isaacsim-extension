import asyncio
from typing import Optional

import carb

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    status,
    Query,
)
from fastapi.websockets import WebSocketState

import isaacsim.core.utils.prims as prims_utils
from pydantic import BaseModel, Field
from wandelbots.omni.datatypes import (
    GhostObjectSource,
    GhostObject,
    WSPose,
    TCPSource,
)
from wandelbots.omni.utils.prims import PrimUtils


from wandelbots.omni.utils.teaching import GhostObjectUtils

teaching_router = APIRouter(prefix="/teaching", tags=["Teaching"])


def validate_prim_path_query(name="prim_path") -> str:
    def validator(
        prim_path: str = Query(..., description="Prim path of the object", alias=name),
    ) -> str:
        if not PrimUtils.is_prim_valid(prim_path):
            raise HTTPException(404, detail=f"Invalid prim path: {prim_path}")
        return prim_path

    return validator


@teaching_router.get(
    path="/ghost-objects/sources",
    operation_id="list_ghost_object_sources",
    response_model=list[GhostObjectSource],
    responses={
        200: {"description": "Successfully retrieved the ghost objects"},
        500: {
            "description": "Internal server error: Unable to fetch ghost object sources from the scene"
        },
    },
)
def list_ghost_object_sources() -> list[GhostObjectSource]:
    """
    Return the prim paths of all prims that are sources for ghost objects i.e. tools.
    These ghost object sources must follow a strict predicate `tool_` and the source ghost must be created in the scene.
    Source ghost is created by default during robot creation
    """
    try:
        return GhostObjectUtils.get_ghost_object_sources()
    except Exception as e:
        raise HTTPException(
            500, f"Unable to fetch ghost object sources from the scene: {e}"
        )


@teaching_router.get(
    path="/tcps/sources",
    operation_id="list_tcp_sources",
    response_model=list[TCPSource],
    responses={
        200: {"description": "Successfully retrieved the tcp sources"},
        500: {
            "description": "Internal server error: Unable to fetch tcp sources from the scene"
        },
    },
)
def list_tcp_sources() -> list[TCPSource]:
    """
    Return the prim paths of all tcps that are defined in the scene which follows a strict predicate `tcp_` or 'TCP_'
    """
    try:
        return GhostObjectUtils.get_all_tcp_sources()
    except Exception as e:
        raise HTTPException(500, f"Unable to fetch tcp sources from the scene: {e}")


class CreateGhostObject(BaseModel):
    prim_path: str = Field(description="prim path of the object to clone")
    ref_pose: Optional[WSPose] = Field(
        None,
        description="The TCP pose to which the ghost object has to be attached",
    )
    tcp_prim_path: Optional[str] = Field(
        None,
        description="Prim path of the TCP which is used as ghost object transform origin",
    )


@teaching_router.post(
    path="/ghost-objects",
    operation_id="create_ghost_object",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses={
        204: {"description": "Successfully added ghost object to the scene"},
        404: {"description": "Invalid prim path"},
        422: {
            "description": "Source ghost object is not created. Make sure that the tool is in robot workspace and the robot is created"
        },
        500: {
            "description": "Internal server error: Unable to add ghost objects to the scene to the scene"
        },
    },
)
async def create_ghost_object(ghost_object_data: CreateGhostObject) -> None:
    """
    Create a ghost object from the prim under the specified path.
    This will clone the prim, apply the specified material and shift the origin of the prim.
    """
    if not prims_utils.is_prim_path_valid(ghost_object_data.prim_path):
        raise HTTPException(
            404, detail=f"Invalid prim path: {ghost_object_data.prim_path}"
        )

    tcp_prim_path = ghost_object_data.tcp_prim_path
    if tcp_prim_path and not prims_utils.is_prim_path_valid(tcp_prim_path):
        raise HTTPException(404, detail=f"Invalid TCP prim path: {tcp_prim_path}")

    try:
        GhostObjectUtils.add_ghost_object(
            prims_utils.get_prim_at_path(ghost_object_data.prim_path),
            ghost_object_data.ref_pose,
            tcp_prim=prims_utils.get_prim_at_path(tcp_prim_path)
            if tcp_prim_path
            else None,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        import traceback

        carb.log_error(traceback.format_exc())
        raise HTTPException(500, f"Unable to add ghost objects to the scene: {e}")


@teaching_router.delete(
    path="/ghost-objects",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="clear_ghost_objects",
    response_model=None,
    responses={204: {"description": "Successfully deleted specified ghost objects"}},
)
async def clear_ghost_objects(prim_path: str = None) -> None:
    """
    Remove all ghost objects
    """
    existing_ghost_paths: set[str] = {
        g.prim_path for g in GhostObjectUtils.get_ghost_objects()
    }

    if prim_path and prim_path not in existing_ghost_paths:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ghost object {prim_path} not found",
        )

    if prim_path:
        existing_ghost_paths = {prim_path}

    try:
        GhostObjectUtils.delete_ghost_objects(list(existing_ghost_paths))
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to delete ghost objects: {e}")


@teaching_router.get(
    path="/ghost-objects",
    operation_id="list_ghost_objects",
    response_model=list[GhostObject],
    responses={
        200: {"description": "Successfully fetched all the ghost objects in the scene"},
        500: {"description": "Unable to fetch all the ghost objects in the scene"},
    },
)
def list_ghost_objects(
    relative_to_prim: str = Query(
        None, description="Prim path to which the ghost object poses are relative"
    ),
) -> list[GhostObject]:
    """
    Fetches all ghost objects defined in the scene
    """
    try:
        return GhostObjectUtils.get_ghost_objects(relative_to_prim)
    except Exception as e:
        raise HTTPException(500, f"Unable to fetch ghost objects from the scene: {e}")


class GhostObjectsMessage(BaseModel):
    ghost_objects: list[GhostObject]


@teaching_router.websocket("/ghost-objects/track")
async def pose_tracker_websocket(
    websocket: WebSocket,
    interval: float = Query(
        1.0, ge=0.05, le=5.0, description="Time delay in seconds between updates"
    ),
    relative_to_prim: str = Query(
        None, description="Prim path to which the ghost object poses are relative"
    ),
) -> GhostObjectsMessage:
    """
    WebSocket endpoint that streams ghost object poses to connected clients.
    """
    await websocket.accept()
    carb.log_info("WebSocket connection accepted for ghost objects tracking")
    try:
        while True:
            ghost_objects_data = GhostObjectsMessage(
                ghost_objects=GhostObjectUtils.get_ghost_objects(relative_to_prim)
            )
            if websocket.client_state != WebSocketState.CONNECTED:
                carb.log_info("WebSocket connection closed")
                break
            await websocket.send_text(ghost_objects_data.model_dump_json())
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        carb.log_info("WebSocket connection closed by client.")
    except Exception as e:
        carb.log_error(f"Unexpected error in websocket connection: {e}")
