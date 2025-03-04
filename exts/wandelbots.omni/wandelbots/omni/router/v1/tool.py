from typing import Dict, List, Union
import numpy as np
from fastapi import status, Body
import omni.timeline
from fastapi.exceptions import HTTPException
import omni.isaac.core.utils.stage as stage_utils
from wandelbots.omni.core.tools import (
    ConfigurableTool,
    Conveyor,
    ArticulationChain,
    SurfaceGripper,
    AnalogArticulationChain,
)
from wandelbots.omni.environment import host_database
from wandelbots.omni.datatypes import MockAnalogSignal

from fastapi import APIRouter
import carb

tool_router = APIRouter(prefix="/tool", tags=["tool"])


def fetch_all_tool_configurations() -> tuple[ConfigurableTool.Configuration, ...]:
    configurations = []
    for tool_name in ConfigurableTool.tools_registry:
        tool_cls = ConfigurableTool.tools_registry.get(tool_name)
        configurations.append(tool_cls.Configuration)
    return tuple(configurations)


def fetch_all_tools() -> tuple[ConfigurableTool, ...]:
    tools = []
    for tool_name in ConfigurableTool.tools_registry:
        tool_cls = ConfigurableTool.tools_registry.get(tool_name)
        tools.append(tool_cls)
    return tuple(tools)


@tool_router.get(
    path="/get_all_available_tools",
    operation_id="get_all_available_tools",
    response_model=List[str],
)
async def get_all_available_tools() -> List[str]:
    """
    Lists all the tools which are configurable
    Returns:
        a list of configurable tools
    """
    return list(ConfigurableTool.tools_registry.keys())


@tool_router.post(
    path="/create_tools",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="create_tools",
    response_model=None,
)
async def create_tools(
    configuration: List[Union[fetch_all_tool_configurations()]] = Body(),
) -> None:
    """
    Create tools given configuration
    Args:
        configuration: tool configuration

    Returns:
        None

    """
    for each in configuration:
        if each.identifier in host_database["tools"]:
            raise HTTPException(
                404,
                f"{each.identifier} is already created. Please delete it first to create a new tool",
            )

    tool_names = list(ConfigurableTool.tools_registry.keys())
    for tool_config in configuration:
        if tool_config.type not in tool_names:
            raise HTTPException(
                404,
                f"{tool_config.type} is not in list of available tools. Choose one in {tool_names}",
            )

        try:
            tool_instance = ConfigurableTool.tools_registry[tool_config.type](
                tool_config
            )
        except ValueError as e:
            raise HTTPException(404, str(e))

        try:
            robot_instance = host_database[f"robots.{tool_config.robot}.instance"]
            robot_instance.connect_tools(tool_config.identifier)
        except Exception as e:
            raise HTTPException(
                404,
                f"Robot {tool_config.robot} associated with tool {tool_config.identifier} not created yet",
            ) from e

        host_database[f"tools.{tool_config.identifier}.configuration"] = (
            tool_instance.to_dict
        )
        host_database[f"tools.{tool_config.identifier}.instance"] = tool_instance


@tool_router.delete(
    path="/delete_tool",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_tool",
    response_model=None,
)
async def delete_tool(tool_name: str) -> None:
    """
    Delete a specific tool
    Args:
        tool_name: name of the tool

    Returns:
        None
    """
    if tool_name in host_database["tools"]:
        del host_database[f"tools.{tool_name}"]
    else:
        raise HTTPException(404, f"{tool_name} is not configured yet")


@tool_router.get(
    path="/get_all_tools",
    operation_id="get_all_tools",
    response_model=Dict[
        str,
        Union[
            Conveyor.Configuration,
            ArticulationChain.Configuration,
            SurfaceGripper.Configuration,
            AnalogArticulationChain.Configuration,
        ],
    ],
)
async def get_all_tools() -> Dict[
    str,
    Union[
        Conveyor.Configuration,
        ArticulationChain.Configuration,
        SurfaceGripper.Configuration,
        AnalogArticulationChain.Configuration,
    ],
]:
    """
    Fetches all the tools configured
    Returns:
        a dictionary of tools configured along with their configurations

    """
    all_tools = {}
    for each in host_database["tools"]:
        all_tools.update({each: host_database[f"tools.{each}.configuration"]})
    return all_tools


@tool_router.delete(
    path="/delete_all_tools",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="remove_all_tools",
    response_model=None,
)
async def delete_all_tools() -> None:
    """
    Removes or deletes all the configured tools
    Returns:
        None

    """
    if "tools" in host_database:
        del host_database["tools"]


