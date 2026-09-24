import asyncio
import json
import math

import carb
import carb.settings
import omni.timeline
import omni.usd
import torch
from pxr import PhysicsSchemaTools, UsdGeom
import wandelbots_api_client.v2 as wb
import wandelbots_api_client.v2.models as wb_models
from wandelbots_api_client.v2.models import JointTypeEnum
from isaacsim.core.utils.types import ArticulationAction
from omni.physx import get_physx_simulation_interface

from wandelbots.omni.core.networks import ReconnectingWebsocket
from wandelbots.omni.manipulators.utils import get_articulation_joint_indices
from wandelbots.omni.utils.api import ApiConfiguration, get_api_client_from_config

from .motion_group import MotionGroup, MotionStreamConfiguration

# Joint targets closer to the last applied ones than this (radians for revolute,
# millimeters for prismatic) are treated as unchanged and not re-applied.
_JOINT_TARGET_EPSILON = 1e-5

# After this many frames without a new joint target, the articulation is
# explicitly put to sleep (see maybe_sleep_when_idle): a sleeping articulation
# stops PhysX's unconditional per-frame transform/joint-state writeback, which
# is what keeps notice listeners (BVH rebuilds, UI panels, hydra sync) busy
# even when the robot is visually at rest. Sleep thresholds alone never engage
# because residual drive chatter keeps the reported joint velocities above any
# workable threshold. Counted in frames (~0.5s at 60fps) so the mechanism
# needs no clock state.
_IDLE_SLEEP_FRAMES = 30


# _direct_physics_write value meaning "checked, and unusable for this connection".
_DIRECT_PHYSICS_WRITE_UNAVAILABLE = object()


# Off means replies echo the commanded positions with zero velocities, so the
# simulation reports perfect tracking whatever the scene does.
_PHYSICS_FEEDBACK_SETTING = "/exts/wandelbots.omni/externalJointStream/physicsFeedback"


def apply_joint_positions(
    motion_group: MotionGroup,
    joint_positions: list[float],
    joint_indices: list[int] | None = None,
) -> bool:
    """Drive a motion group's Isaac articulation to the given joint targets.

    Mirrors ``MotionStreamConnector.apply_joints`` (prismatic joints scaled mm ->
    stage units; merged-articulation joint indices honoured) but is standalone so
    tools like the IK Playground can apply a solved configuration directly via
    dynamic control — for controllers with no virtual-controller backend (e.g. a
    real/non-NOVA-simulated controller), trajectory execution isn't available, so
    this is the only way to reflect a solved pose on the in-scene robot. Requires
    the timeline to be playing and the articulation to be valid/initialised (the
    physics view only exists while playing). Returns True on a successful apply.
    """
    articulation = motion_group.articulation
    if articulation is None or not articulation.is_valid():
        carb.log_error(f"Invalid articulation for {motion_group.identifier}")
        return False

    positions = list(joint_positions)
    dh_parameters = motion_group.motion_group_dh_parameters
    if any(dh.type == JointTypeEnum.PRISMATIC_JOINT for dh in dh_parameters):
        stage = omni.usd.get_context().get_stage()
        meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        mm_to_stage_units = 0.001 / meters_per_unit
        for i, dh in enumerate(dh_parameters):
            if i < len(positions) and dh.type == JointTypeEnum.PRISMATIC_JOINT:
                positions[i] *= mm_to_stage_units

    positions_array = torch.tensor(positions, dtype=torch.float32)
    if joint_indices is not None:
        indices_array = torch.tensor(joint_indices, dtype=torch.long)
    else:
        indices_array = torch.tensor(range(len(positions)), dtype=torch.long)

    articulation.apply_action(
        ArticulationAction(joint_positions=positions_array, joint_indices=indices_array)
    )
    return True


