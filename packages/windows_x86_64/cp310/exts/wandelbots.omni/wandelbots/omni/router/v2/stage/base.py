from typing import Literal

import omni.usd
import omni.ui
from pxr import UsdGeom
import omni.usd
from pydantic import BaseModel, Field, RootModel
from wandelbots.omni.datatypes import UsdStageModel
from wandelbots.omni.manipulators import get_scene_motion_group_prim_paths
import omni.timeline

from typing import Annotated
import yaml
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
    Body,
)
from wandelbots.omni.environment import host_database
from wandelbots.omni.manipulators import (
    MotionGroup,
    MotionGroupService,
    get_motion_group_service,
)
from wandelbots.omni.utils.data import format_object_for_export


stage_router = APIRouter(prefix="/stage", tags=["Stage"])
TimelineState = Literal["playing", "paused", "stopped"]
TimelineAction = Literal["play", "pause", "stop"]


class SimulationState(BaseModel):
    timeline: TimelineState = Field(
        default=None, description="State simulation timeline"
    )


class StageUnits(BaseModel):
    meters_per_unit: float = Field(
        default=1.0, description="Unit for length (m/unit) e.g 10cm = 0.01"
    )


@stage_router.put(
    "/scene",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="open_scene",
    response_model=None,
    responses={
        204: {"description": "Successfully loaded the scene"},
        500: {"description": "Unable to load scene from the USD file"},
    },
)
async def open_scene(
    data: UsdStageModel = Body(..., description="the uri of the scene"),
) -> None:
    """
    Opens the scene given path or uri of the scene
    """
    success, error = await omni.usd.get_context().open_stage_async(data.uri)
    if not success:
        raise HTTPException(500, f"{error}")


@stage_router.get(
    "/scene",
    operation_id="get_active_scene",
    response_model=str,
    responses={
        200: {"description": "Successfully retrieved the active scene"},
        500: {"description": "Unable to fetch active scene"},
    },
)
async def get_active_scene() -> str:
    """
    Fetches the current active scene in the scene
    """
    try:
        uri = omni.usd.get_context().get_stage_url()
        return uri
    except Exception as e:
        raise HTTPException(500, f"Unable to fetch active scene : {e}")


# Openapi generator would generate list[Optional[str]] if not enforced with this model
class StageMotionGroupsResponse(RootModel):
    root: list[str]


@stage_router.get(
    path="/motion-groups",
    operation_id="list_stage_motion_groups",
    response_model=StageMotionGroupsResponse,
)
async def list_stage_motion_groups() -> StageMotionGroupsResponse:
    """
    Fetches all the robot prim paths in the scene

    Returns:
        list of robot prim paths
    """
    return get_scene_motion_group_prim_paths()


