import asyncio
from typing import Dict, Union, List
from wandelbots.omni.environment import host_database
from wandelbots.omni.core.networks import (
    IOStateConnector,
    PoseTracker,
    RobotStateConnector,
    StreamingConnector,
)
from wandelbots.omni.router.v1.utils import (
    get_stream_manager,
    fetch_all_streaming_configurations,
    fetch_streamers,
)
from fastapi import APIRouter, Body, HTTPException, WebSocket, status, Depends

stream_router = APIRouter(prefix="/streams", tags=["streams"], dependencies=[Depends(get_stream_manager)])



@stream_router.websocket("/pose_tracker")
async def pose_tracker_websocket(websocket: WebSocket) -> None:
    """
    Pose tracker websocket connection
    """
    pose_trackers = await fetch_streamers("PoseTracker")
    pose_tracker = pose_trackers.values()[0]
    await pose_tracker.connect(websocket)
    try:
        while True:
            if pose_tracker.streaming:
                poses = await pose_tracker.receive()
                await pose_tracker.send(poses)
    except asyncio.CancelledError:
        pass
    finally:
        await pose_tracker.disconnect(websocket)


@stream_router.get(
    path="/get_all_available_streams",
    operation_id="get_all_available_streams",
    response_model=List[str],
)
async def get_all_available_streams() -> List[str]:
    """
    Lists all the streams which are configurable
    Returns:
        a list of all available streams which can be configured
    """
    return list(StreamingConnector.streams_registry.keys())


@stream_router.post(
    path="/create_streams",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="create_streams",
    response_model=None,
)
async def create_streams(
    configuration: List[Union[fetch_all_streaming_configurations()]] = Body(),
) -> None:
    """
    Creates a stream with the given configuration
    Args:
        configuration: stream configuration

    Returns:
        None

    """
    for each in configuration:
        if each.identifier in host_database["streams"]:
            raise HTTPException(
                404,
                f"{each.identifier} is already created. Please delete it first to create a new stream",
            )
    streams = list(StreamingConnector.streams_registry.keys())
    for stream_config in configuration:
        if stream_config.type not in streams:
            raise HTTPException(
                404,
                f"{stream_config.type} is not a possible stream. Choose one in {streams}",
            )

        try:
            stream = StreamingConnector.streams_registry[stream_config.type](
                stream_config
            )
            await stream.check_connection()
        except Exception as e:
            raise HTTPException(404, f"Unable to create stream: {str(e)}")

        host_database[f"streams.{stream_config.identifier}.configuration"] = (
            stream.to_dict
        )
        host_database[f"streams.{stream_config.identifier}.instance"] = stream


@stream_router.delete(
    path="/delete_stream",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_stream",
    response_model=None,
)
async def delete_stream(stream_name: str) -> None:
    """
    Deletes a configured stream
    Args:
        stream_name: the name of the stream which needs to be deleted
    """
    # Obtain the stream instance
    stream_instance = host_database.get(f"streams.{stream_name}.instance")
    if stream_instance is None:
        raise HTTPException(404, f"{stream_name} is not configured yet")

    # Stop the stream if it is running
    if await stream_instance.is_running:
        await stop_stream(stream_name)

    # Delete the stream
    del host_database[f"streams.{stream_name}"]


@stream_router.get(
    path="/get_all_streams",
    operation_id="get_all_streams",
    response_model=Dict[
        str,
        Union[
            RobotStateConnector.Configuration,
            IOStateConnector.Configuration,
            PoseTracker.Configuration,
        ],
    ],
)
async def get_all_streams() -> Dict[
    str,
    Union[
        RobotStateConnector.Configuration,
        IOStateConnector.Configuration,
        PoseTracker.Configuration,
    ],
]:
    """
    Fetches all the streams which are configured
    Returns:
        a dict containing the configured streams and their configurations
    """
    all_streams = {}
    for each in host_database["streams"]:
        all_streams[each] = host_database["streams"][each]["configuration"]
    return all_streams


@stream_router.get(
    path="/get_stream_status",
    operation_id="get_stream_status",
    response_model=Dict[str, bool],
)
async def get_stream_status() -> Dict[str, bool]:
    """
    Fetches all the streams configured with their status
    Returns:
        a dict containing the configured streams and if they are running or not
    """
    status = {}
    for each in host_database["streams"]:
        stream_instance = host_database[f"streams.{each}.instance"]
        status[each] = await stream_instance.is_running
    return status


