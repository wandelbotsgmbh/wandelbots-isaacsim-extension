import omni.usd
import omni.ui
from pxr import UsdGeom
from fastapi.exceptions import HTTPException
from fastapi import status
import omni.usd
import carb.settings
from wandelbots.omni.datatypes import STAGE_UNITS, UsdStageModel
from wandelbots.omni.utils.scene import SceneUtils

from fastapi import APIRouter

scene_router = APIRouter(prefix="/scene", tags=["scene"])


@scene_router.post(
    "/open_stage",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="open_stage",
    response_model=None,
)
async def open_stage(data: UsdStageModel) -> None:
    """
    Opens the stage given path or uri of the scene
    Args:
        data: the uri of the scene

    Returns:
        None
    """
    success, error = await omni.usd.get_context().open_stage_async(data.uri)
    if not success:
        raise HTTPException(404, f"Failed to open - {data.uri}")


@scene_router.get(
    "/get_current_stage", operation_id="get_current_stage", response_model=str
)
async def get_current_stage() -> str:
    """
    Fetches the current active stage in the scene
    Returns:
        the uri or path of the scene

    """
    uri = omni.usd.get_context().get_stage_url()
    return uri


@scene_router.get(
    path="/get_stage_units", operation_id="get_stage_units", response_model=STAGE_UNITS
)
async def get_stage_units() -> STAGE_UNITS:
    """
    Fetches the units set for a scene in the stage
    Returns:
        the stage units in one of these units - ["mm", "cm", "m"]

    """
    stage = omni.usd.get_context().get_stage()
    stage_unit = UsdGeom.GetStageMetersPerUnit(stage)
    if stage_unit == 1:
        return "m"
    elif stage_unit == 0.01:
        return "cm"
    elif stage_unit == 0.001:
        return "mm"
    else:
        raise ValueError("Stage units are not in [mm, cm, m]")


@scene_router.post(
    path="/set_stage_units",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_stage_units",
    response_model=None,
)
async def set_stage_units(unit_type: STAGE_UNITS) -> None:
    """
    Sets units for the stage
    Args:
        unit_type: unit type to be set for a scene in ["mm", "cm", "m"]

    Returns:

    """
    # ToDo: Process stage after setting units
    stage = omni.usd.get_context().get_stage()
    if unit_type == "m":
        UsdGeom.SetStageMetersPerUnit(stage, 1)
    elif unit_type == "cm":
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    elif unit_type == "mm":
        UsdGeom.SetStageMetersPerUnit(stage, 0.001)
    else:
        raise HTTPException(404, "Unable to set stage units are not in [mm, cm, m]")


@scene_router.post(
    path="/show_only_viewport",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="show_only_viewport",
    response_model=None,
    deprecated=True,
)
async def show_only_viewport(state: bool = True) -> None:
    """
    Toggles between full screen mode of viewport and the original view
    Args:
        state: a bool variable which tells if only viewport should be the visible

    Returns:
        None
    """
    settings = carb.settings.get_settings()
    settings.set("/app/window/hideUi", state)


@scene_router.post(
    path="/play_simulation",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="play_simulation",
    response_model=None,
)
async def play_simulation() -> None:
    """
    Starts/plays the simulation
    Returns:
        None
    """
    simulation_context, is_playing = await SceneUtils.check_simulation()
    simulation_context.play()


@scene_router.post(
    path="/stop_simulation",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="stop_simulation",
    response_model=None,
)
async def stop_simulation() -> None:
    """
    Stops the simulation in the scene
    Returns:
        None

    """
    simulation_context, is_playing = await SceneUtils.check_simulation()
    simulation_context.stop()


@scene_router.post(
    path="/pause_simulation",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="pause_simulation",
    response_model=None,
)
async def pause_simulation() -> None:
    """
    Pauses the simulation in the scene
    Returns:
        None

    """
    simulation_context, is_playing = await SceneUtils.check_simulation()
    simulation_context.pause()


@scene_router.post(
    path="/is_simulation_running",
    operation_id="is_simulation_running",
    response_model=bool,
)
async def is_simulation_running() -> bool:
    """
    Fetches if the simulation is running or stopped
    Returns:
        if the simulation is either playing (1) or is stopped (0)

    """
    simulation_context, _is_playing = await SceneUtils.check_simulation()
    return _is_playing
