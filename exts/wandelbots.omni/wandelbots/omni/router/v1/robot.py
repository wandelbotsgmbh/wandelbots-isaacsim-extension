from typing import Dict, Any
from fastapi import status
from fastapi.exceptions import HTTPException
from wandelbots.omni.core.robot import ConfigurableRobot
from wandelbots.omni.environment import host_database
from pxr import UsdPhysics, Sdf
import omni.isaac.core.utils.stage as stage_utils
from fastapi import APIRouter
import carb
import omni.isaac.core.utils.prims as prims_utils
from wandelbots.omni.router.v1.stream import delete_stream
from wandelbots.omni.router.v1.tool import delete_tool
from wandelbots.omni.utils.ghost_teaching import (
    get_possible_ghost_object_sources,
    add_source_ghost_object,
)

robot_router = APIRouter(prefix="/robot", tags=["robot"])


@robot_router.post(
    path="/create_robot",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="create_robot",
    response_model=None,
)
async def create_robot(configuration: ConfigurableRobot.Configuration) -> None:
    """
    Create a robot using the given configuration
    Args:
        configuration: robot configuration

    Returns:
        None

    """
    if configuration.identifier in host_database["robots"]:
        raise HTTPException(
            404,
            f"{configuration.identifier} is already created. Please delete it first to create a new robot",
        )

    try:
        robot_instance = ConfigurableRobot(configuration)
    except Exception as e:
        raise HTTPException(404, f"Invalid configuration: {str(e)}") from e

    try:
        await robot_instance.check_connection()
    except Exception as e:
        raise HTTPException(
            404,
            f"{configuration.controller_id} is not reachable from {configuration.host}: {str(e)}",
        )

    host_database[f"robots.{configuration.identifier}.configuration"] = (
        robot_instance.to_dict
    )
    host_database[f"robots.{configuration.identifier}.instance"] = robot_instance

    # Experimental: add ghost object sources
    all_ghost_object_sources = set(
        each.prim_path for each in get_possible_ghost_object_sources()
    )
    child_prims = set(
        prim.GetPath().pathString
        for prim in prims_utils.get_all_matching_child_prims(
            Sdf.Path(configuration.prim_path).GetParentPath(), lambda _: True
        )
    )

    for ghost_path in all_ghost_object_sources.intersection(child_prims):
        await add_source_ghost_object(ghost_path)


@robot_router.get(
    path="/get_all_robots", operation_id="get_all_robots", response_model=Dict[str, Any]
)
async def get_all_robots() -> Dict[str, Any]:
    """
    Fetches all the robots configured in the scene
    Returns:
        a dict of robots along with their configurations

    """
    all_robots = {}
    for each in host_database["robots"]:
        all_robots.update({each: host_database[f"robots.{each}.configuration"]})
    return all_robots


@robot_router.delete(
    path="/delete_robot",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_robot",
    response_model=None,
)
async def delete_robot(robot_name: str) -> None:
    """
    Delete a specific configured robot.
    This involves the following steps:
        - Delete all streams connected to the robot
        - Delete all tools connected to the robot
        - Delete the robot
    Args:
        robot_name: the name of the robot

    Returns:
        None

    """
    if robot_name not in host_database["robots"]:
        raise HTTPException(404, f"{robot_name} is not configured yet")

    # Obtain all streams connected to the robot and delete them
    # (we cannot mutate the dict while iterating, so we do this in two steps)
    streams_to_delete = [
        stream_name
        for stream_name in host_database["streams"]
        if host_database.get(f"streams.{stream_name}.configuration.robot") == robot_name
    ]
    for stream_name in streams_to_delete:
        await delete_stream(stream_name)

    # Delete all tools connected to the robot
    tools_to_delete = [
        tool_name
        for tool_name in host_database["tools"]
        if host_database.get(f"tools.{tool_name}.configuration.robot") == robot_name
    ]
    for tool_name in tools_to_delete:
        await delete_tool(tool_name)

    # Delete the robot
    del host_database[f"robots.{robot_name}"]


@robot_router.delete(
    path="/delete_all_robots",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_all_robots",
    response_model=None,
)
async def delete_all_robots() -> None:
    """
    Deletes all the robots configured in the scene
    Returns:
        None

    """
    if "robots" in host_database:
        robot_names = [robot_name for robot_name in host_database["robots"]]
        for robot_name in robot_names:
            await delete_robot(robot_name)

        del host_database["robots"]


@robot_router.get(
    path="/get_connected_tools",
    operation_id="get_connected_tools",
    response_model=list[str],
)
async def get_connected_tools(robot_name: str) -> list[str]:
    """
    Fetches all the connected tools to a specific robot
    Args:
        robot: the name of the robot

    Returns:
        a list of all connected tools for a robot

    """
    if "robots" in host_database:
        try:
            robot_instance = host_database[f"robots.{robot_name}.instance"]
            connected_tools = robot_instance.connected_tools
            return connected_tools
        except Exception as e:
            carb.log_error(f"Error: {e}")
            raise HTTPException(404, f"{robot_name} is not configured yet.")


@robot_router.get(
    path="/get_robot_data_from_scene",
    operation_id="get_robot_data_from_scene",
    response_model=list[str],
)
async def get_robot_data_from_scene() -> list[str]:
    """
    Fetches all the robot prim paths in the scene
    Returns:
        list of robot prim paths

    """
    robots_data = [
        prim.GetPrimPath().pathString
        for prim in stage_utils.traverse_stage()
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI)
    ]
    return robots_data