@stream_router.delete(
    path="/delete_all_streams",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_all_streams",
    response_model=None,
)
async def delete_all_streams() -> None:
    """
    Deletes all the configured streams
    Returns:
        None
    """
    if "streams" in host_database:
        for stream_name in host_database["streams"]:
            streamer = host_database[f"streams.{stream_name}.instance"]
            if await streamer.is_running:
                await stop_stream(streamer)

        del host_database["streams"]


@stream_router.post(
    path="/start_stream",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="start_stream",
    response_model=None,
)
async def start_stream(stream_name: str, stream_manager = Depends(get_stream_manager)) -> None:
    """
    Starts a configured stream
    Args:
        stream_name: the name of the stream to be started

    Returns:
        None
    """
    try:
        if "streams" in host_database and stream_name in host_database["streams"]:
            stream_type = host_database[f"streams.{stream_name}.configuration.type"]
            streamer_dict = await fetch_streamers(stream_type)
            streamer = streamer_dict[stream_name]
            await stream_manager.start_stream(streamer)
    except Exception as e:
        raise HTTPException(404, f"{stream_name} could not be started: {str(e)}")


@stream_router.post(
    path="/stop_stream",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="stop_stream",
    response_model=None,
)
async def stop_stream(stream_name: str, stream_manager = Depends(get_stream_manager)) -> None:
    """
    Stops a configured stream
    Args:
        stream_name: the name of the stream to be stopped

    Returns:
        None
    """
    try:
        if "streams" in host_database and stream_name in host_database["streams"]:
            stream_type = host_database[f"streams.{stream_name}.configuration.type"]
            streamer_dict = await fetch_streamers(stream_type)
            streamer = streamer_dict[stream_name]
            await stream_manager.stop_stream(streamer)
    except Exception as e:
        raise HTTPException(404, f"{stream_name} could not be started: {str(e)}")


@stream_router.post(
    path="/start_all_streams",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="start_all_streams",
    response_model=None,
)
async def start_all_streams(stream_manager = Depends(get_stream_manager)) -> None:
    """
    Starts all configured streams
    Returns:
        None
    """
    await stream_manager.start_all_streams()


@stream_router.post(
    path="/stop_all_streams",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="stop_all_streams",
    response_model=None,
)
async def stop_all_streams(stream_manager = Depends(get_stream_manager)) -> None:
    """
    Stops all configured streams
    Returns:
        None
    """
    await stream_manager.stop_all_streams()


@stream_router.put(
    path="/external-joint-stream/enabled",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="enable_external_joint_stream",
    response_model=None,
)
async def enable_external_joint_stream(
    stream_name: str,
    enabled: bool = Body(
        ...,
        description="Enables external joint feedback loop from robot articulation chain",
    ),
) -> None:
    """
    A stream reconnect is necessary for this to be applied.

    Args:
        stream_name: the name of the stream to be stopped

    Returns:
        None
    """
    stream_instance_key = f"streams.{stream_name}.instance"
    if stream_instance_key not in host_database:
        raise HTTPException(404, f"{stream_name} not found")

    stream_type = stream_type = host_database[
        f"streams.{stream_name}.configuration.type"
    ]

    required_stream_type = "RobotStateConnector"
    if stream_type != required_stream_type:
        raise HTTPException(400, f"{stream_name} is not a {required_stream_type}")

    stream_instance = host_database[stream_instance_key]
    return stream_instance.enable_external_joint_stream(enabled)


@stream_router.get(
    path="/external-joint-stream/enabled",
    status_code=status.HTTP_200_OK,
    operation_id="is_external_joint_stream",
    response_model=bool,
)
async def is_external_joint_stream(
    stream_name: str,
) -> bool:
    """

    Args:
        stream_name: the name of the stream to be stopped

    Returns:
        True if robot state stream uses the external joint stream endpoint
    """
    stream_instance_key = f"streams.{stream_name}.instance"
    if stream_instance_key not in host_database:
        raise HTTPException(404, f"{stream_name} not found")

    stream_type = host_database[
        f"streams.{stream_name}.configuration.type"
    ]

    required_stream_type = "RobotStateConnector"
    if stream_type != required_stream_type:
        raise HTTPException(400, f"{stream_name} is not a {required_stream_type}")

    return host_database[stream_instance_key].is_external_joint_stream()
