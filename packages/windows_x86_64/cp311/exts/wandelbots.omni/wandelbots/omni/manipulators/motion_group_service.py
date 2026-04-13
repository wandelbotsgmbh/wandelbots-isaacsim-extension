import asyncio
from copy import deepcopy

import carb
import omni.timeline
import omni.usd
from pxr import Sdf, Usd

import wandelbots.usd as wb_schema  # type: ignore
from wandelbots.omni.manipulators import (
    MotionGroup,
    MotionGroupConfiguration,
    get_motion_group_configuration_from_prim,
    is_prim_motion_group,
)

from .motion_stream_configuration import MotionStreamConfiguration
from .motion_stream_connector import MotionStreamConnector
from .utils import get_scene_motion_group_prim_paths


class MotionGroupService:
    def __init__(self):
        self.stream_action_lock = asyncio.Lock()
        self.motion_group_lock = asyncio.Lock()
        self.timeline = omni.timeline.get_timeline_interface()
        self._streams: dict[str, MotionStreamConnector] = {}

    @property
    def _stage(self) -> Usd.Stage:
        return omni.usd.get_context().get_stage()

    def _get_prim(self, prim_path: Sdf.Path | str) -> Usd.Prim:
        if isinstance(prim_path, str):
            prim_path = Sdf.Path(prim_path)
        prim = self._stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            raise ValueError(f"Prim at path {prim_path} is not valid")
        return prim

    def has_motion_group(self, motion_group_prim_path: str) -> bool:
        return is_prim_motion_group(self._get_prim(motion_group_prim_path))

    def get_motion_group_configuration(
        self,
        motion_group_prim_path: Sdf.Path | str,
    ) -> MotionGroupConfiguration | None:
        return get_motion_group_configuration_from_prim(
            self._get_prim(motion_group_prim_path)
        )

    def get_all_motion_group_prim_paths(self) -> list[str]:
        return get_scene_motion_group_prim_paths(include_prims_without_api=False)

    def _get_motion_group_stream(
        self, motion_group_prim_path: str
    ) -> MotionStreamConnector | None:
        return self._streams.get(motion_group_prim_path, None)

    async def create_motion_group(self, configuration: MotionGroupConfiguration):
        async with self.motion_group_lock:
            try:
                await configuration.check_connection()
            except Exception as ex:
                raise RuntimeError(
                    f"Connection validation failed {ex} ({ex.__class__.__name__})"
                )

            configuration.apply_to_prim(self._stage)

    async def update_motion_group_stream_configuration(
        self,
        motion_group_prim_path: str,
        motion_stream_configuration: MotionStreamConfiguration,
    ):
        async with self.motion_group_lock:
            old_configuration = self.get_motion_group_configuration(
                motion_group_prim_path
            )
            if old_configuration is None:
                raise RuntimeError(
                    f"Configuration of {motion_group_prim_path} not found"
                )

            updated_configuration = deepcopy(old_configuration)
            updated_configuration.motion_stream_configuration = (
                motion_stream_configuration
            )
            try:
                await updated_configuration.check_connection()
            except Exception as ex:
                f"Connection validation failed ({ex.__class__.__name__})"

            updated_configuration.apply_to_prim(self._stage)

        # Create new stream from config if exists
        async with self.stream_action_lock:
            motion_stream = self._get_motion_group_stream(motion_group_prim_path)
            if motion_stream is None:
                return

            # Store for new stream starting state
            was_streaming = motion_stream.stream.streaming

            await self._remove_stream(motion_group_prim_path)
            if not updated_configuration.enabled:
                carb.log_verbose(
                    f"Motion group {motion_group_prim_path} is not enabled, skipping stream creation"
                )
                return

            motion_stream = await self._create_stream(motion_group_prim_path)
            if was_streaming:
                await self._start_stream(motion_stream)

    async def remove_motion_group(self, motion_group_prim_path: str):
        async with self.motion_group_lock:
            if not self.has_motion_group(motion_group_prim_path):
                raise RuntimeError(f"MotionGroup {motion_group_prim_path} not found")

            await self._remove_stream(motion_group_prim_path)

            prim: Usd.Prim = self._get_prim(motion_group_prim_path)
            prim.RemoveAPI(wb_schema.MotionGroupAPI)

    async def start_streams(self):
        async with self.stream_action_lock:
            for motion_group_prim_path in self.get_all_motion_group_prim_paths():
                try:
                    motion_group_configuration = (
                        get_motion_group_configuration_from_prim(
                            self._get_prim(motion_group_prim_path)
                        )
                    )

                    if not motion_group_configuration.enabled:
                        carb.log_verbose(
                            f"Skipping stream for {motion_group_prim_path} as it is not enabled"
                        )
                        continue

                    # Always recreate stream to ensure MotionGroup reflects current articulation structure
                    # This handles cases where articulations are connected/disconnected between runs
                    await self._remove_stream(motion_group_prim_path)
                    stream = await self._create_stream(motion_group_prim_path)
                    try:
                        await self._start_stream(stream)
                    except Exception as start_ex:
                        # Clean up the created stream if starting failed
                        await self._remove_stream(motion_group_prim_path)
                        raise start_ex
                except Exception as ex:
                    carb.log_error(f"Failed to stream {motion_group_prim_path}. {ex}")

    async def stop_streams(self):
        async with self.stream_action_lock:
            for motion_group_prim_path in self.get_all_motion_group_prim_paths():
                try:
                    stream = self._get_motion_group_stream(motion_group_prim_path)
                    if not stream:
                        continue
                    await self._stop_stream(stream)
                except Exception as ex:
                    carb.log_error(
                        f"Failed to stop stream {motion_group_prim_path}. {ex}"
                    )

    async def _create_stream(self, motion_group_prim_path: str):
        if self._get_motion_group_stream(motion_group_prim_path):
            raise RuntimeError(
                f"{motion_group_prim_path} is already created. Please delete it first to create a new stream"
            )
        try:
            stream = MotionStreamConnector(
                motion_group=MotionGroup(
                    self._stage,
                    self.get_motion_group_configuration(motion_group_prim_path),
                )
            )
            await stream.check_connection()
        except Exception as e:
            raise RuntimeError(f"Unable to connect stream: {str(e)}")
        self._streams[motion_group_prim_path] = stream
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
            if not stream:
                carb.log_verbose(
                    f"Stream for {stream_connector.motion_group.identifier} was never created"
                )
                return
            if stream.streaming:
                await stream.close()
        except Exception as e:
            raise RuntimeError(
                f"Stream {stream_connector.motion_group.identifier} could not be stopped: {str(e)}",
            )
        carb.log_info(f"Stream:{stream_connector.motion_group.identifier} stopped")

    async def _remove_stream(self, motion_group_prim_path: str):
        if not self.has_motion_group(motion_group_prim_path):
            return

        stream_connector = self._get_motion_group_stream(motion_group_prim_path)
        if not stream_connector:
            return

        if stream_connector.stream.streaming:
            await self._stop_stream(stream_connector)

        # Delete the stream
        del self._streams[motion_group_prim_path]


_motion_group_service = MotionGroupService()


def get_motion_group_service() -> MotionGroupService | None:
    return _motion_group_service