@stage_router.get(
    path="/units",
    operation_id="get_stage_units",
    responses={
        200: {"description": "Successfully retrieved the scene units"},
        422: {"description": "Stage units are not supported"},
        500: {"description": "Internal server error"},
    },
)
async def get_stage_units() -> StageUnits:
    """
    Retrieves the current unit scale of the scene.
    """
    try:
        return StageUnits(
            meters_per_unit=UsdGeom.GetStageMetersPerUnit(
                omni.usd.get_context().get_stage()
            ),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unable to fetch scene units: {e}")


@stage_router.put(
    path="/units",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="update_stage_units",
    response_model=None,
    responses={
        204: {"description": "Successfully set the scene units"},
        500: {"description": "Unable to set scene units"},
    },
)
async def update_stage_units(
    scene_units: StageUnits,
) -> None:
    """
    Sets units for the scene
    """
    try:
        # ToDo: Process scene after setting units
        UsdGeom.SetStageMetersPerUnit(
            omni.usd.get_context().get_stage(), scene_units.meters_per_unit
        )

    except Exception as e:
        raise HTTPException(500, f"Unable to set scene units: {e}")


def apply_timeline_state(state: TimelineState):
    timeline = omni.timeline.get_timeline_interface()
    if state == "playing":
        timeline.play()
    if state == "paused":
        timeline.pause()
    if state == "stopped":
        timeline.stop()


@stage_router.patch(
    path="/simulation/timeline/{action}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="timeline_action",
    response_model=None,
    responses={
        status.HTTP_204_NO_CONTENT: {
            "description": "Successfully set the simulation state"
        },
    },
)
async def timeline_action(
    action: TimelineAction,
) -> None:
    """
    Controls the simulation in the scene

    - `play`: starts the simulation
    - `pause`: pauses the simulation
    - `stop`: stops the simulation
    """
    try:
        timeline = omni.timeline.get_timeline_interface()
        getattr(timeline, action)()
    except Exception as e:
        raise HTTPException(500, f"Failed to change simulation state - {e}")


def get_timeline_state() -> TimelineState:
    timeline = omni.timeline.get_timeline_interface()
    if timeline.is_playing():
        return "playing"
    if timeline.is_stopped():
        return "stopped"
    if not timeline.is_playing():
        return "paused"


@stage_router.get(
    path="/simulation",
    operation_id="simulation_state",
    response_model=SimulationState,
    responses={
        status.HTTP_200_OK: {"description": "Successfully retrieved simulation status"},
    },
)
async def simulation_state() -> bool:
    """
    Fetches if the simulation is running or stopped as a boolean
    """
    try:
        return SimulationState(timeline=get_timeline_state())
    except Exception as e:
        raise HTTPException(500, f"Failed to check simulation state - {e}")


data_router = APIRouter(prefix="/data", tags=["Data"])

RobotServiceDep = Annotated[MotionGroupService, Depends(get_motion_group_service)]


@stage_router.post(
    path="/configuration",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="import_configuration",
    response_model=None,
    responses={
        204: {"description": "Configuration loaded successfully"},
        400: {
            "description": "Invalid yaml file format or invalid configuration values for omniservice configuration"
        },
        500: {
            "description": "Internal server error: Configuration could not be applied"
        },
    },
)
async def import_configuration(
    robot_service: RobotServiceDep,
    file: UploadFile = File(
        ...,
        description="The file containing configuration for the scene in YAML format.",
    ),
) -> None:
    """
    Configures the scene using the uploaded configuration. It can have the following keys:

    - `scene`: uri of the scene which has to be loaded
    - `robots`: the robots which you want to configure
    - `streams`: the streams which you want to configure
    """
    # ToDo: verify this functionality after setting periphery
    try:
        yaml_content = await file.read()
        config = yaml.safe_load(yaml_content.decode("utf-8"))
    except Exception as e:
        raise HTTPException(
            400, f"Invalid yaml file format for omniservice configuration: {e}"
        )

    try:
        # load scene
        uri = config.get("scene", {}).get("uri")
        if uri:
            success, error = await omni.usd.get_context().open_stage_async(uri)
            if not success:
                raise HTTPException(404, f"Failed to open - {uri}")

        # configure robots
        for identifier, val in config.get("robots", {}).items():
            config_data: MotionGroup.Configuration = val.get("configuration", {})
            config_data.identifier = identifier
            await robot_service.create_robot(config_data)

    except Exception as e:
        if "404" in e:
            raise HTTPException(status_code=404, detail=f"Resource not found: {e}")
        elif "401" in e:
            raise HTTPException(status_code=401, detail=f"Unauthorized: {e}")
        else:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


@stage_router.get(
    path="/configuration",
    operation_id="export_configuration",
    response_model=None,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Configuration exported successfully"},
        500: {"description": "Could not export configuration"},
    },
)
async def export_configuration():
    """
    Exports the configured scene setup as a `.yaml` file
    """
    try:
        # Define keys to remove from exported configuration
        keys_to_remove = {"instance", "identifier", "default_poses", "scene"}
        config_data = format_object_for_export(host_database.data, keys_to_remove)

        yaml_data = yaml.dump(config_data, sort_keys=False)
        return Response(
            content=yaml_data,
            media_type="text/yaml",
            headers={
                "Content-Disposition": 'attachment; filename="omniservice_config.yaml"'
            },
        )

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Could not export configuration: {e}"
        )