@tool_router.post(
    path="/set_tool_state",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_tool_state",
    response_model=None,
)
async def set_tool_state(tool_identifier: str, mode: str) -> None:
    """
    Sets tool state based on the given mode. Can set state only for tools with digital modes
    Args:
        tool_identifier: name of the tool configured
        mode: mode defined in the corresponding tool states

    Returns:
        None
    """
    if "instance" in host_database[f"tools.{tool_identifier}"]:
        tool = host_database[f"tools.{tool_identifier}.instance"]
    else:
        raise HTTPException(
            404, f"{tool_identifier} is not created yet. Create tool first"
        )

    timeline = omni.timeline.get_timeline_interface()
    if not timeline.is_playing():
        raise HTTPException(404, "Simulation has to be started first")

    if mode not in tool.modes:
        raise HTTPException(
            404, f"Use have to use one of the available tool states {tool.modes}"
        )

    try:
        tool.reinitialize()
        tool.set_tool_state(mode)
    except ValueError as e:
        raise HTTPException(400, f"Bad request: {str(e)}") from e
    except Exception as e:
        raise HTTPException(500, f"An error occurred: {str(e)}") from e


@tool_router.post(
    path="/set_analog_tool_signals",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_analog_tool_signals",
    response_model=None,
)
async def set_analog_tool_signals(
    tool_identifier: str, mock_signals: list[MockAnalogSignal]
) -> None:
    """
    Performs tool action based on the given signal mapping for tools with analog states
    Args:
        tool_identifier: name of the tool configured
        mock_signals: the signal mapping to act on tool positions

    Returns:

    """
    if "instance" in host_database[f"tools.{tool_identifier}"]:
        tool = host_database[f"tools.{tool_identifier}.instance"]
    else:
        raise HTTPException(
            404, f"{tool_identifier} is not created yet. Create tool first"
        )

    timeline = omni.timeline.get_timeline_interface()
    if not timeline.is_playing():
        raise HTTPException(404, "Simulation has to be started first")

    try:
        tool.reinitialize()
        mock_signals = [each.dict() for each in mock_signals]
        tool.on_io_stream_message(mock_signals)
    except ValueError as e:
        raise HTTPException(400, f"Bad request: {str(e)}") from e
    except Exception as e:
        raise HTTPException(500, f"An error occurred: {str(e)}") from e


@tool_router.get(
    path="/get_tool_data_from_scene",
    operation_id="get_tool_data_from_scene",
    response_model=dict[str, list[str]],
)
async def get_tool_data_from_scene() -> dict[str, list[str]]:
    """
    Fetches all the tool prim paths from the scene
    Returns:
        dict of tool prim paths in the scene along with their types

    """
    available_tools = await get_all_available_tools()
    tools_data = {each: [] for each in available_tools}
    for prim in stage_utils.traverse_stage():
        custom_data = prim.GetCustomData()
        # Check if the prim has metadata and category is tool
        if (
            custom_data
            and "metadata" in custom_data
            and "category" in custom_data["metadata"]
        ):
            custom_data = prim.GetCustomData()["metadata"]
            # Check if the category is tool
            if custom_data["category"] == "tool":
                # Check if the type of the tool is in the available tools
                if custom_data["type"] not in available_tools:
                    carb.log_warn(
                        f"Tool at {prim.GetPrimPath().pathString} has a different type than one of {available_tools}."
                        f"Either re-register or deregister tool"
                    )
                    # Skip the tool if it is not registered
                    continue
                # Append the prim path to the corresponding tool type
                tools_data[custom_data["type"]].append(prim.GetPrimPath().pathString)
    return tools_data


@tool_router.post(
    path="/determine_analog_signals",
    operation_id="determine_analog_signals",
    response_model=list[float],
)
async def determine_analog_signals(
    tool_name: str, tool_positions: list[float]
) -> list[float]:
    """
    Determines analog signal values based on tool configuration and corresponding joint positions
    Args:
        tool_name: the name of the tool
        tool_positions: the joint positions for which corresponding analog signals have to be determined

    Returns:
        a list of analog values which correspond to the given tool positions

    """
    if tool_name in host_database["tools"]:
        tool_instance = host_database[f"tools.{tool_name}.instance"]
        tool_config = host_database[f"tools.{tool_name}.configuration"]
        if tool_instance.is_digital:
            raise HTTPException(404, f"{tool_name} does not support analog mode")

        if len(tool_config["upper_limit"]) != len(tool_positions):
            raise HTTPException(
                400,
                f"Given Joint positions should have the same length as tool limits for {tool_name}",
            )

        analog_signals = []
        for idx, tool_pos in enumerate(tool_positions):
            if (
                not min(
                    tool_config["upper_limit"][idx], tool_config["lower_limit"][idx]
                )
                <= tool_pos
                <= max(tool_config["upper_limit"][idx], tool_config["lower_limit"][idx])
            ):
                raise HTTPException(
                    404, f"{tool_positions} are out of configured tool joint limits"
                )

            joint_ranges = [
                tool_config["upper_limit"][idx],
                tool_config["lower_limit"][idx],
            ]
            signal_ranges = tool_config["signals"][idx].range
            value = np.interp(tool_pos, joint_ranges, signal_ranges).round(4).tolist()
            analog_signals.append(value)
        return analog_signals
    else:
        raise HTTPException(404, f"{tool_name} is not configured yet")
