from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, status
from fastapi.exceptions import HTTPException
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    MotionGroupService,
    get_motion_group_service,
    MotionStreamConfiguration,
)
from pxr import Usd
import carb

import isaacsim.core.utils.stage as stage_utils

motion_groups_router = APIRouter(
    prefix="/manipulators/motion-groups", tags=["Manipulators (Motion-Group)"]
)

MotionGroupServiceDep = Annotated[MotionGroupService, Depends(get_motion_group_service)]


def find_motion_group_or_raise(
    prim_path: str, motion_group_service: MotionGroupServiceDep
) -> str | NoReturn:
    prim: Usd.Prim = stage_utils.get_current_stage().GetPrimAtPath(prim_path)

    if prim.IsValid():
        if not motion_group_service.has_motion_group(prim_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Motion-Group for prim_path='{prim_path}' not found",
            )
    else:
        # Find prim by their ID, use first motion_group found
        # !DEPRECATED!
        motion_group_prim_configurations = [
            motion_group_service.get_motion_group_configuration(prim_path)
            for prim_path in motion_group_service.get_all_motion_group_prim_paths()
        ]
        for config in motion_group_prim_configurations:
            if not config:
                continue
            if config.motion_stream_configuration.motion_group == prim_path:
                carb.log_verbose(
                    f"Using motion group '{prim_path}' instead of prim_path"
                )
                return config.prim_path

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Motion-Group for motion_group='{prim_path}' not found",
        )
    return prim_path


MotionGroupPrimPath = Annotated[str, Depends(find_motion_group_or_raise)]


@motion_groups_router.post(
    path="",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="create_motion_group",
    response_model=None,
    responses={
        status.HTTP_204_NO_CONTENT: {
            "description": "Motion-Group created. The connection will be established when the simulation is playing",
        },
        status.HTTP_400_BAD_REQUEST: {
            "description": "Create motion_group is not possible with this configuration"
        },
        status.HTTP_409_CONFLICT: {
            "description": "A motion_group with this id already exists"
        },
    },
)
async def create_motion_group(
    configuration: MotionGroupConfiguration,
    motion_group_service: MotionGroupServiceDep,
) -> None:
    """
    Create and link a motion_group to a motion stream
    """
    if motion_group_service.has_motion_group(configuration.prim_path):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{configuration.prim_path} is already created. Please delete it first to create a new motion_group",
        )

    try:
        await motion_group_service.create_motion_group(configuration)
    except RuntimeError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Failed to create motion_group: {str(e)}"
        ) from e
    except ValueError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Failed to create motion_group: {str(e)}"
        ) from e


@motion_groups_router.put(
    path="/{prim_path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="update_motion_group_stream",
    response_model=None,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Motion-Group not found"},
        status.HTTP_400_BAD_REQUEST: {
            "description": "Create motion_group is not possible with this configuration"
        },
    },
)
async def update_motion_group_motion_stream(
    prim_path: MotionGroupPrimPath,
    configuration: MotionStreamConfiguration,
    motion_group_service: MotionGroupServiceDep,
) -> None:
    """
    Update motion_group motion stream configuration.
    While it is possible to update the motion while simulating, its not guaranteed that all relations will directly pick up the new config
    """

    try:
        await motion_group_service.update_motion_group_stream_configuration(
            prim_path, configuration
        )
    except RuntimeError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Failed to create motion_group: {str(e)}"
        ) from e


@motion_groups_router.get(
    path="",
    operation_id="list_motion_groups",
    response_model=dict[str, MotionGroupConfiguration],
)
async def list_motion_groups(
    motion_group_service: MotionGroupServiceDep,
) -> dict[str, MotionGroupConfiguration]:
    """
    Fetches all the motion_groups configured in the scene
    """
    return dict(
        (
            prim_path,
            motion_group_service.get_motion_group_configuration(prim_path),
        )
        for prim_path in motion_group_service.get_all_motion_group_prim_paths()
    )


@motion_groups_router.get(
    path="/{prim_path:path}",
    operation_id="get_motion_group",
    response_model=MotionGroupConfiguration,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Motion-Group not found"}},
)
async def get_motion_group(
    motion_group_service: MotionGroupServiceDep, prim_path: MotionGroupPrimPath
) -> MotionGroupConfiguration:
    """
    Get the configuration of a motion_group
    """
    return motion_group_service.get_motion_group_configuration(prim_path)


@motion_groups_router.delete(
    path="/{prim_path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="remove_motion_group",
    response_model=None,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Motion-Group not found"}},
)
async def remove_motion_group(
    prim_path: MotionGroupPrimPath,
    motion_group_service: MotionGroupServiceDep,
) -> None:
    """
    Remove a motion_group
    """
    await motion_group_service.remove_motion_group(prim_path)


@motion_groups_router.delete(
    path="",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="clear_motion_groups",
    response_model=None,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Motion-Group not found"},
    },
)
async def clear_motion_groups(motion_group_service: MotionGroupServiceDep) -> None:
    """
    Removes all motion_groups
    """

    for prim_path in list(motion_group_service.get_all_motion_group_prim_paths()):
        await motion_group_service.remove_motion_group(prim_path)
