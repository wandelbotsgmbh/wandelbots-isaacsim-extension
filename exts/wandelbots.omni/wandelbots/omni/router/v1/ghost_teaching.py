import asyncio
import carb

import omni.usd
import omni.isaac.core.utils.stage as stage_utils
from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Query,
    status,
    Body,
)
from fastapi.websockets import WebSocketState

import omni.isaac.core.utils.prims as prims_utils
from wandelbots.omni.datatypes import (
    GhostObjectSource,
    GhostObject,
    WSPose,
    TCPSource,
)

from wandelbots.omni.utils.ghost_teaching import (
    register_ghost_object,
)

from wandelbots.omni.utils.prim_utils import PrimUtils
from pxr import Sdf

from wandelbots.omni.utils.ghost_teaching import (
    get_robot_prim_path,
    get_all_tcp_sources,
)

ghost_teaching_router = APIRouter(prefix="/ghost_teaching", tags=["ghost_teaching"])


@ghost_teaching_router.get(
    path="/ghost_object_sources", operation_id="get_ghost_object_sources"
)
def get_ghost_object_sources() -> list[GhostObjectSource]:
    """
    Return the prim paths of all prims that are sources for ghost objects i.e. tools.
    These ghost object sources must follow a strict predicate `tool_` and the source ghost must be created in the scene.
    Source ghost is created by default during robot creation
    """
    ghost_objects = []
    for prim in stage_utils.traverse_stage():
        custom_data = prim.GetCustomData()
        if (
            custom_data
            and "metadata" in custom_data
            and "is_ghost" in custom_data["metadata"]
            and custom_data["metadata"]["source_ghost"]
        ):
            ghost_objects.append(
                GhostObjectSource(
                    name=prim.GetPrimPath().pathString.split("/")[-1],
                    prim_path=prim.GetPrimPath().pathString,
                )
            )

    return ghost_objects


@ghost_teaching_router.get(path="/tcp_sources", operation_id="tcp_sources")
def get_tcp_sources() -> list[TCPSource]:
    """
    Return the prim paths of all tcps that are defined in the scene which follows a strict predicate `tcp_` or 'TCP_'
    """
    return get_all_tcp_sources(base_prim_path="/")


@ghost_teaching_router.post(
    path="/ghost_object",
    operation_id="create_ghost_object",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def add_ghost_object(
    prim_path: str = Query(..., description="The prim path of the object to clone"),
    ref_pose: WSPose = Body(
        None, description="The TCP pose to which the ghost object has to be attached"
    ),
) -> None:
    """
    Create a ghost object from the prim under the specified path.
    This will clone the prim, apply the specified material and shift the origin of the prim.
    """
    ghost_base_path = "/".join(prim_path.split("/")[:-2])
    ghost_object_name = prim_path.split("/")[-1]

    clone_prim_path = f"{ghost_base_path}/poses/{ghost_object_name}"
    target_path = stage_utils.get_next_free_path(clone_prim_path)

    stage = omni.usd.get_context().get_stage()
    source_prim = stage.GetPrimAtPath(prim_path)

    # Find the first available tcp when traversed
    ghost_object_source_path = next(
        (
            ghost.prim_path
            for ghost in get_ghost_object_sources()
            if ghost.prim_path == prim_path
        ),
        None,
    )

    if ghost_object_source_path is None:
        raise ValueError("Source ghost object is not created.")
    try:
        Sdf.CopySpec(
            stage.GetRootLayer(),
            ghost_object_source_path,
            stage.GetRootLayer(),
            target_path,
        )
    except Exception:
        raise HTTPException(
            422,
            "Source ghost object is not created. Make sure that the tool is in robot workspace and the robot is created",
        )

    # set visibility
    target_prim = stage.DefinePrim(target_path, source_prim.GetTypeName())
    visibility_attribute = target_prim.GetAttribute("visibility")
    visibility_attribute.Set("inherited")

    # register prim
    register_ghost_object(target_prim, source_ghost=False)

    # set ghost object to active TCP pose
    if ref_pose:
        PrimUtils.set_pose(target_path, ref_pose)


@ghost_teaching_router.delete(
    path="/ghost_object",
    operation_id="delete_ghost_object",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_ghost_object(
    prim_path: str = Query(..., description="The prim path of the object to remove"),
):
    """
    Removes or deletes a ghost object from the scene
    """
    # Check if a ghost object with the specified prim path exists
    ghost_objects = get_ghost_objects()
    for each in ghost_objects:
        if each.prim_path == prim_path:
            prims_utils.delete_prim(prim_path)
            return
    raise HTTPException(404, "Given prim path is not a valid ghost object")


@ghost_teaching_router.delete(
    path="/all_ghost_objects",
    operation_id="delete_all_ghost_objects",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_all_ghost_objects():
    """
    Removes or delete all ghost objects from the scene
    """
    # Check if a ghost object with the specified prim path exists
    ghost_objects = get_ghost_objects()
    for each in ghost_objects:
        prims_utils.delete_prim(each.prim_path)


@ghost_teaching_router.get(path="/ghost_objects", operation_id="get_ghost_objects")
def get_ghost_objects() -> list[GhostObject]:
    """
    Fetches all ghost objects defined in the scene
    """
    ghost_objects = []
    for prim in stage_utils.traverse_stage():
        custom_data = prim.GetCustomData()

        # Check if the prim has metadata and 'is_ghost' object is present
        if (
            custom_data
            and "metadata" in custom_data
            and "is_ghost" in custom_data["metadata"]
            and not custom_data["metadata"]["source_ghost"]
        ):
            path = prim.GetPrimPath().pathString
            name = prim.GetPrimPath().pathString.split("/")[-1]
            ws_pose = PrimUtils.get_pose(path)
            robot_prim_path = get_robot_prim_path(prim)
            ghost_objects.append(
                GhostObject(
                    prim_path=path,
                    name=name,
                    robot_prim_path=robot_prim_path,
                    pose=ws_pose,
                )
            )

    return ghost_objects


@ghost_teaching_router.post(
    path="/select_ghost_object",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="select_ghost_object",
    response_model=None,
)
async def select_ghost_object(prim_path: str) -> None:
    """
    Select the ghost object by its prim_path
    Args:
        prim_path: the path of the prim to select in the scene

    Returns:
        None
    """
    PrimUtils.select_object(prim_path)


@ghost_teaching_router.websocket("/track_ghost_objects")
async def pose_tracker_websocket(websocket: WebSocket) -> None:
    """
    Pose tracker websocket connection
    """
    await websocket.accept()

    try:
        while True:
            ghost_object_dicts = [
                ghost_object.dict() for ghost_object in get_ghost_objects()
            ]
            if websocket.client_state != WebSocketState.CONNECTED:
                carb.log_info("Websocket connection closed")
                break
            await websocket.send_json(ghost_object_dicts)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        carb.log_error(f"Unexpected error in websocket connection: {e}")
