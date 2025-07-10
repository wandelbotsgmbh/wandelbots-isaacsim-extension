import asyncio
from copy import deepcopy

import carb

try:
    import isaacsim.core.utils.prims as prims_utils
except ImportError:
    import omni.isaac.core.utils.prims as prims_utils

    carb.log_warn("motion_group_service is using legacy isaac sim imports")
import omni.timeline
from pxr import Sdf
from wandelbots.omni.environment import host_database
from wandelbots.omni.manipulators import MotionGroup, MotionGroupConfiguration
from wandelbots.omni.utils.auth import get_auth_token
from wandelbots.omni.utils.teaching import GhostObjectUtils

from .motion_stream_configuration import MotionStreamConfiguration
from .motion_stream_connector import MotionStreamConnector


class MotionGroupService:
    def __init__(self):
        self.stream_action_lock = asyncio.Lock()
        self.motion_group_lock = asyncio.Lock()
        self.timeline = omni.timeline.get_timeline_interface()

    def has_motion_group(self, motion_group_name: str) -> bool:
        return motion_group_name in host_database["motion_groups"]

    def get_all_motion_group_names(self) -> list[str]:
        return host_database["motion_groups"] if host_database["motion_groups"] else []

    def _get_motion_group_dict(self, motion_group_name: str) -> dict:
        db_path = f"motion_groups.{motion_group_name}"
        if motion_group_name not in host_database["motion_groups"]:
            raise KeyError(f"Robot {motion_group_name} not found")
        return host_database[db_path]

    def get_motion_group_instance(self, motion_group_name: str) -> MotionGroup | None:
        motion_group = self._get_motion_group_dict(motion_group_name)
        return motion_group["instance"] if "instance" in motion_group else None

    def get_motion_group_configuration(
        self,
        motion_group_name: str,
    ) -> MotionGroupConfiguration | None:
        motion_group = self._get_motion_group_dict(motion_group_name)
        return (
            motion_group["configuration"] if "configuration" in motion_group else None
        )

    def _get_motion_group_stream(
        self, motion_group_name: str
    ) -> MotionStreamConnector | None:
        motion_group = self._get_motion_group_dict(motion_group_name)
        return motion_group["stream"] if "stream" in motion_group else None

    def get_motion_group_by_prim_path(
        self, prim_path: str
    ) -> MotionGroupConfiguration | None:
        for motion_group_name in host_database["motion_groups"]:
            config = self.get_motion_group_configuration(motion_group_name)
            if config.prim_path == prim_path:
                return config
        return None

    async def create_motion_group(self, configuration: MotionGroupConfiguration):
        async with self.motion_group_lock:
            motion_group_instance = MotionGroup(configuration)

            try:
                await motion_group_instance.check_connection(get_auth_token())
            except Exception as ex:
                raise RuntimeError(
                    f"Connection validation failed {ex} ({ex.__class__.__name__})"
                )

            host_database[f"motion_groups.{configuration.name}.configuration"] = (
                configuration
            )
            host_database[f"motion_groups.{configuration.name}.instance"] = (
                motion_group_instance
            )

            # Experimental: add ghost object sources
            all_ghost_object_sources = set(
                each.prim_path
                for each in GhostObjectUtils.get_possible_ghost_object_sources()
            )
            child_prims = set(
                prim.GetPath().pathString
                for prim in prims_utils.get_all_matching_child_prims(
                    Sdf.Path(configuration.prim_path).GetParentPath(), lambda _: True
                )
            )

            for ghost_path in all_ghost_object_sources.intersection(child_prims):
                await GhostObjectUtils.add_source_ghost_object(ghost_path)

    async def update_motion_group_stream_configuration(
        self,
        motion_group_name: str,
        motion_stream_configuration: MotionStreamConfiguration,
    ):
        async with self.motion_group_lock:
            old_configuration = self.get_motion_group_configuration(motion_group_name)
            if old_configuration is None:
                raise RuntimeError(f"Configuration of {motion_group_name} not found")

            updated_configuration = deepcopy(old_configuration)
            updated_configuration.motion_stream_configuration = (
                motion_stream_configuration
            )

            motion_group_instance = MotionGroup(updated_configuration)

            try:
                await motion_group_instance.check_connection(get_auth_token())
            except Exception as ex:
                f"Connection validation failed ({ex.__class__.__name__})"

            host_database[
                f"motion_groups.{updated_configuration.name}.configuration"
            ] = updated_configuration
            host_database[f"motion_groups.{updated_configuration.name}.instance"] = (
                motion_group_instance
            )

        # Create new stream from config if exists
        async with self.stream_action_lock:
            motion_stream = self._get_motion_group_stream(motion_group_name)
            if motion_stream is None:
                return

            # Store for new stream starting state
            was_streaming = motion_stream.stream.streaming

            await self._remove_stream(motion_group_name)
            motion_stream = await self._create_stream(motion_group_name)
            if was_streaming:
                await self._start_stream(motion_stream)

    async def remove_motion_group(self, motion_group_name: str):
        async with self.motion_group_lock:
            if not self.has_motion_group(motion_group_name):
                raise RuntimeError(f"Robot {motion_group_name} not found")

            await self._remove_stream(motion_group_name)

            # Delete the motion_group
            del host_database[f"motion_groups.{motion_group_name}"]

    async def start_streams(self):
        async with self.stream_action_lock:
            for motion_group_name in self.get_all_motion_group_names():
                try:
                    stream = self._get_motion_group_stream(motion_group_name)
                    if not stream:
                        stream = await self._create_stream(motion_group_name)
                    await self._start_stream(stream)
                except Exception as ex:
                    carb.log_error(f"Failed to stream {motion_group_name}. {ex}")

    async def stop_streams(self):
        async with self.stream_action_lock:
            for motion_group_name in self.get_all_motion_group_names():
                try:
                    stream = self._get_motion_group_stream(motion_group_name)
                    if not stream:
                        continue
                    await self._stop_stream(stream)
                except Exception as ex:
                    carb.log_error(f"Failed to stop stream {motion_group_name}. {ex}")

    async def _create_stream(self, motion_group_name: str):
        if self._get_motion_group_stream(motion_group_name):
            raise RuntimeError(
                f"{motion_group_name} is already created. Please delete it first to create a new stream"
            )
        try:
            stream = MotionStreamConnector(
                motion_group=self.get_motion_group_instance(motion_group_name),
                configuration=self.get_motion_group_configuration(
                    motion_group_name
                ).motion_stream_configuration,
            )
            await stream.check_connection(get_auth_token())
        except Exception as e:
            raise RuntimeError(f"Unable to connect stream: {str(e)}")
        host_database[f"motion_groups.{motion_group_name}.stream"] = stream
        return stream

    async def _start_stream(self, stream_connector: MotionStreamConnector) -> None:
        if self.timeline.is_stopped():
            raise RuntimeError("Simulation has to be started first")

        try:
            carb.log_info(f"Opening {stream_connector.motion_group.identifier} stream")
            stream_connector.motion_group.articulation.initialize()
            asyncio.create_task(stream_connector.open())
        except Exception as e:
            raise RuntimeError(
                f"Failed to start motion_group {stream_connector.motion_group.identifier}: {str(e)}",
            )

        carb.log_info(f"Stream {stream_connector.motion_group.identifier} started")

    async def _stop_stream(self, stream_connector: MotionStreamConnector):
        try:
            stream = stream_connector.stream
            if stream.streaming:
                await stream.close()
        except Exception as e:
            raise RuntimeError(
                f"Stream {stream_connector.motion_group.identifier} could not be stopped: {str(e)}",
            )
        carb.log_info(f"Stream:{stream_connector.motion_group.identifier} stopped")

    async def _remove_stream(self, motion_group_name: str):
        if not self.has_motion_group(motion_group_name):
            return

        stream_connector = self._get_motion_group_stream(motion_group_name)
        if not stream_connector:
            return

        if stream_connector.stream.streaming:
            await self._stop_stream(stream_connector)

        # Delete the stream
        del host_database[f"motion_groups.{motion_group_name}.stream"]


_motion_group_service = MotionGroupService()


def get_motion_group_service() -> MotionGroupService | None:
    return _motion_group_service
