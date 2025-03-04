import asyncio
import carb
from typing import Any, Literal
from fastapi import HTTPException
import omni.timeline
from wandelbots.omni.core.networks import StreamingConnector
from wandelbots.omni.environment import host_database

def fetch_all_streaming_configurations() -> tuple[Any, ...]:
    configurations = []
    for stream_service in StreamingConnector.streams_registry:
        stream_cls = StreamingConnector.streams_registry.get(stream_service)
        configurations.append(stream_cls.Configuration)
    return tuple(configurations)


def get_stream_types() -> list[str]:
    return list(StreamingConnector.streams_registry.keys())


async def fetch_streamers(stream_type: Literal[get_stream_types()]) -> dict:
    try:
        streamers = {}
        for stream in host_database["streams"]:
            if host_database[f"streams.{stream}.configuration.type"] == stream_type:
                streamers.update(
                    {
                        host_database[
                            f"streams.{stream}.configuration.identifier"
                        ]: host_database[f"streams.{stream}.instance"]
                    }
                )
        return streamers
    except Exception as e:
        raise HTTPException(404, "No valid streams found") from e


class StreamManager:

    def __init__(self):
        self.stream_action_lock = asyncio.Lock()
        self.timeline = omni.timeline.get_timeline_interface()
        carb.log_verbose(f"{self} listening to timeline events")
        self.timeline_sub = self.timeline.get_timeline_event_stream().create_subscription_to_pop(
            lambda event: self._on_timeline_events(async_loop=asyncio.get_event_loop(), event=event)
        )

        # Define a callback function to handle timeline events
    def _on_timeline_events(self, async_loop, event):
        if event.type == omni.timeline.TimelineEventType.PLAY.value:
            async_loop.create_task(self.start_all_streams())

        if event.type == omni.timeline.TimelineEventType.STOP.value or event.type == omni.timeline.TimelineEventType.PAUSE.value:
            async_loop.create_task(self.stop_all_streams())

    def _get_all_streams(self) -> list[str]:
        return host_database["streams"] if host_database["streams"] else []

    async def start_all_streams(self):
        for stream in self._get_all_streams():
            await self.start_stream(host_database[f"streams.{stream}.instance"])

    def close(self) -> asyncio.Future:
        carb.log_verbose("Closing stream manager")
        if self.timeline_sub:
            carb.log_verbose("Clearing stream manager timeline subscription")
            self.timeline_sub.unsubscribe()
            self.timeline_sub = None
        
        for stream in self._get_all_streams():
            streamer = host_database[f"streams.{stream}.instance"]
            self.close_stream(streamer)

    async def stop_all_streams(self):
        for stream in self._get_all_streams():
            await self.stop_stream(host_database[f"streams.{stream}.instance"])

    async def start_io_stream(self, streamer) -> None:
        
        if self.timeline.is_stopped():
            raise HTTPException(404, "Simulation has to be started first")
        
        async with self.stream_action_lock:
            robot = streamer.to_dict["robot"]
            robot_instance = host_database[f"robots.{robot}.instance"]
            registered_tools = []
            io_ids = set()
            for tool in robot_instance.connected_tools:
                tool_instance = host_database[f"tools.{tool}.instance"]
                registered_tools.append(tool_instance)
                io_ids.update(tool_instance.signals)

            # Reinitialize all tools connected to the robot
            for tool_instance in registered_tools:
                try:
                    tool_instance.reinitialize()
                except Exception as e:
                    raise HTTPException(
                        404,
                        f"Unable to initialize {tool_instance.identifier}: {str(e)}",
                    )

            try:
                await streamer.open(list(io_ids))
            except Exception as e:
                raise HTTPException(
                    404,
                    f"Stream:{streamer.configuration.identifier} could not be started with the list of IOs given: {str(e)}",
                )

            try:
                await streamer.start_stream(tools=registered_tools)
            except Exception as e:
                raise HTTPException(
                    404,
                    f"Stream:{streamer.configuration.identifier} could not be started: {str(e)}",
                )

            carb.log_info(f"Stream:{streamer.configuration.identifier} started")

        

    async def start_robot_state_stream(self, streamer) -> None:
        if self.timeline.is_stopped():
            raise HTTPException(404, "Simulation has to be started first")
        
        async with self.stream_action_lock:            
            try:
                robot = streamer.to_dict["robot"]
                carb.log_info(f"Opening {robot} websocket")
                await streamer.open()
            except Exception as e:
                raise HTTPException(
                    404,
                    f"Stream:{streamer.configuration.identifier} is not created: {str(e)}",
                )
            
            try:
                robot_instance = host_database[f"robots.{robot}.instance"]
                robot_instance.articulation.initialize()
                await streamer.start_stream(robot=robot_instance)
            except Exception as e:
                raise HTTPException(
                    404,
                    f"Stream:{streamer.configuration.identifier} could not started: {str(e)}",
                )

            carb.log_info(f"Stream {streamer.configuration.identifier} started")
            

    async def start_pose_tracker(self, streamer, prim_paths: list[str] | None):
        async with self.stream_action_lock:
            try:
                await streamer.open()
            except Exception as e:
                raise HTTPException(
                    404,
                    f"Stream:{streamer.configuration.identifier} is not created: {str(e)}",
                )

            try:
                await streamer.start_stream(prim_paths)
            except Exception as e:
                raise HTTPException(
                    404,
                    f"Stream:{streamer.configuration.identifier} could not be started: {str(e)}",
                )

            carb.log_info(f"Stream:{streamer.configuration.identifier} started")

    async def start_stream(self, streamer, **kwargs):
        if streamer.configuration.type not in get_stream_types():
            raise HTTPException(
                404, f"{streamer.configuration.identifier} is not a valid stream"
            )

        async with self.stream_action_lock:
            if await streamer.is_running:
                stream_name = streamer.identifier
                carb.log_warn(f"Stream {stream_name} is already running")
                return
        
        if streamer.configuration.type == "IOStateConnector":
            await self.start_io_stream(streamer)
        elif streamer.configuration.type == "RobotStateConnector":
            await self.start_robot_state_stream(streamer)
        elif streamer.configuration.type == "PoseTracker":
            await self.start_pose_tracker(streamer, kwargs.get("prim_paths"))
        else:
            raise ValueError("Invalid stream type")

    def close_stream(self, streamer: StreamingConnector):
        if streamer.stream_event_loop is None:
            return
        streamer.stream_event_loop.run_until_complete(self.stop_stream(streamer))


    async def stop_stream(self, streamer):
        try:
            if await streamer.is_running:
                await streamer.stop_stream()
                await streamer.close()
        except Exception as e:
            raise HTTPException(
                404,
                f"Stream:{streamer.configuration.identifier} could not be stopped: {str(e)}",
            )

        carb.log_info(f"Stream:{streamer.configuration.identifier} stopped")


def get_stream_manager() -> StreamManager | None:
    return None