class MotionStreamConnector:
    def __init__(self, motion_group: MotionGroup):
        self.motion_group = motion_group
        self.receive_lock = asyncio.Lock()

        # This configuration is updated with every connect/state call which needs a token
        self.api_configuration: ApiConfiguration = (
            self.configuration.get_api_configuration()
        )
        self.stream: ReconnectingWebsocket | None = None

        self.timeline = omni.timeline.get_timeline_interface()

        # Set once in open(), fixed for the lifetime of the connection.
        self.stream_joint_count: int | None = None
        self.joint_indices: list[int] | None = None

        # CACHES, derived once in open() from the values above.
        self._joint_indices_tensor: torch.Tensor | None = None
        self._has_prismatic_joints: bool = False
        self._stream_zeros: list[float] | None = None
        # CACHE, derived on first sleep/wake call (needs the live articulation)
        # and re-derived whenever PhysX rebuilds the simulation view.
        self._sleep_body_ids: list[int] | None = None
        self._sleep_paths: list[str] | None = None
        self._sleep_physics_view = None

        # Per-frame joint flow: newest unapplied target, last applied target
        # (dedupe), last known state (echoed as feedback while paused).
        self._pending_joint_positions: list[float] | None = None
        self._last_applied_joints: list[float] | None = None
        self._last_joints: list[float] | None = None

        # Measured-state feedback (external joint stream): newest per-frame
        # PhysX sample in stream joint order and API units, replied per
        # received message. None until the first successful readback after
        # open(); replies echo the command until then.
        self._measured_positions: list[float] | None = None
        self._measured_velocities: list[float] | None = None
        self._readback_failure_logged = False
        self._physics_view_probe_logged = False
        # Resolved from _PHYSICS_FEEDBACK_SETTING in open().
        self._physics_feedback = True
        # apply_joints runs per frame; log a rejection only once.
        self._apply_rejection_logged = False

        # Direct physics-view apply path: None = not checked yet (or re-check
        # after a sim-view rebuild), _DIRECT_PHYSICS_WRITE_UNAVAILABLE, or a ready
        # (physics_view, env_indices) pair (see _init_direct_physics_write).
        self._direct_physics_write = None

        # Idle sleep: frames since the last applied target is the ONLY state.
        # Reaching _IDLE_SLEEP_FRAMES issues the (idempotent) sleep exactly
        # once; a value at or past it means "we slept", so the next target
        # wakes first. Deliberately no mirror of PhysX's sleep state - an
        # external wake (user dragging the robot) just leaves it awake until
        # the cycle repeats.
        self._idle_frames = 0
        self._sleep_unavailable = False
        self._sleep_failure_logged = False

    @property
    def configuration(self) -> MotionStreamConfiguration:
        return self.motion_group.configuration.motion_stream_configuration

    @property
    def is_external_joint_stream(self) -> bool:
        return self.configuration.use_external_joint_stream

    @property
    def is_live(self) -> bool:
        """Whether joint state is actually flowing for this motion group.

        ``stream.streaming`` only says the websocket task was created - the
        connect itself, and the state message that follows it, take longer.
        ``_last_joints`` is set from the first parsed message, so this is the
        point from which driving the robot through the backend also moves the
        articulation in the scene.
        """
        return (
            self.stream is not None
            and self.stream.streaming
            and self._last_joints is not None
        )

    @property
    def _websocket_uri(self):
        base_url = self.api_configuration.base_url_websocket
        if self.is_external_joint_stream:
            return f"{base_url}/cells/{self.configuration.cell}/virtual-controllers/{self.configuration.controller}/external-joints-stream"
        return f"{base_url}/cells/{self.configuration.cell}/controllers/{self.configuration.controller}/motion-groups/{self.configuration.motion_group}/state-stream?response_rate={self.configuration.response_rate}"

    async def check_connection(self):
        """
        Tests if a connection can be established. Will throw an error if check failed
        """
        self.api_configuration = self.configuration.get_api_configuration()
        await self.get_motion_group_state()

    async def get_motion_group_state(self) -> wb_models.MotionGroupState:
        async with get_api_client_from_config(self.api_configuration) as api_client:
            state = await wb.MotionGroupApi(
                api_client=api_client
            ).get_current_motion_group_state(
                self.configuration.cell,
                self.configuration.controller,
                self.configuration.motion_group,
            )
            return state

    async def close(self):
        if self.stream:
            await self.stream.close()

    async def _receive_data(self, data: str):
        async with self.receive_lock:
            return await self._parse(json.loads(data))

    async def open(self):
        self.api_configuration = self.configuration.get_api_configuration()

        if self.stream and self.stream.streaming:
            carb.log_warn(
                f"Websocket for MotionStreamConnector {self.configuration.motion_group} {self.api_configuration} is already open"
            )
            return
        self.stream = ReconnectingWebsocket(
            self._websocket_uri,
            on_receive=self._receive_data,
            token=self.api_configuration.access_token,
        )

        state = await self.get_motion_group_state()
        self.stream_joint_count = len(state.joint_position)
        self._stream_zeros = [0.0] * self.stream_joint_count

        self.joint_indices = get_articulation_joint_indices(self.motion_group)
        self._joint_indices_tensor = torch.tensor(self.joint_indices, dtype=torch.long)
        await self.motion_group.get_dh_parameters()
        self._has_prismatic_joints = any(
            dh_param.type == JointTypeEnum.PRISMATIC_JOINT
            for dh_param in self.motion_group.motion_group_dh_parameters
        )

        carb.log_info(
            f"Start {self.configuration.motion_group} jointCount={self.stream_joint_count} externalJoints={self.is_external_joint_stream}"
        )

        await self.stream.open()
        if not self.is_external_joint_stream:
            return

        settings_value = carb.settings.get_settings().get(_PHYSICS_FEEDBACK_SETTING)
        self._physics_feedback = (
            True if settings_value is None else bool(settings_value)
        )
        self._drop_measured_state()
        self._readback_failure_logged = False

        # If we are in external joint stream mode, we need to ensure the controller is in control mode
        # otherwise the backend will not send joint states
        await self._ensure_control_mode()

        joint_positions = self._stream_joint_positions_in_api_units()
        # external joint stream requires the simulation to send its state first
        await self.send_joint_positions(joint_positions)

    async def _ensure_control_mode(self) -> None:
        async with get_api_client_from_config(self.api_configuration) as api_client:
            controller_api = wb.ControllerApi(api_client=api_client)

            controller_state = await controller_api.get_current_robot_controller_state(
                self.configuration.cell, self.configuration.controller
            )

            controller_mode = controller_state.mode
            if controller_mode == wb_models.RobotSystemMode.MODE_MONITOR:
                carb.log_info(
                    f"MotionGroup {self.configuration.motion_group} is in monitor mode, switching to control mode"
                )
                await controller_api.set_default_mode(
                    self.configuration.cell,
                    self.configuration.controller,
                    wb_models.SettableRobotSystemMode.ROBOT_SYSTEM_MODE_CONTROL,
                )
            elif controller_mode != wb_models.RobotSystemMode.MODE_CONTROL:
                carb.log_warn(
                    f"MotionGroup {self.configuration.motion_group} is in unexpected mode: {controller_mode}, expected control mode"
                )

    async def send_joint_positions(
        self, positions: list[float], velocities: list[float] | None = None
    ):
        if positions is None:
            carb.log_warn(
                f"Cannot send {self.configuration.motion_group} position because its None"
            )
            return

        joint_state_request = wb_models.ExternalJointStreamDatapoint(
            motion_group=self.configuration.motion_group,
            value=wb_models.MotionGroupJoints(
                positions=positions,
                velocities=velocities if velocities is not None else self._stream_zeros,
                accelerations=self._stream_zeros,
                torques=self._stream_zeros,
            ),
        ).to_dict()
        await self.stream.send(json.dumps({"states": [joint_state_request]}))

    async def _send_positions_raw(
        self, positions: list[float], velocities: list[float] | None = None
    ):
        """Hot-path variant of send_joint_positions: identical wire format,
        built with plain dicts instead of pydantic model construction +
        to_dict. Runs once per received external-stream message (up to the
        controller's cycle rate), so per-call cost matters here. Velocities
        default to zeros (command echo); the measured-feedback path passes
        the per-frame PhysX sample."""
        await self.stream.send(
            json.dumps(
                {
                    "states": [
                        {
                            "motion_group": self.configuration.motion_group,
                            "value": {
                                "positions": positions,
                                "velocities": velocities
                                if velocities is not None
                                else self._stream_zeros,
                                "accelerations": self._stream_zeros,
                                "torques": self._stream_zeros,
                            },
                        }
                    ]
                }
            )
        )

    async def _parse(self, data: dict):
        if "error" in data:
            error_message = data["error"]["message"]
            carb.log_error(
                f"{self.configuration.motion_group_id} Received error {error_message}"
            )
            return
        if not data:
            carb.log_warn(
                f"Received empty data from RobotState {self.configuration.motion_group} websocket stream"
            )
            return

        result = data["result"]

        if self.is_external_joint_stream:
            # Raw dict access: only value.positions is read, so skip the full
            # ExternalJointStreamDatapoint.from_dict validation per message.
            if len(result) == 0:
                carb.log_warn(
                    f"Received empty motion state response for {self.configuration.motion_group_id}"
                )
                return
            if len(result) > 1:
                carb.log_warn(
                    f"Received multiple motion states for {self.configuration.motion_group_id}, only using the first one"
                )
            positions = (result[0].get("value") or {}).get("positions")
            if positions is None:
                carb.log_warn(
                    f"Received external joint datapoint without positions for {self.configuration.motion_group_id}"
                )
                return
            if self.timeline.is_playing():
                # Feedback MUST go out per received message, not per frame:
                # with an external source attached the virtual controller does
                # not advance its own joint state -- it waits for ours -- and
                # the trajectory executor paces itself against it, so per-frame
                # feedback time-stretched every execution to viewport rate.
                # The reply carries the measured state sampled once per frame
                # in apply_pending_joints, because physics only advances
                # between frames and reading it per message costs a GPU sync.
                # With no sample yet, or the setting off, the command is
                # echoed so the controller always gets an answer.
                self._pending_joint_positions = positions
                self._last_joints = positions
                if self._measured_positions is not None:
                    await self._send_positions_raw(
                        self._measured_positions, self._measured_velocities
                    )
                else:
                    await self._send_positions_raw(positions)
            else:
                # Paused: nothing to apply, and the plant is not stepping, so
                # answer with the last measured state and zero velocities.
                positions_reply = (
                    self._measured_positions
                    if self._measured_positions is not None
                    else self._last_joints
                )
                await self.send_joint_positions(positions_reply)
        else:
            # TODO the API state stream violates its spec in several ways; relax
            # parsing until resolved server-side:
            # - `execute` is null while the robot is idle, so the nested lookups
            #   must not assume dicts at any level
            # - `state.kind` can be KIND_UNKNOWN, which is not part of the spec
            # - int64 fields (`jogger_session_timestamp_ms`) arrive as strings
            #   (protobuf JSON convention) and fail the strict generated models
            execute = result.get("execute") or {}
            details = execute.get("details") or {}
            state = details.get("state") or {}
            if state.get("kind") == "KIND_UNKNOWN":
                carb.log_info(
                    f"Unsupported state kind KIND_UNKNOWN from {self.configuration.cell}/{self.configuration.motion_group}"
                )
                return
            # Raw dict access: only joint_position is read, and from_dict
            # reduces to obj.get("joint_position") for it, so skipping the
            # MotionGroupState construction (14 validated fields plus nested
            # sub-models, per message, faster than frame rate) is equivalent.
            # Profiling isolated essentially the whole 48ms frame time to this
            # parse path. No model also means the int64-as-string coercion of
            # jogger_session_timestamp_ms is no longer needed.
            joint_position = result.get("joint_position")
            if joint_position is not None:
                self._update_joints(joint_position)

    def _update_joints(self, joint_position: list[float]):
        self._last_joints = joint_position

        if self.timeline.is_stopped():
            return

        # Messages arrive faster than the frame rate (response_rate per robot);
        # only the newest target can take effect, so store it here and let
        # MotionGroupService apply it once per rendered frame.
        self._pending_joint_positions = joint_position

    def apply_pending_joints(self):
        """Apply the newest received joint positions; called once per frame.

        For the external joint stream this also samples the measured joint
        state from PhysX -- once per frame, because physics only advances
        between frames -- and the per-message replies in _parse reuse that
        sample, keeping the GPU->CPU readback off the message hot path.
        """
        if self._physics_feedback and self.is_external_joint_stream:
            self._refresh_measured_state()
        pending = self._pending_joint_positions
        if pending is None:
            return
        self._pending_joint_positions = None
        if self.timeline.is_stopped():
            self._idle_frames = 0
            return
        if self._idle_frames >= _IDLE_SLEEP_FRAMES:
            # We put the articulation to sleep; the direct physics-view write
            # in apply_joints does not wake it by itself.
            self._set_articulation_sleep(False)
        self._idle_frames = 0
        self.apply_joints(pending)

    def _encoded_sleep_body_ids(self) -> list[int]:
        """Bodies to sleep/wake as a unit: the articulation (any link path
        puts the whole articulation to sleep -- we use the PhysX anchor the
        SingleArticulation was resolved to) plus the link_0 satellite body,
        which is welded on via an excludeFromArticulation fixed joint and
        would otherwise stay awake and keep re-waking its neighbor. The prim
        lookups and path encoding run once per simulation view."""
        if self._sleep_body_ids is None:
            paths = [self.motion_group.articulation.prim_path]
            link_0 = f"{self.motion_group.identifier}/link_0"
            stage = omni.usd.get_context().get_stage()
            if stage and stage.GetPrimAtPath(link_0).IsValid():
                paths.append(link_0)
            self._sleep_paths = paths
            self._sleep_body_ids = [
                PhysicsSchemaTools.sdfPathToInt(path) for path in paths
            ]
        return self._sleep_body_ids

    def _sleep_targets_are_live(self) -> bool:
        """Whether every cached sleep/wake path still names an active rigid
        body on the current stage. A cached body id alone cannot tell a live
        body apart from one on a stage that has since been replaced (e.g. a
        playback merge): the path can still resolve there, only with
        physics:rigidBodyEnabled forced off for clean playback."""
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return False
        for path in self._sleep_paths or []:
            prim = stage.GetPrimAtPath(path)
            if not prim.IsValid():
                return False
            enabled_attr = prim.GetAttribute("physics:rigidBodyEnabled")
            if enabled_attr.IsValid() and not enabled_attr.Get():
                return False
        return True

    def _drop_sleep_targets(self) -> None:
        """Force the next sleep/wake to re-resolve paths and body ids."""
        self._sleep_body_ids = None
        self._sleep_paths = None

    def _set_articulation_sleep(self, sleep: bool) -> bool:
        """Idempotent PhysX sleep/wake; safe to re-issue in any state.

        False when nothing was issued, so a caller can retry rather than
        record a state the articulation is not in.
        """
        if self._sleep_unavailable:
            return False
        try:
            # The ids address the simulation the paths were encoded against.
            # A stage change while the timeline plays makes PhysX rebuild the
            # simulation view, so re-resolve when the view moved rather than
            # addressing bodies of the previous one, exactly as
            # _try_direct_physics_write re-inits its cached view.
            live_view = self._live_physics_view(self.motion_group.articulation)
            if live_view is not self._sleep_physics_view:
                self._sleep_physics_view = live_view
                self._drop_sleep_targets()

            physx = get_physx_simulation_interface()
            stage_id = omni.usd.get_context().get_stage_id()
            body_ids = self._encoded_sleep_body_ids()
            if not self._sleep_targets_are_live():
                # Transient: a playback merge disables the bodies and a reload
                # replaces them. Retry on a later idle window instead of
                # latching idle-sleep off for the connection's lifetime.
                self._drop_sleep_targets()
                return False
            for body_id in body_ids:
                if sleep:
                    physx.put_to_sleep(stage_id, body_id)
                else:
                    physx.wake_up(stage_id, body_id)
            # Sleeping works again, so a later failure is new information.
            self._sleep_failure_logged = False
            return True
        except AttributeError as error:
            # The interface or the isaacsim internals are not the shape this
            # relies on, which no later frame changes, so stop trying. Same
            # reading of AttributeError as _live_physics_view.
            self._sleep_unavailable = True
            carb.log_warn(
                f"Idle-sleep unavailable for {self.motion_group.identifier}: {error}"
            )
            return False
        except Exception as error:
            # Broad on purpose: this wraps a native binding and runs from the
            # per-frame hub, where an escaping exception would spam every
            # frame. A rebuilt simulation view can still restore sleeping, so
            # only the message is one-shot and the next idle window retries.
            self._drop_sleep_targets()
            if not self._sleep_failure_logged:
                self._sleep_failure_logged = True
                carb.log_warn(
                    f"Idle-sleep failed for {self.motion_group.identifier}, "
                    f"retrying on later idle windows: {error}"
                )
            return False

    def wake_for_reset(self):
        """Wake the articulation so a timeline STOP can write its reset back.

        Idle-sleep exists to stop PhysX's per-frame transform writeback for a
        robot at rest. That writeback is also what carries the reset-on-stop
        state out to USD and Fabric, so an articulation still asleep when the
        timeline stops keeps the last simulated joint values in both - the
        robot looks frozen mid-pose instead of returning to its authored one.

        The counter is cleared only once the wake was issued. Clearing it
        first would drop the retry in apply_pending_joints on the paths where
        the wake does nothing, leaving the articulation asleep for good.
        """
        if self._set_articulation_sleep(False):
            self._idle_frames = 0

    def maybe_sleep_when_idle(self):
        """Called once per frame by the hub: after _IDLE_SLEEP_FRAMES frames
        without a new joint target, put the articulation to sleep -- exactly
        once, on the frame the counter hits the threshold."""
        if self._sleep_unavailable or not self.timeline.is_playing():
            return
        self._idle_frames += 1
        if self._idle_frames == _IDLE_SLEEP_FRAMES:
            self._set_articulation_sleep(True)

    def _stream_joint_positions_in_api_units(self) -> list[float]:
        """Joint positions in stream order and API units (mm for prismatic).

        Reads through the articulation, which falls back to the USD
        JointStateAPI attributes when no physics view exists. open() primes the
        stream with this before the timeline starts, when there is no view to
        read, and the first measured sample replaces it.
        """
        return self._to_api_units(
            self._select_stream_joints(
                self.motion_group.articulation.get_joint_positions()
            )
        )

    def _select_stream_joints(self, all_values) -> list[float]:
        """Articulation DOF order -> stream joint order (see joint_indices)."""
        if self.joint_indices is None:
            return [float(x) for x in list(all_values)][: self.stream_joint_count]
        return [float(all_values[i]) for i in self.joint_indices]

    def _to_api_units(self, values: list[float]) -> list[float]:
        """Stage units -> API units. Positions and their derivatives alike."""
        return self._scale_prismatic_joints(values, to_api_units=True)

    def _to_stage_units(self, values: list[float]) -> list[float]:
        """API units -> stage units."""
        return self._scale_prismatic_joints(values, to_api_units=False)

    def _scale_prismatic_joints(
        self, values: list[float], to_api_units: bool
    ) -> list[float]:
        """Scale the prismatic entries of `values` between stage units and the
        API's millimeters, mutating and returning the list. Revolute joints are
        radians on both sides, so they are left alone."""
        if not self._has_prismatic_joints:
            return values
        stage = omni.usd.get_context().get_stage()
        meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        factor = meters_per_unit / 0.001 if to_api_units else 0.001 / meters_per_unit
        for index, dh_param in enumerate(self.motion_group.motion_group_dh_parameters):
            if dh_param.type == JointTypeEnum.PRISMATIC_JOINT:
                values[index] *= factor
        return values

    def _refresh_measured_state(self) -> None:
        """Sample the measured joint state (positions + velocities) from PhysX.

        Selection and units mirror the command direction. When the sample
        cannot be taken the old one is dropped, so replies fall back to
        echoing the command rather than repeating a frozen state the
        controller would wait on forever.
        """
        articulation = self.motion_group.articulation
        if (
            not articulation
            or not articulation.is_valid()
            or self.stream_joint_count is None
            or self._live_physics_view(articulation) is None
        ):
            self._drop_measured_state()
            return
        try:
            all_positions = articulation.get_joint_positions()
            all_velocities = articulation.get_joint_velocities()
        except (RuntimeError, ValueError, AttributeError) as error:
            if not self._readback_failure_logged:
                self._readback_failure_logged = True
                carb.log_warn(
                    f"Measured-state readback failed for {self.motion_group.identifier}; "
                    f"external joint stream falls back to echoing commands: {error}"
                )
            self._drop_measured_state()
            return
        if all_positions is None or all_velocities is None:
            self._drop_measured_state()
            return
        self._measured_positions = self._to_api_units(
            self._select_stream_joints(all_positions)
        )
        self._measured_velocities = self._to_api_units(
            self._select_stream_joints(all_velocities)
        )

    def _drop_measured_state(self) -> None:
        self._measured_positions = None
        self._measured_velocities = None

    def _live_physics_view(self, articulation):
        """The articulation's physics view, or None when there is none.

        isaacsim offers no public accessor, so this reads a private attribute.
        A missing attribute means those internals changed, which is reported
        once rather than swallowed. Without a view the articulation falls back
        to the USD JointStateAPI attributes, which are stale on Fabric-only
        stages because physics state is never written back to USD there.
        """
        try:
            return articulation._articulation_view._physics_view
        except AttributeError as error:
            if not self._physics_view_probe_logged:
                self._physics_view_probe_logged = True
                carb.log_warn(
                    f"Cannot read the physics view of {self.motion_group.identifier} "
                    f"({error}); isaacsim internals this relies on have changed. "
                    "Measured feedback and direct target writes are disabled."
                )
            return None

    def _init_direct_physics_write(self):
        """One-time check whether joint targets can be written directly to
        the physics view, bypassing apply_action/set_joint_position_targets:
        both fetch the CURRENT target array from the physics view before
        splicing our values in and writing it back (the isaacsim source has
        its own "# TODO: optimize this operation" there). Profiling traced
        ~100ms of a ~112ms frame to that get-then-splice round trip across
        4 motion-group connections.

        Only safe when joint_indices is a full permutation of this
        articulation's DOF range: then every DOF target is overwritten anyway
        and the fetch-current step is pure waste. If that does not hold (e.g.
        a connection owning a subset of a shared articulation's DOFs), the
        check fails and every call takes the slow-but-always-correct path.
        """
        try:
            articulation = self.motion_group.articulation
            view = articulation._articulation_view
            physics_view = view._physics_view
            if physics_view is None:
                # The physics view isn't built yet (or was just torn down).
                # Stay None so the next frame re-checks instead of latching
                # the direct write off permanently.
                self._direct_physics_write = None
                return
            num_dof = articulation.num_dof
            indices = (
                self.joint_indices
                if self.joint_indices is not None
                else list(range(num_dof))
            )
            if sorted(indices) != list(range(num_dof)):
                carb.log_info(
                    f"{self.configuration.motion_group}: joint_indices isn't a full DOF "
                    "permutation, direct physics-view target write not used"
                )
                self._direct_physics_write = _DIRECT_PHYSICS_WRITE_UNAVAILABLE
                return
            env_indices = view._backend_utils.resolve_indices(
                None, view.count, view._device
            )
            self._direct_physics_write = (physics_view, env_indices)
        except Exception as error:
            carb.log_info(
                f"Direct physics-view target write unavailable, using apply_action instead: {error}"
            )
            self._direct_physics_write = _DIRECT_PHYSICS_WRITE_UNAVAILABLE

    def _try_direct_physics_write(self, joint_positions_array: torch.Tensor) -> bool:
        if self._direct_physics_write is None:
            self._init_direct_physics_write()
        if (
            self._direct_physics_write is None
            or self._direct_physics_write is _DIRECT_PHYSICS_WRITE_UNAVAILABLE
        ):
            return False
        physics_view, env_indices = self._direct_physics_write
        # The cached physics view is a direct reference captured once. Any
        # stage change while the timeline plays (e.g. creating a ghost object)
        # makes PhysX rebuild the simulation view, leaving our cached one
        # detached: set_dof_position_targets would then write into a buffer
        # the live sim no longer reads -- silently, every frame -- and the arm
        # loses joint control and collapses. Re-read the live view and re-init
        # if it moved; SingleArticulation.is_valid() does not catch this.
        live_view = self._live_physics_view(self.motion_group.articulation)
        if live_view is not physics_view:
            self._init_direct_physics_write()
            if (
                self._direct_physics_write is None
                or self._direct_physics_write is _DIRECT_PHYSICS_WRITE_UNAVAILABLE
            ):
                return False
            physics_view, env_indices = self._direct_physics_write
        try:
            all_dof_targets = torch.empty(
                (1, self._joint_indices_tensor.shape[0]), dtype=torch.float32
            )
            all_dof_targets[0, self._joint_indices_tensor] = joint_positions_array
            physics_view.set_dof_position_targets(all_dof_targets, env_indices)
            return True
        except Exception as error:
            carb.log_warn(
                f"Direct physics-view target write failed, falling back to apply_action and "
                f"re-checking the physics view next frame: {error}"
            )
            # Back to None so a rebuilt simulation view can restore the
            # direct write, instead of latching it off for the connection's
            # lifetime.
            self._direct_physics_write = None
            return False

    def _reject(self, message: str) -> bool:
        """Log *message* once for this connector and report the target as unusable."""
        if not self._apply_rejection_logged:
            self._apply_rejection_logged = True
            carb.log_error(message)
        return False

    def _targets_applicable(self, joint_positions: list[float]) -> bool:
        """Whether these targets can be handed to PhysX without corrupting it.

        ``apply_action`` indexes the value tensor with the index tensor in native
        code, so a length mismatch or an out-of-range index is an out-of-bounds
        GPU write: it kills the CUDA context and takes PhysX and RTX down for the
        rest of the session. Non-finite targets are equally fatal. Both are
        reachable when the articulation root could not be resolved (see
        ``get_root_articulation_path``) and the indices come from a different
        articulation than the stream's values.
        """
        for index, value in enumerate(joint_positions):
            if not math.isfinite(value):
                return self._reject(
                    f"Discarding joint target for {self.configuration.motion_group}: "
                    f"value at index {index} is not finite ({value!r}). "
                    f"Targets: {joint_positions}"
                )

        if self.joint_indices is None:
            return True

        if len(self.joint_indices) != len(joint_positions):
            return self._reject(
                f"Discarding joint target for {self.configuration.motion_group}: "
                f"{len(joint_positions)} value(s) for {len(self.joint_indices)} "
                f"joint index/indices {self.joint_indices} on articulation "
                f"'{self.motion_group.identifier}'. The stream and the resolved "
                f"articulation disagree about the joint count."
            )

        num_dof = getattr(self.motion_group.articulation, "num_dof", None)
        if num_dof is not None and max(self.joint_indices, default=-1) >= num_dof:
            return self._reject(
                f"Discarding joint target for {self.configuration.motion_group}: "
                f"joint indices {self.joint_indices} exceed the "
                f"{num_dof} degree(s) of freedom of articulation "
                f"'{self.motion_group.identifier}'."
            )

        return True

    def apply_joints(self, joint_positions: list[float]):
        """
        This function is called when the user changes one of the float fields
        to control a motion_group joint position target. The index of the joint and the new
        desired value are passed in as arguments.

        This function assumes that there is a guarantee it is called safely.
        I.e. A valid Articulation has been selected and initialized
        and the timeline is playing.  These gurantees are given by careful UI
        programming.  The joint control frames are only visible to the user when
        these guarantees are met.

        Args:
            joint_positions (float): New position target for motion_group joints (needs to match joint count)
        """
        if (
            not self.motion_group.articulation
            or not self.motion_group.articulation.is_valid()
        ):
            carb.log_error(f"Invalid articulation for {(self.motion_group.identifier)}")
            return

        if self.stream_joint_count is None:
            carb.log_error(
                f'Attempted to set joints for "{self.configuration.controller}" but joint count is unknown'
            )
            return

        if self.stream_joint_count < len(joint_positions):
            carb.log_verbose(
                f'Attempted to set "{self.configuration.controller}" joints with more joint values than known joint count {self.stream_joint_count}'
            )
            return

        # Re-applying an unchanged target keeps the PhysX articulation awake and
        # dirties USD every frame; Kit's selection-coupled listeners (property
        # widgets, transform gizmo) then reprocess those notices per frame. Skip
        # the apply entirely while the incoming target does not move.
        incoming_positions = list(joint_positions)

        if not self._targets_applicable(incoming_positions):
            return

        if (
            self._last_applied_joints is not None
            and len(self._last_applied_joints) == len(incoming_positions)
            and all(
                abs(last - new) <= _JOINT_TARGET_EPSILON
                for last, new in zip(self._last_applied_joints, incoming_positions)
            )
        ):
            return

        joint_positions = self._to_stage_units(joint_positions)

        # NOTE: reusing a buffer via as_tensor()/copy_() was measured ~35%
        # slower than a fresh torch.tensor at this size; the values change
        # every call, so there is no reuse to be had anyway.
        joint_positions_array = torch.tensor(joint_positions, dtype=torch.float32)

        if self._joint_indices_tensor is None:
            # open() derives the tensor from joint_indices; this fallback only
            # runs when they were never resolved (sequential legacy layout).
            self._joint_indices_tensor = torch.tensor(
                range(len(joint_positions)), dtype=torch.long
            )
        joint_indices_array = self._joint_indices_tensor

        # Direct physics-view write when verified safe (see _init_direct_physics_write).
        if not self._try_direct_physics_write(joint_positions_array):
            motion_group_action = ArticulationAction(
                joint_positions=joint_positions_array,
                joint_indices=joint_indices_array,
            )
            self.motion_group.articulation.apply_action(motion_group_action)
        # Snapshot of the unscaled incoming values for the unchanged-target check.
        self._last_applied_joints = incoming_positions
