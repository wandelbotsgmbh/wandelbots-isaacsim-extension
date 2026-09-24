import asyncio
import time
from copy import deepcopy
import traceback

import carb
import omni.kit.app
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

# How long to wait for the PLAY-triggered start_streams to bring a motion group
# up: it probes every configured host over the network (multi-second timeout per
# host) before any stream opens.
_STREAM_READY_TIMEOUT_S = 12.0
# Follow-up window after rebuilding a single stream - no host probing involved,
# so this only covers the state request plus the websocket handshake.
_STREAM_RESTART_TIMEOUT_S = 5.0


class MotionGroupService:
    def __init__(self):
        self.stream_action_lock = asyncio.Lock()
        self.motion_group_lock = asyncio.Lock()
        self.timeline = omni.timeline.get_timeline_interface()
        self._streams: dict[str, MotionStreamConnector] = {}
        self._apply_update_sub: carb.events.ISubscription | None = None
        # A start_streams pass is probing hosts / opening streams right now.
        # ensure_stream_live waits for it instead of starting a second one.
        self._streams_starting = False

    def _on_update(self, _event) -> None:
        # Coalesced joint application: the websocket receive handlers only store
        # the newest target per motion group (a plain per-connection cache, no
        # USD involved) and reply their feedback inline from the per-frame
        # measured-state sample; this hub refreshes that sample and applies the
        # newest target once per rendered frame, then sleeps articulations
        # that have been idle for a while (stops PhysX's per-frame transform
        # writeback for a robot at rest; the next target wakes them).
        for stream in list(self._streams.values()):
            stream.apply_pending_joints()
            stream.maybe_sleep_when_idle()

    def on_timeline_stop(self) -> None:
        """Tell every stream the timeline stopped, while the stage is still live.

        Synchronous on purpose: stop_streams() is scheduled as a task and runs
        a frame or more later, by which time each stream has missed the frame
        the stop gave it.
        """
        for stream in list(self._streams.values()):
            try:
                stream.wake_for_reset()
            except Exception as error:
                carb.log_warn(
                    f"Could not wake {stream.motion_group.identifier} for the "
                    f"timeline reset: {error}"
                )

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

    @staticmethod
    def _stream_identity(configuration: MotionStreamConfiguration) -> tuple:
        """What makes two stream configurations the same robot connection."""
        return (
            configuration.host,
            configuration.cell,
            configuration.controller,
            configuration.motion_group,
        )

    def _find_stream_connector(
        self, configuration: MotionStreamConfiguration
    ) -> MotionStreamConnector | None:
        """Connector serving *configuration*, whether it is live yet or not."""
        identity = self._stream_identity(configuration)
        for connector in self._streams.values():
            if self._stream_identity(connector.configuration) == identity:
                return connector
        return None

    def _find_streamable_prim_path(
        self, configuration: MotionStreamConfiguration
    ) -> str | None:
        """Stage motion group that *configuration* would stream from.

        Same eligibility as ``_collect_streamable_configurations``: matched by
        connection identity, enabled for simulation, and fully assigned. None
        means nothing in this scene ever streams that connection (a real
        controller, or a robot deliberately not simulated).
        """
        identity = self._stream_identity(configuration)
        for motion_group_prim_path in self.get_all_motion_group_prim_paths():
            try:
                candidate = self.get_motion_group_configuration(motion_group_prim_path)
            except Exception:
                continue
            if candidate is None or not candidate.enabled:
                continue
            candidate_stream = candidate.motion_stream_configuration
            if not candidate_stream.is_connectable:
                continue
            if self._stream_identity(candidate_stream) == identity:
                return motion_group_prim_path
        return None

    def is_stream_live(self, configuration: MotionStreamConfiguration) -> bool:
        """Whether joint state is flowing for *configuration*'s motion group."""
        connector = self._find_stream_connector(configuration)
        return connector is not None and connector.is_live

    def has_streamable_motion_group(
        self, configuration: MotionStreamConfiguration
    ) -> bool:
        """Whether a robot in the scene is set up to follow *configuration*.

        False for a real controller or a robot not enabled for simulation -
        those never stream, so a missing stream is not a problem to report.
        """
        return self._find_streamable_prim_path(configuration) is not None

    async def ensure_stream_live(
        self,
        configuration: MotionStreamConfiguration,
        timeout_s: float = _STREAM_READY_TIMEOUT_S,
    ) -> bool:
        """Wait until *configuration*'s motion stream carries joint state.

        Streams are only built on the timeline PLAY event, and ``start_streams``
        probes every host over the network first, so for several seconds after
        play there is no live connection yet. Anything that drives the robot
        through the backend (trajectory execution) started in that window moves
        the robot in NOVA while the articulation in the scene is not listening
        yet - which is why such a move used to need a stop/play cycle to show
        up. Awaiting this first makes the move land in the scene as well.

        Returns False when no stream can be established (timeline stopped, not
        simulated, host unreachable): that is informational, callers are free to
        proceed - a real controller has no stream to wait for in the first
        place, and those cases return without waiting.
        """
        if self.is_stream_live(configuration):
            return True
        if self.timeline.is_stopped():
            # Streams exist only while the timeline runs, and playing is the
            # caller's decision - _start_stream refuses a stopped timeline.
            return False
        motion_group_prim_path = self._find_streamable_prim_path(configuration)
        if motion_group_prim_path is None:
            carb.log_info(
                f"No simulated motion group streams {configuration.cell}/"
                f"{configuration.controller}/{configuration.motion_group} - "
                f"nothing to wait for."
            )
            return False
        # Only wait when something is actually on its way: a start_streams pass
        # in flight (it probes every host before opening anything), or a
        # connector that exists and is still connecting.
        if self._streams_starting or self._find_stream_connector(configuration):
            if await self._wait_for_live_stream(configuration, timeout_s):
                return True
            if self.timeline.is_stopped():
                return False
        # PLAY never brought this one up (host was unreachable back then, or the
        # motion group was assigned afterwards). Rebuild THIS stream only:
        # start_streams() recreates every other robot's stream as a side effect.
        if not await self._restart_stream(motion_group_prim_path):
            return False
        return await self._wait_for_live_stream(
            configuration, _STREAM_RESTART_TIMEOUT_S
        )

    async def _wait_for_live_stream(
        self, configuration: MotionStreamConfiguration, timeout_s: float
    ) -> bool:
        """Poll per frame - the stream comes up from tasks driven by the app loop."""
        app = omni.kit.app.get_app()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await app.next_update_async()
            if self.is_stream_live(configuration):
                return True
            if self.timeline.is_stopped():
                return False
        return False

    async def _restart_stream(self, motion_group_prim_path: str) -> bool:
        async with self.stream_action_lock:
            try:
                await self._remove_stream(motion_group_prim_path)
                stream = await self._create_stream(
                    motion_group_prim_path, check_connection=False
                )
                await self._start_stream(stream)
            except Exception as ex:
                carb.log_warn(
                    f"Could not start the motion stream for "
                    f"{motion_group_prim_path}: {ex}"
                )
                return False
        return True

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
                raise RuntimeError(
                    f"Connection validation failed {ex} ({ex.__class__.__name__})"
                )

            updated_configuration.apply_to_prim(self._stage)

        # Create new stream from config if exists
        async with self.stream_action_lock:
            motion_stream = self._get_motion_group_stream(motion_group_prim_path)
            if motion_stream is None:
                return

            # Store for new stream starting state
            was_streaming = (
                motion_stream.stream is not None and motion_stream.stream.streaming
            )

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

    def _collect_streamable_configurations(
        self,
    ) -> list[tuple[str, MotionGroupConfiguration]]:
        """Stage motion groups that are enabled for simulation and fully assigned."""
        candidates: list[tuple[str, MotionGroupConfiguration]] = []
        for motion_group_prim_path in self.get_all_motion_group_prim_paths():
            try:
                configuration = get_motion_group_configuration_from_prim(
                    self._get_prim(motion_group_prim_path)
                )
            except Exception as ex:
                carb.log_error(
                    f"Failed to read motion group {motion_group_prim_path}. {ex}"
                )
                continue

            if configuration is None:
                continue

            if not configuration.enabled:
                # Info, not verbose: verbose is off by default, and this is the
                # first thing one looks for when a robot does not stream.
                carb.log_info(
                    f"Skipping stream for {motion_group_prim_path}: "
                    f"not enabled for simulation."
                )
                continue

            if not configuration.motion_stream_configuration.is_connectable:
                carb.log_warn(
                    f"Skipping stream for {motion_group_prim_path}: not assigned to a "
                    f"virtual controller (missing host/cell/controller/motion_group)."
                )
                continue

            candidates.append((motion_group_prim_path, configuration))
        return candidates

    async def _probe_connections(
        self, candidates: list[tuple[str, MotionGroupConfiguration]]
    ) -> list[str]:
        """Probe every candidate concurrently; return the reachable prim paths.

        Each probe waits on a network round trip with a multi-second timeout, so
        serially this grew with the robot count and one dead host stalled every
        robot behind it.
        """
        if not candidates:
            return []

        results = await asyncio.gather(
            *(configuration.check_connection() for _, configuration in candidates),
            return_exceptions=True,
        )

        reachable: list[str] = []
        for (motion_group_prim_path, configuration), result in zip(candidates, results):
            if isinstance(result, asyncio.CancelledError):
                # Playback was aborted while probing; do not swallow it.
                raise result
            if isinstance(result, BaseException):
                host = configuration.motion_stream_configuration.host
                carb.log_warn(
                    f"Skipping stream for {motion_group_prim_path}: host "
                    f"'{host}' is not reachable ({result})."
                )
                continue
            reachable.append(motion_group_prim_path)
        return reachable

    async def start_streams(self):
        self._streams_starting = True
        try:
            await self._start_streams()
        finally:
            self._streams_starting = False

    async def _start_streams(self):
        async with self.stream_action_lock:
            candidates = self._collect_streamable_configurations()
            for motion_group_prim_path in await self._probe_connections(candidates):
                try:
                    # Always recreate stream to ensure MotionGroup reflects current articulation structure
                    # This handles cases where articulations are connected/disconnected between runs
                    await self._remove_stream(motion_group_prim_path)
                    stream = await self._create_stream(
                        motion_group_prim_path, check_connection=False
                    )
                    try:
                        await self._start_stream(stream)
                    except Exception as start_ex:
                        # Clean up the created stream if starting failed
                        await self._remove_stream(motion_group_prim_path)
                        raise start_ex
                except Exception as ex:
                    carb.log_error(f"Failed to stream {motion_group_prim_path}. {ex}")

            if self._streams and self._apply_update_sub is None:
                self._apply_update_sub = (
                    omni.kit.app.get_app()
                    .get_update_event_stream()
                    .create_subscription_to_pop(
                        self._on_update,
                        name="wandelbots.omni.motion_group_apply_pending",
                    )
                )

    async def stop_streams(self):
        """Close and forget every stream.

        Removing the connector, not just closing its websocket, matters
        beyond bookkeeping: each connector's MotionGroup holds the Usd.Stage
        it was built against, so a connector left in _streams keeps that
        stage resident and, once _apply_update_sub gets recreated by a later
        start_streams(), still runs its per-frame update loop against it.
        """
        async with self.stream_action_lock:
            self._apply_update_sub = None
            for motion_group_prim_path in self.get_all_motion_group_prim_paths():
                try:
                    await self._remove_stream(motion_group_prim_path)
                except Exception as ex:
                    carb.log_error(
                        f"Failed to stop stream {motion_group_prim_path}. {ex}"
                    )

    async def _create_stream(
        self, motion_group_prim_path: str, check_connection: bool = True
    ):
        """Build the stream connector for *motion_group_prim_path*.

        ``check_connection=False`` is for callers that already probed (see
        ``_probe_connections``); the probe's only side effect is refreshing
        ``connector.api_configuration``, which ``__init__`` and ``open()`` set anyway.
        """
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
            if check_connection:
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
            task = asyncio.create_task(stream_connector.open())

            def _on_open_done(completed_task: asyncio.Task):
                if not completed_task.cancelled() and completed_task.exception():
                    exception = completed_task.exception()
                    traceback_str = "".join(
                        traceback.format_exception(
                            type(exception), exception, exception.__traceback__
                        )
                    )
                    carb.log_error(
                        f"Stream {stream_connector.motion_group.identifier} failed: {exception}"
                    )
                    carb.log_verbose(traceback_str)

            task.add_done_callback(_on_open_done)
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

        if not stream_connector.stream:
            carb.log_verbose(f"Stream for {motion_group_prim_path} was never created")
        elif stream_connector.stream.streaming:
            await self._stop_stream(stream_connector)

        # Delete the stream
        del self._streams[motion_group_prim_path]


_motion_group_service = MotionGroupService()


def get_motion_group_service() -> MotionGroupService | None:
    return _motion_group_service
